"""
cerebro_cliente.py — Cérebro do app Sirius (modo cliente)

Divide responsabilidades entre local e servidor:

  LOCAL (executa no PC onde o app está rodando):
    ├── controle_pc      → abrir/fechar apps, janelas, volume, mouse/teclado
    ├── sirius_visao     → screenshot, OCR da tela
    ├── sirius_apps      → Spotify, YouTube, WhatsApp, Discord
    ├── sirius_camera    → câmera local
    ├── sirius_tts       → fala em voz no PC local
    ├── interface        → abre/fecha a interface gráfica local
    └── respostas rápidas → saudações, sem precisar do servidor

  REMOTO (delega ao servidor via HTTP/WebSocket):
    ├── memoria          → histórico, dúvidas, macros, estado
    ├── processar()      → MoE, agentes, classificador neural
    ├── aprendizado      → autodidata, retreino, embeddings
    └── proativo         → lembretes, alertas, briefing

Fluxo de um comando:
  1. processar(texto)
  2. → É controle local? → executa localmente → retorna
  3. → Não? → envia para servidor → recebe resposta → retorna

Uso:
    from cerebro_cliente import CerebroCliente

    cerebro = CerebroCliente(servidor_url="192.168.1.10:5000")
    resposta = cerebro.processar("abre o chrome")   # local
    resposta = cerebro.processar("que dia é hoje")  # remoto
"""

import os
import sys
import re
import time
import threading
import requests
import unicodedata
from typing import Optional

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)


def _norm(texto: str) -> str:
    """Normaliza texto para comparação."""
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Respostas rápidas locais — sem precisar do servidor
# ---------------------------------------------------------------------------

RESPOSTAS_RAPIDAS = {
    "bom dia":   "Bom dia, chefe. Sistemas operacionais.",
    "boa tarde": "Boa tarde, chefe. Pronto para os comandos.",
    "boa noite": "Boa noite, chefe. Ainda estou aqui.",
    "oi":        "Oi. Pode falar.",
    "ola":       "Olá. Aguardando.",
    "tudo bem":  "Sistemas nominais. E você?",
    "valeu":     "Disponha, chefe.",
    "obrigado":  "De nada, chefe.",
    "tchau":     "Até mais.",
}

# ---------------------------------------------------------------------------
# Triggers de controle local — detecta ANTES de ir ao servidor
# ---------------------------------------------------------------------------

# Controle de PC — sempre local (afeta o PC onde o app está)
_TRIGGERS_CONTROLE_PC = frozenset({
    # Janelas
    "fecha ", "fechar ", "minimiza", "maximiza", "abre ", "abrir ",
    "executa ", "inicia ", "mata ", "kill ",
    # Volume / mídia
    "volume", "mudo", "muta", "silencia",
    "pausa", "parar musica", "proxima", "anterior",
    "aumenta o som", "diminui o som",
    # Sistema
    "screenshot", "print da tela", "captura de tela",
    "desliga", "reinicia", "hiberna", "suspende",
    # Mouse / teclado
    "clica", "digita ", "pressiona", "tecla ",
    # Área de trabalho
    "copia ", "cola ", "recorta ", "clipboard",
    # Navegador
    "abre o chrome", "abre o firefox", "abre o edge",
    "abre o navegador", "nova aba",
})

# Apps locais — sempre local
_TRIGGERS_APPS_LOCAIS = frozenset({
    "spotify", "youtube", "discord", "whatsapp",
    "telegram", "steam", "obs", "vlc",
    "reproduz ", "toca ", "pause no spotify",
})

# Câmera — sempre local
_TRIGGERS_CAMERA = frozenset({
    "tira foto", "foto", "quem esta", "quem ta na minha frente",
    "le o qr", "leia o qr", "scanneia",
})

# Visão de tela — sempre local
_TRIGGERS_VISAO = frozenset({
    "o que tem na tela", "o que esta na tela", "leia a tela",
    "le a tela", "analisa a tela", "descreve a tela",
    "o que diz na tela",
})

# Interface — sempre local
_TRIGGERS_INTERFACE = frozenset({
    "abre a interface", "fecha a interface", "mostra a interface",
    "modo wallpaper", "modo janela", "modo tela cheia",
})


