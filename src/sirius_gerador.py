"""
SiriusGerador — Rede seq2seq PyTorch 100% própria
Gera respostas com a personalidade do Sirius, treinada só com seus dados.

Arquitetura: Encoder-Decoder com atenção
- Encoder: GRU bidirecional processa a entrada
- Atenção: foca nas partes relevantes da entrada
- Decoder: GRU gera a resposta token a token
"""

import os
import sys
import pickle
import torch
import torch.nn as nn
import torch.nn.functional as F
import sqlite3
import re
import random
from collections import Counter

# --- PATH ---
diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

# Caminhos dos artefatos
VOCAB_PATH    = os.path.join(CAMINHO_DATA, "sirius_vocab.pkl")
GERADOR_PATH  = os.path.join(CAMINHO_DATA, "sirius_gerador.pth")
DB_PESSOAL    = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO     = os.path.join(CAMINHO_DATA, "sirius_treino.db")

# Tokens especiais
PAD, SOS, EOS, UNK = 0, 1, 2, 3

# Hiperparâmetros — ajustados para RTX 3060 12GB
EMBED_DIM   = 256
HIDDEN_DIM  = 512
MAX_VOCAB   = 8000
MAX_LEN_IN  = 40
MAX_LEN_OUT = 60
DEVICE      = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------

class Vocabulario:
    def __init__(self):
        self.token2idx = {"<PAD>": 0, "<SOS>": 1, "<EOS>": 2, "<UNK>": 3}
        self.idx2token = {0: "<PAD>", 1: "<SOS>", 2: "<EOS>", 3: "<UNK>"}
        self.frequencia = Counter()

    def __len__(self):
        return len(self.token2idx)

    def tokenizar(self, texto: str) -> list[str]:
        texto = texto.lower().strip()
        texto = re.sub(r"([!?.,;:])", r" \1 ", texto)
        return texto.split()

    def construir(self, textos: list[str], max_vocab: int = MAX_VOCAB):
        for texto in textos:
            for token in self.tokenizar(texto):
                self.frequencia[token] += 1

        mais_comuns = self.frequencia.most_common(max_vocab - 4)
        for token, _ in mais_comuns:
            if token not in self.token2idx:
                idx = len(self.token2idx)
                self.token2idx[token] = idx
                self.idx2token[idx]   = token

        print(f"[VOCAB]: {len(self)} tokens construídos.")

    def encode(self, texto: str, max_len: int) -> list[int]:
        tokens = self.tokenizar(texto)[:max_len]
        ids    = [self.token2idx.get(t, UNK) for t in tokens]
        ids   += [PAD] * (max_len - len(ids))
        return ids

    def decode(self, ids: list[int]) -> str:
        tokens = []
        for i in ids:
            if i == EOS:
                break
            if i not in (PAD, SOS, UNK):
                tokens.append(self.idx2token.get(i, ""))
        return " ".join(tokens).strip()

    def salvar(self):
        with open(VOCAB_PATH, "wb") as f:
            pickle.dump(self, f)
        print(f"[VOCAB]: Salvo em {VOCAB_PATH}")

    @staticmethod
    def carregar():
        if os.path.exists(VOCAB_PATH):
            with open(VOCAB_PATH, "rb") as f:
                return pickle.load(f)
        return None


# ---------------------------------------------------------------------------
# Atenção de Bahdanau
# ---------------------------------------------------------------------------

