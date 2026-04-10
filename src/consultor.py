import os
import sys
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
# REMOVIDO: imports de agents e hub que causavam o erro crítico
from langchain_core.documents import Document

class SiriusConsultor:
    def __init__(self):
        # 1. Configuração de Caminhos
        # Ajustado para garantir que o índice fique na raiz do projeto
        self.diretorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.faiss_db_path = os.path.join(self.diretorio_raiz, "data", "sirius_faiss_index")
        
        # 2. Modelo de Embeddings (Local e Leve)
        print("\033[93m[SIRIUS]: Carregando modelo de memória semântica (FAISS)...\033[0m")
        try:
            self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
            self.vector_db = self._carregar_ou_criar_db()
        except Exception as e:
            print(f"\033[91m[ERRO CRÍTICO EMBEDDINGS]: {e}\033[0m")
            raise e

    def _carregar_ou_criar_db(self):
        """Carrega o índice FAISS do disco ou cria um novo se não existir."""
        if os.path.exists(self.faiss_db_path):
            # allow_dangerous_deserialization é necessário para carregar arquivos locais
            return FAISS.load_local(
                self.faiss_db_path, 
                self.embeddings, 
                allow_dangerous_deserialization=True
            )
        else:
            # Cria um DB inicial vazio com um documento de boas-vindas
            doc_inicial = [Document(page_content="Sistema Sirius iniciado.", metadata={"source": "system"})]
            db = FAISS.from_documents(doc_inicial, self.embeddings)
            db.save_local(self.faiss_db_path)
            return db

    def injetar_conhecimento(self, texto, metadados=None):
        """Adiciona novas informações à memória vetorial do Sirius."""
        try:
            novo_doc = [Document(page_content=texto, metadata=metadados or {"source": "manual_input"})]
            self.vector_db.add_documents(novo_doc)
            self.vector_db.save_local(self.faiss_db_path)
            return True
        except Exception as e:
            print(f"[ERRO MEMÓRIA VETORIAL]: {e}")
            return False

    def buscar_na_memoria(self, query, k=3):
        """Busca as informações mais próximas semanticamente da pergunta."""
        try:
            docs = self.vector_db.similarity_search(query, k=k)
            if not docs:
                return None
            
            resultados = [doc.page_content for doc in docs if len(doc.page_content) > 5]
            return "\n---\n".join(resultados) if resultados else None
        except Exception as e:
            print(f"[CONSULTOR - INFO]: Erro na busca vetorial: {e}")
            return None

    def limpar_memoria_total(self):
        """Reseta o banco de dados vetorial."""
        import shutil
        if os.path.exists(self.faiss_db_path):
            shutil.rmtree(self.faiss_db_path)
            self.vector_db = self._carregar_ou_criar_db()
            return "Memória resetada, chefia."
        return "Memória já estava limpa."

if __name__ == "__main__":
    # Teste rápido de sanidade
    try:
        c = SiriusConsultor()
        c.injetar_conhecimento("Teste de memória: O Sirius é um sistema híbrido.")
        res = c.buscar_na_memoria("O que é o Sirius?")
        print(f"\n[TESTE]: {res}")
    except Exception as e:
        print(f"Erro no teste: {e}")