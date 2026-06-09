"""
cerebro_cliente.py — Cérebro do app Sirius (modo cliente) — v2.0
=================================================================

Novidades em relação à v1:
  • executar_acao_local(dados_pacote) — executa pacotes JSON do tipo
    'exec_local' vindos do servidor via WebSocket
  • Motor de Proatividade — pacotes 'proativo' acionam TTS imediatamente
  • ColetorContexto — coleta bateria, rede, CPU, RAM e empacota como feedback
  • Tratamento de Erros Robusto — toda falha retorna mensagem amigável
    para o log da interface em vez de travar silenciosamente

Divide responsabilidades entre local e servidor:

  LOCAL (executa no PC onde o app está rodando):
    ├── controle_pc      → abrir/fechar apps, janelas, volume, mouse/teclado
    ├── sirius_visao     → screenshot, OCR da tela
    ├── sirius_apps      → Spotify, YouTube, WhatsApp, Discord
    ├── sirius_camera    → câmera local
    ├── audio_handler    → TTS/voz local (SiriusAudio)
    ├── interface        → abre/fecha a interface gráfica local
    └── respostas rápidas→ saudações sem precisar do servidor

  REMOTO (delega ao servidor via HTTP):
    ├── memoria          → histórico, macros, estado
    ├── processar()      → MoE, agentes, classificador neural
    ├── aprendizado      → autodidata, retreino, embeddings
    └── proativo         → lembretes, alertas, briefing

Fluxo de um pacote WebSocket recebido:
  interface_cliente → pacote JSON → executar_acao_local()
     ├── tipo == 'exec_local'  → controle_pc.executar(acao, params)
     ├── tipo == 'proativo'    → audio_handler.falar(texto) imediatamente
     └── tipo == 'outro'       → processar(texto) (fluxo clássico)

Uso:
    from cerebro_cliente import CerebroCliente, criar_cerebro

    cerebro = criar_cerebro(servidor_url="192.168.1.10:5000")
    cerebro.registrar_callback(callback_feedback=minha_fn_envia_ws)
    resultado = cerebro.executar_acao_local(pacote_json_do_servidor)
    resposta  = cerebro.processar("que dia é hoje")
"""

from __future__ import annotations

import os
import sys
import re
import threading
import unicodedata
import platform
import socket
from datetime import datetime
from typing import Any, Callable, Optional

import requests

# =============================================================================
# Detecção de ambiente headless (servidor, Raspberry Pi sem display)
# =============================================================================

def _is_headless() -> bool:
    """
    Retorna True se o ambiente não tem display gráfico disponível.
    Usado para desabilitar câmera, wallpaper e captura de tela.

    Detecta:
      • Linux sem DISPLAY nem WAYLAND_DISPLAY
      • Variável SIRIUS_HEADLESS=1 (forçado pelo usuário)
      • Raspberry Pi sem X server
    """
    # Forçado via variável de ambiente
    if os.environ.get("SIRIUS_HEADLESS", "").strip() == "1":
        return True

    # Windows e macOS sempre têm display
    if platform.system() in ("Windows", "Darwin"):
        return False

    # Linux — verifica variáveis de display
    tem_x11     = bool(os.environ.get("DISPLAY"))
    tem_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    return not (tem_x11 or tem_wayland)

# ── Path setup ─────────────────────────────────────────────────────────────── #
_DIR_SRC = os.path.dirname(os.path.abspath(__file__))
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)


# ===========================================================================
# Utilitários
# ===========================================================================

def _norm(texto: str) -> str:
    """Normaliza texto para comparação (remove acentos, caixa, espaços extras)."""
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _amigavel(acao: str, erro: Exception) -> str:
    """Converte exceção em mensagem legível para o log da interface."""
    tabela = {
        FileNotFoundError:   f"Arquivo ou programa não encontrado ao tentar '{acao}'.",
        PermissionError:     f"Sem permissão para executar '{acao}'.",
        TimeoutError:        f"Tempo esgotado ao executar '{acao}'.",
        ModuleNotFoundError: f"Módulo necessário não instalado para '{acao}'.",
        ConnectionError:     f"Falha de conexão ao executar '{acao}'.",
    }
    for tipo, msg in tabela.items():
        if isinstance(erro, tipo):
            return msg
    return f"Erro ao executar '{acao}': {type(erro).__name__} — {erro}"


# ===========================================================================
# Respostas rápidas locais
# ===========================================================================

