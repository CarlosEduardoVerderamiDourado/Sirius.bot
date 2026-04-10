import os
import sys

# --- AJUSTE DE PATHS ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_atual)
if diretorio_raiz not in sys.path: sys.path.append(diretorio_raiz)
from config.config import GEMINI_API_KEY # Exemplo de como importar do config

from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# --- IMPORTS DOS MOTORES CENTRALIZADOS ---
try:
    from chatbot import llm as gemini_base
    print("\033[92m[Motor]: Gemini importado do chatbot.py\033[0m")
except Exception as e:
    print(f"\033[31m[Erro]: Falha ao importar Gemini do chatbot: {e}\033[0m")
    gemini_base = None

from langchain_openai import ChatOpenAI
from sirius_tools import SIRIUS_TOOLS 
from memoria import SiriusMemory
from consultor import SiriusConsultor

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], lambda x, y: x + y]
    target_brain: str

class SiriusAgente:
    def __init__(self):
        self.memoria_sql = SiriusMemory()
        self.consultor_faiss = SiriusConsultor()

        # 1. Configura Gemini
        if gemini_base:
            self.gemini = gemini_base.bind_tools(SIRIUS_TOOLS)
        else:
            self.gemini = None

        # 2. Configura Llama Local
        self.llama = ChatOpenAI(
            base_url="http://127.0.0.1:11434/v1", # Usando IP para evitar problemas de DNS local
            api_key="ollama",
            model="llama3.1",
            temperature=0.4,
            timeout=30 # Timeout para não travar o Sirius
        ).bind_tools(SIRIUS_TOOLS)

        # 3. Construção do Grafo
        workflow = StateGraph(AgentState)
        workflow.add_node("brain_selector", self._selector_logic)
        workflow.add_node("cloud_brain", self._call_gemini)
        workflow.add_node("local_brain", self._call_llama)
        workflow.add_node("tools", ToolNode(SIRIUS_TOOLS))
        workflow.add_node("learn", self._neuronio_aprendizado)

        workflow.set_entry_point("brain_selector")

        workflow.add_conditional_edges(
            "brain_selector",
            lambda x: x["target_brain"],
            {"cloud": "cloud_brain", "local": "local_brain"}
        )

        for brain in ["cloud_brain", "local_brain"]:
            workflow.add_conditional_edges(brain, self._check_next_step)

        workflow.add_edge("tools", "learn")
        # Correção: Após aprender com a ferramenta, volta para quem chamou a ferramenta
        workflow.add_edge("learn", "brain_selector") 

        self.app = workflow.compile()

    def _selector_logic(self, state: AgentState):
        """Analisa o comando e decide: Nuvem (Gemini) ou Local (Llama)"""
        last_msg = state["messages"][-1].content.lower()
    
        # Gatilhos que exigem o cérebro potente da Nuvem
        cloud_triggers = ["pesquise", "analise", "internet", "google", "web", "procure"]
    
        # Se o Gemini não estiver carregado (erro de import), usa sempre o Local
        if not self.gemini:
            return {"target_brain": "local"}

        # Lógica Automática:
        if any(word in last_msg for word in cloud_triggers):
            print("\033[94m[Seletor]: Encaminhando para Nuvem (Gemini)...\033[0m")
            return {"target_brain": "cloud"}
        else:
            print("\033[94m[Seletor]: Usando Cérebro Local (Llama)...\033[0m")
            return {"target_brain": "local"}

    def _call_gemini(self, state: AgentState):
        try:
            return {"messages": [self.gemini.invoke(state["messages"])]}
        except Exception as e:
            print(f"\033[93m[Fallback]: Gemini falhou (Cota?). Usando Llama...\033[0m")
            return self._call_llama(state)

    def _call_llama(self, state: AgentState):
        try:
            return {"messages": [self.llama.invoke(state["messages"])]}
        except Exception as e:
            print(f"\033[91m[ERRO LLAMA]: {e}\033[0m")
            return {"messages": [AIMessage(content="Mano, o motor local (Ollama) tá fora do ar. Liga ele aí pra nois desenrolar!")]}

    def _check_next_step(self, state: AgentState):
        if state["messages"][-1].tool_calls:
            return "tools"
        return END

    def _neuronio_aprendizado(self, state: AgentState):
        if len(state["messages"]) < 2: return state
        
        # Pega a resposta da ferramenta ou da IA e salva
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.content:
            last_user_msg = next((m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")
            self.memoria_sql.salvar_historico(last_user_msg, last_msg.content)
            
            if len(last_msg.content) > 10 and "não sei" not in last_msg.content.lower():
                self.memoria_sql.salvar_estudo_autonomo(last_user_msg, last_msg.content)
        
        return state

    def executar(self, comando: str):
        memoria_semantica = self.consultor_faiss.buscar_na_memoria(comando, k=2)
        contexto_extra = f"\nLembrete de memória: {memoria_semantica}" if memoria_semantica else ""

        system_prompt = (
            "Você é o Sirius, um parça brasileiro. "
            "Responda de forma curta, natural e brincalhona. Use gírias como mano, eae e fita. "
            "Não use sotaques regionais que dificultem a leitura de voz."
            f"{contexto_extra}"
        )

        mensagens_contexto = [SystemMessage(content=system_prompt)]
        
        historico = self.memoria_sql.obter_historico_db(limit=3)
        for h in historico:
            role = HumanMessage(content=h["content"]) if h["role"] == "user" else AIMessage(content=h["content"])
            mensagens_contexto.append(role)

        mensagens_contexto.append(HumanMessage(content=comando))

        try:
            resultado = self.app.invoke({"messages": mensagens_contexto})
            return resultado["messages"][-1].content
        except Exception as e:
            return f"Deu um erro aqui no meu Grafo, mano: {str(e)}"

if __name__ == "__main__":
    sirius = SiriusAgente()
    print("\033[94m--- Sirius Online e Híbrido ---\033[0m")
    while True:
        user_input = input("\033[92mVocê:\033[0m ")
        if user_input.lower() in ["sair", "exit"]: break
        print(f"\033[96mSirius:\033[0m {sirius.executar(user_input)}")