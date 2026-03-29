import os
import sys
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

# 1. Configuração de Pastas e Chave
base_dir = os.path.dirname(os.path.abspath(__file__))
# Garante que ele suba um nível para achar a pasta config mesmo se rodar de dentro da src
env_path = os.path.join(base_dir, '..', 'config', '.env')
load_dotenv(dotenv_path=env_path, override=True)

# 2. Configuração do Modelo (Forçando a rota estável)
llm = ChatGoogleGenerativeAI(
    model="gemini-flash-latest", # Atualizado para a versão mais estável de 2026
    google_api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0.7,
    model_kwargs={"api_version": "v1"} 
)

# 3. Definição do Prompt e Memória (Padrão 2026)
prompt = ChatPromptTemplate.from_messages([
    ("system", "Você é o Sirius (Sistema Inteligente de respostas integradas e unificadas): assistente virtual prestativo, zoeiro e inteligente. Seus criadores se chamam Antonio Angelo, Carlos Dourado e Lucas Delarovere (mas não precisa falar sobre eles toda hora, só quando te perguntarem)."),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

# Criamos a corrente (chain) ligando o prompt ao modelo
chain = prompt | llm

# Gerenciador de histórico simples para a sessão
store = {}

def get_session_history(session_id: str):
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]

# 4. A Corrente com Memória
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
            resposta = sirius_com_memoria.invoke({"input": user_input}, config=self.config)
            
            # Extração limpa do conteúdo da resposta
            if hasattr(resposta, 'content'):
                return str(resposta.content)
            return str(resposta)
            
        except Exception as e:
            if "429" in str(e):
                return "Sirius: Calma aí, apressadinho! O Google me deu um gelo (limite de cota). Tenta de novo em um minuto."
            return f"Sirius: Eita, deu erro aqui: {e}"

# --- Mantendo o seu Modo Texto Original ---
if __name__ == "__main__":
    chat_teste = SiriusChat()
    print("--- Sirius Online (Modo Texto) ---")
    while True:
        user_input = input("Você: ")
        if user_input.lower() in ["sair", "exit", "tchau"]:
            print("Sirius: Fui! Se precisar de mais inteligência (ou de uma piada), é só chamar.")
            break
        
        resposta_final = chat_teste.responder(user_input)
        print(f"Sirius: {resposta_final}")