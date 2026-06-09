"""
sirius_vector_db.py — Motor de Indexação Vetorial do S.I.R.I.U.S.
==================================================================

Responsabilidades:
  • Ler memoria_permanente + conhecimento_geral do SQLite
  • Transformar cada texto em embedding via sentence-transformers
  • Manter índice FAISS (IndexFlatIP — produto interno = cosine similarity)
  • Persistir índice em disco (.index) e mapa id→texto (.meta)
  • Atualizar automaticamente quando o banco cresce

Integração com o ecossistema:
  • sirius_rag.py   → consulta via SiriusVectorDB.buscar()
  • limpar_banco.py → chama SiriusVectorDB.rebuild() após limpeza
  • memoria.py      → não é alterado (lido somente)

Instalação das dependências:
  pip install faiss-cpu sentence-transformers

Compatibilidade CPU-only:
  O código usa faiss-cpu e define OMP_NUM_THREADS=1 para evitar
  deadlocks em ambientes com multiprocessing.
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import hashlib
import threading
import pickle
import time
from datetime import datetime
from typing import Optional

# Fix para deadlock do OpenMP em alguns ambientes Windows
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ── Paths ──────────────────────────────────────────────────────────────────── #
_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
_DIR_DATA = os.path.join(_DIR_RAIZ, "data")
os.makedirs(_DIR_DATA, exist_ok=True)

DB_TREINO  = os.path.join(_DIR_DATA, "sirius_treino.db")
DB_PESSOAL = os.path.join(_DIR_DATA, "sirius_pessoal.db")

INDICE_PATH = os.path.join(_DIR_DATA, "sirius_vetores.index")
META_PATH   = os.path.join(_DIR_DATA, "sirius_vetores.meta")

# ── Modelo de embeddings ───────────────────────────────────────────────────── #
MODELO_EMBEDDING = "paraphrase-multilingual-MiniLM-L12-v2"
DIM_EMBEDDING    = 384   # dimensão fixa do modelo acima

# ── Constantes ─────────────────────────────────────────────────────────────── #
BATCH_ENCODE      = 64    # sentenças por batch no encode
MIN_TEXTO_CHARS   = 30    # descarta textos muito curtos
MAX_TEXTO_CHARS   = 2000  # trunca textos muito longos para evitar lentidão
REBUILD_THRESHOLD = 50    # novos docs antes de reconstruir automaticamente


# ══════════════════════════════════════════════════════════════════════════════
# Carregamento lazy das dependências pesadas
# ══════════════════════════════════════════════════════════════════════════════

_faiss   = None
_model   = None
_np      = None
_lock_init = threading.Lock()


def _get_faiss():
    global _faiss
    if _faiss is None:
        with _lock_init:
            if _faiss is None:
                import faiss as _f
                _faiss = _f
    return _faiss


def _get_np():
    global _np
    if _np is None:
        import numpy as np
        _np = np
    return _np


def _get_model():
    global _model
    if _model is None:
        with _lock_init:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                print(f"[VECTORDB]: Carregando modelo '{MODELO_EMBEDDING}'…")
                _model = SentenceTransformer(MODELO_EMBEDDING)
                print("[VECTORDB]: Modelo pronto.")
    return _model


# ══════════════════════════════════════════════════════════════════════════════
# Utilitários
# ══════════════════════════════════════════════════════════════════════════════

def _hash_texto(texto: str) -> str:
    """SHA-1 curto do texto — usado para detectar duplicatas."""
    return hashlib.sha1(texto.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _limpar_texto(texto: str) -> str:
    """Normaliza e trunca o texto antes de gerar embedding."""
    t = str(texto or "").strip()
    t = " ".join(t.split())           # colapsa múltiplos espaços/newlines
    return t[:MAX_TEXTO_CHARS]


# ══════════════════════════════════════════════════════════════════════════════
# Leitura do banco
# ══════════════════════════════════════════════════════════════════════════════

def _ler_docs_do_banco(user_id: Optional[str] = None) -> list[dict]:
    """
    Lê documentos de conhecimento_geral e memoria_permanente.

    Retorna lista de dicts:
      { "id": str, "texto": str, "tema": str, "fonte": str, "user_id": str }
    """
    docs = []
    vistos: set[str] = set()

    def _query(db_path: str, tabela: str, col_id: str):
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")

            if user_id:
                rows = conn.execute(
                    f"SELECT {col_id}, tema, conteudo, user_id "
                    f"FROM {tabela} WHERE user_id = ?",
                    (user_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {col_id}, tema, conteudo, "
                    f"COALESCE(user_id, 'admin') "
                    f"FROM {tabela}"
                ).fetchall()
            conn.close()

            for row_id, tema, conteudo, uid in rows:
                texto = _limpar_texto(f"{tema}. {conteudo}")
                if len(texto) < MIN_TEXTO_CHARS:
                    continue
                h = _hash_texto(texto)
                if h in vistos:
                    continue
                vistos.add(h)
                docs.append({
                    "id":      f"{tabela}:{row_id}",
                    "texto":   texto,
                    "tema":    str(tema or "geral"),
                    "fonte":   tabela,
                    "user_id": str(uid or "admin"),
                    "hash":    h,
                })
        except Exception as e:
            print(f"[VECTORDB]: Erro ao ler {tabela}: {e}")

    _query(DB_TREINO, "conhecimento_geral", "id")
    _query(DB_TREINO, "memoria_permanente", "id")
    return docs


# ══════════════════════════════════════════════════════════════════════════════
# SiriusVectorDB — classe principal
# ══════════════════════════════════════════════════════════════════════════════

class SiriusVectorDB:
    """
    Base de dados vetorial FAISS para busca semântica.

    Uso típico:
        vdb = SiriusVectorDB()
        vdb.rebuild()                   # (re)constrói o índice a partir do SQLite
        resultados = vdb.buscar("o que é machine learning", k=3)
        # resultados → [{"texto": ..., "tema": ..., "score": 0.82, ...}, ...]

    Persistência:
        sirius_vetores.index  — índice FAISS binário
        sirius_vetores.meta   — mapa id→metadados (pickle)

    Thread-safety:
        Leitura (buscar) é thread-safe.
        Escrita (rebuild / adicionar) usa _lock_write.

    Integração com limpar_banco.py:
        Chame  SiriusVectorDB().rebuild()  ao final do main() do limpar_banco.py.
        O índice será reconstruído com os dados limpos automaticamente.
    """

    def __init__(
        self,
        indice_path: str = INDICE_PATH,
        meta_path:   str = META_PATH,
        user_id:     Optional[str] = None,
    ):
        self.indice_path = indice_path
        self.meta_path   = meta_path
        self.user_id     = user_id

        self._index: Optional[object] = None   # faiss.Index
        self._meta:  list[dict]       = []      # metadados paralelos ao índice
        self._hashes: set[str]        = set()   # hashes dos docs já indexados
        self._lock_write = threading.RLock()
        self._n_desde_rebuild = 0

        # Tenta carregar índice existente
        self._carregar_indice()

    # ── Índice FAISS ─────────────────────────────────────────────────────── #

    def _criar_indice_vazio(self):
        """Cria um IndexFlatIP normalizado (equivalente a cosine similarity)."""
        faiss = _get_faiss()
        np    = _get_np()
        # IndexFlatIP + normalização L2 → cosine similarity
        self._index = faiss.IndexFlatIP(DIM_EMBEDDING)
        self._meta  = []
        self._hashes = set()

    def _salvar_indice(self):
        faiss = _get_faiss()
        try:
            faiss.write_index(self._index, self.indice_path)
            with open(self.meta_path, "wb") as f:
                pickle.dump({"meta": self._meta, "hashes": self._hashes}, f)
        except Exception as e:
            print(f"[VECTORDB]: Erro ao salvar índice: {e}")

    def _carregar_indice(self):
        faiss = _get_faiss()
        if not os.path.exists(self.indice_path) or not os.path.exists(self.meta_path):
            print("[VECTORDB]: Índice não encontrado — rode rebuild() para criar.")
            return

        try:
            self._index = faiss.read_index(self.indice_path)
            with open(self.meta_path, "rb") as f:
                dados = pickle.load(f)
            self._meta   = dados.get("meta", [])
            self._hashes = dados.get("hashes", set())
            n = self._index.ntotal
            print(f"\033[92m[VECTORDB]: Índice carregado — {n} vetores.\033[0m")
        except Exception as e:
            print(f"[VECTORDB]: Erro ao carregar índice: {e}")
            self._criar_indice_vazio()

    # ── Geração de embeddings ────────────────────────────────────────────── #

    def _encode(self, textos: list[str]):
        """
        Gera embeddings normalizados (L2) para uma lista de textos.
        Retorna array float32 (N, DIM_EMBEDDING).
        """
        np    = _get_np()
        model = _get_model()
        embs  = model.encode(
            textos,
            batch_size=BATCH_ENCODE,
            show_progress_bar=False,
            normalize_embeddings=True,   # L2-normalizado → produto interno = cosine
            convert_to_numpy=True,
        )
        return embs.astype(np.float32)

    # ── Rebuild completo ─────────────────────────────────────────────────── #

    def rebuild(self, user_id: Optional[str] = None) -> bool:
        """
        Reconstrói o índice do zero lendo todo o banco SQLite.

        Chamado por:
          • limpar_banco.py  — após remoção de ruído
          • sirius_rag.py    — quando index está desatualizado
          • Usuário          — "rebuilda o rag"

        Args:
            user_id: filtrar por usuário específico (None = todos)

        Returns:
            True se rebuild foi bem-sucedido, False caso contrário.
        """
        uid = user_id or self.user_id
        print(f"\033[94m[VECTORDB]: Iniciando rebuild (user_id={uid})...\033[0m")

        try:
            docs = _ler_docs_do_banco(uid)
            if not docs:
                print("[VECTORDB]: Nenhum documento encontrado para indexar.")
                return False

            textos = [d["texto"] for d in docs]

            t0   = time.time()
            embs = self._encode(textos)
            dt   = time.time() - t0

            with self._lock_write:
                self._criar_indice_vazio()
                self._index.add(embs)
                self._meta   = docs
                self._hashes = {d["hash"] for d in docs}
                self._n_desde_rebuild = 0
                self._salvar_indice()

            print(
                f"\033[92m[VECTORDB]: Rebuild OK — {len(docs)} docs, "
                f"{dt:.1f}s.\033[0m"
            )
            return True

        except Exception as e:
            print(f"[VECTORDB]: Erro no rebuild: {e}")
            return False

    # ── Adição incremental ───────────────────────────────────────────────── #

    def adicionar(
        self,
        texto: str,
        tema:  str   = "geral",
        fonte: str   = "manual",
        uid:   str   = "admin",
        doc_id: str  = "",
    ) -> bool:
        """
        Adiciona um único documento ao índice sem rebuild completo.

        Usado por:
          • SiriusRAG.adicionar_feedback()   — feedbacks do usuário
          • sirius_agentes.py                — ao aprender novo tema

        Ignora documentos duplicados (via hash).
        Dispara rebuild automático a cada REBUILD_THRESHOLD adições.
        """
        texto_limpo = _limpar_texto(texto)
        if len(texto_limpo) < MIN_TEXTO_CHARS:
            return False

        h = _hash_texto(texto_limpo)
        if h in self._hashes:
            return False   # já indexado

        try:
            emb = self._encode([texto_limpo])   # (1, DIM)

            with self._lock_write:
                if self._index is None:
                    self._criar_indice_vazio()

                self._index.add(emb)
                self._meta.append({
                    "id":      doc_id or f"manual:{_hash_texto(texto_limpo)}",
                    "texto":   texto_limpo,
                    "tema":    tema,
                    "fonte":   fonte,
                    "user_id": uid,
                    "hash":    h,
                    "ts":      datetime.now().isoformat(timespec="seconds"),
                })
                self._hashes.add(h)
                self._n_desde_rebuild += 1
                self._salvar_indice()

            # Rebuild automático quando acumula muitos docs incrementais
            if self._n_desde_rebuild >= REBUILD_THRESHOLD:
                print(f"[VECTORDB]: {REBUILD_THRESHOLD} adições → rebuild automático.")
                threading.Thread(target=self.rebuild, daemon=True).start()

            return True

        except Exception as e:
            print(f"[VECTORDB]: Erro ao adicionar doc: {e}")
            return False

    # ── Busca por similaridade ───────────────────────────────────────────── #

    def buscar(
        self,
        consulta:  str,
        k:         int   = 5,
        user_id:   Optional[str] = None,
        score_min: float = 0.0,
    ) -> list[dict]:
        """
        Busca os k documentos mais similares semanticamente.

        Retorna lista de dicts ordenada por score decrescente:
          {
            "texto":   str,
            "tema":    str,
            "fonte":   str,
            "user_id": str,
            "score":   float,   # cosine similarity ∈ [0, 1]
          }

        Args:
            consulta:  texto da pergunta
            k:         número de resultados desejados
            user_id:   filtrar por usuário (None = sem filtro)
            score_min: descartar resultados com score abaixo deste valor

        Retorna lista vazia se o índice não estiver construído.
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        consulta_limpa = _limpar_texto(consulta)
        if not consulta_limpa:
            return []

        try:
            np   = _get_np()
            emb  = self._encode([consulta_limpa])   # (1, DIM)

            # FAISS retorna produto interno — igual a cosine pois os vetores
            # estão L2-normalizados
            k_busca = min(k * 3, self._index.ntotal)  # pede mais para filtrar depois
            scores, indices = self._index.search(emb, k_busca)

            resultados = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self._meta):
                    continue

                doc = self._meta[idx]

                # Filtro de usuário (opcional)
                if user_id and doc.get("user_id") and doc["user_id"] != user_id:
                    continue

                sc = float(score)
                if sc < score_min:
                    continue

                resultados.append({
                    "texto":   doc["texto"],
                    "tema":    doc["tema"],
                    "fonte":   doc["fonte"],
                    "user_id": doc.get("user_id", "admin"),
                    "score":   round(sc, 4),
                })

                if len(resultados) >= k:
                    break

            return resultados

        except Exception as e:
            print(f"[VECTORDB]: Erro na busca: {e}")
            return []

    # ── Rebaixar / Esquecer memória ──────────────────────────────────────── #

    def rebaixar_memoria(
        self,
        id_vetor:   Optional[str] = None,
        tema:       Optional[str] = None,
        fragmento:  Optional[str] = None,
    ) -> int:
        """
        Remove documentos do índice FAISS para que nunca mais sejam retornados.

        Pode identificar o documento por:
          id_vetor   — ID exato (ex: "conhecimento_geral:42")
          tema       — remove TODOS com esse tema (substring, case-insensitive)
          fragmento  — remove todos que contêm este fragmento no texto

        Não deleta do SQLite — apenas remove do índice vetorial em memória e disco.
        O documento voltaria somente se rebuild() for chamado manualmente.

        Retorna o número de vetores removidos.

        Uso:
            vdb.rebaixar_memoria(tema="minecraft")
            vdb.rebaixar_memoria(id_vetor="conhecimento_geral:17")
            vdb.rebaixar_memoria(fragmento="servidor de minecraft")
        """
        if id_vetor is None and tema is None and fragmento is None:
            print("[VECTORDB]: rebaixar_memoria() precisa de ao menos um argumento.")
            return 0

        with self._lock_write:
            if not self._meta:
                return 0

            # ── Identifica índices a remover ───────────────────────────────
            indices_remover: list[int] = []
            for i, doc in enumerate(self._meta):
                match = False
                if id_vetor and doc.get("id") == id_vetor:
                    match = True
                if tema and tema.lower() in (doc.get("tema") or "").lower():
                    match = True
                if fragmento and fragmento.lower() in (doc.get("texto") or "").lower():
                    match = True
                if match:
                    indices_remover.append(i)

            if not indices_remover:
                print(
                    f"[VECTORDB]: Nenhum documento encontrado para rebaixar "
                    f"(id={id_vetor}, tema={tema}, fragmento={fragmento})."
                )
                return 0

            n_antes = len(self._meta)

            # ── FAISS não suporta remoção direta — reconstrói sem os vetores ──
            # (IndexFlatIP é pequeno o suficiente para isso ser rápido)
            indices_manter = [
                i for i in range(len(self._meta))
                if i not in set(indices_remover)
            ]

            if not indices_manter:
                # Índice ficaria vazio
                self._criar_indice_vazio()
            else:
                # Recria o índice apenas com os documentos restantes
                try:
                    faiss = _get_faiss()
                    np    = _get_np()

                    textos_manter = [self._meta[i]["texto"] for i in indices_manter]
                    embs          = self._encode(textos_manter)

                    novo_index = faiss.IndexFlatIP(DIM_EMBEDDING)
                    novo_index.add(embs)

                    self._index  = novo_index
                    self._meta   = [self._meta[i] for i in indices_manter]
                    self._hashes = {d["hash"] for d in self._meta if "hash" in d}

                except Exception as e:
                    print(f"[VECTORDB]: Erro ao reconstruir índice no rebaixar: {e}")
                    return 0

            self._salvar_indice()

        removidos = n_antes - len(self._meta)
        print(
            f"\033[93m[VECTORDB]: {removidos} vetor(es) rebaixados "
            f"(id={id_vetor}, tema={tema}). "
            f"Índice tem agora {len(self._meta)} documentos.\033[0m"
        )
        return removidos

    def rebaixar_por_feedback(self, query: str, top_k: int = 1) -> int:
        """
        Rebaixa automaticamente o(s) documento(s) retornado(s) para 'query'.
        Chamado quando o usuário diz "Errado", "Esquece isso" ou "Obsoleto".

        Busca os top_k resultados para a query e remove-os do índice.
        """
        resultados = self.buscar(query, k=top_k, score_min=0.0)
        if not resultados:
            print(f"[VECTORDB]: Nenhum resultado para rebaixar por feedback: '{query}'")
            return 0

        total = 0
        for res in resultados:
            # Usa fragmento do texto para identificar o documento
            fragmento = res["texto"][:60]
            total += self.rebaixar_memoria(fragmento=fragmento)

        return total

    # ── Status ───────────────────────────────────────────────────────────── #

    def status(self) -> dict:
        n_indice = self._index.ntotal if self._index else 0
        n_banco  = self._contar_docs_no_banco()
        return {
            "indice_construido":  self._index is not None and n_indice > 0,
            "docs_no_indice":     n_indice,
            "docs_no_banco":      n_banco,
            "indice_desatualizado": n_banco > n_indice + REBUILD_THRESHOLD,
            "indice_path":        self.indice_path,
            "modelo":             MODELO_EMBEDDING,
            "dim":                DIM_EMBEDDING,
            "n_desde_rebuild":    self._n_desde_rebuild,
        }

    def _contar_docs_no_banco(self) -> int:
        total = 0
        for db, tabela in [(DB_TREINO, "conhecimento_geral"), (DB_TREINO, "memoria_permanente")]:
            if not os.path.exists(db):
                continue
            try:
                conn = sqlite3.connect(db, timeout=3)
                total += conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
                conn.close()
            except Exception:
                pass
        return total

    def esta_pronto(self) -> bool:
        return self._index is not None and self._index.ntotal > 0


