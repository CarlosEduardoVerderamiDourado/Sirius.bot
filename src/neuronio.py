import sqlite3
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder
import os
import pickle

# --- ARQUITETURA DEEP SIRIUS ---
class RedeSirius(nn.Module):
    def __init__(self, input_size, hidden_size, num_classes):
        super(RedeSirius, self).__init__()
        self.layer1 = nn.Linear(input_size, hidden_size)
        self.layer2 = nn.Linear(hidden_size, hidden_size // 2)
        self.layer3 = nn.Linear(hidden_size // 2, num_classes)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.2)
    
    def forward(self, x):
        out = self.relu(self.layer1(x))
        out = self.dropout(out)
        out = self.relu(self.layer2(out))
        out = self.layer3(out)
        return out

class SiriusNeuronio:
    def __init__(self):
        diretorio_src = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
    
    # PASTA DE DADOS (Onde a mágica acontece)
        caminho_data = os.path.join(diretorio_raiz, "data")
        if not os.path.exists(caminho_data):
            os.makedirs(caminho_data)

    # O DB de treino fica na /data
        self.db_path = os.path.join(caminho_data, "sirius_treino.db")
    
    # Os arquivos do modelo (os pesos da rede) também vão para /data
        self.modelo_path = os.path.join(caminho_data, "sirius_model.pth")
        self.vectorizer_path = os.path.join(caminho_data, "vectorizer.pkl")
        self.label_path = os.path.join(caminho_data, "label_encoder.pkl")
    
        self.max_features = 1000 
        self.vectorizer = self._carregar_ferramenta(self.vectorizer_path) or TfidfVectorizer(max_features=self.max_features)
        self.label_encoder = self._carregar_ferramenta(self.label_path) or LabelEncoder()
        self.model = None
        self._inicializar_modelo()

    def _carregar_ferramenta(self, caminho):
        if os.path.exists(caminho):
            try:
                with open(caminho, 'rb') as f:
                    return pickle.load(f)
            except: return None
        return None

    def _inicializar_modelo(self):
        """Prepara o modelo se os arquivos de pesos existirem."""
        if os.path.exists(self.modelo_path) and os.path.exists(self.label_path):
            try:
                # Usamos o valor fixo definido no init
                num_classes = len(self.label_encoder.classes_)
                self.model = RedeSirius(self.max_features, 128, num_classes)
                self.model.load_state_dict(torch.load(self.modelo_path, weights_only=True))
                self.model.eval()
            except Exception as e:
                print(f"[AVISO]: Modelo não pôde ser carregado: {e}")

    def predizer(self, texto):
        if self.model is None or not hasattr(self.vectorizer, 'vocabulary_'):
            return "Indefinido (Cérebro não treinado)"
        
        try:
            self.model.eval()
            with torch.no_grad():
                # Transforma e garante que o tamanho seja o max_features
                X_raw = self.vectorizer.transform([texto.lower()]).toarray()
                X_tensor = torch.FloatTensor(X_raw)
                
                outputs = self.model(X_tensor)
                _, predicted = torch.max(outputs, 1)
                
                tema = self.label_encoder.inverse_transform([predicted.item()])[0]
                return tema
        except:
            return "Erro na inferência neural"

    def treinar(self):
        df = self.carregar_dados_com_replay()
        if df is None or len(df) < 5:
            print("[NEURÔNIO]: Dados insuficientes para evoluir.")
            return

        # Fit e Transform (Garante max_features)
        X_raw = self.vectorizer.fit_transform(df['conteudo'].str.lower()).toarray()
        y_raw = self.label_encoder.fit_transform(df['tema'])

        # Se o vocabulário for menor que max_features, precisamos fazer padding
        # ou garantir que o modelo saiba lidar. A melhor forma é usar o valor real do fit:
        input_size_atual = X_raw.shape[1]
        
        # Criamos o tensor e movemos para o tamanho fixo se necessário
        X = torch.zeros((X_raw.shape[0], self.max_features))
        X[:, :input_size_atual] = torch.FloatTensor(X_raw)
        y = torch.LongTensor(y_raw)

        num_classes = len(self.label_encoder.classes_)
        self.model = RedeSirius(self.max_features, 128, num_classes)
        
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)

        print(f"[NEURÔNIO]: Evoluindo rede profunda com {len(df)} registros...")
        for epoch in range(200):
            optimizer.zero_grad()
            outputs = self.model(X)
            loss = criterion(outputs, y)
            loss.backward()
            optimizer.step()
            
        # Salva tudo
        torch.save(self.model.state_dict(), self.modelo_path)
        with open(self.vectorizer_path, 'wb') as f: pickle.dump(self.vectorizer, f)
        with open(self.label_path, 'wb') as f: pickle.dump(self.label_encoder, f)

        print("\033[92m[SUCESSO]: O cérebro local evoluiu!\033[0m")
        self.arquivar_conhecimento(df)

    def carregar_dados_com_replay(self):
        # ... (seu código de sqlite continua igual e está ótimo)
        try:
            conn = sqlite3.connect(self.db_path)
            df_novos = pd.read_sql_query("SELECT conteudo, tema FROM conhecimento_geral", conn)
            try:
                df_antigos = pd.read_sql_query("SELECT conteudo, tema FROM memoria_permanente ORDER BY RANDOM() LIMIT 50", conn)
                df_final = pd.concat([df_novos, df_antigos]).drop_duplicates().reset_index(drop=True)
            except:
                df_final = df_novos
            conn.close()
            return df_final
        except Exception as e:
            print(f"[ERRO BANCO]: {e}")
            return None

    def arquivar_conhecimento(self, df):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS memoria_permanente (id INTEGER PRIMARY KEY AUTOINCREMENT, conteudo TEXT, tema TEXT)")
            df.to_sql("memoria_permanente", conn, if_exists="append", index=False)
            cursor.execute("DELETE FROM conhecimento_geral")
            conn.commit()
            conn.close()
        except: pass

if __name__ == "__main__":
    brain = SiriusNeuronio()
    brain.treinar()