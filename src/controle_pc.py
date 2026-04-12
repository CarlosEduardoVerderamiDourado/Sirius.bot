"""
controle_pc.py - Modulo de controle do sistema pelo Sirius
Cobre: programas, arquivos, janelas, volume, midia, energia, clipboard, screenshots, mouse/teclado.
"""

import os
import re
import time
import shutil
import subprocess
import pyperclip
import pyautogui
import pygetwindow as gw

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.3

# ---------------------------------------------------------------------------
# Apps de mensagens suportados (nicho fixo)
# ---------------------------------------------------------------------------

APPS_MENSAGENS = {
    "discord":  {"titulo": "Discord",  "atalho_busca": ["ctrl", "k"]},
    "whatsapp": {"titulo": "WhatsApp", "atalho_busca": ["ctrl", "f"]},
    "telegram": {"titulo": "Telegram", "atalho_busca": ["ctrl", "f"]},
    "slack":    {"titulo": "Slack",    "atalho_busca": ["ctrl", "k"]},
}

# ---------------------------------------------------------------------------
# Apps do sistema com comando direto (nao precisam de busca)
# ---------------------------------------------------------------------------

APPS_SISTEMA = {
    "notepad":            "notepad",
    "bloco de notas":     "notepad",
    "word":               "winword",
    "excel":              "excel",
    "powerpoint":         "powerpnt",
    "outlook":            "outlook",
    "vscode":             "code",
    "vs code":            "code",
    "visual studio code": "code",
    "explorador":         "explorer",
    "explorer":           "explorer",
    "gerenciador de tarefas": "taskmgr",
    "task manager":       "taskmgr",
    "painel de controle": "control",
    "calculadora":        "calc",
    "calculator":         "calc",
    "cmd":                "cmd",
    "terminal":           "wt",
    "powershell":         "powershell",
    "paint":              "mspaint",
    "configuracoes":      "ms-settings:",
    "configurações":      "ms-settings:",
}

# Palavras que o usuario pode falar para pedir o navegador
PALAVRAS_NAVEGADOR = {
    "navegador", "browser", "chrome", "firefox", "edge", "opera",
    "brave", "vivaldi", "internet", "web"
}

# Pastas de destino amigas
PASTAS_USUARIO = {
    "documentos": os.path.join(os.path.expanduser("~"), "Documents"),
    "documents":  os.path.join(os.path.expanduser("~"), "Documents"),
    "desktop":    os.path.join(os.path.expanduser("~"), "Desktop"),
    "downloads":  os.path.join(os.path.expanduser("~"), "Downloads"),
    "imagens":    os.path.join(os.path.expanduser("~"), "Pictures"),
    "pictures":   os.path.join(os.path.expanduser("~"), "Pictures"),
    "musicas":    os.path.join(os.path.expanduser("~"), "Music"),
    "videos":     os.path.join(os.path.expanduser("~"), "Videos"),
}


# ---------------------------------------------------------------------------
# Navegador padrao via registro do Windows
# ---------------------------------------------------------------------------

def _obter_navegador_padrao():
    """
    Le o navegador padrao configurado no Windows via registro.
    Retorna (caminho_executavel_ou_comando, nome_amigavel).
    Fallback em cascata: registro -> PATH -> start generico.
    """
    try:
        import winreg
        CHAVE = (
            r"SOFTWARE\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\https\UserChoice"
        )
        prog_id = None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE) as k:
                prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        except Exception:
            pass

        if prog_id:
            chave_cmd = r"SOFTWARE\Classes\{}\shell\open\command".format(prog_id)
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, chave_cmd) as k:
                        cmd_str = winreg.QueryValueEx(k, "")[0]
                        exes = re.findall(r'"([^"]+\.exe)"', cmd_str, re.IGNORECASE)
                        if exes:
                            nome = os.path.basename(exes[0]).lower().replace(".exe", "")
                            return exes[0], nome
                except Exception:
                    continue
    except ImportError:
        pass

    for nome, exe in [("chrome","chrome"),("msedge","msedge"),
                      ("firefox","firefox"),("brave","brave"),("opera","opera")]:
        if shutil.which(exe):
            return exe, nome

    return "start", "navegador padrao"


# ---------------------------------------------------------------------------
# Busca dinamica de apps instalados no Windows
# ---------------------------------------------------------------------------

