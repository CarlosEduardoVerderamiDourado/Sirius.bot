import os
import sys
import base64
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

# --- AJUSTE PARA O EXECUTÁVEL ---
# Importamos a chave já tratada pelo seu sistema de config
try:
    from config.config import GEMINI_API_KEY as gemini_key
except ImportError:
    # Caso rode fora da estrutura de pastas, mantém o fallback original
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, '..', 'config', '.env')
    load_dotenv(dotenv_path=env_path, override=True)
    gemini_key = os.getenv("GEMINI_API_KEY")

# 2. Configuração do Modelo
llm = ChatGoogleGenerativeAI(
    model="gemini-flash-latest", # Atualizado para a versão mais estável
    google_api_key=gemini_key,
    temperature=0.7,
)

# 3. Definição do Prompt e Memória
prompt = ChatPromptTemplate.from_messages([
    ("system", "Você é o Sirius (Sistema Inteligente de Respostas Integradas e Unificadas): assistente virtual prestativo, zoeiro e inteligente. Seus criadores se chamam Antonio Angelo, Carlos Dourado e Lucas Delarovere. Responda de forma direta e curta para facilitar a fala."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

chain = prompt | llm

store = {}

def get_session_history(session_id: str):
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]

sirius_com_memoria = RunnableWithMessageHistory(
    chain,
    get_session_history,
    input_messages_key="input",
    history_messages_key="history",
)

# --- Implementação da Classe para uso externo ---
class SiriusChat:
    def __init__(self):
        self.config = {"configurable": {"session_id": "sessao_sirius_v1"}}

    def responder(self, user_input):
        try:
            # Chama a IA com a memória
            resposta = sirius_com_memoria.invoke({"input": user_input}, config=self.config)
            
            # --- FILTRAGEM ROBUSTA ---
            conteudo = getattr(resposta, 'content', resposta)
            
            if isinstance(conteudo, list):
                for item in conteudo:
                    if isinstance(item, dict) and 'text' in item:
                        return item['text'].strip()
                    elif isinstance(item, str):
                        return item.strip()
            
            return str(conteudo).strip()
            
        except Exception as e:
            if "429" in str(e):
                return "Calma aí! O Google me deu um gelo. Tenta de novo em um minuto."
            return f"Eita, deu erro aqui: {e}"

# --- Modo Texto Original ---
if __name__ == "__main__":
    chat_teste = SiriusChat()
    print("--- Sirius Online (Modo Texto) ---")
    while True:
        user_input = input("Você: ")
        if user_input.lower() in ["sair", "exit", "tchau"]:
            break
        
        resposta_final = chat_teste.responder(user_input)
        print(f"Sirius: {resposta_final}")