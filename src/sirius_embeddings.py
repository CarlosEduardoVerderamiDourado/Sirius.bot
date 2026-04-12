"""
SiriusEmbeddings — Word2Vec Skip-gram 100% próprio
Representa palavras como vetores numéricos treinados exclusivamente
com os dados do Sirius. Substitui o HuggingFace completamente.
"""

import os
import sys
import re
import pickle
import sqlite3
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from collections import Counter, defaultdict

# --- PATH ---
diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

EMBED_PATH  = os.path.join(CAMINHO_DATA, "sirius_embeddings.pkl")
DB_PESSOAL  = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO   = os.path.join(CAMINHO_DATA, "sirius_treino.db")

DEVICE      = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Hiperparâmetros
EMBED_DIM   = 128
JANELA      = 3      # palavras de contexto para cada lado
MIN_FREQ    = 1      # frequência mínima para entrar no vocabulário
N_NEGATIVO  = 5      # negative sampling
EPOCAS      = 20
LR          = 0.01
MAX_VOCAB   = 6000


# ---------------------------------------------------------------------------
# Modelo Skip-gram
# ---------------------------------------------------------------------------

class SkipGram(nn.Module):
    def __init__(self, vocab_size: int, embed_dim: int):
        super().__init__()
        self.embeddings_in  = nn.Embedding(vocab_size, embed_dim)
        self.embeddings_out = nn.Embedding(vocab_size, embed_dim)
        # Inicialização uniforme pequena
        nn.init.uniform_(self.embeddings_in.weight,  -0.1, 0.1)
        nn.init.uniform_(self.embeddings_out.weight, -0.1, 0.1)

    def forward(self, centro, contexto, negativos):
        emb_centro   = self.embeddings_in(centro)    # (batch, dim)
        emb_contexto = self.embeddings_out(contexto) # (batch, dim)
        emb_neg      = self.embeddings_out(negativos) # (batch, n_neg, dim)

        # Positivo
        score_pos = (emb_centro * emb_contexto).sum(dim=1)
        perda_pos = -torch.log(torch.sigmoid(score_pos) + 1e-8).mean()

        # Negativos
        score_neg = torch.bmm(emb_neg, emb_centro.unsqueeze(2)).squeeze(2)
        perda_neg = -torch.log(torch.sigmoid(-score_neg) + 1e-8).mean()

        return perda_pos + perda_neg


# ---------------------------------------------------------------------------
# Gerenciador de embeddings
# ---------------------------------------------------------------------------

