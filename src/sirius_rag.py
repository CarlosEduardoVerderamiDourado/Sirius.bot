"""
sirius_rag.py — RAG Semântico do S.I.R.I.U.S.  (Retrieval-Augmented Generation)
=================================================================================

Arquitetura de 3 backends em cascata (escolhe o melhor disponível):

  Backend 1 — SiriusVectorDB   (sirius_vector_db.py + faiss-cpu + sentence-transformers)
  Backend 2 — FAISS Inline     (faiss-cpu + sentence-transformers, sem sirius_vector_db.py)
  Backend 3 — TF-IDF Fallback  (scikit-learn apenas — sem GPU, sem binários C++)

  O backend é escolhido automaticamente no __init__.
  A interface pública é idêntica independente do backend ativo.

  ┌─────────────────────────────────────────────────────────────────────┐
  │                      SiriusRAG.responder(query)                     │
  │  backend → .buscar() → MMR rerank → threshold dual:                │
  │    qualidade_min < 0.60  → TEXTO BRUTO   (contexto do sanduíche)   │
  │    qualidade_min ≥ 0.60  → RESPOSTA SINTETIZADA (falar direto)     │
  └─────────────────────────────────────────────────────────────────────┘

Bugs corrigidos:
  [BUG 1] adicionar_feedback() passava user_id= a salvar_estudo_autonomo() → TypeError.
  [BUG 2] responder(0.35) retornava texto sintetizado → contaminava sanduíche.
  [BUG 3] Threshold morto após buscar(score_min=) — removido.

Instalação mínima (TF-IDF):   pip install scikit-learn
Instalação completa:           pip install faiss-cpu sentence-transformers
"""

from __future__ import annotations

import os
import sys
import re
import threading
import time
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────── #
_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
_DIR_DATA = os.path.join(_DIR_RAIZ, "data")
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

# ── Thresholds ──────────────────────────────────────────────────────────────── #
SCORE_CONFIANTE = 0.60   # acima disso → resposta sintetizada direta
SCORE_CAUTELOSO = 0.40   # acima disso mas abaixo de CONFIANTE → texto bruto cauteloso

# Candidatos retornados pelo FAISS antes do re-ranking
K_CANDIDATOS = 5
# Chunks finais usados no contexto
K_CONTEXTO   = 3

# Fator λ do MMR — 0.0 = máxima diversidade, 1.0 = máxima relevância
MMR_LAMBDA = 0.65

# Limite de caracteres do contexto bruto retornado ao cerebro
MAX_CONTEXTO_CHARS = 800


# =============================================================================
# Re-ranking MMR (Maximal Marginal Relevance)
# =============================================================================

def _mmr_rerank(
    candidatos: list[dict],
    lam: float = MMR_LAMBDA,
    k:   int   = K_CONTEXTO,
) -> list[dict]:
    """
    Aplica MMR para equilibrar relevância e diversidade entre os chunks.

    MMR(d) = λ · relevância(d, query) − (1−λ) · max_similaridade(d, já_selecionados)

    Similaridade entre chunks calculada por Jaccard sobre tokens (sem dependência
    de embeddings secundários em runtime).
    """
    if len(candidatos) <= k:
        return candidatos

    def _jaccard(a: str, b: str) -> float:
        toks_a = set(re.sub(r"[^\w\s]", "", a.lower()).split())
        toks_b = set(re.sub(r"[^\w\s]", "", b.lower()).split())
        if not toks_a or not toks_b:
            return 0.0
        return len(toks_a & toks_b) / len(toks_a | toks_b)

    selecionados = [candidatos[0]]
    restantes    = candidatos[1:]

    while len(selecionados) < k and restantes:
        melhor_score = -1.0
        melhor_idx   = 0

        for i, cand in enumerate(restantes):
            rel     = cand["score"]
            sim_max = max(_jaccard(cand["texto"], sel["texto"]) for sel in selecionados)
            mmr_sc  = lam * rel - (1.0 - lam) * sim_max
            if mmr_sc > melhor_score:
                melhor_score = mmr_sc
                melhor_idx   = i

        selecionados.append(restantes.pop(melhor_idx))

    return selecionados


# =============================================================================
# Formatadores de saída
# =============================================================================