class Atencao(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.W1 = nn.Linear(hidden_dim * 2, hidden_dim)
        self.W2 = nn.Linear(hidden_dim * 2, hidden_dim)
        self.V  = nn.Linear(hidden_dim, 1)

    def forward(self, hidden, encoder_outputs):
        # hidden: (1, batch, hidden*2)  encoder_outputs: (seq, batch, hidden*2)
        hidden_exp = hidden.permute(1, 0, 2).expand_as(encoder_outputs.permute(1, 0, 2))
        enc_perm   = encoder_outputs.permute(1, 0, 2)
        score      = self.V(torch.tanh(self.W1(hidden_exp) + self.W2(enc_perm)))
        pesos      = F.softmax(score, dim=1)
        contexto   = (pesos * enc_perm).sum(dim=1, keepdim=True).permute(1, 0, 2)
        return contexto, pesos


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

class Encoder(nn.Module):
    def __init__(self, vocab_size: int, embed_dim: int, hidden_dim: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=PAD)
        self.gru        = nn.GRU(embed_dim, hidden_dim, bidirectional=True, batch_first=False)
        self.fc         = nn.Linear(hidden_dim * 2, hidden_dim * 2)

    def forward(self, src):
        # src: (seq_len, batch)
        emb     = self.embedding(src)
        outputs, hidden = self.gru(emb)
        # Junta as duas direções
        hidden  = torch.cat([hidden[-2], hidden[-1]], dim=1).unsqueeze(0)
        hidden  = torch.tanh(self.fc(hidden))
        return outputs, hidden


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

class Decoder(nn.Module):
    def __init__(self, vocab_size: int, embed_dim: int, hidden_dim: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=PAD)
        self.atencao    = Atencao(hidden_dim)
        self.gru        = nn.GRU(embed_dim + hidden_dim * 2, hidden_dim * 2, batch_first=False)
        self.fc_out     = nn.Linear(hidden_dim * 2, vocab_size)
        self.dropout    = nn.Dropout(0.3)

    def forward(self, token_in, hidden, encoder_outputs):
        # token_in: (1, batch)
        emb                  = self.dropout(self.embedding(token_in))
        contexto, pesos_attn = self.atencao(hidden, encoder_outputs)
        entrada_gru          = torch.cat([emb, contexto], dim=2)
        saida, hidden        = self.gru(entrada_gru, hidden)
        predicao             = self.fc_out(saida.squeeze(0))
        return predicao, hidden, pesos_attn


# ---------------------------------------------------------------------------
# Seq2Seq completo
# ---------------------------------------------------------------------------

class Seq2Seq(nn.Module):
    def __init__(self, encoder: Encoder, decoder: Decoder):
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder

    def forward(self, src, tgt, teacher_forcing: float = 0.5):
        # src: (src_len, batch)  tgt: (tgt_len, batch)
        tgt_len, batch = tgt.shape
        vocab_size     = self.decoder.fc_out.out_features

        saidas         = torch.zeros(tgt_len, batch, vocab_size).to(src.device)
        enc_out, hidden = self.encoder(src)

        token_in = tgt[0:1]  # <SOS>
        for t in range(1, tgt_len):
            predicao, hidden, _ = self.decoder(token_in, hidden, enc_out)
            saidas[t]           = predicao
            usar_teacher        = random.random() < teacher_forcing
            token_in            = tgt[t:t+1] if usar_teacher else predicao.argmax(1).unsqueeze(0)

        return saidas


# ---------------------------------------------------------------------------
# Gerenciador principal
# ---------------------------------------------------------------------------

class SiriusGerador:
    def __init__(self):
        self.vocab  = Vocabulario.carregar()
        self.modelo = None
        self._carregar_modelo()
        print(f"\033[93m[GERADOR]: Dispositivo → {DEVICE}\033[0m")

    def _criar_modelo(self, vocab_size: int) -> Seq2Seq:
        enc = Encoder(vocab_size, EMBED_DIM, HIDDEN_DIM).to(DEVICE)
        dec = Decoder(vocab_size, EMBED_DIM, HIDDEN_DIM).to(DEVICE)
        return Seq2Seq(enc, dec).to(DEVICE)

    def _carregar_modelo(self):
        if self.vocab and os.path.exists(GERADOR_PATH):
            try:
                self.modelo = self._criar_modelo(len(self.vocab))
                estado      = torch.load(GERADOR_PATH, map_location=DEVICE, weights_only=True)
                self.modelo.load_state_dict(estado)
                self.modelo.eval()
                print("\033[92m[GERADOR]: Modelo carregado!\033[0m")
            except Exception as e:
                print(f"\033[33m[GERADOR]: Não foi possível carregar: {e}\033[0m")
                self.modelo = None

    def _carregar_dados(self) -> list[tuple[str, str]]:
        """Carrega pares pergunta→resposta dos bancos SQLite."""
        pares = []

        # 1. Histórico de conversas (fonte mais rica)
        try:
            conn   = sqlite3.connect(DB_PESSOAL)
            cursor = conn.cursor()
            cursor.execute("SELECT role, content FROM conversas ORDER BY id ASC")
            linhas = cursor.fetchall()
            conn.close()

            i = 0
            while i < len(linhas) - 1:
                if linhas[i][0] == "user" and linhas[i+1][0] == "assistant":
                    pergunta = linhas[i][1].strip()
                    resposta = linhas[i+1][1].strip()
                    if 3 < len(pergunta) < 200 and 3 < len(resposta) < 400:
                        pares.append((pergunta, resposta))
                i += 1
        except Exception as e:
            print(f"[GERADOR]: Erro ao ler histórico: {e}")

        # 2. Conhecimento geral (tema como pergunta, conteúdo como resposta)
        try:
            conn   = sqlite3.connect(DB_TREINO)
            cursor = conn.cursor()
            cursor.execute("SELECT tema, conteudo FROM conhecimento_geral LIMIT 500")
            for tema, conteudo in cursor.fetchall():
                if tema and conteudo and len(conteudo) < 400:
                    pares.append((f"me fala sobre {tema}", conteudo))
            conn.close()
        except Exception as e:
            print(f"[GERADOR]: Erro ao ler treino: {e}")

        # 3. Dados sintéticos mínimos para bootstrap (quando não há dados suficientes)
        bootstrap = [
            ("oi sirius", "Eae! Tô ligado, pode mandar."),
            ("bom dia", "Bom dia, chefia! Tamo junto."),
            ("tudo bem", "Tudo certo por aqui! E você?"),
            ("quem é você", "Sou o Sirius, seu parceiro digital criado do zero."),
            ("o que você faz", "Controlo seu PC, respondo perguntas, e fico mais esperto com o tempo."),
            ("valeu sirius", "Tmj mano, qualquer coisa tô aqui."),
            ("boa noite", "Boa noite! Descansando o processador kkkk"),
            ("me ajuda", "Claro, manda o que precisa que eu resolvo."),
            ("obrigado", "De nada, chefia! Tamo junto sempre."),
            ("tchau", "Até mais! Fica na paz."),
        ]
        pares.extend(bootstrap)

        print(f"[GERADOR]: {len(pares)} pares de treino carregados.")
        return pares

    def treinar(self, epocas: int = 30, batch_size: int = 32):
        """Treina o gerador com os dados dos bancos."""
        print("\033[93m[GERADOR]: Iniciando treinamento...\033[0m")

        pares = self._carregar_dados()
        if len(pares) < 5:
            print("\033[31m[GERADOR]: Dados insuficientes para treinar.\033[0m")
            return

        # Constrói vocabulário
        todos_textos = [p for par in pares for p in par]
        self.vocab   = Vocabulario()
        self.vocab.construir(todos_textos)
        self.vocab.salvar()

        # Codifica os pares
        def encode_par(par):
            src = self.vocab.encode(par[0], MAX_LEN_IN)
            tgt = [SOS] + self.vocab.encode(par[1], MAX_LEN_OUT - 2) + [EOS]
            tgt = tgt[:MAX_LEN_OUT] + [PAD] * max(0, MAX_LEN_OUT - len(tgt))
            return src, tgt

        dados_cod = [encode_par(p) for p in pares]

        # Modelo
        self.modelo = self._criar_modelo(len(self.vocab))
        optimizer   = torch.optim.Adam(self.modelo.parameters(), lr=0.001)
        criterio    = nn.CrossEntropyLoss(ignore_index=PAD)

        self.modelo.train()
        for epoca in range(epocas):
            random.shuffle(dados_cod)
            perda_total = 0
            n_batches   = 0

            for i in range(0, len(dados_cod), batch_size):
                batch = dados_cod[i:i + batch_size]
                if len(batch) < 2:
                    continue

                srcs = torch.tensor([b[0] for b in batch], dtype=torch.long).T.to(DEVICE)
                tgts = torch.tensor([b[1] for b in batch], dtype=torch.long).T.to(DEVICE)

                optimizer.zero_grad()
                saidas = self.modelo(srcs, tgts)

                # saidas: (tgt_len, batch, vocab) — ignora o token SOS na perda
                saidas_flat = saidas[1:].reshape(-1, len(self.vocab))
                tgts_flat   = tgts[1:].reshape(-1)

                perda = criterio(saidas_flat, tgts_flat)
                perda.backward()
                torch.nn.utils.clip_grad_norm_(self.modelo.parameters(), 1.0)
                optimizer.step()

                perda_total += perda.item()
                n_batches   += 1

            if (epoca + 1) % 5 == 0 or epoca == 0:
                media = perda_total / max(n_batches, 1)
                print(f"  Época {epoca+1:3d}/{epocas} | perda: {media:.4f}")

        # Salva
        torch.save(self.modelo.state_dict(), GERADOR_PATH)
        self.modelo.eval()
        print(f"\033[92m[GERADOR]: Treinamento concluído! Modelo salvo.\033[0m")

    def gerar(self, texto: str, temperatura: float = 0.8) -> str | None:
        """Gera uma resposta para o texto de entrada."""
        if self.modelo is None or self.vocab is None:
            return None

        self.modelo.eval()
        with torch.no_grad():
            src = torch.tensor(
                [self.vocab.encode(texto, MAX_LEN_IN)],
                dtype=torch.long
            ).T.to(DEVICE)

            enc_out, hidden = self.modelo.encoder(src)

            token_in = torch.tensor([[SOS]], dtype=torch.long).to(DEVICE)
            tokens_gerados = []

            for _ in range(MAX_LEN_OUT):
                predicao, hidden, _ = self.modelo.decoder(token_in, hidden, enc_out)

                # Temperatura — controla criatividade
                if temperatura != 1.0:
                    predicao = predicao / temperatura

                probs     = F.softmax(predicao, dim=-1)
                token_out = torch.multinomial(probs, 1).item()

                if token_out == EOS:
                    break

                tokens_gerados.append(token_out)
                token_in = torch.tensor([[token_out]], dtype=torch.long).to(DEVICE)

        resposta = self.vocab.decode(tokens_gerados)
        return resposta if resposta else None

    def esta_treinado(self) -> bool:
        return self.modelo is not None and self.vocab is not None


# ---------------------------------------------------------------------------
# Teste standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    gerador = SiriusGerador()

    if not gerador.esta_treinado():
        print("[GERADOR]: Nenhum modelo encontrado. Treinando agora...")
        gerador.treinar(epocas=50)

    print("\n--- Teste de geração ---")
    testes = [
        "oi sirius tudo bem",
        "o que você faz",
        "me fala sobre python",
        "valeu sirius",
    ]
    for t in testes:
        r = gerador.gerar(t)
        print(f"  Entrada: {t}")
        print(f"  Saída:   {r or '(sem resposta — treine mais)'}\n")