def _buscar_no_registro(nome):
    """
    Busca o executavel no registro do Windows.
    Cobre praticamente todos os apps instalados corretamente.
    """
    try:
        import winreg
        chaves = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        ]
        nome_exe = nome if nome.endswith(".exe") else nome + ".exe"
        for chave_base in chaves:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"{}\{}".format(chave_base, nome_exe)) as k:
                    valor, _ = winreg.QueryValueEx(k, "")
                    if valor and os.path.exists(valor):
                        return valor
            except Exception:
                continue
    except ImportError:
        pass
    return None


def _buscar_no_startmenu(nome):
    """
    Busca no Menu Iniciar com scoring de relevancia.
    Evita falsos positivos usando match exato antes de match parcial.
    """
    pastas_start = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.join(os.path.expanduser("~"), "Desktop"),
    ]

    nome_lower = nome.lower().strip()
    candidatos = []  # lista de (score, caminho)

    for pasta in pastas_start:
        if not os.path.exists(pasta):
            continue
        for raiz, _, arquivos in os.walk(pasta):
            for arquivo in arquivos:
                if not arquivo.lower().endswith((".lnk", ".exe")):
                    continue
                nome_arquivo = os.path.splitext(arquivo)[0].lower()

                if nome_arquivo == nome_lower:
                    candidatos.append((100, os.path.join(raiz, arquivo)))
                elif nome_lower in nome_arquivo.split():
                    candidatos.append((80, os.path.join(raiz, arquivo)))
                elif nome_arquivo.startswith(nome_lower):
                    candidatos.append((60, os.path.join(raiz, arquivo)))
                elif len(nome_lower) >= 4 and nome_lower in nome_arquivo:
                    candidatos.append((30, os.path.join(raiz, arquivo)))

    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][1]


