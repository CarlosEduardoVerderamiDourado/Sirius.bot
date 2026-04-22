"""
sirius_apps.py — Interação com aplicativos externos do Windows

O Sirius controla outros apps pelo dispositivo:
  Spotify    → play/pause, próxima, anterior, volume, busca música
  YouTube    → abre vídeo/busca no navegador
  WhatsApp   → envia mensagem via WhatsApp Web
  Discord    → muda canal, muta, desmuta
  VLC/MPC    → controla reprodução local
  Chrome     → abre URL, nova aba, fecha aba
  Navegador  → qualquer navegador padrão
  Explorer   → abre pasta / arquivo

Tecnologia usada (100% local, sem API paga):
  pyautogui  → simula teclado e mouse
  pygetwindow→ encontra e foca janelas
  subprocess → lança processos
  webbrowser → abre URLs
  win32api   → teclas de mídia (Win32 API)
  pycaw      → controle de volume (Windows)
  spotipy    → Spotify API (opcional, mais completo)

Instalação mínima (sem API):
    pip install pyautogui pygetwindow

Instalação completa (com Spotify API):
    pip install spotipy
    # Criar app em: https://developer.spotify.com/dashboard
    # SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET no config.py

Comandos de voz:
  "coloca uma música"
  "toca Bohemian Rhapsody no Spotify"
  "pausa a música"
  "próxima música"
  "música anterior"
  "aumenta o volume do spotify"
  "abre o YouTube e pesquisa lofi"
  "manda mensagem para João no WhatsApp: oi"
  "muta o discord"
  "abre a pasta Downloads"
  "fecha a aba do chrome"
"""

import os
import sys
import re
import time
import subprocess
import threading
import unicodedata
import webbrowser
from typing import Optional, Callable

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)


def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Utilitários de janela (pygetwindow + pyautogui)
# ---------------------------------------------------------------------------

def _focar_janela(titulo_parcial: str) -> bool:
    """Encontra e foca uma janela pelo título parcial."""
    try:
        import pygetwindow as gw
        janelas = gw.getWindowsWithTitle(titulo_parcial)
        if janelas:
            j = janelas[0]
            if j.isMinimized:
                j.restore()
            j.activate()
            time.sleep(0.15)   # era 0.3 — reduzido pela metade
            return True
    except Exception:
        pass
    return False


def _app_esta_aberto(titulo_parcial: str) -> bool:
    """Verifica se uma janela com esse título está aberta."""
    try:
        import pygetwindow as gw
        return bool(gw.getWindowsWithTitle(titulo_parcial))
    except Exception:
        return False


