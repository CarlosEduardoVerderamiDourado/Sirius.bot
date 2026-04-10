import os
import time
import pyperclip
import pyautogui
import pygetwindow as gw

diretorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
caminho_final = os.path.join(diretorio_raiz, "arquivos_gerados")
if not os.path.exists(caminho_final):
    os.makedirs(caminho_final)

class SiriusControl:
    def __init__(self):
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.4 

        self.apps = {
            "discord": {"titulo": "Discord", "atalho_busca": ["ctrl", "k"]},
            "whatsapp": {"titulo": "WhatsApp", "atalho_busca": ["ctrl", "f"]},
            "telegram": {"titulo": "Telegram", "atalho_busca": ["ctrl", "f"]}
        }

    def _forcar_foco(self, titulo_parte):
        try:
            termo_l = titulo_parte.lower()
            todas_janelas = gw.getAllWindows()
            
            janela_alvo = None
            for j in todas_janelas:
                if termo_l in j.title.lower() and j.title != "":
                    janela_alvo = j
                    break
            
            if not janela_alvo: return False

            if janela_alvo.isMinimized:
                janela_alvo.restore()
                time.sleep(0.5)

            janela_alvo.activate()
            time.sleep(0.5)
            
            # Clique no centro para garantir o foco
            pyautogui.click(janela_alvo.left + (janela_alvo.width // 2), 
                            janela_alvo.top + (janela_alvo.height // 2))
            time.sleep(0.3)
            pyautogui.press('alt') 
            return True
        except: return False

    def criar_arquivo_texto(self, nome_arquivo, conteudo, pasta_especifica=None):
        """Cria um arquivo mantendo as quebras de linha corretamente."""
        try:
            # 1. Limpeza básica do nome
            nome_limpo = nome_arquivo.replace(" ", "_").lower()
            if "." not in nome_limpo:
                nome_limpo += ".txt"

            # 2. Define local (Documentos é o padrão)
            caminho_final = os.path.join(os.path.expanduser("~"), "Documents")

            if pasta_especifica:
                root_busca = os.path.expanduser("~") 
                for raiz, diretorios, arquivos in os.walk(root_busca):
                    if pasta_especifica.lower() in [d.lower() for d in diretorios]:
                        caminho_final = os.path.join(raiz, pasta_especifica)
                        break

            caminho_completo = os.path.join(caminho_final, nome_limpo)

            # --- AQUI ESTÁ A CORREÇÃO DAS LINHAS ---
            # Remove aspas extras que a IA as vezes coloca e normaliza quebras de linha
            conteudo_corrigido = conteudo.strip().replace('"', '')
            # Garante que o Windows entenda as quebras de linha (\r\n)
            conteudo_corrigido = conteudo_corrigido.replace('\n', os.linesep)

            with open(caminho_completo, "w", encoding="utf-8") as arquivo:
                arquivo.write(conteudo_corrigido)
            
            return f"Feito! O arquivo '{nome_limpo}' tá na pasta {os.path.basename(caminho_final)}."

        except Exception as e:
            print(f"[ERRO ARQUIVO]: {e}")
            return "Vixi, deu erro ao salvar o arquivo."

    def gerenciar_energia(self, acao):
        """Novo: Desliga o PC ou cancela o desligamento."""
        if acao == "desligar":
            # Dá 60 segundos de aviso antes de desligar
            os.system("shutdown /s /t 60")
            return "PC vai de berço em 60 segundos! Se bateu o arrependimento, pede pra eu cancelar."
        elif acao == "cancelar":
            os.system("shutdown /a")
            return "Operação cancelada! O PC sobreviveu a mais um dia."
        return "Ação de energia desconhecida."

    def enviar_mensagem_universal(self, plataforma, destinatario, mensagem):
        app = self.apps.get(plataforma.lower())
        if not app: return f"App {plataforma} não configurado."

        if not self._forcar_foco(app["titulo"]):
            os.system(f"start {plataforma}")
            time.sleep(10) 
            if not self._forcar_foco(app["titulo"]): return "Não consegui abrir o app."

        pyautogui.press('esc')
        pyautogui.hotkey(*app["atalho_busca"])
        time.sleep(0.5)
        pyperclip.copy(destinatario)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(2.0) 
        pyautogui.press('enter')
        time.sleep(1.0)
        
        pyperclip.copy(mensagem)
        pyautogui.hotkey('ctrl', 'v')
        pyautogui.press('enter')
        return f"Mensagem enviada para {destinatario}."

    def abrir_programa(self, nome):
        try:
            os.system(f"start {nome}")
            return f"Abrindo {nome}..."
        except: return "Erro ao abrir."

    def controle_hardware(self, acao):
        comandos = {
            "volume_mais": "volumeup", "volume_menos": "volumedown",
            "mutar": "volumemute", "proxima_musica": "nexttrack", "pausar_musica": "playpause"
        }
        if acao in comandos:
            for _ in range(3): pyautogui.press(comandos[acao])
            return "Comando enviado."
        return "Ação não reconhecida."