import sqlite3
import os
import pickle
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import LabelEncoder
from collections import Counter
import re

# ---------------------------------------------------------------------------
# Pandas lazy — importado uma vez, reutilizado sempre
# 'import pandas as pd' estava repetido 3x dentro de métodos
# ---------------------------------------------------------------------------

_pd = None

def _get_pd():
    """
    Retorna o módulo pandas, importando-o na primeira chamada.

    Por que como função e não import no topo:
      pandas leva ~200ms para importar e pesa ~30MB em RAM.
      O neurônio só precisa dele durante treino — não no startup
      nem nas predições do caminho quente.

    Uso: pd = _get_pd()
    """
    global _pd
    if _pd is None:
        import pandas as _pandas
        _pd = _pandas
    return _pd

def _validar_user_id(user_id):
    """Valida user_id: deve ser string não vazia, sem caracteres especiais perigosos."""
    if not user_id or not isinstance(user_id, str):
        return None
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:  # limite razoável
        return None
    # Permitir apenas alfanuméricos, underscore, hífen
    if not re.match(r'^[a-zA-Z0-9_-]+$', user_id):
        return None
    return user_id

# ---------------------------------------------------------------------------
# Arquitetura — rede neural do Sirius
# ---------------------------------------------------------------------------

class RedeSirius(nn.Module):
    def __init__(self, vocab_size, embed_dim, hidden_dim, num_classes, max_len=50):
        super().__init__()
        self.embed_dim = embed_dim
        self.hidden_dim = hidden_dim
        self.max_len = max_len
        
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, batch_first=True, bidirectional=True, dropout=0.2)
        self.dropout = nn.Dropout(0.3)
        self.fc = nn.Linear(hidden_dim * 2, num_classes)  # bidirectional, então *2

    def forward(self, x):
        # x: (batch, seq_len)
        embedded = self.embedding(x)  # (batch, seq_len, embed_dim)
        lstm_out, (h_n, c_n) = self.lstm(embedded)  # lstm_out: (batch, seq_len, hidden*2)
        # Usar o último hidden state
        out = self.dropout(h_n[-1])  # h_n[-1] é o último da bidirectional
        out = self.fc(out)
        return out


