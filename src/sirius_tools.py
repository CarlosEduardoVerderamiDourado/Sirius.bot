from langchain.tools import tool
from controle_pc import SiriusControl
from neuronio import SiriusNeuronio
from consultor import SiriusConsultor

# Instanciando os "braços" e os "sentidos" do Sirius
control = SiriusControl()
neuronio_local = SiriusNeuronio() # Sua Rede Neural PyTorch
consultor_faiss = SiriusConsultor() # Seu Banco FAISS

@tool
def manipular_pc(comando: str) -> str:
    """
    Útil para abrir programas ou gerenciar energia (desligar).
    O Agente decide o comando (ex: 'notepad', 'discord', 'msedge', 'desligar').
    """
    if comando in ["desligar", "cancelar"]:
        return control.gerenciar_energia(comando)
    return control.abrir_programa(comando)

@tool
def gerenciar_arquivos(nome: str, conteudo: str) -> str:
    """
    Útil para criar arquivos, anotar algo ou salvar documentos.
    Extrai o nome do arquivo (ex: 'projeto.py') e o texto interno.
    """
    return control.criar_arquivo_texto(nome, conteudo)

@tool
def enviar_mensagem_social(plataforma: str, pessoa: str, mensagem: str) -> str:
    """
    Envia mensagens no WhatsApp ou Discord. 
    Plataforma: 'whatsapp' ou 'discord'. Pessoa: Nome do contato.
    """
    return control.enviar_mensagem_universal(plataforma, pessoa, mensagem)

@tool
def consultar_memoria_tecnica(pergunta: str) -> str:
    """
    Consulta o banco de dados vetorial FAISS. 
    Use quando o usuário fizer perguntas técnicas sobre temas que você já estudou 
    (como Python, Minecraft, ou documentações salvas).
    """
    return consultor_faiss.buscar_na_memoria(pergunta)

@tool
def consultar_intuicao_neural(texto: str) -> str:
    """
    Consulta a Rede Neural local (PyTorch) para classificar o tema ou 
    obter uma predição baseada no histórico de treino do Sirius.
    """
    return neuronio_local.predizer(texto)

@tool
def treinar_cerebro_local() -> str:
    """
    Inicia o treinamento (Refino) da rede neural local com novos dados. 
    Use se o usuário pedir para você 'aprender' ou 'evoluir' seu conhecimento.
    """
    neuronio_local.treinar()
    return "Treinamento concluído. Meus pesos neurais foram atualizados!"

# Lista consolidada de ferramentas que o Agente ReAct pode usar
SIRIUS_TOOLS = [
    manipular_pc, 
    gerenciar_arquivos, 
    enviar_mensagem_social, 
    consultar_memoria_tecnica,
    consultar_intuicao_neural,
    treinar_cerebro_local
]