def _e_controle_local(texto: str) -> bool:
    """Retorna True se o comando deve ser executado localmente."""
    t = _norm(texto)
    return (
        any(trigger in t for trigger in _TRIGGERS_CONTROLE_PC) or
        any(trigger in t for trigger in _TRIGGERS_APPS_LOCAIS) or
        any(trigger in t for trigger in _TRIGGERS_CAMERA)      or
        any(trigger in t for trigger in _TRIGGERS_VISAO)       or
        any(trigger in t for trigger in _TRIGGERS_INTERFACE)
    )


# ---------------------------------------------------------------------------
# Executor local — roda os módulos locais
# ---------------------------------------------------------------------------

class ExecutorLocal:
    """
    Executa comandos que afetam o PC local.
    Carrega cada módulo de forma lazy — só quando necessário.
    """

    def __init__(self):
        self._control    = None
        self._apps       = None
        self._camera     = None
        self._visao      = None
        self._tts        = None
        self._interface  = None

    # ── Loaders lazy ──────────────────────────────────────────────────────

    def _get_control(self):
        if not self._control:
            try:
                from controle_pc import SiriusControl
                self._control = SiriusControl()
            except Exception as e:
                print(f"[LOCAL]: controle_pc indisponível: {e}")
        return self._control

    def _get_apps(self):
        if not self._apps:
            try:
                from sirius_apps import SiriusApps
                self._apps = SiriusApps()
            except Exception as e:
                print(f"[LOCAL]: sirius_apps indisponível: {e}")
        return self._apps

    def _get_camera(self):
        if not self._camera:
            try:
                from sirius_camera import SiriusCamera
                self._camera = SiriusCamera()
            except Exception as e:
                print(f"[LOCAL]: sirius_camera indisponível: {e}")
        return self._camera

    def _get_visao(self):
        if not self._visao:
            try:
                from sirius_visao import SiriusVisao
                self._visao = SiriusVisao()
            except Exception as e:
                print(f"[LOCAL]: sirius_visao indisponível: {e}")
        return self._visao

    def falar(self, texto: str):
        """Fala em voz no PC local (TTS)."""
        def _t():
            try:
                from sirius_tts import get_tts
                get_tts().falar(texto)
            except Exception:
                try:
                    import pyttsx3
                    e = pyttsx3.init()
                    e.say(texto)
                    e.runAndWait()
                except Exception:
                    pass
        threading.Thread(target=_t, daemon=True).start()

    # ── Processamento de comandos locais ──────────────────────────────────

    def processar(self, comando: str) -> Optional[str]:
        """
        Tenta executar o comando localmente.
        Retorna string com resposta, ou None se não souber.
        """
        t = _norm(comando)

        # Controle de PC
        ctrl = self._get_control()
        if ctrl:
            try:
                from controle_pc import _parsear_controle_pc
                resp = _parsear_controle_pc(comando, ctrl)
                if resp and not _e_falha(resp):
                    return resp
            except Exception as e:
                print(f"[LOCAL]: controle_pc erro: {e}")

        # Apps locais (Spotify, YouTube, Discord…)
        apps = self._get_apps()
        if apps:
            try:
                if apps.e_comando_app(t):
                    resp = apps.processar(comando)
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL]: sirius_apps erro: {e}")

        # Câmera
        camera = self._get_camera()
        if camera:
            try:
                if camera.e_comando_camera(t):
                    resp = camera.processar_comando(comando)
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL]: camera erro: {e}")

        # Visão de tela
        visao = self._get_visao()
        if visao:
            try:
                if any(tr in t for tr in _TRIGGERS_VISAO):
                    resp = visao.ler_tela()
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL]: visao erro: {e}")

        return None


def _e_falha(texto: str) -> bool:
    """Detecta se a resposta indica falha (para não retornar lixo)."""
    t = _norm(texto)
    return any(p in t for p in {
        "nao sei", "nao encontrei", "desculpe",
        "nao tenho acesso", "erro ao", "falhou",
    })


# ---------------------------------------------------------------------------
# Cliente HTTP/WebSocket — comunica com o servidor
# ---------------------------------------------------------------------------

