"""
controle_pc.py — Módulo de Controle do PC para o S.I.R.I.U.S.
==============================================================
Responsabilidades:
  • Fornecer funções de controle do Windows em background (sem travar a UI)
  • Ser instanciado pelo SiriusCerebro como self.controle
  • Expor apenas funções seguras, sem deletar arquivos ou formatar discos

Funções disponíveis (referenciadas no MASTER_SYSTEM_PROMPT):
  abrir_programa(nome_ou_caminho)
  fechar_programa(nome)
  gerenciar_volume(acao)
  tirar_screenshot()
  matar_processo(nome_ou_pid)
  uso_cpu_ram()
  criar_arquivo_com_conteudo(nome, conteudo, pasta)
  copiar_para_area_transferencia(texto)
  abrir_url(url)
  pesquisar_na_web(query)
  enviar_mensagem_universal(plataforma, destinatario, mensagem)

Design:
  • Cada método retorna uma string de confirmação (ex: "Word aberto.")
  • Operações pesadas usam subprocess.Popen (non-blocking)
  • Operações de volume e processo usam ctypes/psutil (background-safe)
  • pyautogui é importado apenas em executar_macro() (explicitamente bloqueante)
"""

from __future__ import annotations

import os
import sys
import re
import subprocess
import threading
import webbrowser
import datetime
from typing import Union, Optional

# ── Dependências opcionais ─────────────────────────────────────────────────── #

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

try:
    import pyperclip
    _PYPERCLIP_OK = True
except ImportError:
    _PYPERCLIP_OK = False

try:
    from PIL import ImageGrab
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# ── Caminhos de screenshots ────────────────────────────────────────────────── #
_PASTA_SCREENSHOTS = os.path.join(os.path.expanduser("~"), "Pictures", "Sirius")