class SiriusEmbeddings:
    def __init__(self):
        self.vocab      = {}         # token → idx
        self.idx2token  = {}         # idx → token
        self.vetores    = None       # np.ndarray (vocab_size, embed_dim)
        self.embed_dim  = EMBED_DIM
        self._carregar()

    # -----------------------------------------------------------------------
    # Persistência
    # -----------------------------------------------------------------------

    def _carregar(self):
        if os.path.exists(EMBED_PATH):
            try:
                with open(EMBED_PATH, "rb") as f:
                    dados = pickle.load(f)
                self.vocab     = dados["vocab"]
                self.idx2token = dados["idx2token"]
                self.vetores   = dados["vetores"]
                self.embed_dim = dados.get("embed_dim", EMBED_DIM)
                print(f"\033[92m[EMBEDDINGS]: {len(self.vocab)} palavras carregadas.\033[0m")
            except Exception as e:
                print(f"\033[33m[EMBEDDINGS]: Falha ao carregar: {e}\033[0m")

    def _salvar(self):
        with open(EMBED_PATH, "wb") as f:
            pickle.dump({
                "vocab":     self.vocab,
                "idx2token": self.idx2token,
                "vetores":   self.vetores,
                "embed_dim": self.embed_dim,
            }, f)
        print(f"[EMBEDDINGS]: Salvo ({len(self.vocab)} palavras).")

    # -----------------------------------------------------------------------
    # Coleta de textos dos bancos
    # -----------------------------------------------------------------------

    def _coletar_textos(self) -> list[str]:
        textos = []

        try:
            conn = sqlite3.connect(DB_PESSOAL)
            for row in conn.execute("SELECT content FROM conversas"):
                if row[0]:
                    textos.append(row[0])
            conn.close()
        except Exception:
            pass

        try:
            conn = sqlite3.connect(DB_TREINO)
            for row in conn.execute("SELECT conteudo, tema FROM conhecimento_geral"):
                if row[0]: textos.append(row[0])
                if row[1]: textos.append(row[1])
            for row in conn.execute("SELECT conteudo, tema FROM memoria_permanente"):
                if row[0]: textos.append(row[0])
            conn.close()
        except Exception:
            pass

        # Bootstrap mínimo
        textos += [
            "sirius assistente inteligente parceiro digital",
            "controle computador abrir fechar programas arquivos",
            "aprender treinar rede neural pytorch",
            "responder perguntas conhecimento geral",
        ]

        print(f"[EMBEDDINGS]: {len(textos)} textos coletados para treino.")
        return textos

    # -----------------------------------------------------------------------
    # Tokenização
    # -----------------------------------------------------------------------

    def _tokenizar(self, texto: str) -> list[str]:
        texto = texto.lower().strip()
        texto = re.sub(r"[^a-záéíóúàãõêôüç\s]", " ", texto)
        return [t for t in texto.split() if len(t) > 1]

    # -----------------------------------------------------------------------
    # Geração de pares Skip-gram
    # -----------------------------------------------------------------------

    def _gerar_pares(self, corpus: list[list[str]]) -> list[tuple[int, int]]:
        pares = []
        for sentenca in corpus:
            ids = [self.vocab.get(t) for t in sentenca if t in self.vocab]
            for i, centro in enumerate(ids):
                inicio = max(0, i - JANELA)
                fim    = min(len(ids), i + JANELA + 1)
                for j in range(inicio, fim):
                    if j != i:
                        pares.append((centro, ids[j]))
        return pares

    # -----------------------------------------------------------------------
    # Treinamento
    # -----------------------------------------------------------------------

    def treinar(self, epocas: int = EPOCAS):
        print("\033[93m[EMBEDDINGS]: Iniciando treinamento Word2Vec...\033[0m")

        textos    = self._coletar_textos()
        corpus    = [self._tokenizar(t) for t in textos]
        corpus    = [s for s in corpus if len(s) >= 2]

        # Frequência
        freq = Counter(t for s in corpus for t in s)
        vocab_filtrado = [t for t, c in freq.most_common(MAX_VOCAB) if c >= MIN_FREQ]

        self.vocab     = {t: i for i, t in enumerate(vocab_filtrado)}
        self.idx2token = {i: t for t, i in self.vocab.items()}
        vocab_size     = len(self.vocab)

        if vocab_size < 10:
            print("\033[31m[EMBEDDINGS]: Vocabulário muito pequeno. Adicione mais dados.\033[0m")
            return

        print(f"[EMBEDDINGS]: Vocabulário com {vocab_size} palavras.")

        # Frequências para negative sampling
        freqs_arr = np.array([freq.get(self.idx2token[i], 1) for i in range(vocab_size)], dtype=np.float32)
        freqs_arr = freqs_arr ** 0.75
        freqs_arr = freqs_arr / freqs_arr.sum()

        pares     = self._gerar_pares(corpus)
        if len(pares) < 10:
            print("\033[31m[EMBEDDINGS]: Poucos pares de treino.\033[0m")
            return

        print(f"[EMBEDDINGS]: {len(pares)} pares skip-gram gerados.")

        modelo    = SkipGram(vocab_size, EMBED_DIM).to(DEVICE)
        optimizer = optim.Adam(modelo.parameters(), lr=LR)

        centros   = torch.tensor([p[0] for p in pares], dtype=torch.long)
        contextos = torch.tensor([p[1] for p in pares], dtype=torch.long)

        batch_size = 512
        for epoca in range(epocas):
            perm       = torch.randperm(len(pares))
            centros    = centros[perm]
            contextos  = contextos[perm]
            perda_total = 0.0
            n_batches  = 0

            for i in range(0, len(pares), batch_size):
                c_batch = centros[i:i + batch_size].to(DEVICE)
                x_batch = contextos[i:i + batch_size].to(DEVICE)

                neg = torch.tensor(
                    np.random.choice(vocab_size, size=(len(c_batch), N_NEGATIVO), p=freqs_arr),
                    dtype=torch.long
                ).to(DEVICE)

                optimizer.zero_grad()
                perda = modelo(c_batch, x_batch, neg)
                perda.backward()
                optimizer.step()
                perda_total += perda.item()
                n_batches   += 1

            if (epoca + 1) % 5 == 0 or epoca == 0:
                print(f"  Época {epoca+1:3d}/{epocas} | perda: {perda_total/max(n_batches,1):.4f}")

        # Extrai vetores finais (embeddings de entrada)
        self.vetores   = modelo.embeddings_in.weight.detach().cpu().numpy()
        self.embed_dim = EMBED_DIM
        self._salvar()
        print("\033[92m[EMBEDDINGS]: Treinamento concluído!\033[0m")

    # -----------------------------------------------------------------------
    # Consulta
    # -----------------------------------------------------------------------

    def esta_treinado(self) -> bool:
        return self.vetores is not None and len(self.vocab) > 0

    def vetor(self, palavra: str) -> np.ndarray | None:
        idx = self.vocab.get(palavra.lower())
        if idx is not None and self.vetores is not None:
            return self.vetores[idx]
        return None

    def vetor_sentenca(self, texto: str) -> np.ndarray | None:
        """Média dos vetores das palavras da sentença."""
        if not self.esta_treinado():
            return None
        tokens  = self._tokenizar(texto)
        vetores = [self.vetores[self.vocab[t]] for t in tokens if t in self.vocab]
        if not vetores:
            return None
        return np.mean(vetores, axis=0)

    def similaridade(self, texto1: str, texto2: str) -> float:
        """Similaridade cosseno entre duas sentenças."""
        v1 = self.vetor_sentenca(texto1)
        v2 = self.vetor_sentenca(texto2)
        if v1 is None or v2 is None:
            return 0.0
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
        return float(cos)

    def palavras_similares(self, palavra: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Retorna as top_k palavras mais similares."""
        v = self.vetor(palavra)
        if v is None or self.vetores is None:
            return []

        sims = self.vetores @ v / (
            np.linalg.norm(self.vetores, axis=1) * np.linalg.norm(v) + 1e-8
        )
        top_ids = np.argsort(sims)[::-1][1:top_k + 1]
        return [(self.idx2token[i], float(sims[i])) for i in top_ids]

    def buscar_mais_similar(self, query: str, candidatos: list[str]) -> str | None:
        """Dado um query e lista de textos, retorna o mais similar."""
        if not candidatos:
            return None
        sims  = [(c, self.similaridade(query, c)) for c in candidatos]
        melhor = max(sims, key=lambda x: x[1])
        return melhor[0] if melhor[1] > 0.1 else None


# ---------------------------------------------------------------------------
# Teste standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    emb = SiriusEmbeddings()

    if not emb.esta_treinado():
        print("Treinando embeddings...")
        emb.treinar()

    print("\n--- Testes ---")
    for palavra in ["sirius", "computador", "abrir", "python"]:
        sim = emb.palavras_similares(palavra, top_k=3)
        print(f"  '{palavra}' → {sim}")

    print(f"\n  Similaridade 'abrir programa' ↔ 'executar arquivo': "
          f"{emb.similaridade('abrir programa', 'executar arquivo'):.3f}")