def _abrir_app(caminho_ou_nome: str, aguardar: float = 0.8) -> bool:
    """
    Abre um aplicativo pelo caminho ou nome.
    aguardar: segundos de espera após lançar (era fixo em 1.5s).
    """
    try:
        subprocess.Popen(caminho_ou_nome, shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(aguardar)
        return True
    except Exception as e:
        print(f"[APPS]: Erro ao abrir '{caminho_ou_nome}': {e}")
        return False


def _tecla(atalho: str):
    """Envia atalho de teclado via pyautogui."""
    try:
        import pyautogui
        pyautogui.hotkey(*atalho.split("+"))
        time.sleep(0.1)
    except Exception as e:
        print(f"[APPS]: Erro ao enviar tecla '{atalho}': {e}")


def _digitar(texto: str, intervalo: float = 0.05):
    """Digita texto via pyautogui."""
    try:
        import pyautogui
        pyautogui.typewrite(texto, interval=intervalo)
    except Exception as e:
        print(f"[APPS]: Erro ao digitar: {e}")


import ctypes as _ctypes

# ---------------------------------------------------------------------------
# Estruturas Win32 para teclas de mídia — definidas uma vez no módulo
# Antes ficavam dentro de _tecla_midia() e eram recriadas a cada chamada
# ---------------------------------------------------------------------------

try:
    class _KEYBDINPUT(_ctypes.Structure):
        _fields_ = [
            ("wVk",       _ctypes.c_ushort),
            ("wScan",     _ctypes.c_ushort),
            ("dwFlags",   _ctypes.c_ulong),
            ("time",      _ctypes.c_ulong),
            ("dwExtraInfo", _ctypes.POINTER(_ctypes.c_ulong)),
        ]

    class _INPUT(_ctypes.Structure):
        _fields_ = [
            ("type",    _ctypes.c_ulong),
            ("ki",      _KEYBDINPUT),
            ("padding", _ctypes.c_ubyte * 8),
        ]

    _WIN32_DISPONIVEL = True
except Exception:
    _WIN32_DISPONIVEL = False

_TECLAS_MIDIA = {
    "play_pause": 0xB3,
    "next":       0xB0,
    "prev":       0xB1,
    "stop":       0xB2,
}
_NOMES_PYAUTOGUI = {
    "play_pause": "playpause",
    "next":       "nexttrack",
    "prev":       "prevtrack",
    "stop":       "stop",
}


def _tecla_midia(acao: str):
    """
    Envia tecla de mídia do Windows (funciona globalmente, sem focar janela).
    Estruturas ctypes definidas uma vez no nível do módulo — sem overhead por chamada.
    """
    codigo = _TECLAS_MIDIA.get(acao)
    if not codigo:
        return

    if _WIN32_DISPONIVEL:
        try:
            extra = _ctypes.c_ulong(0)
            ptr   = _ctypes.pointer(extra)

            ii  = _KEYBDINPUT(codigo, 0, 0, 0, ptr)
            x   = _INPUT(1, ii)
            _ctypes.windll.user32.SendInput(1, _ctypes.pointer(x), _ctypes.sizeof(x))

            time.sleep(0.04)

            ii2 = _KEYBDINPUT(codigo, 0, 0x0002, 0, ptr)
            x2  = _INPUT(1, ii2)
            _ctypes.windll.user32.SendInput(1, _ctypes.pointer(x2), _ctypes.sizeof(x2))
            return
        except Exception:
            pass

    # Fallback pyautogui
    try:
        import pyautogui
        pyautogui.press(_NOMES_PYAUTOGUI.get(acao, "playpause"))
    except Exception as e:
        print(f"[APPS]: Tecla de mídia falhou: {e}")


# ---------------------------------------------------------------------------
# Controle de Volume do Windows (pycaw)
# ---------------------------------------------------------------------------

class ControladorVolume:
    """Controla volume do sistema e de apps individuais via pycaw."""

    def volume_sistema(self, nivel: int) -> str:
        """Define volume do sistema (0-100)."""
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            iface   = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume  = cast(iface, POINTER(IAudioEndpointVolume))
            # Converte 0-100 para escala dB do Windows (-65.25 a 0)
            nivel_f = max(0.0, min(1.0, nivel / 100.0))
            volume.SetMasterVolumeLevelScalar(nivel_f, None)
            return f"Volume do sistema: {nivel}%."
        except ImportError:
            return "pycaw não instalado. pip install pycaw"
        except Exception as e:
            return f"Erro ao ajustar volume: {e}"

    def mudo_sistema(self, mutar: bool) -> str:
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            iface   = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume  = cast(iface, POINTER(IAudioEndpointVolume))
            volume.SetMute(1 if mutar else 0, None)
            return "Sistema mutado." if mutar else "Sistema desmutado."
        except ImportError:
            # Fallback: tecla de mute
            _tecla("volumemute")
            return "Mute ativado." if mutar else "Mute desativado."
        except Exception as e:
            return f"Erro: {e}"


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

class ControleSpotify:
    """
    Controla o Spotify no Windows.

    Modos (em ordem de prioridade):
      1. spotipy (API oficial) — controle total, busca precisa
      2. Teclas de mídia Win32 — play/pause/next sem API
    """

    _SPOTIFY_EXE = r"C:\Users\%USERNAME%\AppData\Roaming\Spotify\Spotify.exe"

    def __init__(self):
        self._sp   = None
        self._pronto = threading.Event()
        # Conecta à API em background — não bloqueia o primeiro uso
        threading.Thread(target=self._tentar_spotipy, daemon=True).start()

    def _tentar_spotipy(self):
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
            try:
                from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
            except ImportError:
                return
            if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
                return
            self._sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id     = SPOTIFY_CLIENT_ID,
                client_secret = SPOTIFY_CLIENT_SECRET,
                redirect_uri  = "http://localhost:8080",
                scope         = "user-modify-playback-state user-read-playback-state",
                open_browser  = False,
            ))
            print("\033[92m[APPS]: Spotify API conectado.\033[0m")
        except Exception:
            pass
        finally:
            self._pronto.set()

    def _garantir_aberto(self) -> bool:
        """Garante que o Spotify está aberto."""
        if _app_esta_aberto("Spotify"):
            return True
        caminho = os.path.expandvars(self._SPOTIFY_EXE)
        if os.path.exists(caminho):
            return _abrir_app(f'"{caminho}"')
        return _abrir_app("spotify")

    # ── API pública ──────────────────────────────────────────────────────────

    def play_pause(self) -> str:
        if self._sp:
            try:
                estado = self._sp.current_playback()
                if estado and estado.get("is_playing"):
                    self._sp.pause_playback()
                    return "Spotify pausado."
                else:
                    self._sp.start_playback()
                    return "Spotify tocando."
            except Exception:
                pass
        _tecla_midia("play_pause")
        return "Play/Pause enviado."

    def proxima(self) -> str:
        if self._sp:
            try:
                self._sp.next_track()
                return "Próxima música."
            except Exception:
                pass
        _tecla_midia("next")
        return "Próxima música."

    def anterior(self) -> str:
        if self._sp:
            try:
                self._sp.previous_track()
                return "Música anterior."
            except Exception:
                pass
        _tecla_midia("prev")
        return "Música anterior."

    def pausar(self) -> str:
        if self._sp:
            try:
                self._sp.pause_playback()
                return "Spotify pausado."
            except Exception:
                pass
        _tecla_midia("play_pause")
        return "Pausado."

    def tocar_musica(self, busca: str) -> str:
        """
        Toca uma música específica.
        Com spotipy: busca e toca direto.
        Sem spotipy: abre spotify://search no navegador.
        """
        if self._sp:
            try:
                resultados = self._sp.search(busca, type="track", limit=1)
                tracks = resultados.get("tracks", {}).get("items", [])
                if tracks:
                    uri = tracks[0]["uri"]
                    nome = tracks[0]["name"]
                    artista = tracks[0]["artists"][0]["name"]
                    self._sp.start_playback(uris=[uri])
                    return f"Tocando '{nome}' — {artista}."
                return f"Não encontrei '{busca}' no Spotify."
            except Exception as e:
                print(f"[APPS/Spotify]: Erro na API: {e}")

        # Sem API: abre URL de busca
        self._garantir_aberto()
        time.sleep(1)
        url = f"spotify:search:{busca.replace(' ', '%20')}"
        webbrowser.open(url)
        return f"Buscando '{busca}' no Spotify."

    def musica_atual(self) -> str:
        if self._sp:
            try:
                estado = self._sp.current_playback()
                if estado and estado.get("item"):
                    nome    = estado["item"]["name"]
                    artista = estado["item"]["artists"][0]["name"]
                    tocando = "tocando" if estado["is_playing"] else "pausado"
                    return f"'{nome}' — {artista} ({tocando})."
                return "Nada tocando no Spotify agora."
            except Exception:
                pass
        # Sem API: lê o título da janela
        try:
            import pygetwindow as gw
            for j in gw.getWindowsWithTitle("Spotify"):
                titulo = j.title
                if " - " in titulo and titulo != "Spotify":
                    return f"Tocando: {titulo}."
        except Exception:
            pass
        return "Não consegui ver o que está tocando."

    def volume(self, nivel: int) -> str:
        """Ajusta volume do Spotify (0–100). Requer API."""
        if self._sp:
            try:
                self._sp.volume(max(0, min(100, nivel)))
                return f"Volume do Spotify: {nivel}%."
            except Exception:
                pass
        return "Volume do Spotify requer autenticação. Configure SPOTIFY_CLIENT_ID no config.py."


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

