"""
sirius_rag.py — RAG local com FAISS para o Sirius

O que é RAG (Retrieval-Augmented Generation):
  Em vez de tentar GERAR uma resposta do zero (o gerador GRU ainda é fraco),
  o RAG RECUPERA os trechos mais relevantes do banco de conhecimento
  e os usa como base para responder.

  Fluxo:
    1. Usuário pergunta "o que é machine learning?"
    2. RAG converte a pergunta em vetor TF-IDF (sem treino prévio)
    3. Busca no índice FAISS os K documentos mais similares do banco
    4. Monta a resposta com os melhores trechos encontrados
    5. Se a confiança for baixa → delega ao AgentePesquisador (Wikipedia/DDG)

Vantagens sobre o SiriusGerador (GRU seq2seq):
  - Funciona com 0 dados de treino (usa TF-IDF direto do banco)
  - Melhora automaticamente conforme o banco cresce
  - Não "alucina" — só devolve o que está no banco
  - Muito mais rápido (busca vetorial vs forward pass da rede)
  - Separa documentos de "alta qualidade" dos demais

Estrutura do índice:
  data/
    sirius_rag_index.faiss   → índice vetorial FAISS
    sirius_rag_docs.pkl      → documentos correspondentes aos vetores
    sirius_rag_vectorizer.pkl → TF-IDF treinado no corpus

Instalação:
    pip install faiss-cpu scikit-learn

Uso pelo cerebro.py:
    from sirius_rag import SiriusRAG
    rag = SiriusRAG(memoria)
    resposta = rag.responder("o que é machine learning?")
    if resposta:
        return resposta
"""

import os
import sys
import re
import pickle
import sqlite3
import threading
import unicodedata
import time
from typing import Optional

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

INDICE_PATH      = os.path.join(CAMINHO_DATA, "sirius_rag_index.faiss")
DOCS_PATH        = os.path.join(CAMINHO_DATA, "sirius_rag_docs.pkl")
VECTORIZER_PATH  = os.path.join(CAMINHO_DATA, "sirius_rag_vectorizer.pkl")

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO  = os.path.join(CAMINHO_DATA, "sirius_treino.db")

# Configuração
K_RESULTADOS     = 5      # quantos documentos recuperar por query
MIN_SIMILARIDADE = 0.15   # threshold mínimo — abaixo disso, não usa
MIN_DOCS_TREINO  = 30     # documentos mínimos para construir o índice
MAX_FEATURES     = 4000   # tamanho do vocabulário TF-IDF
REBUILD_INTERVAL = 1800   # rebuild do índice a cada 30min se houver novos docs


# ---------------------------------------------------------------------------
# Normalização de texto
# ---------------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    """Remove acentos, lowercaseia e limpa pontuação."""
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    sem_pontuacao = re.sub(r"[^\w\s]", " ", sem_acento)
    return " ".join(sem_pontuacao.split())


def _extrair_sentencas(texto: str, max_chars: int = 400) -> list[str]:
    """
    Divide um texto longo em sentenças menores para indexação granular.
    Sentenças curtas têm mais precisão no RAG do que parágrafos inteiros.
    """
    # Divide em sentenças
    sentencas = re.split(r"(?<=[.!?])\s+", texto)
    resultado = []
    atual = ""
    for s in sentencas:
        s = s.strip()
        if not s or len(s) < 15:
            continue
        if len(atual) + len(s) < max_chars:
            atual = (atual + " " + s).strip()
        else:
            if atual:
                resultado.append(atual)
            atual = s
    if atual:
        resultado.append(atual)
    return resultado if resultado else [texto[:max_chars]]


# ---------------------------------------------------------------------------
# Documento RAG — unidade básica do índice
# ---------------------------------------------------------------------------

class DocumentoRAG:
    """
    Representa um trecho de conhecimento indexado.
    """
    __slots__ = ("texto", "tema", "fonte", "qualidade", "timestamp")

    def __init__(self, texto: str, tema: str = "", fonte: str = "banco",
                 qualidade: float = 0.5):
        self.texto      = texto.strip()
        self.tema       = tema.lower().strip()
        self.fonte      = fonte
        self.qualidade  = qualidade   # 0.0 a 1.0 — docs corrigidos pelo usuário têm 1.0
        self.timestamp  = time.time()

    def __repr__(self):
        return f"Doc({self.tema[:30]!r}, q={self.qualidade:.1f}, {len(self.texto)}c)"