# Marcadores de contaminação — chunks com estes padrões são descartados do RAG
_CONTAMINANTES_RAG = (
    "mensagem enviada para",
    "mensagem enviada",
    "parça",
    "parca",
    "eae, mano",
    "boa noite pra você também",
)

def _chunk_contaminado(texto: str) -> bool:
    """Retorna True se o chunk contém contaminação de contexto cruzado."""
    t = texto.lower()
    return any(c in t for c in _CONTAMINANTES_RAG)


def _limpar_chunk_agressivo(texto: str, query: Optional[str] = None) -> str:
    """
    Limpeza agressiva de um chunk — remove artefatos de scraping e contaminantes.
    
    Mantém e aproveita o conteúdo do banco, mas remove:
      • URLs inline [https://...] e soltas https://...
      • Prefixos de fonte [wikipedia_pt], [ironhack.com], etc
      • Marcadores de contaminação (mano, kkkk, "peço desculpas")
      • Ecos da query (se query fornecida)
      • Espaços múltiplos
    
    Retorna a string limpa, ou '' se ficar muito curta após limpeza.
    """
    t = texto.strip()
    
    # Remove URLs inline [https://...]
    t = re.sub(r'\s*\[https?://[^\]]*\]', '', t)
    # Remove URLs soltas
    t = re.sub(r'\s*https?://\S+', '', t)
    
    # Remove prefixo de fonte [nome] no início
    t = re.sub(r'^\[[^\]]+\]\s*', '', t)
    
    # Remove marcadores de contaminação conhecidos
    contaminantes = [
        'peço desculpas pelo mal-entendido',
        'kkkk',
        'vê se faz sentido',
    ]
    for marcador in contaminantes:
        t = re.sub(rf'(?i).*{re.escape(marcador)}.*[\r\n]*', '', t)
    
    # Remove ecos da query se fornecida (padrão: "query. " ou "query:")
    if query:
        query_lower = query.lower().strip().rstrip('.!?')
        query_escaped = re.escape(query_lower)
        
        # Remove "query." ou "query " seguido de espaço (echo do agente)
        # Variações: "query.", "query. ", "query " (com/sem ponto)
        t = re.sub(rf'(?i)^{query_escaped}[\.\s]+', '', t)  # início
        t = re.sub(rf'(?i)\s{query_escaped}[\.\s]+', ' ', t)  # meio (preserva espaço)
        # Se sobrou só "me " do "me fale sobre python. [url]", remove
        t = re.sub(r'^\s*me\s+(?=[A-Z])', '', t)  # "me Existem" → "Existem"
    
    # Normaliza espaços
    t = re.sub(r'\s{2,}', ' ', t).strip()
    
    # Se sobrou muito pouco, retorna vazio (será descartado)
    if len(t) < 10:
        return ''
    
    return t


def _texto_bruto(chunks: list[dict], query: Optional[str] = None) -> str:
    """
    Concatena os chunks em texto limpo para uso como contexto no sanduíche.
    Sem ressalvas, sem síntese — texto direto do banco, mas agressivamente limpo.

    Chamado quando qualidade_min < SCORE_CONFIANTE (ex: 0.35).
    O GerenciadorContexto vai injetar esse texto como [MEMORIA: ...].
    """
    partes       = []
    chars_usados = 0

    for c in chunks:
        # Limpa agressivamente, não descarta
        trecho = _limpar_chunk_agressivo(c["texto"], query=query)
        if not trecho:  # Sobrou muito pouco após limpeza
            continue
        espaco = MAX_CONTEXTO_CHARS - chars_usados
        if espaco <= 0:
            break
        if len(trecho) > espaco:
            trecho = trecho[:espaco].rsplit(" ", 1)[0]
        partes.append(trecho)
        chars_usados += len(trecho) + 3  # +3 para " | "

    return " | ".join(partes)


