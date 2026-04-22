import sqlite3
import os
import pickle
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder
import scipy.sparse

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

# ---------------------------------------------------------------------------
# Arquitetura — rede neural do Sirius
# ---------------------------------------------------------------------------

class RedeSirius(nn.Module):
    def __init__(self, input_size, hidden_size, num_classes):
        super().__init__()
        self.layer1  = nn.Linear(input_size, hidden_size)
        self.layer2  = nn.Linear(hidden_size, hidden_size // 2)
        self.layer3  = nn.Linear(hidden_size // 2, num_classes)
        self.relu    = nn.ReLU()
        self.dropout = nn.Dropout(0.2)

    def forward(self, x):
        out = self.relu(self.layer1(x))
        out = self.dropout(out)
        out = self.relu(self.layer2(out))
        return self.layer3(out)


class SiriusNeuronio:
    def __init__(self):
        diretorio_src  = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
        caminho_data   = os.path.join(diretorio_raiz, "data")
        os.makedirs(caminho_data, exist_ok=True)

        self.db_path         = os.path.join(caminho_data, "sirius_treino.db")
        self.modelo_path     = os.path.join(caminho_data, "sirius_model.pth")
        self.vectorizer_path = os.path.join(caminho_data, "vectorizer.pkl")
        self.label_path      = os.path.join(caminho_data, "label_encoder.pkl")
        self.max_features    = 1000

        self.vectorizer    = self._carregar_ferramenta(self.vectorizer_path) \
                             or TfidfVectorizer(max_features=self.max_features)
        self.label_encoder = self._carregar_ferramenta(self.label_path) or LabelEncoder()
        self.model         = None

        # Tensor pré-alocado — reutilizado em cada predição (evita malloc)
        self._X_buffer = torch.zeros(1, self.max_features)

        self._inicializar_modelo()

    def _carregar_ferramenta(self, caminho):
        if os.path.exists(caminho):
            try:
                with open(caminho, "rb") as f:
                    return pickle.load(f)
            except Exception:
                return None
        return None

    def _inicializar_modelo(self):
        if os.path.exists(self.modelo_path) and os.path.exists(self.label_path):
            try:
                num_classes = len(self.label_encoder.classes_)
                self.model  = RedeSirius(self.max_features, 128, num_classes)
                self.model.load_state_dict(
                    torch.load(self.modelo_path, weights_only=True, map_location="cpu")
                )
                self.model.eval()   # setado uma vez — nunca mais chamado no predizer()
                print("\033[92m[NEURÔNIO]: Modelo carregado.\033[0m")
            except Exception as e:
                print(f"[NEURÔNIO]: Modelo não pôde ser carregado: {e}")

    def predizer(self, texto: str) -> str:
        if self.model is None or not hasattr(self.vectorizer, "vocabulary_"):
            return "Indefinido (Cérebro não treinado)"
        try:
            # sparse → dense direto no buffer pré-alocado (sem toarray() caro)
            X_sparse = self.vectorizer.transform([texto.lower()])
            X_arr    = X_sparse.toarray()          # ainda necessário para torch
            n_cols   = X_arr.shape[1]

            self._X_buffer.zero_()
            self._X_buffer[0, :n_cols] = torch.from_numpy(X_arr[0]).float()

            with torch.no_grad():                  # model.eval() já setado no init
                outputs   = self.model(self._X_buffer)
                _, pred   = torch.max(outputs, 1)
                return self.label_encoder.inverse_transform([pred.item()])[0]
        except Exception:
            return "Erro na inferência neural"

    def treinar(self):
        # pandas importado lazy — não carrega no startup
        pd = _get_pd()

        df = self.carregar_dados_com_replay()
        if df is None or len(df) < 5:
            print("[NEURÔNIO]: Dados insuficientes para evoluir.")
            return

        X_raw          = self.vectorizer.fit_transform(df["conteudo"].str.lower()).toarray()
        y_raw          = self.label_encoder.fit_transform(df["tema"])
        input_size_atual = X_raw.shape[1]

        X = torch.zeros(X_raw.shape[0], self.max_features)
        X[:, :input_size_atual] = torch.FloatTensor(X_raw)
        y = torch.LongTensor(y_raw)

        num_classes = len(self.label_encoder.classes_)
        self.model  = RedeSirius(self.max_features, 128, num_classes)

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)

        print(f"[NEURÔNIO]: Evoluindo com {len(df)} registros...")
        for _ in range(200):
            optimizer.zero_grad()
            outputs = self.model(X)
            loss    = criterion(outputs, y)
            loss.backward()
            optimizer.step()

        # Coloca em eval após treino e recria buffer
        self.model.eval()
        self._X_buffer = torch.zeros(1, self.max_features)

        torch.save(self.model.state_dict(), self.modelo_path)
        with open(self.vectorizer_path, "wb") as f: pickle.dump(self.vectorizer, f)
        with open(self.label_path, "wb") as f:      pickle.dump(self.label_encoder, f)
        print("\033[92m[NEURÔNIO]: Cérebro evoluído!\033[0m")
        self.arquivar_conhecimento(df)

    def carregar_dados_com_replay(self):
        pd = _get_pd()
        try:
            conn    = sqlite3.connect(self.db_path)
            df_novos = pd.read_sql_query(
                "SELECT conteudo, tema FROM conhecimento_geral", conn
            )
            try:
                df_antigos = pd.read_sql_query(
                    "SELECT conteudo, tema FROM memoria_permanente "
                    "ORDER BY RANDOM() LIMIT 50", conn
                )
                df_final = pd.concat([df_novos, df_antigos]).drop_duplicates().reset_index(drop=True)
            except Exception:
                df_final = df_novos
            conn.close()
            return df_final
        except Exception as e:
            print(f"[NEURÔNIO]: Erro no banco: {e}")
            return None

    def arquivar_conhecimento(self, df):
        pd = _get_pd()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memoria_permanente "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, conteudo TEXT, tema TEXT)"
            )
            df_novos = pd.read_sql_query(
                "SELECT conteudo, tema FROM conhecimento_geral", conn
            )
            df_novos.to_sql("memoria_permanente", conn, if_exists="append", index=False)
            conn.execute("DELETE FROM conhecimento_geral")
            conn.commit()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    brain = SiriusNeuronio()
    brain.treinar()