class SiriusNeuronio:
    def __init__(self):
        diretorio_src  = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
        caminho_data   = os.path.join(diretorio_raiz, "data")
        os.makedirs(caminho_data, exist_ok=True)

        self.db_path         = os.path.join(caminho_data, "sirius_treino.db")
        self.modelo_path     = os.path.join(caminho_data, "sirius_model.pth")
        self.vocab_path      = os.path.join(caminho_data, "vocab.pkl")
        self.label_path      = os.path.join(caminho_data, "label_encoder.pkl")
        
        self.embed_dim = 128
        self.hidden_dim = 256
        self.max_len = 50
        self.min_freq = 2  # frequência mínima para palavra no vocab

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[NEURÔNIO]: Usando device: {self.device}")

        self.vocab = self._carregar_vocab() or {}
        self.label_encoder = self._carregar_ferramenta(self.label_path) or LabelEncoder()
        self.model = None

        # Tensor pré-alocado — reutilizado em cada predição
        self._X_buffer = torch.zeros(1, self.max_len, dtype=torch.long).to(self.device)

        self._inicializar_modelo()

    def _carregar_vocab(self):
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

    def _carregar_ferramenta(self, caminho):
        if os.path.exists(caminho):
            try:
                with open(caminho, "rb") as f:
                    return pickle.load(f)
            except Exception:
                return None
        return None

    def _tokenizar(self, texto):
        # Tokenização simples: split por espaço, remover pontuação
        texto = re.sub(r'[^\w\s]', '', texto.lower())
        return texto.split()

    def _texto_para_indices(self, texto):
        tokens = self._tokenizar(texto)
        indices = [self.vocab.get(token, 1) for token in tokens[:self.max_len]]  # 1 para <UNK>
        # Padding
        if len(indices) < self.max_len:
            indices += [0] * (self.max_len - len(indices))  # 0 para <PAD>
        return indices

    def _construir_vocab(self, textos):
        all_tokens = []
        for texto in textos:
            all_tokens.extend(self._tokenizar(texto))
        counter = Counter(all_tokens)
        vocab = {'<PAD>': 0, '<UNK>': 1}
        idx = 2
        for token, freq in counter.items():
            if freq >= self.min_freq:
                vocab[token] = idx
                idx += 1
        self.vocab = vocab
        self._salvar_vocab()

    def _inicializar_modelo(self):
        if os.path.exists(self.modelo_path) and os.path.exists(self.label_path) and self.vocab:
            try:
                num_classes = len(self.label_encoder.classes_)
                vocab_size = len(self.vocab)
                self.model = RedeSirius(vocab_size, self.embed_dim, self.hidden_dim, num_classes, self.max_len)
                self.model.load_state_dict(
                    torch.load(self.modelo_path, weights_only=True, map_location=self.device)
                )
                self.model.to(self.device)
                self.model.eval()
                print("\033[92m[NEURÔNIO]: Modelo carregado.\033[0m")
            except Exception as e:
                print(f"[NEURÔNIO]: Modelo não pôde ser carregado: {e}")

    def predizer(self, texto: str) -> str:
        if self.model is None or not self.vocab:
            return "Indefinido (Cérebro não treinado)"
        try:
            indices = self._texto_para_indices(texto)
            self._X_buffer.zero_()
            self._X_buffer[0] = torch.tensor(indices, dtype=torch.long)
            
            with torch.no_grad():
                outputs = self.model(self._X_buffer)
                _, pred = torch.max(outputs, 1)
                return self.label_encoder.inverse_transform([pred.item()])[0]
        except Exception as e:
            print(f"Erro na predição: {e}")
            return "Erro na inferência neural"

    def treinar(self, user_id=None):
        user_id = _validar_user_id(user_id)
        # pandas importado lazy
        pd = _get_pd()

        df = self.carregar_dados_com_replay(user_id)
        if df is None or len(df) < 5:
            print("[NEURÔNIO]: Dados insuficientes para evoluir.")
            return

        textos = df["conteudo"].str.lower().tolist()
        self._construir_vocab(textos)
        
        X_indices = []
        for texto in textos:
            indices = self._texto_para_indices(texto)
            X_indices.append(indices)
        
        X = torch.tensor(X_indices, dtype=torch.long).to(self.device)
        y_raw = self.label_encoder.fit_transform(df["tema"])
        y = torch.LongTensor(y_raw).to(self.device)

        vocab_size = len(self.vocab)
        num_classes = len(self.label_encoder.classes_)
        self.model = RedeSirius(vocab_size, self.embed_dim, self.hidden_dim, num_classes, self.max_len).to(self.device)

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)

        print(f"[NEURÔNIO]: Evoluindo com {len(df)} registros...")
        batch_size = 32
        for epoch in range(50):  # Menos epochs, mas com batches
            for i in range(0, len(X), batch_size):
                batch_X = X[i:i+batch_size]
                batch_y = y[i:i+batch_size]
                
                optimizer.zero_grad()
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()

        self.model.eval()
        self._X_buffer = torch.zeros(1, self.max_len, dtype=torch.long).to(self.device)

        torch.save(self.model.state_dict(), self.modelo_path)
        with open(self.label_path, "wb") as f:
            pickle.dump(self.label_encoder, f)
        print("\033[92m[NEURÔNIO]: Cérebro evoluído!\033[0m")
        self.arquivar_conhecimento(df, user_id)

    def carregar_dados_com_replay(self, user_id=None):
        user_id = _validar_user_id(user_id)
        pd = _get_pd()
        try:
            conn = sqlite3.connect(self.db_path)
            if user_id:
                df_novos = pd.read_sql_query(
                    "SELECT conteudo, tema FROM conhecimento_geral WHERE user_id = ?", conn, params=(user_id,)
                )
                df_antigos = pd.read_sql_query(
                    "SELECT conteudo, tema FROM memoria_permanente WHERE user_id = ? ORDER BY RANDOM() LIMIT 50", 
                    conn, params=(user_id,)
                )
            else:
                df_novos = pd.read_sql_query(
                    "SELECT conteudo, tema FROM conhecimento_geral", conn
                )
                df_antigos = pd.read_sql_query(
                    "SELECT conteudo, tema FROM memoria_permanente ORDER BY RANDOM() LIMIT 50", conn
                )
            df_final = pd.concat([df_novos, df_antigos]).drop_duplicates().reset_index(drop=True)
            conn.close()
            return df_final
        except Exception as e:
            print(f"[NEURÔNIO]: Erro no banco: {e}")
            return None

    def arquivar_conhecimento(self, df, user_id=None):
        user_id = _validar_user_id(user_id)
        pd = _get_pd()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memoria_permanente "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, conteudo TEXT, tema TEXT, user_id TEXT)"
            )
            query_novos = "SELECT conteudo, tema FROM conhecimento_geral"
            if user_id:
                query_novos += " WHERE user_id = ?"
                df_novos = pd.read_sql_query(query_novos, conn, params=(user_id,))
                df_novos['user_id'] = user_id
            else:
                df_novos = pd.read_sql_query(query_novos, conn)
            df_novos.to_sql("memoria_permanente", conn, if_exists="append", index=False)
            delete_query = "DELETE FROM conhecimento_geral"
            if user_id:
                delete_query += " WHERE user_id = ?"
                conn.execute(delete_query, (user_id,))
            else:
                conn.execute(delete_query)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erro ao arquivar: {e}")


if __name__ == "__main__":
    brain = SiriusNeuronio()
    brain.treinar()