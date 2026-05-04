"""
GUIA DE INTEGRACAO: Agentes Autonomos Sirius com LangGraph

Este arquivo demonstra como integrar sirius_os_vision.py e sirius_executor.py
ao sirius_nucleo.py para criar um agente autonomo completo usando LangGraph.

MODULOS NOVOS:
1. sirius_os_vision.py    - Visao computacional + webcam (LGPD compliant)
2. sirius_executor.py     - Aprendizado dinamico de tarefas de sistema

REQUISITOS:
    pip install opencv-python pyautogui RestrictedPython ddgs
    pip install langgraph langchain-core

EXEMPLO DE USO BASICO:
"""

import os
import sys
from typing import Optional, Dict, Any

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)


# ==============================================================================
# 1. EXEMPLO SIMPLES - Usar modulos diretamente
# ==============================================================================

def exemplo_visao_simples():
    """Captura tela e descreve contexto visual."""
    from sirius_os_vision import SiriusOSVision
    
    vision = SiriusOSVision()
    
    # Captura tela
    screenshot_b64 = vision.capture_screen()
    print(f"[VISION]: Screenshot capturado ({len(screenshot_b64)} bytes base64)")
    
    # Descreve contexto (template para integrar com LLM)
    prompt = vision.describe_visual_context(screenshot_b64)
    print(f"[VISION]: Template de prompt preparado para multimodal LLM")
    print(f"Prompt preview: {prompt[:200]}...")


def exemplo_executor_simples():
    """Aprende dinamicamente como listar arquivos."""
    from sirius_executor import SiriusExecutor
    
    executor = SiriusExecutor(user_id="carlos")
    
    # Pesquisa como fazer algo
    tarefa = "list all files recursively in a directory"
    codigo = executor.pesquisar_solucao(tarefa)
    
    if codigo:
        print(f"[EXECUTOR]: Codigo encontrado em DuckDuckGo ({len(codigo)} chars)")
        
        # Valida codigo
        ok, detalhes = executor.validar_codigo(codigo)
        if ok:
            print(f"[EXECUTOR]: Codigo validado! Score de confianca: {detalhes['score_confianca']:.2f}")
            
            # Executa em sandbox
            resultado = executor.executar_sandbox(codigo, "listar_arquivos")
            print(f"[EXECUTOR]: Resultado: {resultado['resultado'][:100]}...")
            
            # Salva para uso futuro
            if resultado['sucesso']:
                executor.salvar_procedimento(
                    "listar_arquivos_recursivo",
                    codigo,
                    "Lista todos os arquivos recursivamente em um diretorio"
                )
    else:
        print("[EXECUTOR]: Nenhum codigo encontrado")


# ==============================================================================
# 2. INTEGRACAO COM LANGGRAPH
# ==============================================================================

def exemplo_langgraph_agent():
    """
    Cria um agente LangGraph autonomo que pode:
    - Capturar visao do sistema
    - Aprender tarefas dinamicamente
    - Executar procedimentos em sandbox
    """
    try:
        from langgraph.graph import StateGraph, START, END
        from typing_extensions import TypedDict
    except ImportError:
        print("pip install langgraph langchain-core")
        return
    
    from sirius_os_vision import vision_tool_factory
    from sirius_executor import executor_tool_factory
    from memoria import SiriusMemory
    
    # ── State Definition ──────────────────────────────────────────────────
    class SiriusAgentState(TypedDict):
        """State do agente autonomo."""
        user_input: str
        user_id: str
        visual_context: Optional[str]
        procedures: list
        last_result: str
        iteration: int
    
    # ── Inicializar memoria e tools ───────────────────────────────────────
    memoria = SiriusMemory()
    user_id = "carlos"
    
    vision_tool = vision_tool_factory(memoria=memoria, user_id=user_id)
    executor_tool = executor_tool_factory(memoria=memoria, user_id=user_id)
    
    # ── Nodes ─────────────────────────────────────────────────────────────
    
    def node_capturar_visao(state: SiriusAgentState) -> SiriusAgentState:
        """Node que captura contexto visual."""
        print("\n[GRAPH]: Capturando contexto visual...")
        
        vision_instance = vision_tool["instance"]
        screenshot = vision_instance.capture_screen()
        
        state["visual_context"] = screenshot
        return state
    
    def node_pesquisar_solucao(state: SiriusAgentState) -> SiriusAgentState:
        """Node que pesquisa como fazer uma tarefa."""
        print("\n[GRAPH]: Pesquisando solucao para: {state['user_input']}")
        
        executor_instance = executor_tool["instance"]
        codigo = executor_instance.pesquisar_solucao(state["user_input"])
        
        if codigo:
            state["last_result"] = "Codigo encontrado no DuckDuckGo"
        else:
            state["last_result"] = "Nenhum codigo encontrado"
        
        return state
    
    def node_validar_executar(state: SiriusAgentState) -> SiriusAgentState:
        """Node que valida e executa codigo."""
        print("\n[GRAPH]: Validando e executando codigo...")
        
        executor_instance = executor_tool["instance"]
        procedimentos = executor_instance.obter_procedimentos()
        
        state["procedures"] = procedimentos
        return state
    
    def node_aprender(state: SiriusAgentState) -> SiriusAgentState:
        """Node que persiste procedimento aprendido."""
        print("\n[GRAPH]: Salvando procedimento aprendido...")
        
        state["iteration"] += 1
        return state
    
    # ── Build Graph ───────────────────────────────────────────────────────
    graph = StateGraph(SiriusAgentState)
    
    # Add nodes
    graph.add_node("capturar_visao", node_capturar_visao)
    graph.add_node("pesquisar", node_pesquisar_solucao)
    graph.add_node("validar_executar", node_validar_executar)
    graph.add_node("aprender", node_aprender)
    
    # Add edges
    graph.add_edge(START, "capturar_visao")
    graph.add_edge("capturar_visao", "pesquisar")
    graph.add_edge("pesquisar", "validar_executar")
    graph.add_edge("validar_executar", "aprender")
    graph.add_edge("aprender", END)
    
    # Compile
    compiled_graph = graph.compile()
    
    # ── Run Agent ─────────────────────────────────────────────────────────
    initial_state: SiriusAgentState = {
        "user_input": "listar arquivos no desktop",
        "user_id": user_id,
        "visual_context": None,
        "procedures": [],
        "last_result": "",
        "iteration": 0
    }
    
    print("\033[94m[GRAPH]: Iniciando agente autonomo...\033[0m")
    final_state = compiled_graph.invoke(initial_state)
    
    print("\n\033[92m[GRAPH]: Agente terminou!\033[0m")
    print(f"Contexto visual capturado: {bool(final_state['visual_context'])}")
    print(f"Procedimentos aprendidos: {len(final_state['procedures'])}")
    print(f"Resultado: {final_state['last_result']}")
    
    return compiled_graph