def _resposta_sintetizada(chunks: list[dict], confianca_max: float, query: Optional[str] = None) -> str:
    """
    Monta uma resposta legível para ser falada diretamente ao Carlos.
    Chamado quando qualidade_min >= SCORE_CONFIANTE (0.60).

    Estratégia:
      • score >= 0.75 → usa o chunk mais relevante direto, sem ressalvas
      • score ∈ [0.60, 0.75) → usa o primeiro chunk com pequena ressalva
    
    Todo chunk é agressivamente limpo antes de usar — remove URLs, ecos, contaminantes.
    """
    def _truncar(texto: str, limite: int = 350) -> str:
        if len(texto) <= limite:
            return texto
        idx = texto.find(".", 200)
        if 200 < idx < limite:
            return texto[:idx + 1]
        return texto[:limite - 3] + "..."

    # Limpa todos os chunks agressivamente — não filtra nenhum
    partes = []
    for c in chunks:
        limpo = _limpar_chunk_agressivo(c["texto"], query=query)
        if limpo:  # Se sobrou conteúdo após limpeza
            partes.append(_truncar(limpo))
    
    if not partes:
        return ""

    if confianca_max >= 0.75:
        # Muito confiante — resposta direta
        resposta = partes[0]
        if len(partes) > 1:
            # Adiciona segundo chunk se não for repetitivo
            overlap = len(
                set(partes[0].lower().split()) & set(partes[1].lower().split())
            )
            if overlap < 10:
                resposta += " " + partes[1]
    else:
        # Confiante mas não absoluto — ressalva mínima
        primeiro = partes[0]
        resposta = f"Pelo que tenho registrado: {primeiro[0].lower()}{primeiro[1:]}"
        if not resposta.endswith((".", "!", "?")):
            resposta += "."

    return re.sub(r"\s{2,}", " ", resposta).strip()


# =============================================================================
# SiriusRAG — classe pública
# =============================================================================


# =============================================================================
# Backend 2 — FAISS Inline (faiss + sentence-transformers sem sirius_vector_db)
# =============================================================================

class _FaissInlineBackend:
    """
    Backend FAISS gerido internamente — usado quando sirius_vector_db.py não
    está disponível mas faiss-cpu e sentence-transformers estão instalados.

    Persiste documentos em SQLite (data/sirius_rag_inline.db) e mantém o
    índice FAISS em RAM, reconstruindo do banco a cada restart.
    """

    _DB_FILE = os.path.join(_DIR_DATA, "sirius_rag_inline.db")

    def __init__(self, user_id: Optional[str] = None):
        import sqlite3 as _sqlite3
        import faiss as _faiss
        from sentence_transformers import SentenceTransformer as _ST

        self._user_id = user_id
        self._lock    = threading.Lock()
        self._faiss   = _faiss
        self._model   = _ST("paraphrase-multilingual-MiniLM-L12-v2")
        self._dim     = 384

        # Banco de documentos
        os.makedirs(_DIR_DATA, exist_ok=True)
        self._conn = _sqlite3.connect(self._DB_FILE, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT, tema TEXT, "
            "fonte TEXT, uid TEXT, criado_em DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        self._conn.commit()

        # Índice FAISS + mapa id→texto
        self._index: Optional[object]    = None
        self._id_map: list[dict]         = []
        self._pronto                     = False
        self.rebuild()

    # ── Interface pública (espelha SiriusVectorDB) ────────────────────────────

    def esta_pronto(self) -> bool:
        return self._pronto and self._index is not None

    def status(self) -> dict:
        n = len(self._id_map)
        return {
            "backend":           "FaissInline",
            "indice_construido": self._pronto,
            "docs_no_indice":    n,
            "docs_no_banco":     n,
            "indice_desatualizado": False,
        }

    def adicionar(
        self,
        texto: str,
        tema:  str  = "geral",
        fonte: str  = "manual",
        uid:   str  = "admin",
        doc_id: Optional[str] = None,
    ) -> bool:
        if not texto or not texto.strip():
            return False
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO docs (texto, tema, fonte, uid) VALUES (?,?,?,?)",
                    (texto.strip(), tema, fonte, uid or self._user_id or "admin"),
                )
                self._conn.commit()
            # Adiciona ao índice incrementalmente
            vec = self._model.encode([texto], normalize_embeddings=True)
            if self._index is None:
                self._index = self._faiss.IndexFlatIP(self._dim)
                self._pronto = True
            with self._lock:
                self._index.add(vec.astype("float32"))
                self._id_map.append({"texto": texto, "tema": tema, "fonte": fonte})
            return True
        except Exception as e:
            print(f"[FaissInline]: Erro ao adicionar: {e}")
            return False

    def buscar(
        self,
        consulta:  str,
        k:         int   = 5,
        user_id:   Optional[str] = None,
        score_min: float = 0.0,
    ) -> list[dict]:
        if not self.esta_pronto() or not consulta:
            return []
        try:
            vec = self._model.encode([consulta], normalize_embeddings=True)
            k_real = min(k, self._index.ntotal)
            if k_real == 0:
                return []
            scores, indices = self._index.search(vec.astype("float32"), k_real)
            resultados = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or float(score) < score_min:
                    continue
                doc = self._id_map[idx]
                resultados.append({
                    "texto":  doc["texto"],
                    "tema":   doc["tema"],
                    "fonte":  doc["fonte"],
                    "score":  float(score),
                })
            return resultados
        except Exception as e:
            print(f"[FaissInline]: Erro na busca: {e}")
            return []

    def limpar_contaminacao(self) -> int:
        """Remove entradas contaminadas do banco inline e reconstrói o índice."""
        try:
            with self._lock:
                rows_antes = self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
                for marcador in _CONTAMINANTES_RAG:
                    self._conn.execute(
                        "DELETE FROM docs WHERE LOWER(texto) LIKE ?",
                        (f"%{marcador}%",)
                    )
                self._conn.commit()
                rows_depois = self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            removidos = rows_antes - rows_depois
            if removidos:
                print(f"[FaissInline]: {removidos} entradas contaminadas removidas do banco.")
                self.rebuild()
            return removidos
        except Exception as e:
            print(f"[FaissInline]: Erro ao limpar contaminação: {e}")
            return 0

    def rebuild(self, user_id: Optional[str] = None) -> bool:
        try:
            rows = self._conn.execute(
                "SELECT texto, tema, fonte FROM docs"
            ).fetchall()
            if not rows:
                self._pronto = False
                return False
            textos = [r[0] for r in rows]
            vecs   = self._model.encode(textos, normalize_embeddings=True,
                                        show_progress_bar=False)
            index  = self._faiss.IndexFlatIP(self._dim)
            index.add(vecs.astype("float32"))
            with self._lock:
                self._index  = index
                self._id_map = [{"texto": r[0], "tema": r[1], "fonte": r[2]}
                                 for r in rows]
                self._pronto = True
            print(f"[FaissInline]: Índice reconstruído — {len(rows)} documentos.")
            return True
        except Exception as e:
            print(f"[FaissInline]: Erro no rebuild: {e}")
            return False


