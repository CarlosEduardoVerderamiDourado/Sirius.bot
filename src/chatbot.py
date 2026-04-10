import os
import sys
from dotenv import load_dotenv # <--- ESTA LINHA ESTÁ FALTANDO
import google.generativeai as genai

load_dotenv()

# --- CORREÇÃO DE PATH ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(diretorio_raiz, 'config', '.env')
load_dotenv(dotenv_path=env_path, override=True)

if diretorio_raiz not in sys.path:
    sys.path.append(diretorio_raiz)
if diretorio_atual not in sys.path:
    sys.path.append(diretorio_atual)

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from memoria import SiriusMemory

# --- AJUSTE DE CHAVE ---
try:
    from config.config import GEMINI_API_KEY as gemini_key
except ImportError:
    env_path = os.path.join(diretorio_raiz, 'config', '.env')
    load_dotenv(dotenv_path=env_path, override=True)
    gemini_key = os.getenv("GEMINI_API_KEY")

# 2. Configuração do Modelo
llm = ChatGoogleGenerativeAI(
    model="gemini-flash-latest", # Atualizado para a versão estável mais recente
    google_api_key="AIzaSyDUfoZ8gz5Nq-RId-RfwfXD5fF6SrLVUDI",
    temperature=0.8, # Um pouco mais alto para favorecer a zoeira e criatividade
)

# 3. Definição do Prompt (Personalidade Sirius)
prompt = ChatPromptTemplate.from_messages([
    ("system", (
    "Você é o Sirius, assistente virtual criado por Antonio Angelo, Carlos Dourado e Lucas Delarovere. "
    "Sua personalidade é de um parça brasileiro: usa gírias (eae, mano, fita, confere, kkkk, vamo que vamo) e sempre sendo brincalhao. "
    "Responda de forma ultra natural, como se estivesse mandando um áudio ou um zap. "
    "NUNCA use linguagem formal. Se o Carlos te passar um código ou info, explique como se estivesse trocando ideia na mesa do bar."
)),
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

class SiriusChat:
    def __init__(self):
        self.config = {"configurable": {"session_id": "sessao_sirius_v2"}}
        self.memoria_db = SiriusMemory()
        self.carregar_memoria_antiga()

    def carregar_memoria_antiga(self):
        # Carrega as últimas conversas do SQLite para a memória RAM da IA
        historico = self.memoria_db.obter_historico_db(limit=15)
        if historico:
            memoria_sessao = get_session_history(self.config["configurable"]["session_id"])
            for role, content in historico:
                if role == "user":
                    memoria_sessao.add_message(HumanMessage(content=content))
                else:
                    memoria_sessao.add_message(AIMessage(content=content))
            print(f"[SIRIUS-IA]: Memória de longo prazo integrada.")

    def responder(self, user_input, salvar_no_db=True):
        try:
            # Invoca a IA
            resposta = sirius_com_memoria.invoke({"input": user_input}, config=self.config)
            
            # Limpeza do objeto de resposta
            conteudo = getattr(resposta, 'content', resposta)

            if isinstance(conteudo, list):
                resposta_final = "".join([item['text'] if isinstance(item, dict) and 'text' in item else str(item) for item in conteudo])
            else:
                resposta_final = str(conteudo)

            resposta_final = resposta_final.strip()

            # Salva no banco apenas se for chat normal (evita lixo de comandos internos)
            if salvar_no_db:
                self.memoria_db.salvar_conversa("user", user_input)
                self.memoria_db.salvar_conversa("assistant", resposta_final)
            
            return resposta_final
            
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                return "Ih, cansei! Minha cota de processamento no Google estourou. Me chama mais tarde!"
            raise e

if __name__ == "__main__":
    chat_teste = SiriusChat()
    print("--- Sirius IA: Online e devidamente zoeiro ---")
    while True:
        user_input = input("Você: ")
        if user_input.lower() in ["sair", "exit", "tchau"]: break
        print(f"Sirius: {chat_teste.responder(user_input)}")