RESPOSTAS_RAPIDAS: dict[str, str] = {
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


# ===========================================================================
# Triggers de controle local
# ===========================================================================

_TRIGGERS_CONTROLE_PC = frozenset({
    "fecha ", "fechar ", "minimiza", "maximiza", "abre ", "abrir ",
    "executa ", "inicia ", "mata ", "kill ",
    "volume", "mudo", "muta", "silencia",
    "pausa", "parar musica", "proxima", "anterior",
    "aumenta o som", "diminui o som",
    "screenshot", "print da tela", "captura de tela",
    "desliga", "reinicia", "hiberna", "suspende",
    "clica", "digita ", "pressiona", "tecla ",
    "copia ", "cola ", "recorta ", "clipboard",
    "abre o chrome", "abre o firefox", "abre o edge",
    "abre o navegador", "nova aba",
})
_TRIGGERS_APPS_LOCAIS = frozenset({
    "spotify", "youtube", "discord", "whatsapp",
    "telegram", "steam", "obs", "vlc",
    "reproduz ", "toca ", "pause no spotify",
})
_TRIGGERS_CAMERA = frozenset({
    "tira foto", "foto", "quem esta", "quem ta na minha frente",
    "le o qr", "leia o qr", "scanneia",
})
_TRIGGERS_VISAO = frozenset({
    "o que tem na tela", "o que esta na tela", "leia a tela",
    "le a tela", "analisa a tela", "descreve a tela",
    "o que diz na tela",
})
_TRIGGERS_INTERFACE = frozenset({
    "abre a interface", "fecha a interface", "mostra a interface",
    "modo wallpaper", "modo janela", "modo tela cheia",
})


def _e_controle_local(texto: str) -> bool:
    t = _norm(texto)
    return (
        any(tr in t for tr in _TRIGGERS_CONTROLE_PC) or
        any(tr in t for tr in _TRIGGERS_APPS_LOCAIS) or
        any(tr in t for tr in _TRIGGERS_CAMERA)      or
        any(tr in t for tr in _TRIGGERS_VISAO)       or
        any(tr in t for tr in _TRIGGERS_INTERFACE)
    )


def _e_falha(texto: str) -> bool:
    t = _norm(texto)
    return any(p in t for p in {
        "nao sei", "nao encontrei", "desculpe",
        "nao tenho acesso", "erro ao", "falhou",
    })


# ===========================================================================
# ColetorContexto — snapshot do sistema local para o pacote de feedback
# ===========================================================================

class ColetorContexto:
    """
    Coleta dados básicos do sistema local para incluir no pacote de feedback
    enviado ao servidor após cada execução.

    Campos retornados:
      bateria   : {"percent": float, "carregando": bool} ou None
      rede      : bool (conectividade básica via socket)
      cpu_pct   : float | None  (requer psutil)
      ram_pct   : float | None  (requer psutil)
      plataforma: str
      hostname  : str
      horario   : str  (ISO 8601)
    """

    _PING_HOST    = ("8.8.8.8", 53)
    _PING_TIMEOUT = 2

    @staticmethod
    def coletar() -> dict[str, Any]:
        dados: dict[str, Any] = {
            "plataforma": platform.system(),
            "hostname":   socket.gethostname(),
            "horario":    datetime.now().isoformat(timespec="seconds"),
            "bateria":    None,
            "rede":       False,
            "cpu_pct":    None,
            "ram_pct":    None,
        }

        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat:
                dados["bateria"] = {
                    "percent":    round(bat.percent, 1),
                    "carregando": bat.power_plugged,
                }
            dados["cpu_pct"] = psutil.cpu_percent(interval=0.2)
            dados["ram_pct"] = psutil.virtual_memory().percent
        except Exception:
            pass

        try:
            s = socket.create_connection(
                ColetorContexto._PING_HOST,
                timeout=ColetorContexto._PING_TIMEOUT,
            )
            s.close()
            dados["rede"] = True
        except OSError:
            dados["rede"] = False

        return dados


# ===========================================================================
# ExecutorLocal — executa módulos locais
# ===========================================================================

class ExecutorLocal:
    """
    Executa comandos que afetam o PC local.
    Carrega cada módulo de forma lazy — só quando necessário.
    """

    def __init__(self):
        self._control = None
        self._apps    = None
        self._camera  = None
        self._visao   = None
        self._audio   = None

    # ── Loaders lazy ──────────────────────────────────────────────────── #

    def _get_control(self):
        if self._control is None:
            try:
                from controle_pc import SiriusControl
                self._control = SiriusControl()
            except Exception as e:
                print(f"[LOCAL] controle_pc indisponível: {e}")
        return self._control

    def _get_apps(self):
        if self._apps is None:
            try:
                from sirius_apps import SiriusApps
                self._apps = SiriusApps()
            except Exception as e:
                print(f"[LOCAL] sirius_apps indisponível: {e}")
        return self._apps

    def _get_camera(self):
        if self._camera is None:
            if _is_headless():
                print("[LOCAL] sirius_camera desabilitado — ambiente headless/sem display.")
                return None
            try:
                from sirius_camera import SiriusCamera
                self._camera = SiriusCamera()
            except Exception as e:
                print(f"[LOCAL] sirius_camera indisponível: {e}")
        return self._camera

    def _get_visao(self):
        if self._visao is None:
            if _is_headless():
                # Em headless usa get_visao() — sem pyautogui, só OCR de arquivo
                try:
                    from sirius_visao import get_visao
                    self._visao = get_visao()
                except Exception as e:
                    print(f"[LOCAL] sirius_visao indisponível: {e}")
            else:
                try:
                    from sirius_visao import get_visao
                    self._visao = get_visao()
                except Exception as e:
                    print(f"[LOCAL] sirius_visao indisponível: {e}")
        return self._visao

    def _get_audio(self):
        if self._audio is None:
            try:
                from audio_handler import SiriusAudio
                self._audio = SiriusAudio()
            except Exception as e:
                print(f"[LOCAL] audio_handler indisponível: {e}")
        return self._audio

    # ── TTS local via audio_handler ───────────────────────────────────── #

    def falar(self, texto: str, bloqueante: bool = False) -> None:
        """
        Fala o texto no PC local via audio_handler.SiriusAudio.

        bloqueante=False → thread daemon (não bloqueia a UI — padrão)
        bloqueante=True  → executa na thread atual (alertas urgentes)
        """
        def _executar():
            audio = self._get_audio()
            if audio:
                try:
                    audio.falar(texto)
                    return
                except Exception as e:
                    print(f"[LOCAL] audio_handler.falar falhou: {e}")
            # Fallback: pyttsx3
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 185)
                for voz in engine.getProperty("voices"):
                    langs = voz.languages
                    if langs and "pt" in str(langs[0]).lower():
                        engine.setProperty("voice", voz.id)
                        break
                engine.say(texto)
                engine.runAndWait()
            except Exception as e:
                print(f"[LOCAL] pyttsx3 fallback falhou: {e}")

        if bloqueante:
            _executar()
        else:
            threading.Thread(target=_executar, daemon=True).start()

    # ── Execução de ações estruturadas (pacotes exec_local) ──────────── #

    def executar_acao(self, acao: str, params: dict) -> str:
        """
        Executa uma ação estruturada recebida num pacote JSON exec_local.

        Ações suportadas:
          abrir_programa   params: {programa: str}
          fechar_janela    params: {titulo: str}
          volume           params: {nivel: int} | {delta: int} | {mudo: bool}
          screenshot       params: {caminho: str}  (opcional)
          tecla            params: {tecla: str}  ex: 'ctrl+c'
          mouse_click      params: {x: int, y: int, botao: str}
          digitar          params: {texto: str}
          sistema          params: {cmd: str}  ex: 'desligar'|'reiniciar'
          minimizar_todas  params: {}
          maximizar_janela params: {titulo: str}
          comando_voz      params: {texto: str}  (fallback texto livre)
        """
        ctrl = self._get_control()
        if ctrl is None:
            return f"Módulo controle_pc indisponível para ação '{acao}'."

        try:
            if acao == "abrir_programa":
                programa = str(params.get("programa", "")).strip()
                if not programa:
                    return "Nome do programa não informado."
                ctrl.abrir_programa(programa)
                return f"Programa '{programa}' aberto com sucesso."

            elif acao == "fechar_janela":
                titulo = str(params.get("titulo", "")).strip()
                if not titulo:
                    return "Título da janela não informado."
                ctrl.fechar_janela(titulo)
                return f"Janela '{titulo}' fechada."

            elif acao == "volume":
                if "nivel" in params:
                    nivel = int(params["nivel"])
                    if not (0 <= nivel <= 100):
                        return "Nível de volume deve estar entre 0 e 100."
                    ctrl.definir_volume(nivel)
                    return f"Volume definido para {nivel}%."
                elif "delta" in params:
                    delta = int(params["delta"])
                    ctrl.ajustar_volume(delta)
                    return f"Volume ajustado em {'+' if delta >= 0 else ''}{delta}%."
                elif params.get("mudo"):
                    ctrl.alternar_mudo()
                    return "Áudio alternado (mudo/som)."
                return "Parâmetro de volume inválido (use 'nivel', 'delta' ou 'mudo')."

            elif acao == "screenshot":
                caminho = params.get("caminho")
                resultado = ctrl.screenshot(caminho)
                return f"Screenshot salvo em: {resultado or 'diretório padrão'}."

            elif acao == "tecla":
                tecla = str(params.get("tecla", "")).strip()
                if not tecla:
                    return "Tecla não informada."
                ctrl.pressionar_tecla(tecla)
                return f"Tecla '{tecla}' pressionada."

            elif acao == "mouse_click":
                x      = int(params.get("x", 0))
                y      = int(params.get("y", 0))
                botao  = str(params.get("botao", "left"))
                ctrl.clicar_mouse(x, y, botao)
                return f"Clique em ({x}, {y}) com botão '{botao}'."

            elif acao == "digitar":
                texto_dig = str(params.get("texto", "")).strip()
                if not texto_dig:
                    return "Texto para digitar não informado."
                ctrl.digitar_texto(texto_dig)
                preview = texto_dig[:40] + ("..." if len(texto_dig) > 40 else "")
                return f"Texto digitado: '{preview}'."

            elif acao == "sistema":
                cmd = str(params.get("cmd", "")).strip().lower()
                mapa = {
                    "desligar":  ctrl.desligar,
                    "reiniciar": ctrl.reiniciar,
                    "hibernar":  ctrl.hibernar,
                    "suspender": ctrl.suspender,
                }
                fn = mapa.get(cmd)
                if fn:
                    fn()
                    return f"Comando de sistema '{cmd}' executado."
                return f"Comando de sistema desconhecido: '{cmd}'."

            elif acao == "minimizar_todas":
                ctrl.minimizar_todas_janelas()
                return "Todas as janelas minimizadas."

            elif acao == "maximizar_janela":
                titulo = str(params.get("titulo", "")).strip()
                ctrl.maximizar_janela(titulo)
                return f"Janela '{titulo}' maximizada."

            elif acao == "comando_voz":
                texto_livre = str(params.get("texto", "")).strip()
                if texto_livre:
                    return self.processar(texto_livre) or "Ação executada."
                return "Texto de comando não informado."

            else:
                return f"Ação '{acao}' não reconhecida pelo executor local."

        except (ValueError, TypeError) as e:
            return f"Parâmetro inválido para '{acao}': {e}"
        except FileNotFoundError as e:
            return _amigavel(acao, e)
        except PermissionError as e:
            return _amigavel(acao, e)
        except Exception as e:
            return _amigavel(acao, e)

    # ── Processamento de texto livre ──────────────────────────────────── #

    def processar(self, comando: str) -> Optional[str]:
        """
        Tenta executar o comando localmente via texto livre.
        Retorna string com resposta, ou None se não souber tratar.
        """
        t = _norm(comando)

        ctrl = self._get_control()
        if ctrl:
            try:
                from controle_pc import _parsear_controle_pc
                resp = _parsear_controle_pc(comando, ctrl)
                if resp and not _e_falha(resp):
                    return resp
            except Exception as e:
                print(f"[LOCAL] controle_pc.parsear: {e}")

        apps = self._get_apps()
        if apps:
            try:
                if apps.e_comando_app(t):
                    resp = apps.processar(comando)
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL] sirius_apps: {e}")

        camera = self._get_camera()
        if camera:
            try:
                if camera.e_comando_camera(t):
                    resp = camera.processar_comando(comando)
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL] camera: {e}")

        visao = self._get_visao()
        if visao:
            try:
                if any(tr in t for tr in _TRIGGERS_VISAO):
                    resp = visao.ler_tela()
                    if resp:
                        return resp
            except Exception as e:
                print(f"[LOCAL] visao: {e}")

        return None