class SiriusControle:
    """
    Módulo de controle do Windows para o S.I.R.I.U.S.

    Todos os métodos:
      • Rodam em primeiro plano (mas retornam rápido — Popen é non-blocking)
      • Retornam str de confirmação para o Sirius falar ao Carlos
      • Não movem mouse nem digitam (exceto executar_macro, que requer permissão)
    """

    def __init__(self):
        os.makedirs(_PASTA_SCREENSHOTS, exist_ok=True)
        print("\033[92m[CONTROLE]: SiriusControle inicializado.\033[0m")

    # =========================================================================
    # Programas e processos
    # =========================================================================

    def abrir_programa(self, nome_ou_caminho: str) -> str:
        """
        Abre um programa em background via subprocess.Popen.

        Aceita:
          • Nome amigável: 'notepad', 'chrome', 'code', 'winword', 'excel'
          • Caminho completo: r'C:\\Program Files\\...\\app.exe'
          • Comando shell: 'start spotify'
        """
        cmd = nome_ou_caminho.strip()

        # Mapa de apelidos para comandos reais
        _APELIDOS = {
            "word":       "start winword",
            "winword":    "start winword",
            "excel":      "start excel",
            "powerpoint": "start powerpnt",
            "outlook":    "start outlook",
            "chrome":     "start chrome",
            "firefox":    "start firefox",
            "edge":       "start msedge",
            "notepad":    "notepad",
            "bloco de notas": "notepad",
            "code":       "start code",
            "vscode":     "start code",
            "vs code":    "start code",
            "terminal":   "start cmd",
            "cmd":        "start cmd",
            "powershell": "start powershell",
            "explorer":   "explorer",
            "discord":    "start discord",
            "spotify":    "start spotify",
            "taskmgr":    "taskmgr",
            "calculadora": "calc",
            "calc":       "calc",
            "paint":      "mspaint",
        }

        cmd_real = _APELIDOS.get(cmd.lower(), cmd)

        try:
            subprocess.Popen(cmd_real, shell=True)
            nome_curto = cmd.split("\\")[-1].replace("start ", "").title()
            return f"{nome_curto} iniciado em segundo plano."
        except Exception as e:
            return f"⚠ Falha ao abrir '{cmd}': {e}"

    def fechar_programa(self, nome: str) -> str:
        """
        Encerra um programa pelo nome do processo (ex: 'chrome', 'notepad').
        Usa taskkill para garantir encerramento mesmo sem psutil.
        """
        nome = nome.strip()
        try:
            # Garante a extensão .exe se não tiver
            alvo = nome if nome.lower().endswith(".exe") else f"{nome}.exe"
            result = subprocess.run(
                ["taskkill", "/IM", alvo, "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return f"{nome} encerrado."
            else:
                return f"⚠ Não foi possível encerrar '{nome}': {result.stderr.strip()}"
        except Exception as e:
            return f"⚠ Erro ao fechar '{nome}': {e}"

    def matar_processo(self, nome_ou_pid: Union[str, int]) -> str:
        """
        Mata um processo pelo nome (substring) ou PID.
        Mais granular que fechar_programa — usa psutil quando disponível.
        """
        if _PSUTIL_OK:
            return self._matar_com_psutil(nome_ou_pid)
        # Fallback: taskkill
        return self.fechar_programa(str(nome_ou_pid))

    def _matar_com_psutil(self, nome_ou_pid: Union[str, int]) -> str:
        mortos = []
        try:
            # Por PID
            if isinstance(nome_ou_pid, int) or str(nome_ou_pid).isdigit():
                pid = int(nome_ou_pid)
                p = psutil.Process(pid)
                nome = p.name()
                p.kill()
                return f"Processo {nome} (PID {pid}) encerrado."

            # Por nome (substring, case-insensitive)
            alvo = str(nome_ou_pid).lower()
            for p in psutil.process_iter(["pid", "name"]):
                if alvo in (p.info["name"] or "").lower():
                    try:
                        p.kill()
                        mortos.append(p.info["name"])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            if mortos:
                return f"Encerrado(s): {', '.join(mortos)}."
            return f"Nenhum processo com '{nome_ou_pid}' encontrado."
        except Exception as e:
            return f"⚠ Erro ao matar processo: {e}"

    def uso_cpu_ram(self) -> str:
        """Retorna string com uso atual de CPU e RAM."""
        if not _PSUTIL_OK:
            return "⚠ psutil não instalado — pip install psutil"
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            estado = (
                "⚠ CRÍTICO" if cpu > 90 or ram.percent > 90
                else "moderado" if cpu > 60 or ram.percent > 70
                else "tranquilo"
            )
            return (
                f"CPU: {cpu:.0f}% | RAM: {ram.percent:.0f}% "
                f"({ram.used // 1024**2}/{ram.total // 1024**2} MB) — {estado}"
            )
        except Exception as e:
            return f"⚠ Erro ao ler recursos: {e}"

    # =========================================================================
    # Volume do sistema
    # =========================================================================

    def gerenciar_volume(self, acao: Union[str, int]) -> str:
        """
        Controla o volume do Windows.

        acao pode ser:
          'aumentar'  → +10%
          'diminuir'  → -10%
          'silenciar' → mute
          int (0-100) → nível absoluto via nircmd (se instalado)
        """
        try:
            if isinstance(acao, int) or (isinstance(acao, str) and acao.isdigit()):
                nivel = max(0, min(100, int(acao)))
                return self._volume_absoluto(nivel)

            acao = str(acao).lower().strip()

            if acao in ("aumentar", "subir", "mais", "up"):
                self._tecla_volume("up", repeticoes=5)
                return "Volume aumentado."

            if acao in ("diminuir", "baixar", "menos", "down"):
                self._tecla_volume("down", repeticoes=5)
                return "Volume diminuído."

            if acao in ("silenciar", "mudo", "mute", "mutar"):
                self._tecla_volume("mute")
                return "Volume silenciado."

            return f"⚠ Ação de volume desconhecida: '{acao}'"

        except Exception as e:
            return f"⚠ Erro ao controlar volume: {e}"

    def _tecla_volume(self, direcao: str, repeticoes: int = 1):
        """Simula teclas de volume via PowerShell (background-safe)."""
        _MAPA = {
            "up":   "0xAF",   # VK_VOLUME_UP
            "down": "0xAE",   # VK_VOLUME_DOWN
            "mute": "0xAD",   # VK_VOLUME_MUTE
        }
        vk = _MAPA.get(direcao, "0xAF")
        script = (
            f"$wsh = New-Object -ComObject WScript.Shell; "
            + ("$wsh.SendKeys([char][int]" + vk + "); ") * repeticoes
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    def _volume_absoluto(self, nivel: int) -> str:
        """Define volume absoluto via nircmd (se disponível) ou PowerShell."""
        # Tenta nircmd primeiro (mais preciso)
        try:
            subprocess.run(
                ["nircmd", "setsysvolume", str(int(nivel / 100 * 65535))],
                capture_output=True, timeout=3
            )
            return f"Volume definido para {nivel}%."
        except FileNotFoundError:
            pass

        # Fallback via PowerShell Audio API
        script = f"""
        $vol = [math]::Round({nivel} / 100 * 65535);
        $wsh = New-Object -ComObject WScript.Shell;
        # Reseta para 0 e sobe até o nivel
        for($i=0;$i -lt 50;$i++){{$wsh.SendKeys([char][int]0xAE)}};
        $steps = [math]::Round({nivel} / 2);
        for($i=0;$i -lt $steps;$i++){{$wsh.SendKeys([char][int]0xAF)}};
        """
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return f"Volume ajustado para ~{nivel}%."

    # =========================================================================
    # Screenshot
    # =========================================================================

    def tirar_screenshot(self, nome: Optional[str] = None) -> str:
        """
        Captura a tela e salva em ~/Pictures/Sirius/.
        Retorna o caminho do arquivo salvo.
        """
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = nome or f"screenshot_{ts}.png"
        caminho = os.path.join(_PASTA_SCREENSHOTS, nome_arquivo)

        if _PIL_OK:
            try:
                img = ImageGrab.grab()
                img.save(caminho)
                return f"Screenshot salvo: {caminho}"
            except Exception as e:
                return f"⚠ Erro ao capturar tela (PIL): {e}"

        # Fallback via PowerShell
        try:
            script = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"$bmp = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                f"$img = New-Object System.Drawing.Bitmap($bmp.Width, $bmp.Height); "
                f"$gfx = [System.Drawing.Graphics]::FromImage($img); "
                f"$gfx.CopyFromScreen($bmp.Location, [System.Drawing.Point]::Empty, $bmp.Size); "
                f"$img.Save('{caminho}');"
            )
            subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command", script],
                timeout=10,
            )
            return f"Screenshot salvo: {caminho}"
        except Exception as e:
            return f"⚠ Erro ao capturar tela (PowerShell): {e}"

    # =========================================================================
    # Arquivos e área de transferência
    # =========================================================================

    def criar_arquivo_com_conteudo(
        self,
        nome:     str,
        conteudo: str,
        pasta:    str = "",
    ) -> str:
        """
        Cria um arquivo de texto com o conteúdo fornecido.
        Se pasta não for informada, salva em ~/Documents.
        """
        if not pasta:
            pasta = os.path.join(os.path.expanduser("~"), "Documents")

        pasta = os.path.expandvars(pasta)
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, nome)

        try:
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(conteudo)
            return f"Arquivo criado: {caminho}"
        except Exception as e:
            return f"⚠ Erro ao criar arquivo: {e}"

    def copiar_para_area_transferencia(self, texto: str) -> str:
        """Copia texto para a área de transferência."""
        if _PYPERCLIP_OK:
            try:
                pyperclip.copy(str(texto))
                return f"Copiado para a área de transferência ({len(texto)} caracteres)."
            except Exception as e:
                return f"⚠ Erro ao copiar (pyperclip): {e}"

        # Fallback via PowerShell
        try:
            texto_esc = texto.replace("'", "''")
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard '{texto_esc}'"],
                timeout=5,
            )
            return "Copiado para a área de transferência."
        except Exception as e:
            return f"⚠ Erro ao copiar (PowerShell): {e}"

    # =========================================================================
    # Web e comunicação
    # =========================================================================

    def abrir_url(self, url: str) -> str:
        """Abre uma URL no navegador padrão."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            webbrowser.open(url)
            return f"URL aberta: {url}"
        except Exception as e:
            return f"⚠ Erro ao abrir URL: {e}"

    def pesquisar_na_web(self, query: str) -> str:
        """Pesquisa no Google via navegador padrão."""
        query_enc = query.strip().replace(" ", "+")
        url = f"https://www.google.com/search?q={query_enc}"
        try:
            webbrowser.open(url)
            return f"Pesquisando: '{query}'"
        except Exception as e:
            return f"⚠ Erro ao pesquisar: {e}"

    def enviar_mensagem_universal(
        self,
        plataforma:   str,
        destinatario: str,
        mensagem:     str,
    ) -> str:
        """
        Abre a plataforma com um link de mensagem pré-preenchido.
        Suporta: 'whatsapp', 'telegram', 'discord'.

        Nota: para automação real de digitação, use executar_macro().
        """
        plat = plataforma.lower().strip()
        msg_enc = mensagem.strip().replace(" ", "%20")

        if plat == "whatsapp":
            num = re.sub(r"\D", "", destinatario)
            url = f"https://wa.me/{num}?text={msg_enc}"
            webbrowser.open(url)
            return f"WhatsApp aberto para {destinatario}."

        if plat == "telegram":
            url = f"https://t.me/{destinatario}?text={msg_enc}"
            webbrowser.open(url)
            return f"Telegram aberto para {destinatario}."

        if plat == "discord":
            # Discord não tem deep-link universal de DM — abre o app
            subprocess.Popen("start discord", shell=True)
            return (
                f"Discord aberto. Para automação completa de mensagens, "
                "peça ao Carlos para usar executar_macro()."
            )

        return f"⚠ Plataforma não suportada: '{plataforma}'. Use: whatsapp, telegram, discord."

    # =========================================================================
    # Utilitários do sistema
    # =========================================================================

    def listar_processos(self, filtro: str = "") -> str:
        """Lista processos em execução, opcionalmente filtrado por nome."""
        if not _PSUTIL_OK:
            return "⚠ psutil não instalado."
        try:
            procs = [
                f"{p.info['name']} (PID {p.info['pid']})"
                for p in psutil.process_iter(["pid", "name"])
                if filtro.lower() in (p.info["name"] or "").lower()
            ]
            if not procs:
                return f"Nenhum processo com '{filtro}' encontrado."
            return "\n".join(procs[:20])  # limita a 20 para não lotar o áudio
        except Exception as e:
            return f"⚠ Erro ao listar processos: {e}"

    def abrir_pasta(self, caminho: str) -> str:
        """Abre uma pasta no Explorador de Arquivos."""
        caminho = os.path.expandvars(caminho.strip())
        if not os.path.exists(caminho):
            return f"⚠ Caminho não encontrado: {caminho}"
        try:
            subprocess.Popen(f'explorer "{caminho}"', shell=True)
            return f"Pasta aberta: {caminho}"
        except Exception as e:
            return f"⚠ Erro ao abrir pasta: {e}"

    def desligar(self, minutos: int = 0) -> str:
        """Agenda ou executa o desligamento do PC. minutos=0 → imediato."""
        segundos = minutos * 60
        try:
            subprocess.run(
                ["shutdown", "/s", "/t", str(segundos)],
                check=True, capture_output=True,
            )
            msg = "agora" if minutos == 0 else f"em {minutos} minutos"
            return f"PC será desligado {msg}."
        except Exception as e:
            return f"⚠ Erro ao agendar desligamento: {e}"

    def cancelar_desligamento(self) -> str:
        """Cancela um desligamento agendado."""
        try:
            subprocess.run(["shutdown", "/a"], check=True, capture_output=True)
            return "Desligamento cancelado."
        except Exception as e:
            return f"⚠ Erro ao cancelar: {e}"

    # =========================================================================
    # executar_macro — uso explícito com pyautogui (requer permissão do Carlos)
    # =========================================================================

    def executar_macro(self, acoes: list[dict]) -> str:
        """
        Executa uma sequência de ações com pyautogui.
        USE APENAS quando Carlos pedir explicitamente "digitar" ou "clicar".

        Formato de acoes:
          [
            {"tipo": "escrever",  "texto": "Olá"},
            {"tipo": "clicar",    "x": 100, "y": 200},
            {"tipo": "tecla",     "tecla": "enter"},
            {"tipo": "esperar",   "segundos": 1.0},
            {"tipo": "hotkey",    "teclas": ["ctrl", "c"]},
          ]
        """
        try:
            import pyautogui
            import time as _time

            pyautogui.FAILSAFE  = True
            pyautogui.PAUSE     = 0.05

            for acao in acoes:
                tipo = str(acao.get("tipo", "")).lower()

                if tipo == "escrever":
                    pyautogui.write(str(acao.get("texto", "")), interval=0.03)

                elif tipo == "clicar":
                    x = int(acao.get("x", 0))
                    y = int(acao.get("y", 0))
                    btn = str(acao.get("botao", "left"))
                    pyautogui.click(x, y, button=btn)

                elif tipo == "duplo_clique":
                    pyautogui.doubleClick(int(acao.get("x", 0)), int(acao.get("y", 0)))

                elif tipo == "tecla":
                    pyautogui.press(str(acao.get("tecla", "")))

                elif tipo == "hotkey":
                    teclas = acao.get("teclas", [])
                    pyautogui.hotkey(*teclas)

                elif tipo == "mover":
                    pyautogui.moveTo(int(acao.get("x", 0)), int(acao.get("y", 0)))

                elif tipo == "scroll":
                    pyautogui.scroll(int(acao.get("clicks", 3)))

                elif tipo == "esperar":
                    _time.sleep(float(acao.get("segundos", 1.0)))

                else:
                    print(f"[CONTROLE MACRO]: Ação desconhecida: '{tipo}'")

            return f"Macro executado ({len(acoes)} ações)."

        except ImportError:
            return "⚠ pyautogui não instalado — pip install pyautogui"
        except pyautogui.FailSafeException:
            return "⚠ Macro interrompido: mouse no canto da tela (failsafe)."
        except Exception as e:
            return f"⚠ Erro na macro: {e}"


# =============================================================================
# Standalone
# =============================================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  SiriusControle — Teste Standalone")
    print("=" * 55)

    c = SiriusControle()

    print("\n[CPU/RAM]")
    print(" ", c.uso_cpu_ram())

    print("\n[CLIPBOARD]")
    print(" ", c.copiar_para_area_transferencia("Teste do SiriusControle"))

    print("\n[SCREENSHOT]")
    print(" ", c.tirar_screenshot())

    print("\n[ABRIR PROGRAMA]")
    print(" ", c.abrir_programa("notepad"))

    print("\nTeste concluído.")