# =============================================================================
# Backend 3 — TF-IDF Fallback (scikit-learn apenas, sem dependências pesadas)
# =============================================================================

class _TfIdfBackend:
    """
    Backend TF-IDF usando scikit-learn — funciona em qualquer ambiente onde
    faiss ou sentence-transformers não estejam disponíveis.

    Busca léxica com cosine similarity normalizada para 0–1.
    Armazena documentos em SQLite e reconstrói o índice em RAM a cada restart.

    Scores são normalizados: 0.0 = sem sobreposição, 1.0 = match perfeito.
    Na prática, scores > 0.15 já indicam relevância léxica útil.
    """

    _DB_FILE = os.path.join(_DIR_DATA, "sirius_rag_tfidf.db")

    def __init__(self, user_id: Optional[str] = None):
        import sqlite3 as _sqlite3
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as _np

        self._user_id          = user_id
        self._lock             = threading.Lock()
        self._TfidfVectorizer  = TfidfVectorizer
        self._cosine_similarity = cosine_similarity
        self._np               = _np

        # Banco de documentos
        os.makedirs(_DIR_DATA, exist_ok=True)
        self._conn = _sqlite3.connect(self._DB_FILE, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT, tema TEXT, "
            "fonte TEXT, uid TEXT, criado_em DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        self._conn.commit()

        # Índice TF-IDF + corpus em RAM
        self._vectorizer: Optional[object] = None
        self._matrix     = None
        self._corpus: list[dict]           = []
        self._pronto                       = False
        self.rebuild()

    # ── Interface pública ─────────────────────────────────────────────────────

    def esta_pronto(self) -> bool:
        return self._pronto

    def status(self) -> dict:
        n = len(self._corpus)
        return {
            "backend":            "TF-IDF",
            "indice_construido":  self._pronto,
            "docs_no_indice":     n,
            "docs_no_banco":      n,
            "indice_desatualizado": False,
        }

    def adicionar(
        self,
        texto: str,
        tema:  str  = "geral",
        fonte: str  = "manual",
        uid:   str  = "admin",
        doc_id: Optional[str] = None,
    ) -> bool:
        if not texto or not texto.strip():
            return False
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO docs (texto, tema, fonte, uid) VALUES (?,?,?,?)",
                    (texto.strip(), tema, fonte, uid or self._user_id or "admin"),
                )
                self._conn.commit()
                self._corpus.append({"texto": texto, "tema": tema, "fonte": fonte})
                # Reconstrói o índice de forma leve (corpus pequeno)
                self._rebuildar_indice()
            return True
        except Exception as e:
            print(f"[TF-IDF]: Erro ao adicionar: {e}")
            return False

    def buscar(
        self,
        consulta:  str,
        k:         int   = 5,
        user_id:   Optional[str] = None,
        score_min: float = 0.0,
    ) -> list[dict]:
        if not self._pronto or not consulta or not self._corpus:
            return []
        try:
            with self._lock:
                vec_query = self._vectorizer.transform([consulta.lower()])
                scores    = self._cosine_similarity(vec_query, self._matrix)[0]

            # Pares (score, índice) ordenados
            pares = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

            resultados = []
            for idx, score in pares[:k]:
                score_f = float(score)
                if score_f < score_min:
                    continue
                doc = self._corpus[idx]
                resultados.append({
                    "texto":  doc["texto"],
                    "tema":   doc["tema"],
                    "fonte":  doc["fonte"],
                    "score":  score_f,
                })
            return resultados
        except Exception as e:
            print(f"[TF-IDF]: Erro na busca: {e}")
            return []

    def rebuild(self, user_id: Optional[str] = None) -> bool:
        try:
            rows = self._conn.execute(
                "SELECT texto, tema, fonte FROM docs"
            ).fetchall()
            if not rows:
                self._pronto = False
                return False
            with self._lock:
                self._corpus = [
                    {"texto": r[0], "tema": r[1], "fonte": r[2]} for r in rows
                ]
                self._rebuildar_indice()
            print(f"[TF-IDF]: Índice reconstruído — {len(rows)} documentos.")
            return True
        except Exception as e:
            print(f"[TF-IDF]: Erro no rebuild: {e}")
            return False

    def _rebuildar_indice(self):
        """Reconstrói o vectorizer e a matriz TF-IDF do corpus atual."""
        if not self._corpus:
            self._pronto = False
            return
        textos = [d["texto"].lower() for d in self._corpus]
        self._vectorizer = self._TfidfVectorizer(
            analyzer  = "word",
            ngram_range = (1, 2),
            min_df    = 1,
            max_df    = 0.95,
            sublinear_tf = True,
        ).fit(textos)
        self._matrix = self._vectorizer.transform(textos)
        self._pronto = True