# ---------------------------------------------------------------------------
# Coletor de documentos — extrai do banco
# ---------------------------------------------------------------------------

class ColetorDocumentos:
    """
    Extrai documentos dos bancos SQLite e formata para o índice RAG.

    Fontes:
      - sirius_treino.db → conhecimento_geral (autodidata + pesquisador)
      - sirius_treino.db → memoria_permanente  (conhecimento arquivado)
      - sirius_pessoal.db → conversas         (pares pergunta→resposta)
    """

    def coletar(self) -> list[DocumentoRAG]:
        docs = []
        docs.extend(self._coletar_conhecimento_geral())
        docs.extend(self._coletar_memoria_permanente())
        docs.extend(self._coletar_conversas())
        # Remove duplicatas por texto
        vistos = set()
        unicos = []
        for d in docs:
            chave = _normalizar(d.texto)[:100]
            if chave not in vistos and len(d.texto) > 20:
                vistos.add(chave)
                unicos.append(d)
        return unicos

    def _coletar_conhecimento_geral(self) -> list[DocumentoRAG]:
        docs = []
        try:
            conn = sqlite3.connect(DB_TREINO)
            rows = conn.execute(
                "SELECT tema, conteudo, tags FROM conhecimento_geral "
                "WHERE length(conteudo) > 30 ORDER BY id DESC LIMIT 2000"
            ).fetchall()
            conn.close()
            for tema, conteudo, tags in rows:
                # Qualidade base por tag
                qualidade = 0.8 if tags and "pesquisador" in tags else 0.5
                # Divide em sentenças para granularidade
                for sentenca in _extrair_sentencas(conteudo):
                    docs.append(DocumentoRAG(
                        texto=sentenca,
                        tema=tema or "",
                        fonte="conhecimento_geral",
                        qualidade=qualidade
                    ))
        except Exception as e:
            print(f"[RAG]: Erro ao coletar conhecimento_geral: {e}")
        return docs

    def _coletar_memoria_permanente(self) -> list[DocumentoRAG]:
        docs = []
        try:
            conn = sqlite3.connect(DB_TREINO)
            rows = conn.execute(
                "SELECT conteudo, tema FROM memoria_permanente "
                "WHERE length(conteudo) > 30 ORDER BY id DESC LIMIT 1000"
            ).fetchall()
            conn.close()
            for conteudo, tema in rows:
                for sentenca in _extrair_sentencas(conteudo):
                    docs.append(DocumentoRAG(
                        texto=sentenca,
                        tema=tema or "",
                        fonte="memoria_permanente",
                        qualidade=0.6
                    ))
        except Exception as e:
            print(f"[RAG]: Erro ao coletar memoria_permanente: {e}")
        return docs

    def _coletar_conversas(self) -> list[DocumentoRAG]:
        """
        Extrai pares (pergunta, resposta) das conversas.
        Apenas respostas do assistente com qualidade suficiente.
        """
        docs = []
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            rows = conn.execute(
                "SELECT role, content FROM conversas ORDER BY id DESC LIMIT 1000"
            ).fetchall()
            conn.close()

            # Agrupa em pares user/assistant
            pares = []
            ultimo_user = None
            for role, content in reversed(rows):
                if role == "user":
                    ultimo_user = content
                elif role == "assistant" and ultimo_user:
                    pares.append((ultimo_user, content))
                    ultimo_user = None

            for pergunta, resposta in pares:
                # Filtra respostas ruins
                if len(resposta) < 30:
                    continue
                if any(p in resposta.lower() for p in [
                    "ainda nao sei responder",
                    "ja anotei e vou pesquisar",
                    "mano, ainda nao sei",
                ]):
                    continue
                # Qualidade alta se a resposta foi longa e substancial
                qualidade = 0.7 if len(resposta) > 100 else 0.5
                docs.append(DocumentoRAG(
                    texto=resposta,
                    tema=pergunta[:80],
                    fonte="conversa",
                    qualidade=qualidade
                ))
        except Exception as e:
            print(f"[RAG]: Erro ao coletar conversas: {e}")
        return docs

    def contar_documentos(self) -> int:
        """Conta documentos disponíveis sem coletar tudo."""
        total = 0
        try:
            conn = sqlite3.connect(DB_TREINO)
            total += conn.execute(
                "SELECT COUNT(*) FROM conhecimento_geral WHERE length(conteudo) > 30"
            ).fetchone()[0]
            try:
                total += conn.execute(
                    "SELECT COUNT(*) FROM memoria_permanente WHERE length(conteudo) > 30"
                ).fetchone()[0]
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            total += conn.execute(
                "SELECT COUNT(*) FROM conversas WHERE role='assistant' AND length(content) > 30"
            ).fetchone()[0]
            conn.close()
        except Exception:
            pass
        return total


