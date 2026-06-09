#!/usr/bin/env python3
"""
neuronio.py — S.I.R.I.U.S. v4.0 — Transformer Unificado
=========================================================

Junção das versões v2.0 e v3.0, pegando o melhor de cada uma:

  Da v3.0 (upload) — base principal:
    • nn.TransformerEncoder nativo PyTorch (2 camadas, norm_first=True / Pre-LN)
    • Mean Pooling ponderado sobre tokens válidos (matematicamente superior)
    • LayerNorm pós-pooling para estabilizar representação final
    • Máscara de padding correta: True = ignorar (padrão PyTorch)
    • carregar_embeddings_pretreinados() com hook de gradiente granular
    • Fine-tuning seletivo: termos_finetune descongelados, resto congelado
    • _tentar_carregar_embeddings() automático pós-treino
    • _expandir_pesos_cirurgicamente() no SiriusNeuronio (separação correta)
    • Herança de embeddings já treinados entre treinos
    • predizer(debug=True) com visualização de atenção por token
    • SiriusNeuronio.__init__ com paths relativos robustos (sirius_treino.db)
    • weights_only=True no torch.load (segurança)
    • CUDA automático (cuda se disponível, senão cpu)
    • class Attention legada (compatibilidade com test_upgrade.py)

  Da v2.0 (documento) — adicionado/melhorado:
    • torch.set_num_threads(4) para otimização explícita em CPU
    • EmbeddingLoader como classe standalone (útil para scripts externos)
    • treinar() aceita embeddings_path e label_smoothing como parâmetros
      (v3.0 tinha label_smoothing hardcoded=0.1; v4.0 permite ajuste)
    • scheduler ReduceLROnPlateau sem verbose (removido no PyTorch >= 2.2)
    • RedeSirius.__init__ aceita pretrained_embeddings e freeze_embeddings
      para injeção direta de pesos no construtor (fluxo alternativo)
    • expand_output_layer() mantido no RedeSirius como alias de compatibilidade
    • Docstrings expandidas: fórmulas, shapes, notas de implementação

Interface pública (compatível com sirius_agentes.py):
    predizer(texto, temp=1.0, debug=False, user_id=None) → (tema, confiança)
    treinar(user_id, epochs, batch_size, embeddings_path, label_smoothing)
    carregar_dados_com_replay(user_id) → DataFrame | None
    arquivar_conhecimento(df, user_id)
"""

import sqlite3
import os
import pickle
import threading
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import LabelEncoder
from collections import Counter
import re
import math
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Pandas lazy loading
# ---------------------------------------------------------------------------

_pd = None

def _get_pd():
    global _pd
    if _pd is None:
        import pandas as _pandas
        _pd = _pandas
    return _pd


def _validar_user_id(user_id):
    """Valida e sanitiza user_id — retorna None se inválido."""
    if not user_id or not isinstance(user_id, str):
        return None
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        return None
    if not re.match(r'^[a-zA-Z0-9_-]+$', user_id):
        return None
    return user_id


# ---------------------------------------------------------------------------
# Positional Encoding — sinusoidal (Vaswani et al., 2017)
# ---------------------------------------------------------------------------