class SiriusRAG:
    """
    Motor RAG semântico do S.I.R.I.U.S.

    Interface pública:
        responder(pergunta, qualidade_min=0.4) → str | None
        buscar_contexto(pergunta, k=3)         → list[dict]
        adicionar_feedback(pergunta, resposta) → None
        rebuild()                              → bool
        status()                               → dict
        _vdb                                   → SiriusVectorDB (acesso direto)

    Comportamento dual de responder():
        qualidade_min < SCORE_CONFIANTE  → texto bruto (para injeção no sanduíche)
        qualidade_min ≥ SCORE_CONFIANTE  → resposta sintetizada (para falar direto)
    """

    def __init__(
        self,
        memoria=None,
        user_id: Optional[str] = None,
        auto_rebuild: bool = True,
    ):
        self.memoria  = memoria
        self.user_id  = user_id
        self._lock    = threading.Lock()
        self._vdb: Optional[object] = None   # SiriusVectorDB

        self._inicializar_vdb(auto_rebuild)

        # Purga entradas contaminadas do banco inline no startup (em background)
        threading.Thread(
            target=self._limpar_contaminacao_startup,
            daemon=True,
            name="SiriusRAG-LimparContaminacao",
        ).start()

    # =========================================================================
    # Limpeza de contaminação
    # =========================================================================

    def _limpar_contaminacao_startup(self):
        """Remove entradas contaminadas do banco inline no startup."""
        try:
            if self._vdb and hasattr(self._vdb, "limpar_contaminacao"):
                removidos = self._vdb.limpar_contaminacao()
                if removidos:
                    print(f"[RAG]: Startup — {removidos} entradas contaminadas purgas do banco.")
        except Exception as e:
            print(f"[RAG]: Erro na limpeza de contaminação: {e}")

    def limpar_contaminacao(self) -> int:
        """Interface pública para limpar contaminação — chamável pelo cerebro.py."""
        if self._vdb and hasattr(self._vdb, "limpar_contaminacao"):
            return self._vdb.limpar_contaminacao()
        return 0

    # =========================================================================
    # Inicialização
    # =========================================================================

    def _inicializar_vdb(self, auto_rebuild: bool):
        """
        Inicializa o backend RAG em cascata — escolhe o melhor disponível.

        Ordem de preferência:
          1. SiriusVectorDB (sirius_vector_db.py + faiss-cpu + sentence-transformers)
          2. _FaissInlineBackend (faiss-cpu + sentence-transformers)
          3. _TfIdfBackend (scikit-learn apenas)

        Se nenhum estiver disponível, self._vdb = None e o RAG é desabilitado.
        """
        # ── Backend 1: SiriusVectorDB ─────────────────────────────────────────
        try:
            from sirius_vector_db import SiriusVectorDB
            self._vdb = SiriusVectorDB(user_id=self.user_id)
            s = self._vdb.status()
            print("\033[92m[RAG]: Backend ativo: SiriusVectorDB (FAISS + embeddings).\033[0m")

            if auto_rebuild and not s["indice_construido"]:
                threading.Thread(
                    target=self._vdb.rebuild,
                    kwargs={"user_id": self.user_id},
                    daemon=True, name="SiriusRAG-Rebuild",
                ).start()
            elif auto_rebuild and s.get("indice_desatualizado"):
                threading.Thread(
                    target=self._vdb.rebuild,
                    kwargs={"user_id": self.user_id},
                    daemon=True, name="SiriusRAG-Rebuild",
                ).start()
            return

        except ImportError:
            pass
        except Exception as e:
            print(f"[RAG]: SiriusVectorDB falhou: {e}")

        # ── Backend 2: FAISS Inline ───────────────────────────────────────────
        try:
            import faiss          # noqa: F401
            import sentence_transformers  # noqa: F401
            self._vdb = _FaissInlineBackend(user_id=self.user_id)
            print("\033[93m[RAG]: Backend ativo: FAISS Inline (sem sirius_vector_db).\033[0m")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[RAG]: FaissInline falhou: {e}")

        # ── Backend 3: TF-IDF Fallback ────────────────────────────────────────
        try:
            import sklearn  # noqa: F401
            self._vdb = _TfIdfBackend(user_id=self.user_id)
            print("\033[93m[RAG]: Backend ativo: TF-IDF (scikit-learn). "
                  "Para busca semântica: pip install faiss-cpu sentence-transformers\033[0m")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[RAG]: TfIdf falhou: {e}")

        # ── Sem backend disponível ────────────────────────────────────────────
        print(
            "\033[91m[RAG]: Nenhum backend disponível. RAG desabilitado.\n"
            "  Instale ao menos: pip install scikit-learn\033[0m"
        )
        self._vdb = None

    # =========================================================================
    # responder() — interface principal
    # =========================================================================

    def responder(
        self,
        pergunta:      str,
        qualidade_min: float = SCORE_CAUTELOSO,
        user_id:       Optional[str] = None,
        k:             int   = K_CANDIDATOS,
    ) -> Optional[str]:
        """
        Retorna contexto ou resposta a partir do índice FAISS.

        Comportamento duplo (alinhado com os dois usos no cerebro.py):

          qualidade_min < SCORE_CONFIANTE (0.60):
            → Chamado pelo cerebro para buscar CONTEXTO a ser injetado no sanduíche.
            → Retorna texto bruto concatenado dos chunks — sem ressalvas, sem síntese.
            → Exemplo: cerebro chama com qualidade_min=0.35.

          qualidade_min ≥ SCORE_CONFIANTE (0.60):
            → Chamado pelo cerebro para obter RESPOSTA DIRETA ao Carlos.
            → Retorna texto sintetizado com tom adequado ao score.
            → Exemplo: cerebro chama com qualidade_min=0.60.

        Retorna None se nenhum resultado superar qualidade_min.
        """
        if self._vdb is None or not self._vdb.esta_pronto():
            return None

        if not pergunta or not pergunta.strip():
            return None

        uid = user_id or self.user_id

        try:
            # ── 1. Busca vetorial ──────────────────────────────────────────
            candidatos = self._vdb.buscar(
                consulta  = pergunta,
                k         = k,
                user_id   = uid,
                score_min = qualidade_min,   # FAISS já descarta abaixo do threshold
            )

            if not candidatos:
                return None

            confianca_max = candidatos[0]["score"]

            # ── 2. Re-ranking MMR ──────────────────────────────────────────
            chunks = _mmr_rerank(candidatos, lam=MMR_LAMBDA, k=K_CONTEXTO)

            # ── 3. Saída dependente do uso ─────────────────────────────────
            #
            # [BUG 2 CORRIGIDO]
            # Antes: sempre retornava texto sintetizado, contaminando o contexto
            #        com "Pelo que tenho registrado..." quando chamado com 0.35.
            # Agora: diferencia pelo threshold para separar os dois usos.
            #
            if qualidade_min < SCORE_CONFIANTE:
                # Uso como contexto → texto bruto, limpo, sem tom de resposta
                resultado = _texto_bruto(chunks, query=pergunta)
            else:
                # Uso como resposta direta → síntese com tom adequado
                resultado = _resposta_sintetizada(chunks, confianca_max, query=pergunta)

            if not resultado or len(resultado) < 15:
                return None

            print(
                f"[RAG]: {'contexto' if qualidade_min < SCORE_CONFIANTE else 'resposta'} "
                f"— score={confianca_max:.3f} | chunks={len(chunks)} | "
                f"chars={len(resultado)}"
            )
            return resultado

        except Exception as e:
            print(f"[RAG]: Erro em responder(): {e}")
            return None

    # =========================================================================
    # buscar_contexto() — chunks brutos sem síntese
    # =========================================================================

    def buscar_contexto(
        self,
        pergunta:  str,
        k:         int   = K_CONTEXTO,
        user_id:   Optional[str] = None,
        score_min: float = SCORE_CAUTELOSO,
    ) -> list[dict]:
        """
        Retorna os chunks mais relevantes sem gerar texto.

        Útil para:
          • Passar contexto para gerador externo (OpenAI, Ollama, SiriusGerador)
          • Debug e análise de qualidade do índice

        Cada item: { "texto": str, "tema": str, "fonte": str, "score": float }
        """
        if self._vdb is None or not self._vdb.esta_pronto():
            return []

        uid = user_id or self.user_id

        try:
            candidatos = self._vdb.buscar(
                consulta  = pergunta,
                k         = k * 2,
                user_id   = uid,
                score_min = score_min,
            )
            return _mmr_rerank(candidatos, k=k)
        except Exception as e:
            print(f"[RAG]: Erro em buscar_contexto(): {e}")
            return []

    # =========================================================================
    # adicionar_feedback()
    # =========================================================================

    def adicionar_feedback(
        self,
        pergunta:         str,
        resposta_correta: str,
        user_id:          Optional[str] = None,
    ) -> None:
        """
        Registra uma correção do Carlos no índice vetorial.

        O texto indexado é "Pergunta: X\\nResposta: Y" para que buscas futuras
        similares à pergunta retornem esta resposta corrigida.

        [BUG 1 CORRIGIDO]
        Antes: chamava salvar_estudo_autonomo(..., user_id=uid) →
               SiriusMemoria não tem esse parâmetro → TypeError sempre.
        Agora: chama salvar_estudo_autonomo(tema, conteudo, tags) — assinatura correta.
        """
        uid   = user_id or self.user_id or "admin"
        texto = f"Pergunta: {pergunta}\nResposta: {resposta_correta}"

        # 1. Adiciona ao índice FAISS imediatamente
        if self._vdb:
            self._vdb.adicionar(
                texto  = texto,
                tema   = "feedback_usuario",
                fonte  = "feedback",
                uid    = uid,
                doc_id = f"feedback:{hash(pergunta) & 0xFFFFFFFF}",
            )

        # 2. Persiste no banco para sobreviver ao rebuild
        # [BUG 1] REMOVIDO o argumento user_id= que não existe em SiriusMemoria
        if self.memoria:
            try:
                self.memoria.salvar_estudo_autonomo(
                    tema     = pergunta[:80],
                    conteudo = resposta_correta,
                    tags     = "feedback_usuario",
                )
            except Exception as e:
                print(f"[RAG]: Erro ao salvar feedback no banco: {e}")

        print(f"[RAG]: Feedback indexado para '{pergunta[:50]}' (uid={uid}).")

    # =========================================================================
    # rebuild()
    # =========================================================================

    def rebuild(self, user_id: Optional[str] = None) -> bool:
        """
        Reconstrói o índice vetorial lendo todo o banco SQLite.
        Compatível com o padrão usado em cerebro.py.
        """
        if self._vdb is None:
            print("[RAG]: VectorDB não disponível para rebuild.")
            return False

        uid = user_id or self.user_id
        return self._vdb.rebuild(user_id=uid)

    # =========================================================================
    # status()
    # =========================================================================

    def status(self) -> dict:
        """
        Retorna estado do índice.
        Chaves garantidas (usadas pelo cerebro.py):
          indice_construido, docs_no_indice, docs_no_banco, indice_desatualizado
        """
        if self._vdb is None:
            return {
                "disponivel":           False,
                "indice_construido":    False,
                "docs_no_indice":       0,
                "docs_no_banco":        0,
                "indice_desatualizado": False,
                "erro":                 "faiss-cpu ou sentence-transformers não instalado",
            }

        s = self._vdb.status()
        s["disponivel"] = True
        return s

    def esta_pronto(self) -> bool:
        return self._vdb is not None and self._vdb.esta_pronto()