# ===========================================================================
# ClienteServidor — comunicação HTTP com o servidor
# ===========================================================================

class ClienteServidor:
    """
    Envia comandos ao servidor Sirius e recebe respostas via REST.
    WebSocket bidirecional é gerenciado pela interface_cliente.py;
    aqui apenas usamos HTTP para comandos, status e feedback.
    """

    TIMEOUT_PADRAO = 15

    def __init__(self, url: str = ""):
        self._url        = self._normalizar(url)
        self._disponivel = False

    def _normalizar(self, url: str) -> str:
        url = url.strip().rstrip("/")
        if not url:
            return ""
        if not url.startswith("http"):
            url = f"http://{url}"
        return url

    def configurar(self, url: str) -> None:
        self._url        = self._normalizar(url)
        self._disponivel = False
        self.testar_conexao()

    def testar_conexao(self) -> bool:
        if not self._url:
            return False
        try:
            r = requests.get(f"{self._url}/status", timeout=3)
            self._disponivel = r.status_code == 200
            if self._disponivel:
                print(f"\033[92m[CLIENTE] Servidor {self._url} disponível.\033[0m")
            return self._disponivel
        except Exception:
            self._disponivel = False
            return False

    @property
    def disponivel(self) -> bool:
        return self._disponivel and bool(self._url)

    def enviar_comando(self, texto: str) -> Optional[str]:
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
            print(f"[CLIENTE] Timeout em {self._url}")
            return None
        except Exception as e:
            print(f"[CLIENTE] Erro enviar_comando: {e}")
            self._disponivel = False
            return None

    def enviar_feedback(self, pacote: dict) -> bool:
        """
        Envia o pacote de feedback (contexto + resultado da execução local)
        ao servidor via POST /feedback.
        Falha silenciosa: não deve travar o fluxo principal.
        """
        if not self.disponivel:
            return False
        try:
            r = requests.post(
                f"{self._url}/feedback",
                json=pacote,
                timeout=5,
            )
            return r.status_code in (200, 201, 204)
        except Exception as e:
            print(f"[CLIENTE] Erro ao enviar feedback: {e}")
            return False

    def obter_historico(self, n: int = 20) -> list:
        if not self.disponivel:
            return []
        try:
            r = requests.get(
                f"{self._url}/historico", params={"limit": n}, timeout=5
            )
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

    def criar_lembrete(
        self, descricao: str, hora: int, minuto: int, repetir: bool = False
    ) -> Optional[str]:
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