class PositionalEncoding(nn.Module):
    """
    Codificação posicional sinusoidal injectada nos embeddings.

    Necessária porque o Transformer processa todos os tokens em paralelo
    e, sem isso, seria invariante à ordem das palavras.

    PE(pos, 2i)   = sin(pos / 10000^(2i / d_model))
    PE(pos, 2i+1) = cos(pos / 10000^(2i / d_model))

    O buffer 'pe' é registado via register_buffer:
      • Não é parâmetro treinável (não recebe gradiente)
      • Viaja com o modelo em .to(device) e state_dict
    """

    def __init__(self, d_model: int, max_len: int = 512, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(dropout)

        pe       = torch.zeros(max_len, d_model)                        # (max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)  # (max_len, 1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float)
            * (-math.log(10000.0) / d_model)
        )                                                                # (d_model/2,)

        pe[:, 0::2] = torch.sin(position * div_term)   # dimensões pares
        pe[:, 1::2] = torch.cos(position * div_term)   # dimensões ímpares
        pe = pe.unsqueeze(0)                            # (1, max_len, d_model)
        self.register_buffer('pe', pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (batch_size, seq_len, d_model)
        Returns:
            x + PE[:, :seq_len, :] — mesma shape
        """
        x = x + self.pe[:, :x.size(1), :]
        return self.dropout(x)


# ---------------------------------------------------------------------------
# Attention legada — mantida para compatibilidade com test_upgrade.py
# ---------------------------------------------------------------------------

class Attention(nn.Module):
    """
    Mecanismo de Atenção dot-product (v3.0).

    Mantido exclusivamente para compatibilidade com test_upgrade.py.
    No pipeline principal a atenção é realizada internamente pelo
    nn.TransformerEncoderLayer com nn.MultiheadAttention.

    Não instanciar directamente em código novo.
    """

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.query   = nn.Linear(hidden_dim, hidden_dim)
        self.key     = nn.Linear(hidden_dim, hidden_dim)
        self.value   = nn.Linear(hidden_dim, hidden_dim)
        self.scale   = math.sqrt(hidden_dim)
        self.dropout = nn.Dropout(0.1)

    def forward(self, hidden_states: torch.Tensor, mask=None):
        """
        Args:
            hidden_states: (batch, seq_len, hidden_dim)
            mask:          (batch, seq_len) — 1=válido, 0=padding
        Returns:
            context:           (batch, hidden_dim)
            attention_weights: (batch, seq_len)
        """
        Q = self.query(hidden_states)
        K = self.key(hidden_states)
        V = self.value(hidden_states)

        scores = torch.bmm(Q, K.transpose(1, 2)) / self.scale   # (batch, seq, seq)

        if mask is not None:
            scores = scores.masked_fill(mask.unsqueeze(1) == 0, -1e9)

        attention_weights = torch.softmax(scores, dim=-1)
        attention_weights = self.dropout(attention_weights)

        context       = torch.bmm(attention_weights, V).mean(dim=1)   # (batch, hidden_dim)
        attention_map = attention_weights.mean(dim=1)                  # (batch, seq_len)

        return context, attention_map


# ---------------------------------------------------------------------------
# RedeSirius v4.0 — Transformer Encoder (2 camadas, Pre-LN, Mean Pooling)
# ---------------------------------------------------------------------------

class RedeSirius(nn.Module):
    """
    Transformer Encoder compacto para classificação de intenções em Português.

    Pipeline (v4.0):
      1. Embedding Layer          — tokens → vetores densos
                                    Aceita pesos pré-treinados no construtor (v2.0)
                                    ou via carregar_embeddings_pretreinados() (v3.0)
      2. Positional Encoding      — injeta ordem da sequência
      3. Transformer Encoder (×2) — 4 cabeças, FFN=512, norm_first=True (Pre-LN)
                                    Pre-LN converge melhor em sequências curtas
      4. Padding Mask             — True=ignorar (padrão correto do PyTorch)
      5. Mean Pooling ponderado   — agrega sequência → vetor fixo
                                    Mais representativo que Max Pooling
      6. LayerNorm pós-pooling    — estabiliza a representação final
      7. Dropout (0.3)            — regularização
      8. Linear (fc)              — classificação final

    Parâmetros do S.I.R.I.U.S.:
      vocab_size  = dinâmico
      embed_dim   = 128
      hidden_dim  = 256  (referência; FFN interna = 512)
      num_classes = dinâmico
      max_len     = 50
    """

    def __init__(
        self,
        vocab_size:            int,
        embed_dim:             int,
        hidden_dim:            int,
        num_classes:           int,
        max_len:               int   = 50,
        pretrained_embeddings        = None,   # (v2.0) tensor [vocab, embed_dim]
        freeze_embeddings:     bool  = False,  # (v2.0) congela embeddings globalmente
    ):
        super().__init__()
        self.embed_dim  = embed_dim
        self.hidden_dim = hidden_dim
        self.max_len    = max_len
        self.num_classes = num_classes

        # 1. Embedding Layer
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)

        # Injecção de pesos pré-treinados no construtor (fluxo v2.0 / EmbeddingLoader)
        if pretrained_embeddings is not None:
            print("[NEURÔNIO]: Carregando embeddings pré-treinados no construtor...")
            self.embedding.weight.data.copy_(pretrained_embeddings)
            if freeze_embeddings:
                self.embedding.weight.requires_grad = False
                print("[NEURÔNIO]: Embeddings congelados globalmente.")

        # 2. Positional Encoding sinusoidal
        self.pos_encoding = PositionalEncoding(embed_dim, max_len=max_len + 2)

        # 3. Transformer Encoder — 2 camadas, 4 cabeças, FFN=512, Pre-LN
        #    norm_first=True  → pré-normalização (mais estável em sequências curtas)
        #    batch_first=True → convenção (batch, seq, dim) em todo o código
        encoder_layer = nn.TransformerEncoderLayer(
            d_model        = embed_dim,
            nhead          = 4,
            dim_feedforward= 512,
            dropout        = 0.1,
            activation     = 'relu',
            batch_first    = True,
            norm_first     = True,
        )
        self.transformer_encoder = nn.TransformerEncoder(
            encoder_layer,
            num_layers          = 2,
            enable_nested_tensor= False,   # compatibilidade CPU
        )

        # 4. LayerNorm pós-pooling (v3.0)
        self.layer_norm = nn.LayerNorm(embed_dim)

        # 5. Dropout
        self.dropout = nn.Dropout(0.3)

        # 6. Camada de classificação
        self.fc = nn.Linear(embed_dim, num_classes)

        # Inicialização Xavier
        self._init_weights()

    # ------------------------------------------------------------------
    # Inicialização de pesos
    # ------------------------------------------------------------------

    def _init_weights(self):
        """
        Xavier/Glorot para convergência rápida em CPU.
        Não reinicializa embeddings (podem estar pré-treinados).
        """
        for name, param in self.named_parameters():
            if 'weight' in name and param.dim() > 1:
                if 'embedding' not in name:
                    nn.init.xavier_uniform_(param)
            elif 'bias' in name:
                nn.init.zeros_(param)

    # ------------------------------------------------------------------
    # Máscara de padding
    # ------------------------------------------------------------------

    def _create_padding_mask(self, x: torch.Tensor) -> torch.Tensor:
        """
        Cria máscara para o TransformerEncoder.

        nn.TransformerEncoder espera src_key_padding_mask com shape
        (batch, seq_len), onde True indica posições A IGNORAR (padding).

        Args:
            x: (batch, seq_len) — índices de tokens (0 = <PAD>)
        Returns:
            mask: (batch, seq_len) — True onde há padding
        """
        return (x == 0)   # True = ignorar (padrão correto do PyTorch)

    # ------------------------------------------------------------------
    # Forward
    # ------------------------------------------------------------------

    def forward(
        self,
        x:                torch.Tensor,
        return_attention: bool = False,
    ):
        """
        Forward pass completo.

        Args:
            x:                (batch, seq_len) — índices de tokens
            return_attention: se True, retorna também mapa de atenção

        Returns:
            logits:        (batch, num_classes)
            attention_map: (batch, seq_len) — apenas se return_attention=True
        """
        # 1. Embedding + Positional Encoding
        embedded = self.embedding(x)           # (batch, seq, embed_dim)
        embedded = self.pos_encoding(embedded) # (batch, seq, embed_dim)

        # 2. Máscara de padding (True = ignorar)
        pad_mask = self._create_padding_mask(x)  # (batch, seq)

        # 3. Transformer Encoder (2 camadas, Pre-LN)
        encoded = self.transformer_encoder(
            embedded,
            src_key_padding_mask=pad_mask,
        )                                      # (batch, seq, embed_dim)

        # 4. Mean Pooling ponderado — apenas tokens válidos (v3.0)
        #    Mais representativo que Max Pooling para frases completas
        valid_mask  = (~pad_mask).float().unsqueeze(-1)   # (batch, seq, 1)
        sum_encoded = (encoded * valid_mask).sum(dim=1)   # (batch, embed_dim)
        n_valid     = valid_mask.sum(dim=1).clamp(min=1)  # (batch, 1)
        pooled      = sum_encoded / n_valid               # (batch, embed_dim)

        # 5. LayerNorm + Dropout
        pooled = self.layer_norm(pooled)
        pooled = self.dropout(pooled)

        # 6. Classificação
        logits = self.fc(pooled)              # (batch, num_classes)

        if return_attention:
            # Proxy de atenção: norma L2 dos tokens codificados, normalizada
            attn_proxy = encoded.norm(dim=-1)                        # (batch, seq)
            attn_proxy = attn_proxy.masked_fill(pad_mask, 0.0)
            attn_sum   = attn_proxy.sum(dim=1, keepdim=True).clamp(min=1e-9)
            attention_map = attn_proxy / attn_sum                    # (batch, seq)
            return logits, attention_map

        return logits

    # ------------------------------------------------------------------
    # Carregamento de embeddings pré-treinados via método (v3.0)
    # ------------------------------------------------------------------

    def carregar_embeddings_pretreinados(
        self,
        caminho_vec:    str,
        vocab:          dict,
        congelar_comuns: bool = True,
        termos_finetune: set  = None,
    ) -> int:
        """
        Inicializa a camada de embedding com vectores pré-treinados.

        Suporta formato .vec (texto, FastText / GloVe):
          <palavra> <v1> <v2> ... <vN>

        Estratégia de congelamento granular (v3.0):
          • Palavras comuns com vector carregado → congeladas (hook de gradiente)
          • termos_finetune (vocab do S.I.R.I.U.S.) → descongelados para fine-tuning
          • Palavras sem vector → inicialização Xavier, treináveis

        Args:
            caminho_vec:      caminho para ficheiro .vec
            vocab:            dicionário {token: idx}
            congelar_comuns:  congelar palavras comuns pré-treinadas
            termos_finetune:  termos do domínio que ficam treináveis

        Returns:
            n_carregados: número de vectores efectivamente carregados
        """
        if not os.path.exists(caminho_vec):
            print(f"[NEURÔNIO]: Ficheiro de embeddings não encontrado: {caminho_vec}")
            return 0

        print(f"[NEURÔNIO]: Carregando embeddings pré-treinados de {caminho_vec}...")
        termos_finetune = termos_finetune or set()
        vectores = {}

        try:
            with open(caminho_vec, 'r', encoding='utf-8', errors='ignore') as f:
                primeira_linha = f.readline().strip().split()

                # Verifica se é cabeçalho FastText "n_palavras dim"
                if len(primeira_linha) != 2:
                    token = primeira_linha[0]
                    if token in vocab:
                        try:
                            vec = torch.tensor(
                                [float(v) for v in primeira_linha[1:]], dtype=torch.float
                            )
                            if vec.shape[0] == self.embed_dim:
                                vectores[token] = vec
                        except ValueError:
                            pass

                for linha in f:
                    partes = linha.rstrip().split(' ')
                    token  = partes[0]
                    if token not in vocab:
                        continue
                    try:
                        vec = torch.tensor(
                            [float(v) for v in partes[1:]], dtype=torch.float
                        )
                        if vec.shape[0] == self.embed_dim:
                            vectores[token] = vec
                    except ValueError:
                        continue

        except Exception as e:
            print(f"[NEURÔNIO]: Erro ao ler embeddings: {e}")
            return 0

        # Injectar vectores na camada de embedding
        with torch.no_grad():
            for token, vec in vectores.items():
                idx = vocab[token]
                if idx < self.embedding.num_embeddings:
                    self.embedding.weight[idx] = vec

        n_carregados = len(vectores)
        cobertura    = n_carregados / max(len(vocab), 1) * 100
        print(f"[NEURÔNIO]: {n_carregados}/{len(vocab)} vectores carregados ({cobertura:.1f}% cobertura).")

        # Hook de gradiente granular — congela palavras comuns, descongela termos_finetune
        if congelar_comuns and n_carregados > 0:
            frozen_mask = torch.zeros(self.embedding.num_embeddings, dtype=torch.bool)
            for token, idx in vocab.items():
                if token in vectores and token not in termos_finetune:
                    frozen_mask[idx] = True

            def _freeze_hook(grad: torch.Tensor) -> torch.Tensor:
                grad = grad.clone()
                grad[frozen_mask] = 0.0
                return grad

            self.embedding.weight.register_hook(_freeze_hook)
            n_congelados = frozen_mask.sum().item()
            print(
                f"[NEURÔNIO]: {n_congelados} embeddings congelados, "
                f"{len(termos_finetune)} em fine-tuning."
            )

        return n_carregados

    # ------------------------------------------------------------------
    # Expansão Cirúrgica de Pesos — alias de compatibilidade (v2.0)
    # ------------------------------------------------------------------

    def expand_output_layer(
        self,
        new_num_classes: int,
        old_weights = None,
        old_bias    = None,
    ):
        """
        Alias de compatibilidade com código que chama este método directamente
        no RedeSirius (v2.0). Em código novo usar
        SiriusNeuronio._expandir_pesos_cirurgicamente().

        Expande a camada fc preservando pesos das classes antigas.
        """
        if new_num_classes <= self.num_classes:
            return

        print(f"[NEURÔNIO]: Expansão Cirúrgica (alias): {self.num_classes} → {new_num_classes}")

        nova_fc = nn.Linear(self.embed_dim, new_num_classes)
        nn.init.xavier_uniform_(nova_fc.weight)
        nn.init.zeros_(nova_fc.bias)

        src_w = old_weights if old_weights is not None else self.fc.weight.data
        src_b = old_bias    if old_bias    is not None else self.fc.bias.data

        with torch.no_grad():
            nova_fc.weight[:self.num_classes, :] = src_w
            nova_fc.bias[:self.num_classes]      = src_b

        self.fc          = nova_fc
        self.num_classes = new_num_classes


# ---------------------------------------------------------------------------
# EmbeddingLoader — classe standalone (v2.0) para scripts externos
# ---------------------------------------------------------------------------

class EmbeddingLoader:
    """
    Carrega embeddings pré-treinados de arquivos FastText ou GloVe.
    Útil para scripts externos que precisam pré-computar a matriz
    antes de instanciar o RedeSirius.

    Formatos suportados:
      • FastText .vec — primeira linha é "vocab_size embed_dim"
      • GloVe .txt   — sem cabeçalho

    Uso:
        loader = EmbeddingLoader(vocab_dict, embed_dim=128)
        tensor = loader.load("fasttext_pt.vec")   # [vocab_size, embed_dim]
        model  = RedeSirius(..., pretrained_embeddings=tensor)
    """

    def __init__(self, vocab: dict, embed_dim: int):
        self.vocab      = vocab   # {token: idx}
        self.embed_dim  = embed_dim

    def load(self, filepath: str) -> torch.Tensor | None:
        """
        Carrega embeddings do ficheiro e retorna tensor [vocab_size, embed_dim].
        Palavras não encontradas recebem inicialização Xavier.
        """
        if not os.path.exists(filepath):
            print(f"[NEURÔNIO]: Arquivo de embeddings não encontrado: {filepath}")
            return None

        print(f"[NEURÔNIO]: Carregando embeddings de {filepath}...")
        embeddings  = torch.zeros(len(self.vocab), self.embed_dim)
        loaded      = 0

        with open(filepath, 'r', encoding='utf-8') as f:
            primeira = f.readline().strip().split()
            if len(primeira) != 2:
                f.seek(0)   # GloVe — sem cabeçalho

            for linha in f:
                partes = linha.strip().split()
                if len(partes) < self.embed_dim + 1:
                    continue
                word = partes[0]
                if word in self.vocab:
                    idx = self.vocab[word]
                    embeddings[idx] = torch.tensor(
                        [float(x) for x in partes[1:self.embed_dim + 1]]
                    )
                    loaded += 1

        cobertura = loaded / max(len(self.vocab), 1) * 100
        print(f"[NEURÔNIO]: {loaded}/{len(self.vocab)} palavras carregadas ({cobertura:.1f}%).")

        # Palavras não encontradas → Xavier
        missing_mask = (embeddings.sum(dim=1) == 0)
        n_missing    = missing_mask.sum().item()
        if n_missing > 0:
            nn.init.xavier_uniform_(embeddings[missing_mask].unsqueeze(0))
            print(f"[NEURÔNIO]: {n_missing} palavras inicializadas com Xavier.")

        return embeddings


# ---------------------------------------------------------------------------
# SiriusNeuronio v4.0 — Wrapper de alto nível
# ---------------------------------------------------------------------------

class SiriusNeuronio:
    """
    Interface de alto nível para o modelo neural do S.I.R.I.U.S. v4.0.

    Responsabilidades:
      • Carregar / salvar modelo, vocab e label_encoder de/para disco.
      • Tokenização, indexação e construção de vocabulário.
      • Treino com Expansão Cirúrgica + Label Smoothing (configurável).
      • Predição com Temperatura de Softmax e threshold de confiança.
      • Herança de embeddings entre treinos (evita reaprender do zero).
      • Detecção automática de embeddings pré-treinados (.vec/.bin).
      • Thread-safety completo (treino em background).

    Compatível com sirius_agentes.py:
        predizer(texto) → (tema: str, confiança: float)
        treinar(user_id, epochs, batch_size) — thread-safe
    """

    CONFIANCA_MINIMA = 0.65

    def __init__(self):
        # ── Paths robustos relativos ao ficheiro (v3.0) ───────────────────────
        diretorio_src  = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
        caminho_data   = os.path.join(diretorio_raiz, "data")
        os.makedirs(caminho_data, exist_ok=True)

        self.db_path        = os.path.join(caminho_data, "sirius_treino.db")
        self.modelo_path    = os.path.join(caminho_data, "sirius_model.pth")
        self.vocab_path     = os.path.join(caminho_data, "vocab.pkl")
        self.label_path     = os.path.join(caminho_data, "label_encoder.pkl")
        self.embeddings_dir = caminho_data   # pasta onde procura .vec/.bin

        # ── Hiperparâmetros ───────────────────────────────────────────────────
        self.embed_dim  = 128
        self.hidden_dim = 256
        self.max_len    = 50
        self.min_freq   = 2

        # ── Termos do domínio descongelados para fine-tuning (v3.0) ──────────
        self.termos_finetune: set = {
            "sirius", "pesquisa", "resumo", "analise", "escrita",
            "duvidas", "especialista", "pesquisador", "resumidor",
            "analisador", "escritor",
        }

        # ── Device: CUDA se disponível, senão CPU com 4 threads (v2.0+v3.0) ──
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if self.device.type == "cpu":
            torch.set_num_threads(4)   # optimização explícita para CPU (v2.0)
        print(f"[NEURÔNIO]: Usando device: {self.device}")

        # ── Estado ────────────────────────────────────────────────────────────
        self.vocab         = self._carregar_vocab() or {}
        self.label_encoder = self._carregar_ferramenta(self.label_path) or LabelEncoder()
        self.model: RedeSirius | None = None

        # ── Thread-safety ─────────────────────────────────────────────────────
        self._db_lock    = threading.Lock()
        self._model_lock = threading.RLock()   # RLock: reentrante para predição + treino

        # ── Buffer pré-alocado — zero-cópia nas predições (v3.0) ─────────────
        self._X_buffer = torch.zeros(1, self.max_len, dtype=torch.long).to(self.device)

        self._inicializar_modelo()

    # -----------------------------------------------------------------------
    # Persistência de vocab / ferramentas
    # -----------------------------------------------------------------------

    def _carregar_vocab(self) -> dict | None:
        if os.path.exists(self.vocab_path):
            try:
                with open(self.vocab_path, "rb") as f:
                    return pickle.load(f)
            except Exception:
                return {}
        return {}

    def _salvar_vocab(self):
        with open(self.vocab_path, "wb") as f:
            pickle.dump(self.vocab, f)

    def _carregar_ferramenta(self, caminho: str):
        if os.path.exists(caminho):
            try:
                with open(caminho, "rb") as f:
                    return pickle.load(f)
            except Exception:
                return None
        return None

    # -----------------------------------------------------------------------
    # Tokenização / indexação
    # -----------------------------------------------------------------------

    def _tokenizar(self, texto: str) -> list[str]:
        """Remove pontuação e converte para minúsculas."""
        texto = re.sub(r'[^\w\s]', '', texto.lower())
        return texto.split()

    def _texto_para_indices(self, texto: str) -> list[int]:
        tokens  = self._tokenizar(texto)
        indices = [self.vocab.get(token, 1) for token in tokens[:self.max_len]]
        indices += [0] * (self.max_len - len(indices))   # padding
        return indices

    def _construir_vocab(self, textos: list[str]):
        """Constrói vocabulário com frequência mínima de self.min_freq."""
        all_tokens = []
        for texto in textos:
            all_tokens.extend(self._tokenizar(texto))
        counter = Counter(all_tokens)
        vocab   = {'<PAD>': 0, '<UNK>': 1}
        idx     = 2
        for token, freq in counter.items():
            if freq >= self.min_freq:
                vocab[token] = idx
                idx += 1
        self.vocab = vocab
        self._salvar_vocab()
        print(f"[NEURÔNIO]: Vocabulário construído: {len(self.vocab)} tokens.")

    # -----------------------------------------------------------------------
    # Inicialização do modelo (carregamento de disco)
    # -----------------------------------------------------------------------

    def _inicializar_modelo(self):
        if (os.path.exists(self.modelo_path)
                and os.path.exists(self.label_path)
                and self.vocab):
            try:
                num_classes = len(self.label_encoder.classes_)
                vocab_size  = len(self.vocab)
                self.model  = RedeSirius(
                    vocab_size, self.embed_dim, self.hidden_dim,
                    num_classes, self.max_len,
                )
                self.model.load_state_dict(
                    torch.load(
                        self.modelo_path,
                        weights_only=True,        # segurança (v3.0)
                        map_location=self.device,
                    )
                )
                self.model.to(self.device)
                self.model.eval()
                print(
                    f"\033[92m[NEURÔNIO]: Modelo Transformer carregado "
                    f"({num_classes} classes).\033[0m"
                )
            except Exception as e:
                print(f"[NEURÔNIO]: Modelo não pôde ser carregado: {e}")
                # Checkpoint incompatível com a arquitetura atual (ex: modelo antigo
                # com layer1/2/3 vs Transformer atual). Apaga o arquivo corrompido/obsoleto
                # para que o próximo treino crie um checkpoint novo do zero.
                try:
                    os.remove(self.modelo_path)
                    print(
                        "[93m[NEURÔNIO]: Checkpoint antigo removido automaticamente. "
                        "O modelo será recriado no próximo treino.[0m"
                    )
                except OSError:
                    pass

    # -----------------------------------------------------------------------
    # Detecção automática de embeddings pré-treinados (v3.0)
    # -----------------------------------------------------------------------

    def _tentar_carregar_embeddings(self):
        """
        Procura em self.embeddings_dir por um ficheiro .vec ou .bin.
        Se encontrado com dimensão compatível, inicializa os embeddings.
        Chamado automaticamente após cada treino.
        """
        if self.model is None:
            return

        candidatos = [
            f for f in os.listdir(self.embeddings_dir)
            if f.endswith('.vec') or f.endswith('.bin')
        ]
        if not candidatos:
            return

        # Preferir .vec (formato texto) sobre .bin
        candidatos.sort(key=lambda f: (0 if f.endswith('.vec') else 1))
        caminho = os.path.join(self.embeddings_dir, candidatos[0])

        n = self.model.carregar_embeddings_pretreinados(
            caminho_vec     = caminho,
            vocab           = self.vocab,
            congelar_comuns = True,
            termos_finetune = self.termos_finetune,
        )
        if n > 0:
            print(
                f"\033[93m[NEURÔNIO]: Semântica pré-treinada activa "
                f"({n} vectores de '{candidatos[0]}').\033[0m"
            )

    # -----------------------------------------------------------------------
    # Expansão Cirúrgica de Pesos (v3.0 — no SiriusNeuronio)
    # -----------------------------------------------------------------------

    def _expandir_pesos_cirurgicamente(
        self,
        modelo_antigo:   RedeSirius,
        novo_num_classes: int,
    ) -> nn.Linear:
        """
        Cria nova camada Linear preservando pesos e bias das classes antigas
        nas suas posições originais. Novos neurónios → inicialização Xavier.

        Previne "catastrophic forgetting" ao adicionar novas classes.

        Args:
            modelo_antigo:    modelo com a camada fc actual
            novo_num_classes: total de classes após expansão

        Returns:
            nova nn.Linear pronta para substituir modelo.fc
        """
        fc_antigo   = modelo_antigo.fc
        n_antigos   = fc_antigo.out_features
        in_features = fc_antigo.in_features

        if novo_num_classes <= n_antigos:
            return fc_antigo   # sem expansão necessária

        nova_fc = nn.Linear(in_features, novo_num_classes)
        nn.init.xavier_uniform_(nova_fc.weight)
        nn.init.zeros_(nova_fc.bias)

        with torch.no_grad():
            nova_fc.weight[:n_antigos, :] = fc_antigo.weight.data
            nova_fc.bias[:n_antigos]      = fc_antigo.bias.data

        print(
            f"[NEURÔNIO]: Expansão Cirúrgica: {n_antigos} → {novo_num_classes} classes. "
            f"Pesos históricos preservados intactos."
        )
        return nova_fc

    # -----------------------------------------------------------------------
    # Predição com Temperatura de Softmax (v3.0 + debug da v3.0)
    # -----------------------------------------------------------------------

    def predizer(
        self,
        texto:   str,
        temp:    float = 1.0,
        debug:   bool  = False,
        user_id: str   = None,   # aceite por compatibilidade (v2.0), não usado
    ) -> tuple[str, float]:
        """
        Prediz o tema do texto e retorna (tema, confiança).

        Temperatura de Softmax:
          • temp < 1.0 → distribuição aguçada → rede conservadora (mais precisa)
          • temp = 1.0 → comportamento padrão
          • temp > 1.0 → distribuição suave → rede experimental (mais ousada)

        Args:
            texto:   texto de entrada
            temp:    temperatura do softmax (default=1.0)
            debug:   exibe mapa de atenção por token no console
            user_id: ignorado (mantido para compatibilidade com v2.0)

        Returns:
            (tema, confiança) — ("Novo_Tema", conf) se confiança < CONFIANCA_MINIMA
        """
        if self.model is None or not self.vocab:
            return ("Novo_Tema", 0.0)

        if not texto or not texto.strip():
            return ("Novo_Tema", 0.0)

        try:
            with self._model_lock:
                indices = self._texto_para_indices(texto)
                tokens  = self._tokenizar(texto)

                self._X_buffer.zero_()
                self._X_buffer[0] = torch.tensor(indices, dtype=torch.long)

                with torch.no_grad():
                    logits, attention_weights = self.model(
                        self._X_buffer, return_attention=True
                    )
                    # Temperatura aplicada antes do softmax (v2.0 + v3.0)
                    scaled_logits   = logits / max(temp, 1e-6)
                    probs           = torch.softmax(scaled_logits, dim=-1)
                    confianca, pred = torch.max(probs, dim=1)

                confianca_val = confianca.item()

                if confianca_val < self.CONFIANCA_MINIMA:
                    print(
                        f"[NEURÔNIO]: Confiança {confianca_val:.2f} < "
                        f"{self.CONFIANCA_MINIMA} (temp={temp:.1f}) → Novo_Tema"
                    )
                    return ("Novo_Tema", confianca_val)

                tema = self.label_encoder.inverse_transform([pred.item()])[0]
                print(
                    f"[NEURÔNIO]: Predição='{tema}' conf={confianca_val:.2f} "
                    f"temp={temp:.1f}"
                )

                if debug and len(tokens) > 0:
                    self._visualizar_atencao(tokens, attention_weights[0].cpu().numpy())

                return (tema, confianca_val)

        except Exception as e:
            print(f"[NEURÔNIO]: Erro na predição: {e}")
            return ("Novo_Tema", 0.0)

    def _visualizar_atencao(self, tokens: list[str], attention_weights):
        """Exibe no console quais palavras receberam maior atenção (v3.0)."""
        print("\n" + "=" * 60)
        print("MAPA DE ATENÇÃO — Palavras em foco:")
        print("=" * 60)

        valid_weights = attention_weights[:len(tokens)]
        if valid_weights.sum() > 0:
            valid_weights = valid_weights / valid_weights.sum() * 100

        token_importance = sorted(
            zip(tokens, valid_weights),
            key=lambda x: x[1],
            reverse=True,
        )
        for token, weight in token_importance[:5]:
            bar = "█" * int(weight / 2)
            print(f"  {token:15s} │ {bar} {weight:5.1f}%")

        print("=" * 60 + "\n")

    # -----------------------------------------------------------------------
    # Treino com Expansão Cirúrgica + Label Smoothing (v2.0 + v3.0)
    # -----------------------------------------------------------------------

    def treinar(
        self,
        user_id:        str   = None,
        epochs:         int   = 50,
        batch_size:     int   = 32,
        embeddings_path: str  = None,   # (v2.0) caminho explícito para .vec
        label_smoothing: float = 0.1,   # (v2.0) configurável; v3.0 era hardcoded
    ):
        """
        Treina / retreina o modelo Transformer.

        Destaques v4.0:
          • CrossEntropyLoss(label_smoothing=configurável) — evita sobre-confiança
          • Expansão Cirúrgica da fc — herda pesos das classes antigas
          • Herança dos embeddings já treinados (evita reaprender do zero)
          • Detecção automática de embeddings pré-treinados pós-treino
          • ReduceLROnPlateau sem verbose (removido no PyTorch >= 2.2)
          • Gradient clipping (max_norm=1.0)
          • Salva o melhor modelo (menor loss)

        Args:
            user_id:         identificador do utilizador (opcional)
            epochs:          número de épocas (default=50)
            batch_size:      tamanho do batch (default=32)
            embeddings_path: caminho explícito para .vec (opcional)
            label_smoothing: factor de suavização 0.0–0.2 (default=0.1)
        """
        user_id = _validar_user_id(user_id)
        pd      = _get_pd()

        df = self.carregar_dados_com_replay(user_id)
        if df is None or len(df) < 5:
            print("[NEURÔNIO]: Dados insuficientes para evoluir.")
            return

        textos = df["conteudo"].str.lower().tolist()
        self._construir_vocab(textos)

        # ── Detectar classes novas ──────────────────────────────────────────
        temas_db       = self._buscar_temas_novos_no_db(user_id)
        classes_atuais = (
            set(self.label_encoder.classes_)
            if hasattr(self.label_encoder, 'classes_') else set()
        )
        temas_novos = temas_db - classes_atuais
        if temas_novos:
            print(f"[NEURÔNIO]: Novas classes detectadas: {temas_novos}")

        # ── Preparar dados ─────────────────────────────────────────────────
        le_novo   = LabelEncoder()
        X_indices = [self._texto_para_indices(t) for t in textos]
        X         = torch.tensor(X_indices, dtype=torch.long).to(self.device)
        y_raw     = le_novo.fit_transform(df["tema"])
        y         = torch.LongTensor(y_raw).to(self.device)

        vocab_size  = len(self.vocab)
        num_classes = len(le_novo.classes_)

        # ── Carregar embeddings via EmbeddingLoader se caminho explícito (v2.0)
        pretrained_embeddings = None
        if embeddings_path and os.path.exists(embeddings_path):
            loader = EmbeddingLoader(self.vocab, self.embed_dim)
            pretrained_embeddings = loader.load(embeddings_path)

        # ── Construir novo modelo ──────────────────────────────────────────
        modelo_novo = RedeSirius(
            vocab_size, self.embed_dim, self.hidden_dim,
            num_classes, self.max_len,
            pretrained_embeddings = pretrained_embeddings,
            freeze_embeddings     = (pretrained_embeddings is not None),
        ).to(self.device)

        # ── Expansão Cirúrgica — herdar fc antiga (v3.0) ───────────────────
        if self.model is not None:
            fc_expandida = self._expandir_pesos_cirurgicamente(self.model, num_classes)
            modelo_novo.fc = fc_expandida.to(self.device)

            # Herdar embeddings se vocab não mudou de tamanho (v3.0)
            if (pretrained_embeddings is None
                    and self.model.embedding.num_embeddings == vocab_size):
                with torch.no_grad():
                    modelo_novo.embedding.weight.data.copy_(
                        self.model.embedding.weight.data
                    )
                print("[NEURÔNIO]: Embeddings anteriores herdados.")

        self.model         = modelo_novo
        self.label_encoder = le_novo

        # ── Loss + Optimizador + Scheduler ────────────────────────────────
        criterion = nn.CrossEntropyLoss(label_smoothing=label_smoothing)
        optimizer = optim.AdamW(
            self.model.parameters(),
            lr           = 0.001,
            weight_decay = 0.01,
            betas        = (0.9, 0.999),   # (v2.0)
        )
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', factor=0.5, patience=5  # verbose removido no PyTorch >= 2.2
        )

        print(
            f"[NEURÔNIO]: Transformer evoluindo — {len(df)} registos, "
            f"{num_classes} classes, label_smoothing={label_smoothing}, "
            f"Transformer 4-heads, 2 camadas..."
        )

        best_loss = float('inf')
        for epoch in range(epochs):
            self.model.train()
            epoch_loss  = 0.0
            num_batches = 0

            for i in range(0, len(X), batch_size):
                bX = X[i:i + batch_size]
                by = y[i:i + batch_size]

                optimizer.zero_grad()
                logits = self.model(bX)
                loss   = criterion(logits, by)
                loss.backward()

                torch.nn.utils.clip_grad_norm_(
                    self.model.parameters(), max_norm=1.0
                )
                optimizer.step()

                epoch_loss  += loss.item()
                num_batches += 1

            avg_loss = epoch_loss / max(num_batches, 1)
            scheduler.step(avg_loss)

            if (epoch + 1) % 10 == 0:
                print(f"  Época {epoch + 1}/{epochs} | Loss: {avg_loss:.4f}")

            if avg_loss < best_loss:
                best_loss = avg_loss
                torch.save(self.model.state_dict(), self.modelo_path)

        self.model.eval()
        self._X_buffer = torch.zeros(
            1, self.max_len, dtype=torch.long
        ).to(self.device)

        with open(self.label_path, "wb") as f:
            pickle.dump(self.label_encoder, f)

        # Activar semântica pré-treinada automática se disponível (v3.0)
        self._tentar_carregar_embeddings()

        print(
            "\033[92m[NEURÔNIO]: Transformer evoluído com Expansão Cirúrgica "
            f"+ Label Smoothing ({label_smoothing})! ✨\033[0m"
        )
        self.arquivar_conhecimento(df, user_id)

    # -----------------------------------------------------------------------
    # Detecção de novos temas no DB
    # -----------------------------------------------------------------------

    def _buscar_temas_novos_no_db(self, user_id: str = None) -> set:
        try:
            with self._db_lock:
                conn = sqlite3.connect(
                    self.db_path, timeout=10, check_same_thread=False
                )
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.cursor()
                if user_id:
                    cursor.execute(
                        "SELECT DISTINCT tema FROM conhecimento_geral "
                        "WHERE user_id = ?", (user_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT DISTINCT tema FROM conhecimento_geral"
                    )
                temas = {row[0] for row in cursor.fetchall()}
                conn.close()
                return temas
        except Exception as e:
            print(f"[NEURÔNIO]: Erro ao buscar temas: {e}")
            return set()

    # -----------------------------------------------------------------------
    # Carregamento de dados — Replay Memory
    # -----------------------------------------------------------------------

    def carregar_dados_com_replay(self, user_id: str = None):
        """
        Combina conhecimento_geral (novos) com memoria_permanente (antigos)
        para treino com Replay Memory — evita esquecer o passado.
        """
        user_id = _validar_user_id(user_id)
        pd      = _get_pd()
        try:
            with self._db_lock:
                conn = sqlite3.connect(
                    self.db_path, timeout=10, check_same_thread=False
                )
                conn.execute("PRAGMA journal_mode=WAL")
                if user_id:
                    df_novos   = pd.read_sql_query(
                        "SELECT conteudo, tema FROM conhecimento_geral "
                        "WHERE user_id = ?", conn, params=(user_id,)
                    )
                    df_antigos = pd.read_sql_query(
                        "SELECT conteudo, tema FROM memoria_permanente "
                        "WHERE user_id = ? ORDER BY RANDOM() LIMIT 50",
                        conn, params=(user_id,)
                    )
                else:
                    df_novos   = pd.read_sql_query(
                        "SELECT conteudo, tema FROM conhecimento_geral", conn
                    )
                    df_antigos = pd.read_sql_query(
                        "SELECT conteudo, tema FROM memoria_permanente "
                        "ORDER BY RANDOM() LIMIT 50", conn
                    )
                conn.close()
            return (
                pd.concat([df_novos, df_antigos])
                .drop_duplicates()
                .reset_index(drop=True)
            )
        except Exception as e:
            print(f"[NEURÔNIO]: Erro no banco: {e}")
            return None

    # -----------------------------------------------------------------------
    # Arquivamento resiliente (WAL + timeout)
    # -----------------------------------------------------------------------

    def arquivar_conhecimento(self, df, user_id: str = None):
        """
        Move dados de conhecimento_geral → memoria_permanente após treino.
        Garante que dados antigos não são perdidos entre sessões.
        """
        user_id = _validar_user_id(user_id)
        pd      = _get_pd()
        try:
            with self._db_lock:
                conn = sqlite3.connect(
                    self.db_path, timeout=10, check_same_thread=False
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS memoria_permanente "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "conteudo TEXT, tema TEXT, user_id TEXT)"
                )
                query  = "SELECT conteudo, tema FROM conhecimento_geral"
                params = ()
                if user_id:
                    query  += " WHERE user_id = ?"
                    params  = (user_id,)
                df_novos            = pd.read_sql_query(query, conn, params=params)
                df_novos["user_id"] = user_id
                df_novos.to_sql(
                    "memoria_permanente", conn, if_exists="append", index=False
                )
                delete_query = "DELETE FROM conhecimento_geral"
                if user_id:
                    delete_query += " WHERE user_id = ?"
                    conn.execute(delete_query, (user_id,))
                else:
                    conn.execute(delete_query)
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[NEURÔNIO]: Erro ao arquivar: {e}")


# ---------------------------------------------------------------------------
# Entry-point — smoke test com as 3 temperaturas (v2.0)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("━" * 70)
    print("S.I.R.I.U.S. v4.0 — Transformer Unificado")
    print("━" * 70)

    brain = SiriusNeuronio()

    # Treino com parâmetros configuráveis (v2.0 + v3.0)
    # brain.treinar(label_smoothing=0.1)
    # brain.treinar(embeddings_path="data/fasttext_pt.vec", label_smoothing=0.05)

    comando = "configurar alarme para amanhã às 8h"
    print(f"\nTestando predição: '{comando}'\n")

    for temp, label in [(0.5, "conservador"), (1.0, "normal"), (1.5, "ousado")]:
        tema, conf = brain.predizer(comando, temp=temp)
        print(f"  Temp={temp} ({label:12s}): {tema} ({conf:.2%})")

    print("\n[debug=True — mapa de atenção]")
    brain.predizer(comando, temp=1.0, debug=True)

    print("\n✅ Neurônio v4.0 inicializado com sucesso!")