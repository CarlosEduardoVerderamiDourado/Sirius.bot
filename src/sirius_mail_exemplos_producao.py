"""
EXEMPLO COMPLETO: Usando SiriusMail em Produção

Demonstra integração real do gerenciador de e-mail inteligente
no fluxo de trabalho do Sirius.
"""

import os
import sys
import time
from datetime import datetime

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 1: Verificar e-mails na startup
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_1_startup():
    """Verificar e-mails quando Sirius inicializa."""
    print("\n" + "="*80)
    print("EXEMPLO 1: Verificar E-mails na Startup")
    print("="*80)
    
    from sirius_mail import SiriusEmailManager
    
    print("\n[STARTUP]: Verificando e-mails não lidos...")
    
    manager = SiriusEmailManager(user_id="carlos")
    
    if not manager.conectar():
        print("[STARTUP]: Aviso - Não foi possível acessar e-mails")
        return
    
    try:
        resultado = manager.processar_emails()
        
        quantidade = len(resultado["emails"])
        prioridade = resultado["prioridade_maxima"]
        
        print(f"\n[STARTUP]: ✅ Verificação concluída")
        print(f"  • E-mails encontrados: {quantidade}")
        print(f"  • Prioridade máxima: {prioridade}")
        
        if resultado["requer_interrupcao"]:
            print(f"\n[STARTUP]: 🔴 URGENTE!")
            print(f"  {resultado['mensagem_usuario']}")
        else:
            print(f"\n[STARTUP]: ✅ Nenhuma urgência detectada")
    
    finally:
        manager.desconectar()


# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 2: E-mail periódico (a cada 5 minutos)
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_2_periodico():
    """Verificar e-mails periodicamente durante execução."""
    print("\n" + "="*80)
    print("EXEMPLO 2: Verificação Periódica (5 minutos)")
    print("="*80)
    
    from sirius_mail import SiriusEmailManager
    
    class VerificadorEmail:
        def __init__(self, user_id="carlos", intervalo_min=5):
            self.user_id = user_id
            self.intervalo_seg = intervalo_min * 60
            self.ultima_verificacao = 0
            self.running = False
        
        def verificar_se_tempo(self):
            """Verifica apenas se passou o intervalo."""
            agora = time.time()
            
            if agora - self.ultima_verificacao >= self.intervalo_seg:
                self._verificar()
                self.ultima_verificacao = agora
        
        def _verificar(self):
            """Realiza a verificação."""
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Verificando e-mails...")
            
            manager = SiriusEmailManager(user_id=self.user_id)
            
            if manager.conectar():
                try:
                    resultado = manager.processar_emails()
                    
                    if resultado["requer_interrupcao"]:
                        print(f"🔴 ALERTA: {resultado['mensagem_usuario']}")
                        self._notificar_usuario(resultado)
                    else:
                        print(f"✅ {len(resultado['emails'])} e-mail(s)")
                
                finally:
                    manager.desconectar()
        
        def _notificar_usuario(self, resultado):
            """Notifica usuário de e-mail urgente."""
            # Aqui entra lógica de notificação
            # (push notification, som, popup, etc)
            pass
    
    # Simular verificação
    verificador = VerificadorEmail(user_id="carlos", intervalo_min=0.1)
    
    for i in range(3):
        verificador.verificar_se_tempo()
        time.sleep(0.2)


# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 3: Integrar com SiriusMemoria (Contexto)
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_3_com_memoria():
    """Usar SiriusMemoria para contextualizar decisão."""
    print("\n" + "="*80)
    print("EXEMPLO 3: E-mail com Contexto (SiriusMemoria)")
    print("="*80)
    
    from sirius_mail import SiriusEmailManager
    from memoria import SiriusMemory
    
    # Inicializar memória
    memoria = SiriusMemory()
    
    # Gerenciador com memória
    manager = SiriusEmailManager(memoria=memoria, user_id="carlos")
    
    if manager.conectar():
        try:
            # Processar com callback para IA
            def decisao_ia(emails_analisados):
                """Callback que a IA usa para decidir."""
                print("\n[IA]: Analisando contexto histórico...")
                
                # Aqui a IA consultaria SiriusMemory
                # para decidir ações mais inteligentes
                for email in emails_analisados:
                    print(f"  • {email['assunto']} [{email['prioridade']}]")
            
            resultado = manager.processar_emails(callback_ia=decisao_ia)
            
            print(f"\n[RESULTADO]: {len(resultado['emails'])} e-mails processados")
        
        finally:
            manager.desconectar()


# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 4: Fluxo de Resposta Automática
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_4_resposta_automatica():
    """Responder automaticamente baseado em prioridade."""
    print("\n" + "="*80)
    print("EXEMPLO 4: Resposta Automática por Prioridade")
    print("="*80)
    
    from sirius_mail import SiriusEmailManager
    
    manager = SiriusEmailManager(user_id="carlos")
    
    if manager.conectar():
        try:
            emails = manager.listar_nao_lidos(limite=3)
            
            for email in emails:
                remetente = email["remetente"]
                assunto = email["assunto"]
                corpo = email["corpo"]
                
                # Detectar prioridade
                nivel, score = manager.detectar_prioridade(remetente, assunto, corpo)
                
                print(f"\n📧 {assunto}")
                print(f"   De: {remetente}")
                print(f"   Prioridade: {nivel} (score: {score:.2f})")
                
                # Decidir ação baseado em prioridade
                if nivel == "alta":
                    acao = "RESPONDER_IMEDIATAMENTE"
                    print(f"   Ação: 🔴 {acao}")
                
                elif nivel == "media":
                    acao = "AGENDAR_RESPOSTA"
                    print(f"   Ação: 🟡 {acao}")
                
                else:
                    acao = "REVISAR_DEPOIS"
                    print(f"   Ação: 🟢 {acao}")
                
                # Aqui entra a lógica de execução de ação
                # (enviar resposta, agendar, etc)
        
        finally:
            manager.desconectar()


# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 5: Integração com LangGraph
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_5_langgraph():
    """Agent LangGraph que processa e-mails."""
    print("\n" + "="*80)
    print("EXEMPLO 5: LangGraph Agent com E-mails")
    print("="*80)
    
    try:
        from langgraph.graph import StateGraph, START, END
        from typing_extensions import TypedDict
    except ImportError:
        print("⚠️  LangGraph não instalado: pip install langgraph langchain-core")
        return
    
    from sirius_mail import email_tool_factory
    from memoria import SiriusMemory
    
    # Setup
    memoria = SiriusMemory()
    email_tool = email_tool_factory(memoria=memoria, user_id="carlos")
    manager = email_tool["instance"]
    
    # State
    class AgentState(TypedDict):
        emails: list
        acao: str
        notificacoes: list
    
    # Nodes
    def node_verificar(state):
        print("\n[AGENT]: Verificando e-mails...")
        resultado = manager.processar_emails()
        state["emails"] = resultado["emails"]
        return state
    
    def node_filtrar(state):
        print(f"[AGENT]: Filtrando {len(state['emails'])} e-mails...")
        notificacoes = []
        
        for email in state["emails"]:
            if email["prioridade"] == "alta":
                notificacoes.append({
                    "tipo": "URGENTE",
                    "mensagem": f"De: {email['remetente']}\n{email['assunto']}"
                })
        
        state["notificacoes"] = notificacoes
        return state
    
    def node_executar(state):
        print(f"[AGENT]: Executando {len(state['notificacoes'])} ações...")
        
        for notif in state["notificacoes"]:
            print(f"  🔔 {notif['tipo']}: {notif['mensagem'][:50]}...")
        
        state["acao"] = "COMPLETO"
        return state
    
    # Build graph
    graph = StateGraph(AgentState)
    graph.add_node("verificar", node_verificar)
    graph.add_node("filtrar", node_filtrar)
    graph.add_node("executar", node_executar)
    
    graph.add_edge(START, "verificar")
    graph.add_edge("verificar", "filtrar")
    graph.add_edge("filtrar", "executar")
    graph.add_edge("executar", END)
    
    # Run
    print("\n[AGENT]: Iniciando workflow LangGraph...")
    compiled = graph.compile()
    result = compiled.invoke({
        "emails": [],
        "acao": "",
        "notificacoes": []
    })
    
    print(f"\n[AGENT]: Workflow completo! Ação: {result['acao']}")


# ════════════════════════════════════════════════════════════════════════════════
# EXEMPLO 6: Monitoramento 24/7 com Schedule
# ════════════════════════════════════════════════════════════════════════════════

def exemplo_6_background():
    """Monitorar e-mails em background (requer schedule)."""
    print("\n" + "="*80)
    print("EXEMPLO 6: Monitoramento Background (Não ativo - apenas demo)")
    print("="*80)
    
    print("""
# Para usar em produção (instalar: pip install schedule):

import schedule
from sirius_mail import SiriusEmailManager

def tarefa_email():
    manager = SiriusEmailManager(user_id="carlos")
    if manager.conectar():
        resultado = manager.processar_emails()
        if resultado["requer_interrupcao"]:
            enviar_notificacao(resultado["mensagem_usuario"])
        manager.desconectar()

# Verificar a cada 5 minutos
schedule.every(5).minutes.do(tarefa_email)

# Em thread separada
while True:
    schedule.run_pending()
    time.sleep(1)
    """)


# ════════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "█"*80)
    print("█" + " "*78 + "█")
    print("█  SIRIUS MAIL - EXEMPLOS DE USO EM PRODUÇÃO" + " "*34 + "█")
    print("█" + " "*78 + "█")
    print("█"*80)
    
    print("\nOpcões:")
    print("  [1] Exemplo 1: Verificar na Startup")
    print("  [2] Exemplo 2: Verificação Periódica")
    print("  [3] Exemplo 3: Com SiriusMemory")
    print("  [4] Exemplo 4: Resposta Automática")
    print("  [5] Exemplo 5: LangGraph Agent")
    print("  [6] Exemplo 6: Background Monitor")
    print("  [7] Executar Todos")
    
    print("\n⚠️  Certifique-se de que .env está configurado antes de executar!")
    
    try:
        # Executar todos os exemplos
        print("\n\n" + "="*80)
        print("Executando todos os exemplos...")
        print("="*80)
        
        exemplo_1_startup()
        exemplo_2_periodico()
        exemplo_3_com_memoria()
        exemplo_4_resposta_automatica()
        exemplo_5_langgraph()
        exemplo_6_background()
        
        print("\n" + "█"*80)
        print("✅ Todos os exemplos executados com sucesso!")
        print("█"*80)
        
    except KeyboardInterrupt:
        print("\n\n[INTERROMPIDO] Execução cancelada pelo usuário")
    except Exception as e:
        print(f"\n\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