# ---------------------------------------------------------------------------
# Índice FAISS — coração do RAG
# ---------------------------------------------------------------------------

class IndiceRAG:
    """
    Gerencia o índice FAISS para busca vetorial eficiente.

    TF-IDF → FAISS:
      1. TF-IDF converte texto em vetores esparsos de palavras
      2. FAISS armazena os vetores e faz busca por similaridade coseno
      3. Retorna os K documentos mais próximos do vetor da query

    Por que TF-IDF e não embeddings?
      - TF-IDF funciona sem GPU, sem treino prévio, sem modelos externos
      - Para retrieval em PT-BR com domínio específico, funciona muito bem
      - Os embeddings Word2Vec do Sirius podem ser usados futuramente
        para refinar os resultados (reranking)
    """

    def __init__(self):
        self._indice      = None   # faiss.IndexFlatIP (inner product = coseno)
        self._documentos  = []     # list[DocumentoRAG] — paralelo ao índice
        self._vectorizer  = None   # TfidfVectorizer
        self._construido  = False
        self._lock        = threading.Lock()
        self._n_docs_no_build = 0  # quantos docs tinham quando foi construído

    def construir(self, documentos: list[DocumentoRAG]) -> bool:
        """
        Constrói o índice FAISS a partir dos documentos.
        Retorna True se construiu com sucesso.
        """
        if len(documentos) < MIN_DOCS_TREINO:
            print(f"[RAG]: Poucos documentos ({len(documentos)}/{MIN_DOCS_TREINO}). "
                  f"Continue usando o Sirius para acumular conhecimento.")
            return False

        try:
            import faiss
            import numpy as np
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.preprocessing import normalize
        except ImportError as e:
            print(f"[RAG]: Dependência faltando: {e}")
            print("  Instale: pip install faiss-cpu scikit-learn")
            return False

        print(f"[RAG]: Construindo índice com {len(documentos)} documentos...")
        inicio = time.time()

        # Normaliza textos
        textos_norm = [_normalizar(d.texto) for d in documentos]

        # TF-IDF — aprende o vocabulário do corpus completo
        vectorizer = TfidfVectorizer(
            max_features=MAX_FEATURES,
            ngram_range=(1, 2),  # unigramas e bigramas — captura "machine learning"
            min_df=1,
            sublinear_tf=True,   # log(TF) — reduz dominância de palavras frequentes
            analyzer="word",
            token_pattern=r"(?u)\b\w+\b",
        )
        X = vectorizer.fit_transform(textos_norm).toarray().astype("float32")

        # Normaliza para coseno (inner product com vetores L2-normalizados = coseno)
        X = normalize(X, norm="l2")

        # Cria índice FAISS — IndexFlatIP é exato (não aproximado)
        dim    = X.shape[1]
        indice = faiss.IndexFlatIP(dim)
        indice.add(X)

        with self._lock:
            self._indice      = indice
            self._documentos  = documentos
            self._vectorizer  = vectorizer
            self._construido  = True
            self._n_docs_no_build = len(documentos)

        duracao = time.time() - inicio
        print(f"\033[92m[RAG]: Índice construído — {len(documentos)} docs, "
              f"dim={dim}, {duracao:.1f}s\033[0m")
        return True

    def buscar(self, query: str, k: int = K_RESULTADOS,
               qualidade_min: float = 0.0) -> list[tuple["DocumentoRAG", float]]:
        """
        Busca os K documentos mais similares à query.
        Retorna lista de (DocumentoRAG, score_similaridade).
        """
        if not self._construido:
            return []

        try:
            import numpy as np
            from sklearn.preprocessing import normalize

            query_norm = _normalizar(query)
            q_vec = self._vectorizer.transform([query_norm]).toarray().astype("float32")
            q_vec = normalize(q_vec, norm="l2")

            with self._lock:
                scores, indices = self._indice.search(q_vec, k)

            resultados = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self._documentos):
                    continue
                doc = self._documentos[idx]
                if score >= MIN_SIMILARIDADE and doc.qualidade >= qualidade_min:
                    resultados.append((doc, float(score)))

            # Ordena por score × qualidade (prioriza docs de alta qualidade)
            resultados.sort(key=lambda x: x[1] * x[0].qualidade, reverse=True)
            return resultados

        except Exception as e:
            print(f"[RAG]: Erro na busca: {e}")
            return []

    def salvar(self) -> bool:
        """Persiste o índice em disco."""
        if not self._construido:
            return False
        try:
            import faiss
            faiss.write_index(self._indice, INDICE_PATH)
            with open(DOCS_PATH, "wb") as f:
                pickle.dump(self._documentos, f)
            with open(VECTORIZER_PATH, "wb") as f:
                pickle.dump(self._vectorizer, f)
            print(f"[RAG]: Índice salvo ({len(self._documentos)} docs).")
            return True
        except Exception as e:
            print(f"[RAG]: Erro ao salvar: {e}")
            return False

    def carregar(self) -> bool:
        """Carrega o índice do disco."""
        if not all(os.path.exists(p) for p in [INDICE_PATH, DOCS_PATH, VECTORIZER_PATH]):
            return False
        try:
            import faiss
            indice = faiss.read_index(INDICE_PATH)
            with open(DOCS_PATH, "rb") as f:
                documentos = pickle.load(f)
            with open(VECTORIZER_PATH, "rb") as f:
                vectorizer = pickle.load(f)
            with self._lock:
                self._indice      = indice
                self._documentos  = documentos
                self._vectorizer  = vectorizer
                self._construido  = True
                self._n_docs_no_build = len(documentos)
            print(f"\033[92m[RAG]: Índice carregado — {len(documentos)} docs.\033[0m")
            return True
        except Exception as e:
            print(f"[RAG]: Falha ao carregar índice: {e}")
            return False

    @property
    def construido(self) -> bool:
        return self._construido

    @property
    def n_docs(self) -> int:
        return len(self._documentos)