# =============================================================================
# Hook para limpar_banco.py
# =============================================================================

def reconstruir_indice_apos_limpeza(user_id: Optional[str] = None) -> bool:
    """
    Chame no final do main() do limpar_banco.py para manter o índice sincronizado.

    Exemplo::
        from sirius_rag import reconstruir_indice_apos_limpeza
        reconstruir_indice_apos_limpeza()
    """
    try:
        from sirius_vector_db import rebuild_apos_limpeza
        return rebuild_apos_limpeza(user_id=user_id)
    except ImportError:
        print("[RAG]: sirius_vector_db não encontrado.")
        return False
    except Exception as e:
        print(f"[RAG]: Erro no rebuild pós-limpeza: {e}")
        return False


# =============================================================================
# Standalone / smoke test
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SiriusRAG CLI")
    parser.add_argument("--rebuild",   action="store_true")
    parser.add_argument("--status",    action="store_true")
    parser.add_argument("--pergunta",  type=str)
    parser.add_argument("--qualidade", type=float, default=0.4)
    parser.add_argument("--k",         type=int, default=3)
    parser.add_argument("--user-id",   type=str, default=None)
    args = parser.parse_args()

    rag = SiriusRAG(user_id=args.user_id)

    if args.rebuild:
        ok = rag.rebuild()
        print(f"Rebuild: {'OK' if ok else 'FALHOU'}")

    if args.status:
        s = rag.status()
        print("\n[STATUS RAG]")
        for k, v in s.items():
            print(f"  {k}: {v}")

    if args.pergunta:
        print(f"\nPergunta  : '{args.pergunta}'")
        print(f"qualidade : {args.qualidade}")

        # Mostra chunks brutos
        chunks = rag.buscar_contexto(args.pergunta, k=args.k)
        print(f"\nChunks recuperados ({len(chunks)}):")
        for i, c in enumerate(chunks, 1):
            print(f"  [{i}] score={c['score']:.3f}  tema={c['tema']}")
            print(f"       {c['texto'][:150]}...")

        # Modo contexto (0.35)
        ctx = rag.responder(args.pergunta, qualidade_min=0.35)
        print(f"\n[modo contexto 0.35]\n  {ctx or '(sem resultado)'}")

        # Modo resposta direta (0.60)
        resp = rag.responder(args.pergunta, qualidade_min=0.60)
        print(f"\n[modo resposta 0.60]\n  {resp or '(sem resultado — confiança insuficiente)'}")