# ==============================================================================
# 3. INTEGRACAO COM SIRIUS_NUCLEO
# ==============================================================================

def integrar_ao_nucleo():
    """
    Patches para adicionar ao sirius_nucleo.py:
    
    # Em SiriusNucleo._carregar_opcionais():
    
    try:
        from sirius_os_vision import vision_tool_factory
        self._vision_tool = vision_tool_factory(memoria=self.memoria, user_id="sistema")
        print("[NUCLEO]: Vision module loaded")
    except Exception as e:
        print(f"[NUCLEO]: Vision indisponível: {e}")
    
    try:
        from sirius_executor import executor_tool_factory
        self._executor_tool = executor_tool_factory(memoria=self.memoria, user_id="sistema")
        print("[NUCLEO]: Executor module loaded")
    except Exception as e:
        print(f"[NUCLEO]: Executor indisponível: {e}")
    """
    pass


# ==============================================================================
# 4. FLUXO COMPLETO: Usuario -> Agent -> Execucao
# ==============================================================================

def exemplo_fluxo_completo():
    """
    Demonstra o fluxo completo de um usuario pedindo uma tarefa
    e o Sirius aprendendo/executando dinamicamente.
    """
    print("\n" + "="*80)
    print("FLUXO COMPLETO: Sirius Autonomo em Acao")
    print("="*80)
    
    from sirius_executor import SiriusExecutor
    from sirius_os_vision import SiriusOSVision
    
    user_id = "carlos"
    
    # 1. Usuario pede uma tarefa
    user_input = "me diga quantos arquivos estao na pasta Downloads"
    print(f"\n[USER]: {user_input}")
    
    # 2. Sirius captura contexto visual
    vision = SiriusOSVision(user_id=user_id)
    screenshot = vision.capture_screen()
    print(f"[SIRIUS]: Capturando contexto visual... OK ({len(screenshot)} bytes)")
    
    # 3. Sirius pesquisa como fazer
    executor = SiriusExecutor(user_id=user_id)
    tarefa = "count files in a directory python"
    codigo = executor.pesquisar_solucao(tarefa)
    
    if codigo:
        print(f"[SIRIUS]: Encontrei como fazer: {len(codigo)} chars de codigo")
        
        # 4. Valida
        ok, detalhes = executor.validar_codigo(codigo)
        print(f"[SIRIUS]: Validacao: {ok}, Confianca: {detalhes['score_confianca']:.2f}")
        
        # 5. Executa em sandbox
        if ok:
            resultado = executor.executar_sandbox(codigo, "contar_downloads")
            print(f"[SIRIUS]: Resultado: {resultado['resultado']}")
            
            # 6. Salva para proxima vez
            executor.salvar_procedimento(
                "contar_arquivos_pasta",
                codigo,
                "Conta arquivos em uma pasta especifica"
            )
            print("[SIRIUS]: Procedimento salvo para uso futuro!")
    else:
        print("[SIRIUS]: Nao consegui encontrar como fazer isso")


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("\n\033[96m╔════════════════════════════════════════════════════════╗\033[0m")
    print("\033[96m║  SIRIUS AUTONOMOUS AGENTS - Integration Guide         ║\033[0m")
    print("\033[96m╚════════════════════════════════════════════════════════╝\033[0m")
    
    print("\n[1] Exemplo Visao Simples")
    print("[2] Exemplo Executor Simples")
    print("[3] Exemplo LangGraph Agent")
    print("[4] Fluxo Completo")
    
    try:
        # Executar todos os exemplos
        print("\n\n### RODANDO EXEMPLO 1: VISAO ###")
        try:
            exemplo_visao_simples()
        except Exception as e:
            print(f"Erro: {e}")
        
        print("\n\n### RODANDO EXEMPLO 2: EXECUTOR ###")
        try:
            exemplo_executor_simples()
        except Exception as e:
            print(f"Erro: {e}")
        
        print("\n\n### RODANDO EXEMPLO 3: LANGGRAPH ###")
        try:
            exemplo_langgraph_agent()
        except Exception as e:
            print(f"Erro (LangGraph não instalado?): {e}")
        
        print("\n\n### RODANDO EXEMPLO 4: FLUXO COMPLETO ###")
        try:
            exemplo_fluxo_completo()
        except Exception as e:
            print(f"Erro: {e}")
        
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")