# ===========================================================================
# CerebroCliente — coordenador central  v2.0
# ===========================================================================

class CerebroCliente:
    """
    Cérebro do app em modo cliente — v2.0.

    Novas responsabilidades:
      executar_acao_local(pacote)  → despacha pacotes JSON do servidor
      _tratar_exec_local(pacote)   → executa via controle_pc + coleta feedback
      _tratar_proativo(pacote)     → TTS imediato para alertas espontâneos
      _pacote_feedback(...)        → monta resposta com contexto do sistema
      _disparar_feedback(pacote)   → chama callback + envia ao servidor

    Decisão por prioridade (processar):
      1. Resposta rápida local (saudações)        → retorna imediatamente
      2. Interface local detectada                → executa localmente
      3. Controle local explícito                 → executa localmente
      4. Servidor disponível                      → delega ao servidor
      5. Servidor indisponível + fallback local   → tenta controle local
      6. Nenhum disponível                        → mensagem de erro
    """

    def __init__(
        self,
        servidor_url: str = "",
        falar_local:  bool = True,
    ):
        self._local       = ExecutorLocal()
        self._servidor    = ClienteServidor(servidor_url)
        self._contexto    = ColetorContexto()
        self._falar_local = falar_local
        self._ultimo_estado = "STANDBY"

        # Callbacks registrados pela interface_cliente.py (PySide6 Signals)
        self._cb_estado:   Optional[Callable[[str],  None]] = None
        self._cb_resposta: Optional[Callable[[str],  None]] = None
        self._cb_log:      Optional[Callable[[str],  None]] = None
        self._cb_feedback: Optional[Callable[[dict], None]] = None

        if servidor_url:
            threading.Thread(
                target=self._servidor.testar_conexao, daemon=True
            ).start()

    # ── Configuração ──────────────────────────────────────────────────── #

    def configurar_servidor(self, url: str) -> None:
        """Atualiza a URL do servidor em tempo de execução."""
        self._servidor.configurar(url)

    def registrar_callback(
        self,
        callback_estado:   Optional[Callable] = None,
        callback_resposta: Optional[Callable] = None,
        callback_log:      Optional[Callable] = None,
        callback_feedback: Optional[Callable] = None,
    ) -> None:
        """
        Registra callbacks chamados pelo cérebro para atualizar a interface.

        callback_feedback(pacote: dict):
            Chamado sempre que há resultado de execução local para
            enviar ao servidor. A interface_cliente.py deve usar este
            callback para transmitir via WebSocket.
        """
        if callback_estado:   self._cb_estado   = callback_estado
        if callback_resposta: self._cb_resposta  = callback_resposta
        if callback_log:      self._cb_log       = callback_log
        if callback_feedback: self._cb_feedback  = callback_feedback

    # =========================================================================
    # NOVO — Executor de Pacotes JSON  (exec_local | proativo | outros)
    # =========================================================================

    def executar_acao_local(self, dados_pacote: dict) -> dict:
        """
        Ponto de entrada principal para pacotes JSON vindos do servidor.

        Despacha conforme o campo 'tipo':
          'exec_local'  → executa ação estruturada no controle_pc local
          'proativo'    → dispara TTS imediatamente (sem input do usuário)
          qualquer outro→ encaminha para processar() (fluxo clássico de texto)

        Retorna sempre um dict serializable em JSON:
        {
          "tipo":      "feedback",
          "origem":    <tipo original>,
          "sucesso":   bool,
          "resultado": str,          # mensagem amigável para o log
          "contexto":  dict,         # snapshot do sistema local
          "timestamp": str,          # ISO 8601
          "req_id":    str,          # quando presente no pacote original
        }

        Exemplos de pacotes de entrada:

          # Abrir programa
          {"tipo": "exec_local", "acao": "abrir_programa",
           "params": {"programa": "notepad"}, "id": "req_001"}

          # Ajustar volume
          {"tipo": "exec_local", "acao": "volume",
           "params": {"delta": -10}}

          # Alerta proativo urgente (TTS imediato)
          {"tipo": "proativo", "texto": "Bateria em 5%!", "urgente": true}

          # Comando em texto livre
          {"tipo": "comando", "texto": "que horas são"}
        """
        if not isinstance(dados_pacote, dict):
            return self._pacote_feedback(
                origem="desconhecido",
                sucesso=False,
                resultado="Pacote inválido: esperado um objeto JSON (dict).",
            )

        tipo = str(dados_pacote.get("tipo", "")).strip().lower()

        try:
            if tipo == "exec_local":
                return self._tratar_exec_local(dados_pacote)

            elif tipo == "proativo":
                return self._tratar_proativo(dados_pacote)

            else:
                # Fallback: qualquer outro tipo trata como texto livre
                texto = str(dados_pacote.get("texto", "")).strip()
                if texto:
                    resultado = self.processar(texto) or "Processado sem resposta."
                else:
                    resultado = "Nenhum texto informado no pacote."
                return self._pacote_feedback(
                    origem=tipo or "desconhecido",
                    sucesso=bool(texto),
                    resultado=resultado,
                )

        except Exception as e:
            msg = _amigavel(tipo, e)
            self._log(f"[CEREBRO] Erro fatal em executar_acao_local: {e}")
            return self._pacote_feedback(
                origem=tipo,
                sucesso=False,
                resultado=msg,
            )

    # ── Integração de Controle: _tratar_exec_local ────────────────────── #

    def _tratar_exec_local(self, pacote: dict) -> dict:
        """
        Processa um pacote do tipo 'exec_local'.

        Campos obrigatórios: acao (str)
        Campos opcionais:    params (dict), id (str)

        Usa o ExecutorLocal.executar_acao() que delega ao controle_pc.
        Captura todas as exceções e retorna mensagem amigável.
        """
        acao   = str(pacote.get("acao", "")).strip().lower()
        params = pacote.get("params") or {}
        req_id = str(pacote.get("id", ""))

        if not acao:
            return self._pacote_feedback(
                origem="exec_local",
                sucesso=False,
                resultado="Campo 'acao' ausente no pacote exec_local.",
                req_id=req_id,
            )

        if not isinstance(params, dict):
            return self._pacote_feedback(
                origem="exec_local",
                sucesso=False,
                resultado=(
                    f"Campo 'params' deve ser um objeto JSON, "
                    f"recebi: {type(params).__name__}."
                ),
                req_id=req_id,
            )

        self._log(f"[EXEC_LOCAL] acao='{acao}' params={params}")
        self._set_estado("PROCESSANDO")

        resultado = self._local.executar_acao(acao, params)

        # Detecta falha pela mensagem retornada
        _PALAVRAS_FALHA = {
            "erro", "inválido", "não encontrado",
            "indisponível", "não reconhecida", "não informado",
        }
        sucesso = not any(p in resultado.lower() for p in _PALAVRAS_FALHA)

        self._set_estado("STANDBY")
        self._log(f"[EXEC_LOCAL] resultado='{resultado}'")

        feedback = self._pacote_feedback(
            origem="exec_local",
            sucesso=sucesso,
            resultado=resultado,
            req_id=req_id,
        )
        self._disparar_feedback(feedback)
        return feedback

    # ── Motor de Proatividade: _tratar_proativo ────────────────────────── #

    def _tratar_proativo(self, pacote: dict) -> dict:
        """
        Motor de Proatividade — trata pacotes espontâneos do servidor.

        O servidor pode enviar alertas a qualquer momento sem que o usuário
        tenha feito uma pergunta (lembretes, avisos de bateria, atualizações).
        Este método:
          1. Aciona audio_handler.SiriusAudio.falar() imediatamente
          2. Notifica a interface para exibir no log do chat
          3. Monta e envia feedback de confirmação ao servidor

        Campos do pacote:
          texto   (str)  obrigatório — texto a ser falado
          urgente (bool) opcional    — True = TTS bloqueante (default: False)
          exibir  (bool) opcional    — True = exibe no log da interface (default: True)
        """
        texto   = str(pacote.get("texto", "")).strip()
        urgente = bool(pacote.get("urgente", False))
        exibir  = bool(pacote.get("exibir",  True))

        if not texto:
            return self._pacote_feedback(
                origem="proativo",
                sucesso=False,
                resultado="Pacote proativo recebido sem campo 'texto'.",
            )

        self._log(
            f"[PROATIVO] urgente={urgente} "
            f"texto='{texto[:80]}{'...' if len(texto) > 80 else ''}'"
        )

        # TTS imediato — independente de qualquer input do usuário
        if self._falar_local:
            self._local.falar(texto, bloqueante=urgente)

        # Notifica a interface para exibir no log do chat
        if exibir and self._cb_resposta:
            try:
                self._cb_resposta(f"[Sirius] {texto}")
            except Exception as e:
                print(f"[CEREBRO] cb_resposta em proativo: {e}")

        feedback = self._pacote_feedback(
            origem="proativo",
            sucesso=True,
            resultado=(
                f"Notificação proativa reproduzida: "
                f"'{texto[:60]}{'...' if len(texto) > 60 else ''}'"
            ),
        )
        self._disparar_feedback(feedback)
        return feedback

    # ── Gerenciamento de Contexto: _pacote_feedback ───────────────────── #

    def _pacote_feedback(
        self,
        origem:    str,
        sucesso:   bool,
        resultado: str,
        req_id:    str = "",
    ) -> dict:
        """
        Monta o pacote de feedback com snapshot completo do sistema local.

        Inclui bateria, conectividade de rede, CPU e RAM para que o servidor
        tenha visibilidade do estado do dispositivo cliente após cada ação.
        """
        pacote: dict[str, Any] = {
            "tipo":      "feedback",
            "origem":    origem,
            "sucesso":   sucesso,
            "resultado": resultado,
            "contexto":  self._contexto.coletar(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if req_id:
            pacote["req_id"] = req_id
        return pacote

    def _disparar_feedback(self, pacote: dict) -> None:
        """
        Dispara o callback de feedback registrado (para interface_cliente.py
        enviar via WebSocket) E tenta envio HTTP em background (best-effort).
        """
        if self._cb_feedback:
            try:
                self._cb_feedback(pacote)
            except Exception as e:
                print(f"[CEREBRO] cb_feedback falhou: {e}")

        # Envio HTTP em thread separada para não bloquear o fluxo
        threading.Thread(
            target=self._servidor.enviar_feedback,
            args=(pacote,),
            daemon=True,
        ).start()

    # ── Processamento clássico (texto livre) ──────────────────────────── #

    def processar(
        self,
        texto: str,
        forcar_processamento: bool = False,
    ) -> Optional[str]:
        """
        Processa um comando de texto livre decidindo onde executar.
        Thread-safe — pode ser chamado de qualquer thread da interface.
        """
        if not texto or not texto.strip():
            return None

        cmd = re.sub(
            r"[,!\.\s]*sirius[,!\.\s]*", " ", texto, flags=re.IGNORECASE
        ).strip()
        if not cmd:
            return "Diga, chefe."

        self._set_estado("PROCESSANDO")
        try:
            resposta = self._decidir_e_executar(cmd, texto)
        except Exception as e:
            print(f"[CEREBRO CLIENTE] Erro: {e}")
            resposta = _amigavel("processar", e)
        self._set_estado("STANDBY")

        if resposta:
            if self._falar_local:
                self._local.falar(resposta)
            if self._cb_resposta:
                try:
                    self._cb_resposta(resposta)
                except Exception:
                    pass

        return resposta

    def _decidir_e_executar(
        self, cmd: str, texto_original: str
    ) -> Optional[str]:
        t = _norm(cmd)

        # 1. Resposta rápida local
        resp = RESPOSTAS_RAPIDAS.get(t)
        if resp:
            return resp

        # 2. Interface local
        if any(tr in t for tr in _TRIGGERS_INTERFACE):
            return self._processar_interface(cmd)

        # 3. Controle local explícito
        if _e_controle_local(cmd):
            resp = self._local.processar(cmd)
            if resp:
                return resp

        # 4. Delega ao servidor
        if self._servidor.disponivel:
            resp = self._servidor.enviar_comando(texto_original)
            if resp:
                return resp

        # 5. Fallback local (servidor indisponível)
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
        t = _norm(cmd)
        try:
            if "wallpaper" in t:
                if _is_headless():
                    return "Modo wallpaper não disponível em ambiente sem display."
                from sirius_wallpaper import iniciar_wallpaper, MODO_WALLPAPER
                threading.Thread(
                    target=lambda: iniciar_wallpaper(modo=MODO_WALLPAPER),
                    daemon=True,
                ).start()
                return "Modo wallpaper ativado."
            if "janela" in t or "interface" in t:
                return "Interface em modo janela."
        except Exception as e:
            return f"Erro ao controlar interface: {e}"
        return None

    # ── Estado e log ──────────────────────────────────────────────────── #

    def _set_estado(self, estado: str) -> None:
        self._ultimo_estado = estado
        if self._cb_estado:
            try:
                self._cb_estado(estado)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        print(msg)
        if self._cb_log:
            try:
                self._cb_log(msg)
            except Exception:
                pass

    # ── Atalhos públicos para a interface_cliente.py ──────────────────── #

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

    def criar_lembrete(
        self, descricao: str, hora: int, minuto: int, repetir: bool = False
    ) -> str:
        return (
            self._servidor.criar_lembrete(descricao, hora, minuto, repetir)
            or "Lembrete criado."
        )

    def reconectar(self) -> bool:
        """Testa reconexão manual com o servidor."""
        return self._servidor.testar_conexao()

    def coletar_contexto(self) -> dict:
        """Expõe snapshot do sistema para uso externo."""
        return self._contexto.coletar()


# ===========================================================================
# Singleton global
# ===========================================================================

_cerebro_global: Optional[CerebroCliente] = None


def get_cerebro() -> Optional[CerebroCliente]:
    return _cerebro_global


def criar_cerebro(
    servidor_url: str = "",
    falar_local:  bool = True,
) -> CerebroCliente:
    global _cerebro_global
    _cerebro_global = CerebroCliente(
        servidor_url=servidor_url,
        falar_local=falar_local,
    )
    return _cerebro_global


# ===========================================================================
# Exemplo de integração com interface_cliente.py (PySide6)
# ===========================================================================
#
# from cerebro_cliente import criar_cerebro
#
# class SiriusWorker(QThread):
#     feedback_pronto = Signal(dict)    # envia via WebSocket
#     resposta_pronta = Signal(str)
#     estado_mudou    = Signal(str)
#     log_msg         = Signal(str)
#
#     def __init__(self, audio, cerebro):
#         super().__init__()
#         self.cerebro = cerebro
#         self.cerebro.registrar_callback(
#             callback_estado   = self.estado_mudou.emit,
#             callback_resposta = self.resposta_pronta.emit,
#             callback_log      = self.log_msg.emit,
#             callback_feedback = self.feedback_pronto.emit,   # ← novo
#         )
#
#     def processar_pacote_ws(self, pacote: dict):
#         """Chamado pelo handler do WebSocket quando chega um pacote."""
#         resultado = self.cerebro.executar_acao_local(pacote)
#         # feedback_pronto já foi emitido dentro do cerebro
#
# ===========================================================================


if __name__ == "__main__":
    import json

    print("=== cerebro_cliente.py v2.0 — smoke test ===\n")

    cerebro = criar_cerebro(servidor_url="", falar_local=False)
    cerebro.registrar_callback(
        callback_log=lambda m: print(f"  LOG: {m}"),
        callback_feedback=lambda p: print(
            f"  FEEDBACK → sucesso={p['sucesso']}  "
            f"resultado='{p['resultado']}'"
        ),
    )

    # Teste 1: exec_local
    print("─── Teste 1: exec_local (abrir_programa) ───")
    r = cerebro.executar_acao_local({
        "tipo": "exec_local", "acao": "abrir_programa",
        "params": {"programa": "notepad"}, "id": "req_001",
    })
    print(f"  sucesso:  {r['sucesso']}")
    print(f"  resultado:{r['resultado']}")
    print(f"  rede:     {r['contexto']['rede']}")

    # Teste 2: proativo
    print("\n─── Teste 2: proativo urgente ───")
    r2 = cerebro.executar_acao_local({
        "tipo": "proativo",
        "texto": "Atenção: bateria abaixo de 10%. Conecte o carregador.",
        "urgente": False,
    })
    print(f"  sucesso:  {r2['sucesso']}")
    print(f"  resultado:{r2['resultado']}")

    # Teste 3: texto livre
    print("\n─── Teste 3: texto livre ───")
    r3 = cerebro.executar_acao_local({"tipo": "comando", "texto": "bom dia"})
    print(f"  resultado:{r3['resultado']}")

    # Teste 4: pacote inválido
    print("\n─── Teste 4: pacote inválido ───")
    r4 = cerebro.executar_acao_local("isso não é um dict")   # type: ignore
    print(f"  sucesso:  {r4['sucesso']}")
    print(f"  resultado:{r4['resultado']}")

    # Teste 5: contexto do sistema
    print("\n─── Teste 5: contexto do sistema ───")
    ctx = cerebro.coletar_contexto()
    print(json.dumps(ctx, ensure_ascii=False, indent=2))