class ControleYouTube:
    """Abre e controla YouTube no navegador padrão."""

    _BASE = "https://www.youtube.com"

    def buscar(self, query: str) -> str:
        url = f"{self._BASE}/results?search_query={query.replace(' ', '+')}"
        webbrowser.open(url)
        return f"Buscando '{query}' no YouTube."

    def abrir_video(self, url_ou_id: str) -> str:
        if "youtube.com" in url_ou_id or "youtu.be" in url_ou_id:
            webbrowser.open(url_ou_id)
        else:
            webbrowser.open(f"{self._BASE}/watch?v={url_ou_id}")
        return "Vídeo aberto no YouTube."

    def play_pause(self) -> str:
        """Envia espaço para o navegador focado (pausa/play no YouTube)."""
        try:
            import pyautogui
            pyautogui.press("space")
            return "Play/Pause no YouTube."
        except Exception:
            return "Não consegui controlar o YouTube. Clique na janela primeiro."


# ---------------------------------------------------------------------------
# WhatsApp Web
# ---------------------------------------------------------------------------

class ControleWhatsApp:
    """
    Envia mensagens via WhatsApp Web.
    Abre o navegador em wa.me/[número] ou usa whatsapp://
    """

    def enviar_mensagem(self, contato: str, mensagem: str) -> str:
        """
        Abre o WhatsApp Web com mensagem pré-preenchida.
        contato: nome do contato (abre via busca) ou número (+5511...)
        """
        mensagem_enc = mensagem.replace(" ", "%20")

        if contato.startswith("+") or contato.replace("-", "").isdigit():
            # Número direto
            numero = re.sub(r"[^\d+]", "", contato)
            url = f"https://wa.me/{numero}?text={mensagem_enc}"
        else:
            # Abre WhatsApp Web e deixa usuário escolher o contato
            url = f"https://web.whatsapp.com/"
            webbrowser.open(url)
            time.sleep(3)

            # Tenta usar atalho de busca
            _tecla("ctrl+alt+/")
            time.sleep(0.5)
            _digitar(contato)
            time.sleep(1)
            return (
                f"WhatsApp aberto. Procurei por '{contato}'. "
                "Selecione o contato e mande a mensagem."
            )

        webbrowser.open(url)
        return f"WhatsApp aberto para {contato}."

    def abrir(self) -> str:
        webbrowser.open("https://web.whatsapp.com/")
        return "WhatsApp Web aberto."


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