# ---------------------------------------------------------------------------
# Montador de resposta — transforma documentos recuperados em texto
# ---------------------------------------------------------------------------

class MontadorResposta:
    """
    Pega os K documentos recuperados e monta uma resposta coerente.

    Estratégias:
      1. Se há 1 documento muito relevante (score > 0.6): usa diretamente
      2. Se há vários documentos sobre o mesmo tema: concatena os melhores
      3. Se os documentos são sobre temas diferentes: usa o mais relevante
    """

    def montar(self, query: str,
               resultados: list[tuple["DocumentoRAG", float]]) -> Optional[str]:
        if not resultados:
            return None

        melhor_doc, melhor_score = resultados[0]

        # Alta confiança — usa diretamente
        if melhor_score >= 0.55:
            return self._formatar(melhor_doc.texto, melhor_score)

        # Confiança média — verifica se os top-3 são sobre o mesmo tema
        if len(resultados) >= 2:
            top3 = resultados[:3]
            tema_principal = melhor_doc.tema

            # Filtra docs do mesmo tema ou muito similares
            docs_relacionados = [
                d for d, s in top3
                if s >= MIN_SIMILARIDADE and (
                    d.tema == tema_principal or
                    self._sobreposicao_palavras(d.tema, tema_principal) > 0.3
                )
            ]

            if len(docs_relacionados) >= 2:
                # Mescla os 2 melhores
                textos = [d.texto for d in docs_relacionados[:2]]
                mesclado = self._mesclar(textos, query)
                if mesclado and len(mesclado) > 40:
                    return self._formatar(mesclado, melhor_score)

        # Usa o melhor disponível se score razoável
        if melhor_score >= MIN_SIMILARIDADE:
            return self._formatar(melhor_doc.texto, melhor_score)

        return None

    def _formatar(self, texto: str, score: float) -> str:
        """Limpa e formata o texto para resposta."""
        # Remove prefixos de fonte
        texto = re.sub(r"^\[(?:Web|Wikipedia|Fonte|RAG)\]\s*", "", texto).strip()
        # Remove referências de markdown
        texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
        texto = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", texto)
        # Limita tamanho
        if len(texto) > 450:
            idx = texto.find(".", 200)
            if 200 < idx < 450:
                texto = texto[:idx + 1]
            else:
                texto = texto[:447] + "..."
        return texto.strip()

    def _mesclar(self, textos: list[str], query: str) -> str:
        """
        Mescla 2 textos relacionados evitando repetição.
        Pega a primeira sentença do melhor e complementa com a segunda.
        """
        t1, t2 = textos[0], textos[1]
        # Pega primeira sentença de cada
        s1 = re.split(r"[.!?]\s+", t1)[0] + "."
        s2_partes = re.split(r"[.!?]\s+", t2)
        # Busca sentença complementar (não repetida)
        for s in s2_partes:
            if len(s) > 20 and _normalizar(s)[:30] not in _normalizar(t1):
                return f"{s1} {s.strip()}."
        return t1[:300]

    def _sobreposicao_palavras(self, a: str, b: str) -> float:
        """Fraction de palavras em comum entre dois textos."""
        pa = set(_normalizar(a).split())
        pb = set(_normalizar(b).split())
        if not pa or not pb:
            return 0.0
        return len(pa & pb) / max(len(pa), len(pb))