class ClienteServidor:
    """
    Envia comandos ao servidor Sirius e recebe respostas.

    REST:  POST http://IP:5000/comando
    WS:    ws://IP:5000/ws  (callbacks em tempo real)
    """

    TIMEOUT_PADRAO = 15   # segundos

    def __init__(self, url: str = ""):
        self._url         = self._normalizar_url(url)
        self._disponivel  = False
        self._callbacks   : list = []   # chamados quando servidor envia algo

    def _normalizar_url(self, url: str) -> str:
        url = url.strip().rstrip("/")
        if not url:
            return ""
        if not url.startswith("http"):
            url = f"http://{url}"
        return url

    def configurar(self, url: str):
        self._url        = self._normalizar_url(url)
        self._disponivel = False
        self.testar_conexao()

    def testar_conexao(self) -> bool:
        if not self._url:
            return False
        try:
            r = requests.get(f"{self._url}/status", timeout=3)
            self._disponivel = r.status_code == 200
            if self._disponivel:
                print(f"\033[92m[CLIENTE]: Servidor em {self._url} disponível.\033[0m")
            return self._disponivel
        except Exception:
            self._disponivel = False
            return False

    @property
    def disponivel(self) -> bool:
        return self._disponivel and bool(self._url)

    def enviar_comando(self, texto: str) -> Optional[str]:
        """
        Envia comando ao servidor e retorna a resposta.
        Bloqueia até receber (máx TIMEOUT_PADRAO segundos).
        """
        if not self.disponivel:
            return None
        try:
            r = requests.post(
                f"{self._url}/comando",
                json={"texto": texto},
                timeout=self.TIMEOUT_PADRAO,
            )
            if r.status_code == 200:
                dados = r.json()
                return dados.get("resposta") or dados.get("texto")
            return None
        except requests.Timeout:
            print(f"[CLIENTE]: Timeout ao enviar comando para {self._url}")
            return None
        except Exception as e:
            print(f"[CLIENTE]: Erro ao enviar comando: {e}")
            self._disponivel = False
            return None

    def obter_historico(self, n: int = 20) -> list:
        if not self.disponivel:
            return []
        try:
            r = requests.get(f"{self._url}/historico",
                              params={"limit": n}, timeout=5)
            if r.status_code == 200:
                return r.json().get("historico", [])
        except Exception:
            pass
        return []

    def obter_status(self) -> dict:
        if not self._url:
            return {}
        try:
            r = requests.get(f"{self._url}/status", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    def criar_lembrete(self, descricao: str, hora: int,
                       minuto: int, repetir: bool = False) -> Optional[str]:
        if not self.disponivel:
            return "Servidor indisponível."
        try:
            r = requests.post(
                f"{self._url}/lembrete",
                json={"descricao": descricao, "hora": hora,
                      "minuto": minuto, "repetir": repetir},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("resposta")
        except Exception as e:
            return f"Erro: {e}"
        return None


# ---------------------------------------------------------------------------
# CerebroCliente — ponto central de coordenação
# ---------------------------------------------------------------------------

class CerebroCliente:
    """
    Cérebro do app em modo cliente.

    Decisão por prioridade:
      1. Resposta rápida local (saudações)     → retorna imediatamente
      2. Controle local detectado              → executa localmente
      3. Servidor disponível                   → delega ao servidor
      4. Servidor indisponível + fallback local → tenta controle local
      5. Nenhum disponível                     → mensagem de erro

    Transparente para quem usa:
      resposta = cerebro.processar("abre o chrome")     # local
      resposta = cerebro.processar("que horas são")     # remoto
      resposta = cerebro.processar("me lembra às 15h")  # remoto
    """

    def __init__(self, servidor_url: str = "",
                 falar_local: bool = True):
        self._local        = ExecutorLocal()
        self._servidor     = ClienteServidor(servidor_url)
        self._falar_local  = falar_local
        self._ultimo_estado= "STANDBY"

        # Callbacks para a interface (estado, resposta, log)
        self._cb_estado    = None
        self._cb_resposta  = None
        self._cb_log       = None

        # Testa conexão em background
        if servidor_url:
            threading.Thread(target=self._servidor.testar_conexao,
                              daemon=True).start()

    # ── Configuração ──────────────────────────────────────────────────────

    def configurar_servidor(self, url: str):
        """Atualiza a URL do servidor em tempo de execução."""
        self._servidor.configurar(url)

    def registrar_callback(self, callback_estado=None,
                            callback_resposta=None,
                            callback_log=None):
        if callback_estado:   self._cb_estado   = callback_estado
        if callback_resposta: self._cb_resposta  = callback_resposta
        if callback_log:      self._cb_log       = callback_log

    # ── Processamento principal ───────────────────────────────────────────

    def processar(self, texto: str,
                  forcar_processamento: bool = False) -> Optional[str]:
        """
        Processa um comando decidindo onde executar.
        Thread-safe — pode ser chamado de qualquer thread.
        """
        if not texto or not texto.strip():
            return None

        # Remove wake word para análise
        cmd = re.sub(r"[,!\.\s]*sirius[,!\.\s]*", " ",
                     texto, flags=re.IGNORECASE).strip()
        if not cmd:
            return "Diga, chefe."

        self._set_estado("PROCESSANDO")

        try:
            resposta = self._decidir_e_executar(cmd, texto)
        except Exception as e:
            print(f"[CEREBRO CLIENTE]: Erro: {e}")
            resposta = "Erro ao processar o comando."

        self._set_estado("STANDBY")

        if resposta:
            # TTS local
            if self._falar_local:
                self._local.falar(resposta)
            # Callback de resposta
            if self._cb_resposta:
                try:
                    self._cb_resposta(resposta)
                except Exception:
                    pass

        return resposta

    def _decidir_e_executar(self, cmd: str,
                             texto_original: str) -> Optional[str]:
        t = _norm(cmd)

        # 1. Resposta rápida local
        resp_rapida = RESPOSTAS_RAPIDAS.get(t)
        if resp_rapida:
            return resp_rapida

        # 2. Interface local (modo wallpaper, janela, etc.)
        if any(tr in t for tr in _TRIGGERS_INTERFACE):
            return self._processar_interface(cmd)

        # 3. Comando de controle local explícito
        if _e_controle_local(cmd):
            resp = self._local.processar(cmd)
            if resp:
                return resp
            # Não achou localmente → tenta servidor mesmo assim

        # 4. Delega ao servidor
        if self._servidor.disponivel:
            resp = self._servidor.enviar_comando(texto_original)
            if resp:
                return resp

        # 5. Servidor indisponível — tenta local como fallback
        if not self._servidor.disponivel:
            resp = self._local.processar(cmd)
            if resp:
                return resp
            return (
                "Servidor Sirius indisponível. "
                "Verifique a conexão com o PC principal."
            )

        return "Não consegui processar o comando."

    def _processar_interface(self, cmd: str) -> Optional[str]:
        """Controla a interface gráfica local."""
        t = _norm(cmd)
        try:
            # Importa lazy — interface pode não estar disponível
            if "wallpaper" in t:
                from sirius_wallpaper import iniciar_wallpaper, MODO_WALLPAPER
                threading.Thread(
                    target=lambda: iniciar_wallpaper(modo=MODO_WALLPAPER),
                    daemon=True
                ).start()
                return "Modo wallpaper ativado."
            if "janela" in t or "interface" in t:
                from interface import SiriusInterfaceMainWindow
                return "Interface aberta."
        except Exception as e:
            return f"Erro ao controlar interface: {e}"
        return None

    # ── Estado ───────────────────────────────────────────────────────────

    def _set_estado(self, estado: str):
        self._ultimo_estado = estado
        if self._cb_estado:
            try:
                self._cb_estado(estado)
            except Exception:
                pass

    # ── Atalhos para a interface ──────────────────────────────────────────

    @property
    def servidor_disponivel(self) -> bool:
        return self._servidor.disponivel

    @property
    def servidor_url(self) -> str:
        return self._servidor._url

    def obter_historico(self, n: int = 20) -> list:
        return self._servidor.obter_historico(n)

    def obter_status_servidor(self) -> dict:
        return self._servidor.obter_status()

    def criar_lembrete(self, descricao: str, hora: int,
                       minuto: int, repetir: bool = False) -> str:
        return self._servidor.criar_lembrete(
            descricao, hora, minuto, repetir
        ) or "Lembrete criado."

    def reconectar(self) -> bool:
        """Testa reconexão com o servidor manualmente."""
        return self._servidor.testar_conexao()


# ---------------------------------------------------------------------------
# Singleton global (para o app usar um único cerebro)
# ---------------------------------------------------------------------------

_cerebro_global: Optional[CerebroCliente] = None


def get_cerebro() -> Optional[CerebroCliente]:
    return _cerebro_global


def criar_cerebro(servidor_url: str = "",
                  falar_local: bool = True) -> CerebroCliente:
    global _cerebro_global
    _cerebro_global = CerebroCliente(
        servidor_url=servidor_url,
        falar_local=falar_local,
    )
    return _cerebro_global