# ══════════════════════════════════════════════════════════════════════════════
# Hook para limpar_banco.py
# ══════════════════════════════════════════════════════════════════════════════

def rebuild_apos_limpeza(user_id: Optional[str] = None) -> bool:
    """
    Chamado pelo limpar_banco.py após a limpeza do banco.
    Reconstrói o índice vetorial com os dados já limpos.

    Adicione ao final do main() em limpar_banco.py:
        from sirius_vector_db import rebuild_apos_limpeza
        rebuild_apos_limpeza()
    """
    print("\n[VECTORDB]: Reconstruindo índice após limpeza do banco...")
    vdb = SiriusVectorDB(user_id=user_id)
    ok  = vdb.rebuild(user_id=user_id)
    if ok:
        s = vdb.status()
        print(
            f"[VECTORDB]: Índice reconstruído — "
            f"{s['docs_no_indice']} docs indexados."
        )
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# Standalone
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SiriusVectorDB CLI")
    parser.add_argument("--rebuild",  action="store_true", help="Reconstrói o índice")
    parser.add_argument("--status",   action="store_true", help="Mostra status do índice")
    parser.add_argument("--buscar",   type=str,            help="Busca semântica")
    parser.add_argument("--k",        type=int, default=3, help="Número de resultados")
    parser.add_argument("--user-id",  type=str, default=None)
    args = parser.parse_args()

    vdb = SiriusVectorDB(user_id=args.user_id)

    if args.rebuild:
        vdb.rebuild(user_id=args.user_id)

    if args.status:
        s = vdb.status()
        print("\n[STATUS DO ÍNDICE VETORIAL]")
        for k, v in s.items():
            print(f"  {k}: {v}")

    if args.buscar:
        print(f"\nBuscando: '{args.buscar}' (k={args.k})")
        resultados = vdb.buscar(args.buscar, k=args.k)
        if not resultados:
            print("  Nenhum resultado.")
        for i, r in enumerate(resultados, 1):
            print(f"\n  [{i}] score={r['score']:.3f}  tema={r['tema']}")
            print(f"       {r['texto'][:200]}...")