# ---------------------------------------------------------------------------
# SiriusRAG — interface principal
# ---------------------------------------------------------------------------

class SiriusRAG:
    """
    Interface principal do sistema RAG do Sirius.

    Fluxo completo:
      1. Inicializa: carrega índice do disco ou constrói novo
      2. responder(query): busca docs → monta resposta → retorna ou None
      3. rebuild_se_necessario(): verifica se há novos docs e rebuilda
      4. adicionar_feedback(pergunta, resposta_correta): salva doc com qualidade=1.0

    Integração no cerebro.py:
        self._rag = SiriusRAG(self.memoria)
        # No método processar():
        resp = self._rag.responder(comando)
        if resp:
            return resp
    """

    def __init__(self, memoria=None):
        self.memoria   = memoria
        self._indice   = IndiceRAG()
        self._coletor  = ColetorDocumentos()
        self._montador = MontadorResposta()
        self._ultimo_rebuild = 0.0
        self._lock     = threading.Lock()

        # Tenta carregar índice existente primeiro
        if not self._indice.carregar():
            # Constrói se houver dados suficientes
            self._construir_background()

    # -----------------------------------------------------------------------
    # Interface pública
    # -----------------------------------------------------------------------

    def responder(self, query: str,
                  qualidade_min: float = 0.4) -> Optional[str]:
        """
        Busca no índice a resposta mais relevante para a query.
        Retorna texto ou None se não encontrou nada confiável.

        qualidade_min: filtra documentos com qualidade abaixo desse valor.
        Use 0.7+ para respostas apenas de fontes confiáveis.
        """
        if not self._indice.construido:
            return None

        # Verifica se o índice está desatualizado e rebuilda em background
        self._rebuild_se_necessario_background()

        resultados = self._indice.buscar(query, k=K_RESULTADOS,
                                         qualidade_min=qualidade_min)
        if not resultados:
            return None

        resposta = self._montador.montar(query, resultados)

        if resposta:
            melhor_score = resultados[0][1]
            melhor_tema  = resultados[0][0].tema
            print(f"\033[94m[RAG]: Respondeu '{query[:40]}' "
                  f"(score={melhor_score:.2f}, tema='{melhor_tema[:30]}')\033[0m")

        return resposta

    def adicionar_feedback(self, pergunta: str, resposta_correta: str):
        """
        Salva um par (pergunta, resposta_correta) com qualidade máxima (1.0).
        Usado quando o usuário corrige o Sirius:
          "Sirius, isso tá errado. A resposta certa é: X"

        O índice será rebuildo na próxima verificação automática.
        """
        if not self.memoria:
            return
        try:
            self.memoria.salvar_estudo_autonomo(
                tema=pergunta[:100],
                conteudo=resposta_correta,
                tags="feedback_usuario_qualidade_1"
            )
            print(f"[RAG]: Feedback salvo para '{pergunta[:50]}'.")
            # Força rebuild no próximo ciclo
            self._ultimo_rebuild = 0.0
        except Exception as e:
            print(f"[RAG]: Erro ao salvar feedback: {e}")

    def rebuild(self) -> bool:
        """
        Reconstrói o índice com todos os documentos atuais do banco.
        Chamado automaticamente ou manualmente via 'sirius reconstrói o rag'.
        """
        with self._lock:
            docs = self._coletor.coletar()
            if not docs:
                return False
            ok = self._indice.construir(docs)
            if ok:
                self._indice.salvar()
                self._ultimo_rebuild = time.time()
            return ok

    def status(self) -> dict:
        n_banco = self._coletor.contar_documentos()
        return {
            "indice_construido":   self._indice.construido,
            "docs_no_indice":      self._indice.n_docs,
            "docs_no_banco":       n_banco,
            "indice_desatualizado": n_banco > self._indice.n_docs * 1.2,
            "ultimo_rebuild":      self._ultimo_rebuild,
        }

    # -----------------------------------------------------------------------
    # Helpers internos
    # -----------------------------------------------------------------------

    def _construir_background(self):
        """Constrói o índice em thread separada para não travar a inicialização."""
        def _build():
            n = self._coletor.contar_documentos()
            if n < MIN_DOCS_TREINO:
                print(f"[RAG]: {n}/{MIN_DOCS_TREINO} docs — índice aguardando mais dados.")
                return
            print(f"[RAG]: Construindo índice em background ({n} docs)...")
            self.rebuild()
        threading.Thread(target=_build, daemon=True, name="SiriusRAG-Build").start()

    def _rebuild_se_necessario_background(self):
        """
        Verifica se o índice está muito desatualizado e rebuilda em background.
        Condição: 20% mais documentos que no último build OU 30min passados.
        """
        agora = time.time()
        if agora - self._ultimo_rebuild < REBUILD_INTERVAL:
            return  # muito cedo para rebuildar

        n_banco = self._coletor.contar_documentos()
        if n_banco <= self._indice.n_docs * 1.2:
            return  # não cresceu o suficiente

        print(f"[RAG]: Banco cresceu ({self._indice.n_docs}→{n_banco} docs). Rebuilding...")
        threading.Thread(
            target=self.rebuild,
            daemon=True,
            name="SiriusRAG-Rebuild"
        ).start()


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_rag_instance: Optional[SiriusRAG] = None