class ControleDiscord:
    """Controla o Discord via atalhos de teclado e foco de janela."""

    def mutar(self) -> str:
        if _focar_janela("Discord"):
            _tecla("ctrl+shift+m")
            return "Discord mutado."
        return "Discord não está aberto."

    def desmutar(self) -> str:
        if _focar_janela("Discord"):
            _tecla("ctrl+shift+m")
            return "Discord desmutado."
        return "Discord não está aberto."

    def desativar_camera(self) -> str:
        if _focar_janela("Discord"):
            _tecla("ctrl+shift+e")
            return "Câmera do Discord desativada."
        return "Discord não está aberto."

    def toggle_voz(self) -> str:
        """Muta/desmuta microfone."""
        if not _focar_janela("Discord"):
            return "Discord não está aberto."
        _tecla("ctrl+shift+m")
        return "Microfone do Discord alternado."

    def abrir(self) -> str:
        if _app_esta_aberto("Discord"):
            _focar_janela("Discord")
            return "Discord em foco."
        _abrir_app("discord")
        return "Discord aberto."


# ---------------------------------------------------------------------------
# Navegador (Chrome / Edge / Firefox)
# ---------------------------------------------------------------------------

class ControleNavegador:
    """Controla o navegador padrão via atalhos e webbrowser."""

    def abrir_url(self, url: str) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        webbrowser.open(url)
        return f"Abrindo {url}."

    def nova_aba(self, url: str = "") -> str:
        if _focar_janela("Chrome") or _focar_janela("Edge") or _focar_janela("Firefox"):
            _tecla("ctrl+t")
            if url:
                time.sleep(0.5)
                _digitar(url)
                _tecla("Return")
            return "Nova aba aberta."
        webbrowser.open(url or "about:blank")
        return "Nova aba aberta."

    def fechar_aba(self) -> str:
        if _focar_janela("Chrome") or _focar_janela("Edge") or _focar_janela("Firefox"):
            _tecla("ctrl+w")
            return "Aba fechada."
        return "Nenhum navegador em foco."

    def pesquisar(self, query: str) -> str:
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        webbrowser.open(url)
        return f"Pesquisando '{query}'."

    def voltar(self) -> str:
        if _focar_janela("Chrome") or _focar_janela("Edge") or _focar_janela("Firefox"):
            _tecla("alt+Left")
            return "Voltando."
        return "Nenhum navegador em foco."