def _buscar_nas_pastas_usuario(nome):
    """Busca arquivos nas pastas comuns do usuario (documentos, desktop, downloads)."""
    nome_lower = nome.lower().strip()
    for pasta in [PASTAS_USUARIO["desktop"], PASTAS_USUARIO["documentos"],
                  PASTAS_USUARIO["downloads"]]:
        for raiz, _, arquivos in os.walk(pasta):
            for arquivo in arquivos:
                nome_arq = os.path.splitext(arquivo)[0].lower()
                if nome_arq == nome_lower or nome_arq.startswith(nome_lower):
                    return os.path.join(raiz, arquivo)
    return None


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class SiriusControl:
    def __init__(self):
        self._ultima_janela_focada = None

    # -----------------------------------------------------------------------
    # JANELAS
    # -----------------------------------------------------------------------

    def _forcar_foco(self, titulo_parte):
        try:
            alvo = None
            for j in gw.getAllWindows():
                if titulo_parte.lower() in j.title.lower() and j.title.strip():
                    alvo = j
                    break
            if not alvo:
                return False
            if alvo.isMinimized:
                alvo.restore()
                time.sleep(0.4)
            alvo.activate()
            time.sleep(0.4)
            self._ultima_janela_focada = alvo.title
            return True
        except Exception:
            return False

    def listar_janelas(self):
        try:
            janelas = [j.title for j in gw.getAllWindows() if j.title.strip()]
            if not janelas:
                return "Nao ha janelas abertas."
            return "Janelas abertas:\n" + "\n".join("- " + t for t in janelas[:20])
        except Exception as e:
            return "Erro ao listar janelas: " + str(e)

    def fechar_janela(self, titulo_parte):
        try:
            for j in gw.getAllWindows():
                if titulo_parte.lower() in j.title.lower() and j.title.strip():
                    j.close()
                    return "Fechei '{}'.".format(j.title)
            return "Nao achei janela com '{}' pra fechar.".format(titulo_parte)
        except Exception as e:
            return "Erro ao fechar janela: " + str(e)

    def minimizar_janela(self, titulo_parte):
        try:
            for j in gw.getAllWindows():
                if titulo_parte.lower() in j.title.lower() and j.title.strip():
                    j.minimize()
                    return "Minimizei '{}'.".format(j.title)
            return "Nao achei janela com '{}'.".format(titulo_parte)
        except Exception as e:
            return "Erro ao minimizar: " + str(e)

    def maximizar_janela(self, titulo_parte):
        try:
            for j in gw.getAllWindows():
                if titulo_parte.lower() in j.title.lower() and j.title.strip():
                    j.maximize()
                    return "Maximizei '{}'.".format(j.title)
            return "Nao achei janela com '{}'.".format(titulo_parte)
        except Exception as e:
            return "Erro ao maximizar: " + str(e)

    def mover_janela(self, titulo_parte, direcao="direita"):
        try:
            if self._forcar_foco(titulo_parte):
                seta = "right" if "dire" in direcao.lower() else "left"
                pyautogui.hotkey("win", "shift", seta)
                return "Movi '{}' para a {}.".format(titulo_parte, direcao)
            return "Nao achei janela com '{}' pra mover.".format(titulo_parte)
        except Exception as e:
            return "Erro ao mover janela: " + str(e)

    def alternar_janela(self):
        pyautogui.hotkey("alt", "tab")
        return "Alternei para a proxima janela."

    # -----------------------------------------------------------------------
    # NAVEGADOR — sempre usa o padrao do sistema
    # -----------------------------------------------------------------------

    def abrir_navegador(self, url=""):
        """Abre o navegador padrao do Windows detectado via registro."""
        exe, nome = _obter_navegador_padrao()
        try:
            if url:
                if not url.startswith("http"):
                    url = "https://" + url
                if exe == "start":
                    os.system('start "" "{}"'.format(url))
                else:
                    subprocess.Popen([exe, url])
                return "Abrindo {} no {}...".format(url, nome)
            else:
                if exe == "start":
                    os.system('start "" "https://www.google.com"')
                else:
                    subprocess.Popen([exe])
                return "Abrindo o {}...".format(nome)
        except Exception as e:
            try:
                os.system('start "" "https://www.google.com"')
                return "Abri o navegador padrao."
            except Exception:
                return "Erro ao abrir navegador: " + str(e)

    def pesquisar_na_web(self, query):
        import urllib.parse
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
        return self.abrir_navegador(url)

    def abrir_url(self, url):
        return self.abrir_navegador(url)

    # -----------------------------------------------------------------------
    # PROGRAMAS — busca dinamica em cascata
    # -----------------------------------------------------------------------

    def abrir_programa(self, nome):
        """
        Abre qualquer programa instalado no Windows.
        Ordem de busca:
        1. Palavras de navegador  -> sempre abre o padrao do sistema
        2. Apps do sistema        -> comandos diretos (notepad, calc, etc)
        3. PATH global            -> shutil.which
        4. Registro do Windows    -> cobre 99% dos apps instalados corretamente
        5. Menu Iniciar           -> atalhos .lnk com scoring
        6. Pastas do usuario      -> documentos, desktop, downloads
        """
        nome_l = nome.strip().lower()
        print("[CONTROLE]: Tentando abrir '{}'".format(nome_l))

        # 1. Navegador
        if nome_l in PALAVRAS_NAVEGADOR:
            return self.abrir_navegador()

        # 2. Apps do sistema com comando direto
        cmd = APPS_SISTEMA.get(nome_l)
        if cmd:
            try:
                if cmd.startswith("ms-"):
                    os.system("start " + cmd)
                else:
                    subprocess.Popen(cmd, shell=True)
                return "Abrindo {}...".format(nome)
            except Exception as e:
                return "Erro ao abrir '{}': {}".format(nome, e)

        # 3. PATH global (git, python, node, etc)
        if shutil.which(nome_l):
            try:
                subprocess.Popen(nome_l, shell=True)
                return "Iniciando {}...".format(nome_l)
            except Exception as e:
                return "Erro ao executar '{}': {}".format(nome_l, e)

        # 4. Registro do Windows — apps instalados corretamente
        caminho_reg = _buscar_no_registro(nome_l)
        if caminho_reg:
            try:
                os.startfile(caminho_reg)
                return "Abrindo {} pelo registro!".format(nome)
            except Exception as e:
                return "Encontrei '{}' no registro mas deu erro: {}".format(nome, e)

        # 5. Menu Iniciar com scoring
        caminho_start = _buscar_no_startmenu(nome_l)
        if caminho_start:
            try:
                os.startfile(caminho_start)
                return "Achei '{}' no menu iniciar e abri!".format(
                    os.path.basename(caminho_start))
            except Exception as e:
                return "Achei no menu mas deu erro: " + str(e)

        # 6. Pastas do usuario
        caminho_pasta = _buscar_nas_pastas_usuario(nome_l)
        if caminho_pasta:
            try:
                os.startfile(caminho_pasta)
                return "Achei '{}' e abri!".format(os.path.basename(caminho_pasta))
            except Exception as e:
                return "Achei mas deu erro: " + str(e)

        return (
            "Mano, nao achei '{}' em lugar nenhum. "
            "Verifica se o nome ta certo ou se o app ta instalado."
        ).format(nome)

    def fechar_programa(self, nome):
        nome_l = nome.strip().lower()
        exe = APPS_SISTEMA.get(nome_l, nome_l)
        if not exe.endswith(".exe"):
            exe += ".exe"
        try:
            result = subprocess.run(["taskkill", "/f", "/im", exe],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                return "Fechei o {} com sucesso.".format(nome)
            return self.fechar_janela(nome)
        except Exception as e:
            return "Erro ao fechar {}: {}".format(nome, e)

    # -----------------------------------------------------------------------
    # ARQUIVOS E PASTAS
    # -----------------------------------------------------------------------

    def criar_arquivo_texto(self, nome_arquivo, conteudo, pasta="documentos"):
        try:
            nome_l = nome_arquivo.strip().replace(" ", "_").lower()
            if "." not in nome_l:
                nome_l += ".txt"
            pasta_dest = PASTAS_USUARIO.get(pasta.lower(), PASTAS_USUARIO["documentos"])
            os.makedirs(pasta_dest, exist_ok=True)
            caminho = os.path.join(pasta_dest, nome_l)
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(conteudo.strip().replace('"', ""))
            return "Arquivo '{}' criado em {}!".format(nome_l, os.path.basename(pasta_dest))
        except Exception as e:
            return "Erro ao criar arquivo: " + str(e)

    def abrir_pasta(self, nome="documentos"):
        pasta = PASTAS_USUARIO.get(nome.lower())
        if pasta and os.path.exists(pasta):
            os.startfile(pasta)
            return "Abri a pasta {}.".format(nome)
        if os.path.exists(nome):
            os.startfile(nome)
            return "Abri a pasta {}.".format(nome)
        return "Nao encontrei a pasta '{}'.".format(nome)

    def screenshot(self, nome=""):
        try:
            pasta = PASTAS_USUARIO["imagens"]
            os.makedirs(pasta, exist_ok=True)
            if not nome:
                nome = "screenshot_{}.png".format(int(time.time()))
            elif not nome.endswith(".png"):
                nome += ".png"
            pyautogui.screenshot(os.path.join(pasta, nome))
            return "Screenshot salvo como '{}' em Imagens.".format(nome)
        except Exception as e:
            return "Erro ao tirar screenshot: " + str(e)

    # -----------------------------------------------------------------------
    # CLIPBOARD
    # -----------------------------------------------------------------------

    def copiar_texto(self, texto):
        try:
            pyperclip.copy(texto)
            return "Copiei: '{}'".format(texto[:50])
        except Exception as e:
            return "Erro ao copiar: " + str(e)

    def colar_texto(self):
        pyautogui.hotkey("ctrl", "v")
        return "Conteudo colado."

    def obter_clipboard(self):
        try:
            c = pyperclip.paste()
            return "Clipboard: '{}'".format(c[:200]) if c else "Clipboard vazio."
        except Exception:
            return "Nao consegui ler o clipboard."

    # -----------------------------------------------------------------------
    # VOLUME E MIDIA
    # -----------------------------------------------------------------------

    def controle_hardware(self, acao, repeticoes=3):
        mapa = {
            "volume_mais":     ("volumeup",  "Volume aumentado."),
            "volume_menos":    ("volumedown","Volume diminuido."),
            "mutar":           ("volumemute","Audio mutado/desmutado."),
            "proxima_musica":  ("nexttrack", "Proxima faixa."),
            "musica_anterior": ("prevtrack", "Faixa anterior."),
            "pausar_musica":   ("playpause", "Play/Pause ativado."),
            "parar_musica":    ("stop",      "Reproducao parada."),
        }
        if acao not in mapa:
            return "Acao '{}' nao reconhecida.".format(acao)
        tecla, msg = mapa[acao]
        n = repeticoes if acao in ("volume_mais", "volume_menos") else 1
        for _ in range(n):
            pyautogui.press(tecla)
        return msg

    # -----------------------------------------------------------------------
    # ENERGIA
    # -----------------------------------------------------------------------

    def gerenciar_energia(self, acao, delay_seg=60):
        acao = acao.lower().strip()
        if acao in ("desligar", "shutdown"):
            os.system("shutdown /s /t {}".format(delay_seg))
            return "PC desliga em {}s. Diz 'cancela desligar' pra abortar.".format(delay_seg)
        if acao in ("reiniciar", "restart", "reboot"):
            os.system("shutdown /r /t {}".format(delay_seg))
            return "Reiniciando em {}s!".format(delay_seg)
        if acao in ("suspender", "suspend", "sleep"):
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            return "Suspendendo..."
        if acao in ("hibernar", "hibernate"):
            os.system("shutdown /h")
            return "Hibernando..."
        if acao in ("cancelar", "cancel"):
            os.system("shutdown /a")
            return "Operacao cancelada! PC sobreviveu."
        if acao in ("bloquear", "lock"):
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return "PC bloqueado."
        return "Acao desconhecida: '{}'.".format(acao)

    # -----------------------------------------------------------------------
    # TECLADO E MOUSE
    # -----------------------------------------------------------------------

    def digitar_texto(self, texto):
        try:
            time.sleep(0.3)
            pyautogui.typewrite(texto, interval=0.05)
            return "Digitei: '{}'".format(texto[:60])
        except Exception as e:
            return "Erro ao digitar: " + str(e)

    def pressionar_tecla(self, tecla):
        try:
            tecla = tecla.lower().strip()
            if "+" in tecla:
                pyautogui.hotkey(*[t.strip() for t in tecla.split("+")])
            else:
                pyautogui.press(tecla)
            return "Tecla '{}' pressionada.".format(tecla)
        except Exception as e:
            return "Erro ao pressionar tecla: " + str(e)

    def mover_mouse(self, x, y):
        try:
            pyautogui.moveTo(x, y, duration=0.3)
            return "Mouse em ({}, {}).".format(x, y)
        except Exception as e:
            return "Erro ao mover mouse: " + str(e)

    def clicar(self, x=None, y=None, botao="left"):
        try:
            if x is not None and y is not None:
                pyautogui.click(x, y, button=botao)
            else:
                pyautogui.click(button=botao)
            return "Clicado."
        except Exception as e:
            return "Erro ao clicar: " + str(e)

    def rolar_pagina(self, direcao="baixo", quantidade=3):
        clicks = -quantidade if "baixo" in direcao else quantidade
        pyautogui.scroll(clicks)
        return "Rolei para {}.".format(direcao)

    # -----------------------------------------------------------------------
    # MENSAGENS — Discord, WhatsApp, Telegram, Slack
    # -----------------------------------------------------------------------

    def _abrir_app_mensagem(self, plataforma, app):
        """
        Garante que o app de mensagem esteja aberto e em foco.
        Tenta focar primeiro. Se nao conseguir, abre o app e aguarda carregar.
        Retorna True se conseguiu focar, False caso contrario.
        """
        # Tentativa 1: ja esta aberto, so foca
        if self._forcar_foco(app["titulo"]):
            return True

        print("[CONTROLE]: {} nao esta aberto. Abrindo...".format(plataforma))

        # Tenta abrir pelo nome direto (PATH ou mapa)
        exe = APPS_SISTEMA.get(plataforma.lower(), plataforma.lower())
        try:
            subprocess.Popen(exe, shell=True)
        except Exception:
            try:
                os.system("start {}".format(plataforma.lower()))
            except Exception:
                pass

        # Aguarda o app abrir — tenta em intervalos de 2s por ate 20s
        for tentativa in range(10):
            time.sleep(2)
            if self._forcar_foco(app["titulo"]):
                print("[CONTROLE]: {} abriu na tentativa {}.".format(
                    plataforma, tentativa + 1))
                time.sleep(1)  # aguarda renderizacao completa
                return True

        return False

    def enviar_mensagem_universal(self, plataforma, destinatario, mensagem):
        """
        Envia mensagem via automacao de GUI.
        Abre o app automaticamente se nao estiver aberto.
        """
        app = APPS_MENSAGENS.get(plataforma.lower())
        if not app:
            return "App '{}' nao configurado.".format(plataforma)

        # Abre o app se necessario
        if not self._abrir_app_mensagem(plataforma, app):
            return (
                "Tentei abrir o {} mas nao consegui focar nele. "
                "Abre manualmente e tenta de novo.".format(plataforma)
            )

        # Pequena pausa apos foco para garantir que a UI esta pronta
        time.sleep(0.5)
        pyautogui.press("escape")
        time.sleep(0.3)

        # Abre a busca de contato
        pyautogui.hotkey(*app["atalho_busca"])
        time.sleep(0.6)

        # Cola o nome do destinatario
        pyperclip.copy(destinatario)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(1.5)
        pyautogui.press("enter")
        time.sleep(1.0)

        # Cola a mensagem e envia
        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyautogui.press("enter")

        return "Mensagem enviada para {} no {}.".format(destinatario, plataforma)

    # -----------------------------------------------------------------------
    # CRIAR ARQUIVO COM CONTEUDO
    # -----------------------------------------------------------------------

    def criar_arquivo_com_conteudo(self, nome_arquivo, conteudo, pasta="documentos"):
        """
        Cria um arquivo com qualquer extensao e conteudo.
        Suporta: .txt, .py, .html, .md, .json, .csv, .bat, .js, .css
        """
        try:
            nome_l = nome_arquivo.strip().replace(" ", "_")
            # Garante que tem extensao
            if "." not in nome_l:
                nome_l += ".txt"

            pasta_dest = PASTAS_USUARIO.get(pasta.lower(), PASTAS_USUARIO["documentos"])
            os.makedirs(pasta_dest, exist_ok=True)
            caminho = os.path.join(pasta_dest, nome_l)

            conteudo_final = conteudo if conteudo else ""

            with open(caminho, "w", encoding="utf-8") as f:
                f.write(conteudo_final)

            tamanho = len(conteudo_final)
            return (
                "Arquivo '{}' criado em {} com {} caracteres! "
                "Caminho: {}".format(
                    nome_l,
                    os.path.basename(pasta_dest),
                    tamanho,
                    caminho
                )
            )
        except Exception as e:
            return "Erro ao criar arquivo: " + str(e)

    # -----------------------------------------------------------------------
    # SISTEMA
    # -----------------------------------------------------------------------

    def info_sistema(self):
        try:
            import platform
            s = platform.uname()
            return "Sistema: {} {}\nMaquina: {}\nCPU: {}\nNode: {}".format(
                s.system, s.release, s.machine, s.processor or "N/A", s.node)
        except Exception as e:
            return "Erro: " + str(e)

    def uso_cpu_ram(self):
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            return "CPU: {:.1f}%\nRAM: {:.1f}% ({} MB / {} MB)".format(
                cpu, ram.percent, ram.used//1024**2, ram.total//1024**2)
        except ImportError:
            return "Instale psutil: pip install psutil"
        except Exception as e:
            return "Erro: " + str(e)

    def processos_ativos(self, top_n=10):
        try:
            import psutil
            procs = sorted(psutil.process_iter(["pid","name","cpu_percent"]),
                           key=lambda p: p.info["cpu_percent"], reverse=True)[:top_n]
            return "Top processos:\n" + "\n".join(
                "{} (PID {}) - {:.1f}%".format(p.info["name"],p.info["pid"],p.info["cpu_percent"])
                for p in procs)
        except ImportError:
            return "Instale psutil: pip install psutil"
        except Exception as e:
            return "Erro: " + str(e)

    def matar_processo(self, nome_ou_pid):
        try:
            import psutil
            alvo = nome_ou_pid.strip()
            for proc in psutil.process_iter(["pid","name"]):
                if alvo.isdigit() and proc.info["pid"] == int(alvo):
                    proc.kill()
                    return "PID {} encerrado.".format(alvo)
                if alvo.lower() in proc.info["name"].lower():
                    proc.kill()
                    return "Processo '{}' encerrado.".format(proc.info["name"])
            return "Processo '{}' nao encontrado.".format(alvo)
        except ImportError:
            return self.fechar_programa(nome_ou_pid)
        except Exception as e:
            return "Erro: " + str(e)

    # -----------------------------------------------------------------------
    # MACROS
    # -----------------------------------------------------------------------

    def executar_macro(self, comandos):
        resultados = []
        for cmd in comandos:
            cmd = cmd.strip()
            if cmd.startswith("tecla:"):
                resultados.append(self.pressionar_tecla(cmd[6:]))
            elif cmd.startswith("digitar:"):
                resultados.append(self.digitar_texto(cmd[8:]))
            elif cmd.startswith("esperar:"):
                try:
                    time.sleep(float(cmd[8:]))
                    resultados.append("Esperou {}s.".format(cmd[8:]))
                except Exception:
                    pass
            elif cmd.startswith("abrir:"):
                resultados.append(self.abrir_programa(cmd[6:]))
            elif cmd.startswith("fechar:"):
                resultados.append(self.fechar_janela(cmd[7:]))
            else:
                resultados.append("Comando desconhecido: '{}'".format(cmd))
        return "\n".join(resultados)