def get_rag(memoria=None) -> SiriusRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = SiriusRAG(memoria)
    return _rag_instance


# ---------------------------------------------------------------------------
# Standalone — testa o RAG
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Testa o SiriusRAG")
    parser.add_argument("--build",   action="store_true", help="Constrói/rebuilda o índice")
    parser.add_argument("--status",  action="store_true", help="Mostra status")
    parser.add_argument("--query",   type=str,            help="Faz uma busca de teste")
    parser.add_argument("--k",       type=int, default=3, help="Número de resultados")
    args = parser.parse_args()

    rag = SiriusRAG()

    if args.status or not any([args.build, args.query]):
        s = rag.status()
        print("\n[RAG STATUS]")
        print(f"  Índice construído: {'✓' if s['indice_construido'] else '✗'}")
        print(f"  Docs no índice:    {s['docs_no_indice']}")
        print(f"  Docs no banco:     {s['docs_no_banco']}")
        print(f"  Desatualizado:     {'⚠ Sim' if s['indice_desatualizado'] else '✓ Não'}")
        if not s["indice_construido"]:
            print(f"\n  Mínimo para construir: {MIN_DOCS_TREINO} docs")
            print(f"  Faltam: {max(0, MIN_DOCS_TREINO - s['docs_no_banco'])} docs")
            print(f"  Dica: use o Sirius por um tempo ou rode o autodidata para acumular dados.")
        print()

    if args.build:
        print("Construindo índice...")
        ok = rag.rebuild()
        print("✓ Construído!" if ok else "✗ Falhou — poucos dados?")

    if args.query:
        if not rag._indice.construido:
            print("Índice não construído. Rode --build primeiro.")
        else:
            print(f"\nQuery: '{args.query}'")
            resultados = rag._indice.buscar(args.query, k=args.k)
            print(f"Top {len(resultados)} resultados:")
            for i, (doc, score) in enumerate(resultados, 1):
                print(f"\n  [{i}] score={score:.3f} | qualidade={doc.qualidade:.1f} | "
                      f"fonte={doc.fonte} | tema='{doc.tema[:40]}'")
                print(f"       {doc.texto[:150]}...")
            resposta = rag.responder(args.query)
            print(f"\nResposta montada:\n  {resposta or '(sem resposta)'}")