# ---------------------------------------------------------------------------
# Explorer / Sistema de arquivos
# ---------------------------------------------------------------------------

class ControleExplorer:
    """Abre pastas e arquivos no Explorer."""

    _ATALHOS = {
        "downloads": os.path.expanduser("~/Downloads"),
        "documentos": os.path.expanduser("~/Documents"),
        "area de trabalho": os.path.expanduser("~/Desktop"),
        "desktop": os.path.expanduser("~/Desktop"),
        "musicas": os.path.expanduser("~/Music"),
        "imagens": os.path.expanduser("~/Pictures"),
        "videos": os.path.expanduser("~/Videos"),
    }

    def abrir_pasta(self, pasta: str) -> str:
        t = _norm(pasta)
        # Verifica atalhos conhecidos
        for nome, caminho in self._ATALHOS.items():
            if nome in t:
                subprocess.Popen(f'explorer "{caminho}"')
                return f"Abrindo pasta {nome}."
        # Tenta como caminho direto
        if os.path.exists(pasta):
            subprocess.Popen(f'explorer "{pasta}"')
            return f"Abrindo '{pasta}'."
        return f"Pasta '{pasta}' não encontrada."

    def abrir_arquivo(self, caminho: str) -> str:
        if os.path.exists(caminho):
            os.startfile(caminho)
            return f"Abrindo '{os.path.basename(caminho)}'."
        return f"Arquivo não encontrado: {caminho}"


# ---------------------------------------------------------------------------
# SiriusApps — ponto central de despacho
# ---------------------------------------------------------------------------

class SiriusApps:
    """
    Ponto central de interação com aplicativos externos.

    Lazy initialization — cada controlador (Spotify, YouTube, etc.)
    só é instanciado na primeira vez que for usado. O startup do Sirius
    não paga o custo de inicializar tudo, incluindo apps não utilizados.
    """

    def __init__(self):
        # Todos None — instanciados lazily em _obter_*()
        self._spotify_obj   = None
        self._youtube_obj   = None
        self._whatsapp_obj  = None
        self._discord_obj   = None
        self._navegador_obj = None
        self._explorer_obj  = None
        self._volume_obj    = None
        print("\033[92m[APPS]: Controlador de apps pronto (lazy).\033[0m")

    # Accessors lazy — instanciam apenas quando chamados pela primeira vez
    @property
    def _spotify(self):
        if self._spotify_obj is None:
            self._spotify_obj = ControleSpotify()
        return self._spotify_obj

    @property
    def _youtube(self):
        if self._youtube_obj is None:
            self._youtube_obj = ControleYouTube()
        return self._youtube_obj

    @property
    def _whatsapp(self):
        if self._whatsapp_obj is None:
            self._whatsapp_obj = ControleWhatsApp()
        return self._whatsapp_obj

    @property
    def _discord(self):
        if self._discord_obj is None:
            self._discord_obj = ControleDiscord()
        return self._discord_obj

    @property
    def _navegador(self):
        if self._navegador_obj is None:
            self._navegador_obj = ControleNavegador()
        return self._navegador_obj

    @property
    def _explorer(self):
        if self._explorer_obj is None:
            self._explorer_obj = ControleExplorer()
        return self._explorer_obj

    @property
    def _volume(self):
        if self._volume_obj is None:
            self._volume_obj = ControladorVolume()
        return self._volume_obj

    # -----------------------------------------------------------------------
    # Detecção de comando
    # -----------------------------------------------------------------------

    _TRIGGERS_SPOTIFY = frozenset({
        "spotify", "musica", "música", "toca ", "tocar", "coloca musica",
        "coloca música", "play pause", "proxima musica", "próxima música",
        "musica anterior", "música anterior", "pausa a musica",
        "pausa a música", "para a musica", "para a música",
        "proxima", "próxima", "anterior", "que musica", "que música",
        "o que ta tocando", "o que está tocando",
    })
    _TRIGGERS_YOUTUBE = frozenset({
        "youtube", "abre o youtube", "pesquisa no youtube",
        "busca no youtube", "video no youtube", "vídeo no youtube",
    })
    _TRIGGERS_WHATSAPP = frozenset({
        "whatsapp", "manda mensagem para", "manda msg para",
        "envia mensagem para", "manda zap para",
        "no whatsapp", "pelo whatsapp",
    })
    _TRIGGERS_DISCORD = frozenset({
        "discord", "muta o discord", "desmuta o discord",
        "microfone do discord", "desliga camera do discord",
    })
    _TRIGGERS_NAVEGADOR = frozenset({
        "chrome", "firefox", "edge", "navegador",
        "abre o site", "abre o chrome", "abre o firefox",
        "nova aba", "fecha a aba", "pesquisa no google",
        "busca no google", "abre o google",
    })
    _TRIGGERS_EXPLORER = frozenset({
        "pasta downloads", "pasta documentos", "pasta musicas",
        "pasta imagens", "pasta videos", "area de trabalho",
        "abre a pasta", "abrir pasta", "explorador",
    })
    _TRIGGERS_VOLUME = frozenset({
        "volume do sistema", "muta o sistema", "desmuta o sistema",
        "sem som", "coloca no mudo",
    })

    # Union de todos os triggers — verificação rápida antes de despachar
    _TODOS_TRIGGERS = (
        _TRIGGERS_SPOTIFY | _TRIGGERS_YOUTUBE | _TRIGGERS_WHATSAPP |
        _TRIGGERS_DISCORD | _TRIGGERS_NAVEGADOR | _TRIGGERS_EXPLORER |
        _TRIGGERS_VOLUME
    )

    def e_comando_app(self, texto: str) -> bool:
        """Retorna True se o texto é um comando para um app externo."""
        t = _norm(texto)
        return any(tr in t for tr in self._TODOS_TRIGGERS)

    def processar(self, texto: str) -> Optional[str]:
        """
        Processa um comando de app e retorna a resposta,
        ou None se não reconheceu.
        """
        t = _norm(texto)

        # ── Spotify ───────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_SPOTIFY):
            return self._processar_spotify(t, texto)

        # ── YouTube ───────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_YOUTUBE):
            return self._processar_youtube(t)

        # ── WhatsApp ──────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_WHATSAPP):
            return self._processar_whatsapp(t, texto)

        # ── Discord ───────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_DISCORD):
            return self._processar_discord(t)

        # ── Navegador ─────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_NAVEGADOR):
            return self._processar_navegador(t)

        # ── Explorer ──────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_EXPLORER):
            return self._processar_explorer(t, texto)

        # ── Volume ────────────────────────────────────────────────────────
        if any(tr in t for tr in self._TRIGGERS_VOLUME):
            return self._processar_volume(t)

        return None

    # -----------------------------------------------------------------------
    # Processadores específicos
    # -----------------------------------------------------------------------

    def _processar_spotify(self, t: str, texto_orig: str) -> str:
        # Música específica
        m = re.search(
            r"(?:toca|tocar|coloca|reproduz|play)\s+"
            r"(?:a\s+musica\s+|a\s+música\s+|o\s+som\s+de\s+)?"
            r"(.+?)(?:\s+no\s+spotify)?$",
            t
        )
        if m:
            busca = m.group(1).strip()
            if busca and busca not in {"musica", "música", "som", "spotify"}:
                return self._spotify.tocar_musica(busca)

        if any(p in t for p in ["pausa", "para a musica", "para a música", "pause"]):
            return self._spotify.pausar()
        if any(p in t for p in ["proxima", "próxima", "pula"]):
            return self._spotify.proxima()
        if any(p in t for p in ["anterior", "volta musica", "musica anterior"]):
            return self._spotify.anterior()
        if any(p in t for p in ["play", "toca", "continua"]):
            return self._spotify.play_pause()
        if any(p in t for p in ["que musica", "que está tocando", "o que ta tocando"]):
            return self._spotify.musica_atual()

        # Volume do Spotify
        m_vol = re.search(r"volume\s+(?:do\s+spotify\s+)?(?:para\s+)?(\d+)", t)
        if m_vol:
            return self._spotify.volume(int(m_vol.group(1)))

        # Coloca uma música (genérico — só abre)
        if any(p in t for p in ["coloca musica", "coloca música", "bota musica"]):
            return self._spotify.play_pause()

        return "Comando do Spotify não reconhecido. Tente: 'toca Bohemian Rhapsody no Spotify'."

    def _processar_youtube(self, t: str) -> str:
        m = re.search(
            r"(?:pesquisa|busca|procura|abre|mostra)\s+"
            r"(?:no\s+youtube\s+)?(.+?)(?:\s+no\s+youtube)?$",
            t
        )
        if m:
            query = m.group(1).strip()
            if query and query not in {"youtube", "o youtube"}:
                return self._youtube.buscar(query)
        return self._youtube.buscar("")

    def _processar_whatsapp(self, t: str, texto_orig: str) -> str:
        # "manda mensagem para João: oi tudo bem"
        m = re.search(
            r"(?:manda|envia|envia)\s+(?:mensagem|msg|zap)\s+"
            r"(?:para|pro|pra)\s+(.+?)(?::|dizendo|falando|:\s*)(.+)",
            t
        )
        if m:
            contato  = m.group(1).strip()
            mensagem = m.group(2).strip()
            return self._whatsapp.enviar_mensagem(contato, mensagem)

        # Só abre para o contato
        m2 = re.search(
            r"(?:manda|abre|whatsapp)\s+(?:para|pro|pra|do|da)?\s*(.+)",
            t
        )
        if m2:
            contato = m2.group(1).strip()
            if contato not in {"whatsapp", ""}:
                return self._whatsapp.enviar_mensagem(contato, "")

        return self._whatsapp.abrir()

    def _processar_discord(self, t: str) -> str:
        if any(p in t for p in ["muta", "mute", "silencia", "desliga microfone"]):
            return self._discord.mutar()
        if any(p in t for p in ["desmuta", "unmute", "liga microfone"]):
            return self._discord.desmutar()
        if any(p in t for p in ["desliga camera", "desliga câmera", "camera off"]):
            return self._discord.desativar_camera()
        return self._discord.abrir()

    def _processar_navegador(self, t: str) -> str:
        # Nova aba com URL
        m = re.search(r"(?:abre|nova aba)\s+(?:o\s+site\s+|a\s+pagina\s+)?(.+)", t)
        if m:
            alvo = m.group(1).strip()
            nomes_nav = {"chrome", "firefox", "edge", "navegador", "o chrome",
                         "o firefox", "o edge", "o navegador"}
            if alvo in nomes_nav:
                return self._navegador.nova_aba()
            if "." in alvo or alvo.startswith("http"):
                return self._navegador.abrir_url(alvo)

        if any(p in t for p in ["fecha a aba", "fechar aba", "fecha aba"]):
            return self._navegador.fechar_aba()
        if any(p in t for p in ["nova aba"]):
            return self._navegador.nova_aba()

        m_pesq = re.search(
            r"(?:pesquisa|busca|procura)\s+(?:no\s+google\s+)?(.+)", t
        )
        if m_pesq:
            return self._navegador.pesquisar(m_pesq.group(1).strip())

        return self._navegador.nova_aba()

    def _processar_explorer(self, t: str, texto_orig: str) -> str:
        m = re.search(r"(?:abre|abrir|vai para)\s+(?:a\s+)?pasta\s+(.+)", t)
        if m:
            return self._explorer.abrir_pasta(m.group(1).strip())
        for atalho in self._explorer._ATALHOS:
            if atalho in t:
                return self._explorer.abrir_pasta(atalho)
        return self._explorer.abrir_pasta(texto_orig)

    def _processar_volume(self, t: str) -> str:
        if any(p in t for p in ["muta", "sem som", "no mudo", "mudo"]):
            return self._volume.mudo_sistema(True)
        if any(p in t for p in ["desmuta", "tira o mudo"]):
            return self._volume.mudo_sistema(False)
        m = re.search(r"volume\s+(?:para|em)?\s*(\d+)", t)
        if m:
            return self._volume.volume_sistema(int(m.group(1)))
        return "Diga: 'volume para 50' ou 'muta o sistema'."

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    def status(self) -> str:
        spotify_api = "✓ API" if self._spotify._sp else "✗ teclas de mídia"
        return (
            f"Apps: Spotify ({spotify_api}), YouTube, WhatsApp Web, "
            f"Discord, Navegador, Explorer. "
            f"Para Spotify completo: configure SPOTIFY_CLIENT_ID no config.py"
        )


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_apps_instance: Optional[SiriusApps] = None

def get_apps() -> SiriusApps:
    global _apps_instance
    if _apps_instance is None:
        _apps_instance = SiriusApps()
    return _apps_instance