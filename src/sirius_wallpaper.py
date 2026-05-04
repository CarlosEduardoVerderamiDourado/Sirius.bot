"""
sirius_wallpaper.py — Sirius como papel de parede real do Windows

Como funciona de verdade (mesma técnica do Wallpaper Engine):
  1. Encontra o processo Progman (dono do desktop)
  2. Envia mensagem 0x052C → Windows cria o WorkerW atrás dos ícones
  3. SetParent(nossa_janela → WorkerW) → ficamos ATRÁS dos ícones
  4. Ajuste de estilos Win32 (WS_CHILD, remove WS_POPUP) → integração real

Arquitetura de janelas (corrigida — sem SetParent no OpenGLWidget):
  ┌─────────────────────────────────────────────┐
  │  Taskbar / Alt+Tab / janelas normais         │ ← camada normal
  ├─────────────────────────────────────────────┤
  │  HUDStatus  (Tool + StaysOnTop + NoFocus)   │ ← sempre visível
  │  ChatOverlay (Tool + StaysOnTop, escondido) │ ← aparece ao ativar
  ├─────────────────────────────────────────────┤
  │  Ícones do desktop (WorkerW)                 │
  ├─────────────────────────────────────────────┤
  │  SiriusWallpaperWindow (HWND_BOTTOM)         │ ← esfera Jarvis
  │  ↑ NÃO usa SetParent → WGL context válido   │ ← sem wglMakeCurrent errors
  └─────────────────────────────────────────────┘

Por que não usamos SetParent(OpenGLWidget → WorkerW):
  Após SetParent, o HWND torna-se filho do processo Desktop (DWM).
  O contexto WGL OpenGL NÃO pode ser compartilhado entre processos —
  wglMakeCurrent falha 60× por segundo (a cada frame de animação).
  SOLUÇÃO: usamos apenas SetWindowPos(HWND_BOTTOM) — fica visualmente
  atrás de tudo sem cruzar processos. Mesmo técnica do Rainmeter.

Ativação:
  - Hotkey Ctrl+Alt+S  → abre/fecha o chat overlay
  - Botão no HUD       → abre/fecha o chat overlay
  - Wake word          → continua funcionando sempre

Modos:
  MODO_WALLPAPER  → esfera no fundo real (WorkerW)
  MODO_ATIVO      → janela flutuante com chat (debug)
  MODO_FULLSCREEN → tela cheia

Uso:
    from sirius_wallpaper import iniciar_wallpaper, MODO_WALLPAPER
    app, janela = iniciar_wallpaper(cerebro=cerebro, modo=MODO_WALLPAPER)
    sys.exit(app.exec())
"""

import os
import sys
import json
import ctypes
import ctypes.wintypes
import math
import random
import threading
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Config persistente — salva preferências entre sessões
# ---------------------------------------------------------------------------
def _caminho_config() -> str:
    src  = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(src)
    return os.path.join(raiz, "data", "wallpaper_config.json")

def carregar_config() -> dict:
    """Carrega configuração salva ou retorna padrão."""
    padrao = {
        "modo_wallpaper":  True,   # True = wallpaper, False = janela normal
        "monitor_idx":     0,      # índice do monitor (0 = primário)
        "aberto_em_bg":    True,   # manter em background (não fechar ao abrir app)
        "posicao_chat":   "bottom_right",  # posição do chat overlay
    }
    try:
        caminho = _caminho_config()
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                salvo = json.load(f)
            padrao.update(salvo)
    except Exception:
        pass
    return padrao

def salvar_config(cfg: dict):
    """Salva configuração no disco."""
    try:
        os.makedirs(os.path.dirname(_caminho_config()), exist_ok=True)
        with open(_caminho_config(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WALLPAPER]: Erro ao salvar config: {e}")

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT",    "1")
os.environ.setdefault("QT_SCALE_FACTOR",               "1")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR",   "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING",     "1")
os.environ.setdefault("QT_LOGGING_RULES",
    "qt.qpa.window=false;qt.rhi.general=false;qt.rhi.backend=false;"
    "qt.qpa.gl=false")
# Suprime warnings Kokoro / HuggingFace
os.environ.setdefault("TRANSFORMERS_VERBOSITY",        "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM",        "false")
import warnings as _w
_w.filterwarnings("ignore", message=".*repo_id.*")
_w.filterwarnings("ignore", message=".*unauthenticated.*")
_w.filterwarnings("ignore", message=".*HF_TOKEN.*")
_w.filterwarnings("ignore", category=UserWarning, module="torch")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QLineEdit, QPushButton, QSizePolicy, QFrame,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QPoint, QPointF, QSize,
    QPropertyAnimation, QEasingCurve, QRect,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QRadialGradient,
    QCursor, QKeySequence, QShortcut,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
MODO_WALLPAPER  = "wallpaper"
MODO_ATIVO      = "ativo"
MODO_FULLSCREEN = "fullscreen"

COR_AZUL    = "#5DE2FF"
COR_VERDE   = "#00FF88"
COR_AMARELO = "#FFD700"
COR_BRANCO  = "#FFFFFF"
COR_CINZA   = "#2a3a4a"

ESTADO_STANDBY     = "STANDBY"
ESTADO_OUVINDO     = "OUVINDO"
ESTADO_PROCESSANDO = "PROCESSANDO"
ESTADO_FALANDO     = "FALANDO"


# ---------------------------------------------------------------------------
# Win32 — papel de parede real (arquitetura sem conflito de DC)
#
# Camadas:
#   WorkerW (explorer.exe)
#     └── HWND_proxy  ← janela Win32 nativa, criada por NÓS (mesmo processo)
#           └── hwnd_qt  ← janela Qt/OpenGL, SetParent para proxy
#
# Por que proxy resolve o wglMakeCurrent:
#   SetParent(hwnd_qt → WorkerW) cruza processos → WGL falha
#   SetParent(hwnd_qt → proxy)   mesmo processo  → WGL OK
#   proxy é filha do WorkerW     → proxy fica atrás dos ícones
#   hwnd_qt segue a proxy        → OpenGL aparece como wallpaper
#
# Por que sem SetWindowPos no timer:
#   SetWindowPos invalida o backing store DC do Qt a cada chamada
#   → artefatos visuais, flickering no OpenGL
#   Com a proxy, a posição é fixa (WorkerW não reordena filhos)
#   → sem timer, sem invalidação de DC
# ---------------------------------------------------------------------------

# Classe de janela proxy — registrada uma única vez
_PROXY_CLASS = "SiriusWallpaperProxy"
_proxy_class_registrada = False
_hwnd_proxy: int = 0   # HWND da proxy criada


def _registrar_classe_proxy() -> bool:
    """Registra a classe de janela Win32 para a proxy (uma só vez)."""
    global _proxy_class_registrada
    if _proxy_class_registrada:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        user32   = ctypes.windll.user32

        WNDCLASSW = ctypes.Structure

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style",         ctypes.c_uint),
                ("lpfnWndProc",   ctypes.c_void_p),
                ("cbClsExtra",    ctypes.c_int),
                ("cbWndExtra",    ctypes.c_int),
                ("hInstance",     ctypes.c_void_p),
                ("hIcon",         ctypes.c_void_p),
                ("hCursor",       ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName",  ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
            ]

        wc = WNDCLASSW()
        wc.lpfnWndProc   = ctypes.cast(
            user32.DefWindowProcW, ctypes.c_void_p
        )
        wc.hInstance     = kernel32.GetModuleHandleW(None)
        wc.hbrBackground = ctypes.c_void_p(1)   # COLOR_SCROLLBAR = preto
        wc.lpszClassName = _PROXY_CLASS

        user32.RegisterClassW(ctypes.byref(wc))
        _proxy_class_registrada = True
        return True
    except Exception as e:
        print(f"[WALLPAPER]: Erro ao registrar proxy class: {e}")
        return False


def _encontrar_workerw() -> int:
    """
    Retorna HWND do WorkerW (camada atrás dos ícones do desktop).
    Progman → SendMessage(0x052C) → EnumWindows → SHELLDLL_DefView → WorkerW
    """
    user32  = ctypes.windll.user32
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0

    # Força criação do WorkerW
    user32.SendMessageTimeoutW(
        progman, 0x052C, 0, 0, 0, 2000,
        ctypes.byref(ctypes.c_ulong())
    )

    workerw = ctypes.c_void_p(0)
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                   ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hw, _):
        shell = user32.FindWindowExW(hw, None, "SHELLDLL_DefView", None)
        if shell:
            ww = user32.FindWindowExW(None, hw, "WorkerW", None)
            if ww:
                workerw.value = ww
        return True

    # Mantém referência — GC não pode coletar durante EnumWindows
    _ref = EnumProc(_cb)
    user32.EnumWindows(_ref, None)

    # Fallback Windows 11
    if not workerw.value:
        workerw.value = user32.FindWindowExW(
            progman, None, "WorkerW", None
        ) or 0

    return workerw.value or 0


def _criar_proxy(workerw: int, x: int, y: int, w: int, h: int) -> int:
    """
    Cria janela Win32 nativa como filha do WorkerW (mesmo processo Python).
    Esta proxy não tem OpenGL — é só um container HWND.
    """
    global _hwnd_proxy
    if not _registrar_classe_proxy():
        return 0
    try:
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        WS_CHILD   = 0x40000000
        WS_VISIBLE = 0x10000000

        hwnd = user32.CreateWindowExW(
            0,              # dwExStyle
            _PROXY_CLASS,   # lpClassName
            "SiriusProxy",  # lpWindowName
            WS_CHILD | WS_VISIBLE,
            0, 0, w, h,     # posição relativa ao WorkerW
            workerw,        # pai = WorkerW
            None,           # hMenu
            kernel32.GetModuleHandleW(None),
            None
        )
        if hwnd:
            _hwnd_proxy = hwnd
        return hwnd or 0
    except Exception as e:
        print(f"[WALLPAPER]: Erro ao criar proxy: {e}")
        return 0


def _fixar_workerw(hwnd_qt: int, geo) -> bool:
    """
    Fixa hwnd_qt como papel de parede real via proxy Win32.

    Fluxo:
      1. Encontra WorkerW
      2. Cria proxy nativa no WorkerW (mesmo processo)
      3. SetParent(hwnd_qt → proxy) — WGL context válido (mesmo processo)
      4. Ajusta estilos Win32
      5. SetWindowPos UMA VEZ para posicionar (sem timer)
    """
    try:
        user32 = ctypes.windll.user32

        workerw = _encontrar_workerw()
        if not workerw:
            print("[WALLPAPER]: WorkerW não encontrado.")
            return False

        x, y = geo.x(), geo.y()
        w, h = geo.width(), geo.height()

        # Proxy no WorkerW (mesmo processo → WGL OK)
        proxy = _criar_proxy(workerw, x, y, w, h)
        if not proxy:
            print("[WALLPAPER]: Proxy falhou.")
            return False

        # SetParent: Qt → proxy (mesmo processo, WGL intacto)
        user32.SetParent(hwnd_qt, proxy)

        # Estilos Win32
        GWL_STYLE        = -16
        GWL_EXSTYLE      = -20
        WS_CHILD         = 0x40000000
        WS_POPUP         = 0x80000000
        WS_VISIBLE       = 0x10000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW  = 0x00040000
        WS_EX_NOACTIVATE = 0x08000000

        user32.GetWindowLongPtrW.restype  = ctypes.c_longlong
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype  = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_longlong]

        # WS_CHILD (obrigatório para filho) em vez de WS_POPUP
        estilo = user32.GetWindowLongPtrW(hwnd_qt, GWL_STYLE)
        estilo = (estilo & ~WS_POPUP) | WS_CHILD | WS_VISIBLE
        user32.SetWindowLongPtrW(hwnd_qt, GWL_STYLE, estilo)

        # Remove da taskbar e Alt+Tab, não rouba foco
        ex = user32.GetWindowLongPtrW(hwnd_qt, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        user32.SetWindowLongPtrW(hwnd_qt, GWL_EXSTYLE, ex)

        # SetWindowPos UMA ÚNICA VEZ — posiciona sobre a proxy
        # Coordenadas relativas ao pai (proxy), não à tela
        SWP_SHOWWINDOW   = 0x0040
        SWP_FRAMECHANGED = 0x0020
        SWP_NOACTIVATE   = 0x0010

        user32.SetWindowPos.restype  = ctypes.c_bool
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos(
            hwnd_qt, None,
            0, 0, w, h,
            SWP_SHOWWINDOW | SWP_FRAMECHANGED | SWP_NOACTIVATE
        )

        # Diagnóstico: confirma coordenadas corretas
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(proxy, ctypes.byref(rect))
        print(f"\033[92m[WALLPAPER]: ✓ Papel de parede real ativo "
              f"(WorkerW→proxy→Qt {w}×{h} em x={x},y={y}).\033[0m")
        print(f"\033[92m[WALLPAPER]:   Proxy RECT: "
              f"({rect.left},{rect.top})→({rect.right},{rect.bottom})\033[0m")
        return True

    except Exception as e:
        print(f"[WALLPAPER]: Erro ao fixar: {e}")
        return False


def _destruir_proxy():
    """Remove a janela proxy ao fechar — limpa o WorkerW."""
    global _hwnd_proxy
    if _hwnd_proxy:
        try:
            ctypes.windll.user32.DestroyWindow(_hwnd_proxy)
        except Exception:
            pass
        _hwnd_proxy = 0


def _registrar_hotkey_global(hwnd: int, id_hotkey: int,
                              mod: int, vk: int) -> bool:
    """Registra hotkey global via Win32 RegisterHotKey."""
    try:
        return bool(ctypes.windll.user32.RegisterHotKey(hwnd, id_hotkey, mod, vk))
    except Exception:
        return False



def _parar_hook_zorder():
    """Compatibilidade — sem hook ativo, não faz nada."""
    pass


def _obter_geometria_desktop() -> QRect:
    """
    Retorna o QRect do monitor primário do Windows.

    Com dois monitores, o 'primário' é o que tem a barra de tarefas.
    Usa availableGeometry() em vez de geometry() para excluir a taskbar
    e garantir coordenadas corretas no sistema de coordenadas Win32.
    """
    screen = QApplication.primaryScreen()
    return screen.geometry()   # geometry() = tela inteira incluindo taskbar



# ---------------------------------------------------------------------------
# Esfera 3D OpenGL (reutilizada do interface.py)
# ---------------------------------------------------------------------------

class SiriusNexus3DView(QWidget):
    """
    Esfera Jarvis via QPainter — sem OpenGL.

    QWidget + QPainter (GDI) funciona após SetParent para o WorkerW
    porque GDI pode ser usado entre contextos de processo.
    QOpenGLWidget + OpenGL falha após SetParent porque o DC do WGL
    é invalidado ao mudar de processo (wglMakeCurrent loop de erros).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(QSize(200, 200))
        # Sem background — widget transparente sobre o WorkerW
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._estado = ESTADO_STANDBY
        self._angulo = 0.0
        self._pulse  = 0.0

        self._pontos: list[tuple[float, float, float]] = []
        for _ in range(900):
            phi = random.uniform(0, 2 * math.pi)
            ct  = random.uniform(-1, 1)
            th  = math.acos(ct)
            self._pontos.append((
                math.sin(th) * math.cos(phi),
                math.sin(th) * math.sin(phi),
                math.cos(th),
            ))

        self._nucleo: list[tuple[float, float, float]] = []
        for _ in range(280):
            r   = random.uniform(0, 0.4)
            phi = random.uniform(0, 2 * math.pi)
            ct  = random.uniform(-1, 1)
            th  = math.acos(ct)
            self._nucleo.append((
                r * math.sin(th) * math.cos(phi),
                r * math.sin(th) * math.sin(phi),
                r * math.cos(th),
            ))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(18)   # ~55fps

    def set_estado(self, estado: str):
        self._estado = estado

    def _cor(self) -> QColor:
        return {
            ESTADO_STANDBY:     QColor(93,  226, 255),
            ESTADO_OUVINDO:     QColor(0,   255, 136),
            ESTADO_PROCESSANDO: QColor(255, 215, 0),
            ESTADO_FALANDO:     QColor(255, 255, 255),
        }.get(self._estado, QColor(93, 226, 255))

    def _tick(self):
        if not self.isVisible():
            return
        try:
            if not self.winId():
                return
        except RuntimeError:
            self._parar_timer()
            return
        vel = (1.4 if self._estado == ESTADO_FALANDO
               else 0.8 if self._estado == ESTADO_OUVINDO
               else 0.35)
        self._angulo += vel
        self._pulse  += 0.065
        try:
            self.update()
        except Exception:
            self._parar_timer()

    def paintEvent(self, _):
        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        raio   = min(w, h) * 0.40
        pulso  = 1.0 + math.sin(self._pulse) * (
            0.08 if self._estado == ESTADO_FALANDO else 0.025
        )
        ar  = math.radians(self._angulo)
        ax  = math.radians(self._angulo * 0.38)
        cor = self._cor()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # NÃO preenche com preto — deixa o wallpaper do Windows aparecer
        # O QWidget filho do WorkerW é naturalmente transparente

        # Glow
        gw = QRadialGradient(cx, cy, raio * 0.6)
        gc = QColor(cor); gc.setAlpha(22)
        gw.setColorAt(0, gc); gw.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(gw)); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), raio * 0.6, raio * 0.6)

        # Projeção 3D→2D
        proj: list[tuple[float, float, float]] = []
        for px, py, pz in self._pontos:
            rx  = px * math.cos(ar) - pz * math.sin(ar)
            rz  = px * math.sin(ar) + pz * math.cos(ar)
            ry2 = py * math.cos(ax) - rz * math.sin(ax)
            rz2 = py * math.sin(ax) + rz * math.cos(ax)
            esc = 3.2 / (3.2 + rz2 + 1.5)
            proj.append((cx + rx * raio * pulso * esc,
                          cy + ry2 * raio * pulso * esc,
                          (rz2 + 1) / 2))

        # Arestas (1/3 dos pontos para performance)
        pen = QPen(); pen.setWidthF(0.55)
        lim = raio * 0.30
        am  = proj[::3]
        for i in range(0, len(am) - 1, 2):
            x1, y1, d1 = am[i]; x2, y2, d2 = am[i + 1]
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < lim:
                alfa = int((1 - dist / lim) * 105 * (d1 + d2) / 2)
                ec = QColor(cor); ec.setAlpha(max(4, min(alfa, 130)))
                pen.setColor(ec); p.setPen(pen)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Pontos
        p.setPen(Qt.NoPen)
        for sx, sy, dep in proj:
            a  = int(50 + dep * 160)
            sz = 0.9 + dep * 1.5
            pc = QColor(cor); pc.setAlpha(a)
            p.setBrush(QBrush(pc))
            p.drawEllipse(QPointF(sx, sy), sz / 2, sz / 2)

        # Núcleo
        for px, py, pz in self._nucleo:
            rx  = px * math.cos(ar) - pz * math.sin(ar)
            rz  = px * math.sin(ar) + pz * math.cos(ar)
            ry2 = py * math.cos(ax) - rz * math.sin(ax)
            rz2 = py * math.sin(ax) + rz * math.cos(ax)
            esc = 3.2 / (3.2 + rz2 + 1.5)
            sx  = cx + rx  * raio * pulso * esc * 0.42
            sy  = cy + ry2 * raio * pulso * esc * 0.42
            nc  = QColor(cor); nc.setAlpha(random.randint(70, 200))
            p.setBrush(QBrush(nc)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(sx, sy), 0.75, 0.75)

        # Anel externo
        ac = QColor(cor); ac.setAlpha(28)
        p.setPen(QPen(ac, 0.7)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), raio * pulso, raio * pulso * 0.22)
        p.end()

    def _parar_timer(self):
        """Para o timer de animação de forma segura."""
        if hasattr(self, '_timer') and self._timer.isActive():
            self._timer.stop()

    def _retomar_timer(self):
        """Retoma animação após SetParent (DC estabilizou)."""
        if hasattr(self, '_timer') and not self._timer.isActive():
            self._timer.start(18)
        self.update()

    def hideEvent(self, event):
        """Para animação quando o widget é ocultado (evita GetDC failed)."""
        self._parar_timer()
        super().hideEvent(event)

    def showEvent(self, event):
        """Retoma animação quando o widget volta a ser exibido."""
        super().showEvent(event)
        if hasattr(self, '_timer') and not self._timer.isActive():
            self._timer.start(16)

    def closeEvent(self, event):
        """Para o timer antes de destruir (evita startTimer após destruição)."""
        self._parar_timer()
        super().closeEvent(event)



# ---------------------------------------------------------------------------
# Painel de configurações do wallpaper
# ---------------------------------------------------------------------------

class JanelaConfig(QWidget):
    """
    Painel de configurações do Sirius Wallpaper.

    Permite configurar:
      - Monitor onde exibir a esfera
      - Modo: papel de parede (fundo) ou janela flutuante
      - Manter em background quando o app de chat estiver aberto
    """

    config_aplicada = Signal(dict)   # emitido quando o usuário salva

    def __init__(self, config_atual: dict = None, parent=None):
        super().__init__(parent)
        self._cfg = config_atual or carregar_config()

        self.setWindowFlags(
            Qt.Tool |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420, 260)
        self._construir_ui()
        self._centralizar()

    def _construir_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Container com borda
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 8, 18, 245);
                border: 1px solid {COR_AZUL};
                border-radius: 12px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # ── Cabeçalho ───────────────────────────────────────────────────────
        cab = QHBoxLayout()
        titulo = QLabel("⚙  CONFIGURAÇÕES")
        titulo.setFont(QFont("Consolas", 11, QFont.Bold))
        titulo.setStyleSheet(f"color: {COR_AZUL}; background: transparent;")
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(20, 20)
        btn_x.setCursor(Qt.PointingHandCursor)
        btn_x.setStyleSheet("""
            QPushButton { color: rgba(74,122,154,200); background: transparent; border: none; }
            QPushButton:hover { color: white; }
        """)
        btn_x.clicked.connect(self.close)
        cab.addWidget(titulo)
        cab.addStretch()
        cab.addWidget(btn_x)
        layout.addLayout(cab)

        # ── Divisor ─────────────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {COR_CINZA};")
        layout.addWidget(div)

        def _label(txt):
            l = QLabel(txt)
            l.setFont(QFont("Consolas", 9))
            l.setStyleSheet("color: rgba(74,122,154,220); background: transparent;")
            return l

        def _btn_toggle(texto_on, texto_off, ativo: bool):
            """Cria botão toggle estilo Jarvis."""
            btn = QPushButton(texto_on if ativo else texto_off)
            btn.setCheckable(True)
            btn.setChecked(ativo)
            btn.setFont(QFont("Consolas", 9, QFont.Bold))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn._texto_on  = texto_on
            btn._texto_off = texto_off

            def _atualizar(checked):
                btn.setText(btn._texto_on if checked else btn._texto_off)
                cor_on  = f"background: rgba(0,255,136,0.1); color: {COR_VERDE}; border: 1px solid {COR_VERDE};"
                cor_off = f"background: rgba(255,60,60,0.08); color: #FF5555; border: 1px solid #FF5555;"
                btn.setStyleSheet(f"""
                    QPushButton {{
                        {'background: rgba(0,255,136,0.1); color: ' + COR_VERDE + '; border: 1px solid ' + COR_VERDE + ';' if checked
                         else 'background: rgba(255,60,60,0.08); color: #FF5555; border: 1px solid #FF5555;'}
                        border-radius: 6px; letter-spacing: 1px;
                    }}
                    QPushButton:hover {{ opacity: 0.8; }}
                """)
            btn.toggled.connect(_atualizar)
            _atualizar(ativo)
            return btn

        # ── Modo wallpaper ───────────────────────────────────────────────────
        layout.addWidget(_label("MODO DE EXIBIÇÃO"))
        self._btn_wallpaper = _btn_toggle(
            "✓ PAPEL DE PAREDE (fundo)",
            "○ JANELA FLUTUANTE",
            self._cfg.get("modo_wallpaper", True)
        )
        layout.addWidget(self._btn_wallpaper)

        # ── Manter em background ─────────────────────────────────────────────
        layout.addWidget(_label("COMPORTAMENTO"))
        self._btn_bg = _btn_toggle(
            "✓ MANTER EM BACKGROUND (esfera sempre ativa)",
            "○ OCULTAR AO ABRIR O CHAT",
            self._cfg.get("aberto_em_bg", True)
        )
        layout.addWidget(self._btn_bg)

        # ── Botões de ação ───────────────────────────────────────────────────
        div2 = QFrame()
        div2.setFrameShape(QFrame.HLine)
        div2.setStyleSheet(f"color: {COR_CINZA};")
        layout.addWidget(div2)

        row_btns = QHBoxLayout()
        btn_aplicar = QPushButton("APLICAR")
        btn_aplicar.setFont(QFont("Consolas", 10, QFont.Bold))
        btn_aplicar.setFixedHeight(34)
        btn_aplicar.setCursor(Qt.PointingHandCursor)
        btn_aplicar.setStyleSheet(f"""
            QPushButton {{
                background: rgba(93,226,255,0.1);
                color: {COR_AZUL};
                border: 1px solid {COR_AZUL};
                border-radius: 6px;
                letter-spacing: 2px;
            }}
            QPushButton:hover {{ background: rgba(93,226,255,0.22); }}
            QPushButton:pressed {{ background: rgba(93,226,255,0.35); }}
        """)
        btn_aplicar.clicked.connect(self._aplicar)

        btn_cancelar = QPushButton("CANCELAR")
        btn_cancelar.setFont(QFont("Consolas", 10))
        btn_cancelar.setFixedHeight(34)
        btn_cancelar.setCursor(Qt.PointingHandCursor)
        btn_cancelar.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: rgba(74,122,154,180);
                border: 1px solid rgba(42,58,74,200);
                border-radius: 6px;
            }}
            QPushButton:hover {{ color: {COR_AZUL}; border-color: {COR_AZUL}; }}
        """)
        btn_cancelar.clicked.connect(self.close)

        row_btns.addWidget(btn_cancelar)
        row_btns.addWidget(btn_aplicar)
        layout.addLayout(row_btns)

        root.addWidget(frame)

    def _centralizar(self):
        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.center().x() - self.width()  // 2,
            screen.center().y() - self.height() // 2
        )

    def _aplicar(self):
        nova_cfg = {
            "modo_wallpaper": self._btn_wallpaper.isChecked(),
            "aberto_em_bg":   self._btn_bg.isChecked(),
        }
        salvar_config(nova_cfg)
        self._cfg = nova_cfg
        self.config_aplicada.emit(nova_cfg)
        self.hide()
        print(f"\033[92m[WALLPAPER]: Config salva — "
              f"wallpaper={nova_cfg['modo_wallpaper']}, "
              f"bg={nova_cfg['aberto_em_bg']}\033[0m")

# ---------------------------------------------------------------------------
# HUD de status — sempre visível acima dos ícones
# ---------------------------------------------------------------------------

class HUDStatus(QWidget):
    """
    Barra de status flutuante — fica acima de tudo (StaysOnTop + Tool).
    Mostra: hora | CPU | RAM | estado | botão para abrir chat
    """

    abrir_chat = Signal()   # emitido ao clicar no botão

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.Tool |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.WindowDoesNotAcceptFocus
        )
        self._construir_ui()

        self._timer = QTimer()
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _construir_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(12)

        font_mono  = QFont("Consolas", 10)
        font_bold  = QFont("Consolas", 9, QFont.Bold)

        self.lbl_hora   = self._lbl(font_mono, "--:--:--")
        self.lbl_cpu    = self._lbl(font_mono, "CPU --")
        self.lbl_ram    = self._lbl(font_mono, "RAM --")
        self.lbl_estado = self._lbl(font_bold, "STANDBY", COR_AZUL)

        self.btn_chat = QPushButton("⬛ SIRIUS")
        self.btn_chat.setFont(QFont("Consolas", 9, QFont.Bold))
        self.btn_chat.setFixedHeight(24)
        self.btn_chat.setCursor(Qt.PointingHandCursor)
        self.btn_chat.setStyleSheet(f"""
            QPushButton {{
                color: {COR_AZUL};
                background: rgba(0,0,0,0);
                border: 1px solid {COR_AZUL};
                border-radius: 4px;
                padding: 0 8px;
            }}
            QPushButton:hover {{
                background: rgba(93,226,255,0.15);
            }}
        """)
        self.btn_chat.clicked.connect(self.abrir_chat.emit)

        # Botão de configurações
        self.btn_config = QPushButton("⚙")
        self.btn_config.setFont(QFont("Consolas", 11))
        self.btn_config.setFixedSize(28, 24)
        self.btn_config.setCursor(Qt.PointingHandCursor)
        self.btn_config.setToolTip("Configurações do wallpaper")
        self.btn_config.setStyleSheet(f"""
            QPushButton {{
                color: rgba(74,122,154,180);
                background: transparent;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{ color: {COR_AZUL}; }}
        """)
        self.btn_config.clicked.connect(self._abrir_config)
        self._janela_config = None

        layout.addWidget(self.lbl_hora)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_cpu)
        layout.addWidget(self.lbl_ram)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_estado)
        layout.addWidget(self._sep())
        layout.addWidget(self.btn_chat)
        layout.addWidget(self.btn_config)

        self.setStyleSheet("""
            QWidget {
                background: rgba(0, 5, 12, 175);
                border-radius: 6px;
            }
        """)
        self.setFixedHeight(36)

    def _lbl(self, fonte, txt, cor="rgba(74,122,154,220)"):
        l = QLabel(txt)
        l.setFont(fonte)
        l.setStyleSheet(f"color: {cor}; background: transparent;")
        return l

    def _sep(self):
        s = QLabel("│")
        s.setStyleSheet("color: rgba(42,58,74,200); background: transparent;")
        return s

    def _abrir_config(self):
        """Abre o painel de configurações."""
        if self._janela_config and self._janela_config.isVisible():
            self._janela_config.raise_()
            return
        self._janela_config = JanelaConfig()
        self._janela_config.config_aplicada.connect(self._on_config_aplicada)
        self._janela_config.show()

    def _on_config_aplicada(self, cfg: dict):
        """Recebe nova config — avisa o wallpaper window para aplicar."""
        # A SiriusWallpaperWindow se conecta a este sinal via HUD
        pass

    def _tick(self):
        self.lbl_hora.setText(datetime.now().strftime("%H:%M:%S"))
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            self.lbl_cpu.setText(f"CPU {cpu:.0f}%")
            self.lbl_ram.setText(f"RAM {ram:.0f}%")
            self.lbl_cpu.setStyleSheet(
                f"color: {COR_AMARELO}; background:transparent;" if cpu > 85
                else "color: rgba(74,122,154,220); background:transparent;"
            )
            self.lbl_ram.setStyleSheet(
                f"color: {COR_AMARELO}; background:transparent;" if ram > 88
                else "color: rgba(74,122,154,220); background:transparent;"
            )
        except Exception:
            pass

    def set_estado(self, estado: str):
        cores = {
            ESTADO_STANDBY:     COR_AZUL,
            ESTADO_OUVINDO:     COR_VERDE,
            ESTADO_PROCESSANDO: COR_AMARELO,
            ESTADO_FALANDO:     COR_BRANCO,
        }
        cor = cores.get(estado, COR_AZUL)
        self.lbl_estado.setText(estado)
        self.lbl_estado.setStyleSheet(f"color: {cor}; background: transparent; font-weight: bold;")
        # Atualiza ícone do botão
        icone = {"OUVINDO": "🎙", "PROCESSANDO": "⚙", "FALANDO": "🔊"}.get(estado, "⬛")
        self.btn_chat.setText(f"{icone} SIRIUS")

    def posicionar(self, screen: QRect):
        self.adjustSize()
        self.move(screen.right() - self.width() - 20, screen.top() + 12)


# ---------------------------------------------------------------------------
# Chat Overlay — janela de chat que emerge do canto inferior direito
# ---------------------------------------------------------------------------

class ChatOverlay(QWidget):
    """
    Janela de chat flutuante que aparece/desaparece com animação.
    Fica acima de tudo, no canto inferior direito.
    Não aparece na taskbar.
    """

    comando_enviado = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.Tool |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.WindowDoesNotAcceptFocus
        )
        self.setFixedSize(400, 480)
        self._construir_ui()
        self._posicionar()

        # Animação de entrada/saída
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self.hide()

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container principal
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 5, 12, 230);
                border: 1px solid {COR_AZUL};
                border-radius: 12px;
            }}
        """)
        cont_layout = QVBoxLayout(container)
        cont_layout.setContentsMargins(12, 10, 12, 12)
        cont_layout.setSpacing(8)

        # Cabeçalho
        header = QHBoxLayout()
        lbl_title = QLabel("S.I.R.I.U.S.")
        lbl_title.setFont(QFont("Consolas", 11, QFont.Bold))
        lbl_title.setStyleSheet(f"color: {COR_AZUL}; background: transparent;")
        btn_fechar = QPushButton("✕")
        btn_fechar.setFixedSize(20, 20)
        btn_fechar.setStyleSheet(f"""
            QPushButton {{
                color: rgba(74,122,154,200); background: transparent; border: none;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {COR_AZUL}; }}
        """)
        btn_fechar.clicked.connect(self.fechar_animado)
        header.addWidget(lbl_title)
        header.addStretch()
        header.addWidget(btn_fechar)

        # Histórico
        self.historico = QTextEdit()
        self.historico.setReadOnly(True)
        self.historico.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: rgba(93,226,255,210);
                border: none;
                font-family: Consolas;
                font-size: 12px;
            }}
            QScrollBar:vertical {{
                background: rgba(0,0,0,0);
                width: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(93,226,255,80);
                border-radius: 2px;
            }}
        """)

        # Input
        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Digite ou fale 'Sirius'...")
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(93,226,255,0.06);
                border: 1px solid rgba(93,226,255,0.3);
                border-radius: 8px;
                color: white;
                padding: 8px 12px;
                font-family: Consolas;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {COR_AZUL};
            }}
        """)
        self.input.returnPressed.connect(self._enviar)

        btn_env = QPushButton("▶")
        btn_env.setFixedSize(36, 36)
        btn_env.setStyleSheet(f"""
            QPushButton {{
                background: rgba(93,226,255,0.12);
                border: 1px solid {COR_AZUL};
                border-radius: 8px;
                color: {COR_AZUL};
                font-size: 14px;
            }}
            QPushButton:hover {{ background: rgba(93,226,255,0.25); }}
        """)
        btn_env.clicked.connect(self._enviar)

        input_row.addWidget(self.input)
        input_row.addWidget(btn_env)

        cont_layout.addLayout(header)
        cont_layout.addWidget(self.historico)
        cont_layout.addLayout(input_row)

        layout.addWidget(container)

    def _posicionar(self):
        screen = QApplication.primaryScreen().geometry()
        self._posicionar_em(screen)

    def _posicionar_em(self, screen):
        """Posiciona o chat no canto inferior direito do monitor especificado."""
        x = screen.right()  - self.width()  - 24
        y = screen.bottom() - self.height() - 60
        self.move(x, y)

    def abrir_animado(self, screen=None):
        if screen is None:
            screen = QApplication.primaryScreen().geometry()
        x_final = screen.right()  - self.width()  - 24
        y_final = screen.bottom() - self.height() - 60
        # Começa 40px abaixo (efeito de subir)
        self.move(x_final, y_final + 40)
        self.show()
        self.raise_()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(x_final, y_final))
        self._anim.start()
        self.input.setFocus()

    def fechar_animado(self):
        screen = QApplication.primaryScreen().geometry()
        x = screen.right() - self.width() - 24
        y = screen.bottom() - self.height() - 60
        self._anim.setStartValue(QPoint(x, y))
        self._anim.setEndValue(QPoint(x, y + 40))
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def toggle(self):
        if self.isVisible():
            self.fechar_animado()
        else:
            self.abrir_animado()

    def _enviar(self):
        txt = self.input.text().strip()
        if not txt:
            return
        self.input.clear()
        self.adicionar_mensagem("Você", txt, COR_BRANCO)
        self.comando_enviado.emit(txt)

    def adicionar_mensagem(self, quem: str, msg: str, cor: str = COR_AZUL):
        hora = datetime.now().strftime("%H:%M")
        html = (
            f"<p style='margin:3px 0;'>"
            f"<span style='color:rgba(74,122,154,180);font-size:10px;'>{hora}</span> "
            f"<span style='color:{cor};font-weight:bold;'>{quem}:</span> "
            f"<span style='color:{cor};'>{msg}</span>"
            f"</p>"
        )
        self.historico.append(html)
        self.historico.verticalScrollBar().setValue(
            self.historico.verticalScrollBar().maximum()
        )


# ---------------------------------------------------------------------------
# Worker de voz (roda em background)
# ---------------------------------------------------------------------------

class _VozWorker(QThread):
    """Worker simplificado que escuta voz e emite respostas."""

    estado_mudou      = Signal(str)
    comando_detectado = Signal(str)
    resposta_pronta   = Signal(str)

    def __init__(self, audio, cerebro):
        super().__init__()
        self.audio          = audio
        self.cerebro        = cerebro
        self.rodando        = True
        self.modo_voz_ativo = True
        self._cmd_manual    = None
        self._lock          = threading.Lock()

    def enviar_texto(self, texto: str):
        with self._lock:
            self._cmd_manual = texto

    def run(self):
        while self.rodando:
            try:
                fala = None
                with self._lock:
                    if self._cmd_manual:
                        fala = self._cmd_manual
                        self._cmd_manual = None

                if not fala and self.modo_voz_ativo:
                    self.estado_mudou.emit(ESTADO_OUVINDO)
                    try:
                        fala = self.audio.escutar_fluxo_continuo()
                    except Exception as e:
                        print(f"[WALLPAPER WORKER]: {e}")
                        time.sleep(1)
                        continue

                if fala:
                    self.estado_mudou.emit(ESTADO_PROCESSANDO)
                    self.comando_detectado.emit(str(fala))
                    tem_wake = "sirius" in str(fala).lower()
                    resp = self.cerebro.processar(fala, forcar_processamento=not tem_wake)
                    if resp:
                        self.resposta_pronta.emit(str(resp))
                        self.estado_mudou.emit(ESTADO_FALANDO)
                        try:
                            self.audio.falar(resp)
                        except Exception as e:
                            print(f"[WALLPAPER WORKER TTS]: {e}")
                        finally:
                            self.estado_mudou.emit(ESTADO_STANDBY)
                else:
                    self.estado_mudou.emit(ESTADO_STANDBY)

                time.sleep(0.01)

            except Exception as e:
                print(f"[WALLPAPER WORKER]: Erro inesperado: {e}")
                time.sleep(1)

    def parar(self):
        """Para o worker com timeout — evita travar o closeEvent."""
        self.rodando        = False
        self.modo_voz_ativo = False
        self.quit()
        # Timeout de 2s — se o worker estiver bloqueado em I/O de áudio,
        # não esperamos para sempre. A thread é daemon, o OS a limpa ao sair.
        if not self.wait(2000):
            print("[WALLPAPER WORKER]: timeout no shutdown — forçando.")
            self.terminate()


# ---------------------------------------------------------------------------
# Janela principal de wallpaper
# ---------------------------------------------------------------------------

class SiriusWallpaperWindow(QMainWindow):
    """
    Janela que exibe a esfera 3D como papel de parede real.
    É embebida no WorkerW do Windows (atrás dos ícones).
    Toda a interação acontece via HUD e ChatOverlay separados.
    """

    def __init__(self, cerebro=None, config: dict = None):
        super().__init__()
        self._cerebro = cerebro
        self._worker  = None
        self._cfg     = config or carregar_config()
        self._app_aberto = False

        # Sempre monitor primário
        screen = _obter_geometria_desktop()

        # Flags para modo wallpaper
        # SEM WA_TranslucentBackground — no modo HWND_BOTTOM o compositor
        # do Windows não aplica transparência e a janela some.
        # O fundo preto do OpenGL fica invisível pois fica atrás do wallpaper.
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("SIRIUS_WALLPAPER")
        self.setGeometry(screen)

        # Esfera 3D — ocupa a tela inteira, SEM transparência
        self._esfera = SiriusNexus3DView()
        self.setCentralWidget(self._esfera)

        # HUD — janela separada, sempre no topo
        self._hud = HUDStatus()
        self._hud.posicionar(screen)
        self._hud.show()
        # Recebe config aplicada pelo HUD e reaplicha em tempo real
        self._hud._on_config_aplicada = self._aplicar_config

        # Chat overlay — janela separada, aparece ao ativar
        self._chat = ChatOverlay()
        self._chat.comando_enviado.connect(self._on_texto_manual)
        self._hud.abrir_chat.connect(self._chat.toggle)

        # Injeta callbacks no cérebro
        if cerebro:
            if hasattr(cerebro, "registrar_callback"):
                cerebro.registrar_callback(
                    callback_falar=self._falar_e_mostrar,
                    callback_log=lambda t: self._chat.adicionar_mensagem("Sirius", t, COR_AZUL)
                )
            self._iniciar_worker()

        # Fixa no fundo do Windows após a janela estar visível
        QTimer.singleShot(400, self._fixar_workerw)
        # Hotkey global Ctrl+Alt+S
        QTimer.singleShot(600, self._registrar_hotkey)
        # Monitor de WM_HOTKEY (Windows)
        self._timer_hotkey = QTimer(self)
        self._timer_hotkey.timeout.connect(self._checar_hotkey)
        self._timer_hotkey.start(100)

        self._hotkey_id  = 1
        self._em_wallpaper = True   # controla se o timer de reposicionamento está ativo

        # Timer para manter HWND_BOTTOM — o Windows pode subir a janela quando
        # o usuário clica no desktop ou abre outra janela.
        # Intervalo de 2s é suficiente (imperceptível e não drena CPU).
        self._timer_fundo = QTimer(self)
        self._timer_fundo.timeout.connect(self._manter_no_fundo)
        self._timer_fundo.start(2000)

    def _fixar_workerw(self):
        """Fixa a janela no WorkerW via proxy Win32 no monitor primário."""
        self._esfera._parar_timer()        # evita GetDC failed durante SetParent

        hwnd   = int(self.winId())
        screen = _obter_geometria_desktop()
        ok     = _fixar_workerw(hwnd, screen)

        from PySide6.QtWidgets import QApplication as _App
        _App.processEvents()

        if ok:
            QTimer.singleShot(350, self._esfera._retomar_timer)
        else:
            self.setWindowFlag(Qt.WindowStaysOnBottomHint, True)
            self.show()
            self._esfera._retomar_timer()
            print("[WALLPAPER]: Fallback Qt.WindowStaysOnBottomHint ativo.")

    def _manter_no_fundo(self):
        """Não faz nada — proxy Win32 mantém posição sem SetWindowPos periódico."""
        pass

    def _registrar_hotkey(self):
        hwnd = int(self.winId())
        MOD_CTRL = 0x0002
        MOD_ALT  = 0x0001
        VK_S     = 0x53
        ok = _registrar_hotkey_global(hwnd, self._hotkey_id,
                                       MOD_CTRL | MOD_ALT, VK_S)
        if ok:
            print("[WALLPAPER]: Hotkey Ctrl+Alt+S registrada → abre chat.")
        else:
            print("[WALLPAPER]: Hotkey Ctrl+Alt+S ocupada — use o botão no HUD.")

    def _checar_hotkey(self):
        """Processa mensagem WM_HOTKEY da fila do Windows."""
        try:
            msg = ctypes.wintypes.MSG()
            WM_HOTKEY = 0x0312
            PM_REMOVE  = 0x0001
            if ctypes.windll.user32.PeekMessageW(
                ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE
            ):
                if msg.wParam == self._hotkey_id:
                    self._chat.toggle()
        except Exception:
            pass

    def _iniciar_worker(self):
        try:
            from audio_handler import SiriusAudio
            audio = SiriusAudio()
            self._worker = _VozWorker(audio, self._cerebro)
            self._worker.estado_mudou.connect(self._on_estado)
            self._worker.comando_detectado.connect(
                lambda t: self._chat.adicionar_mensagem("Você", t, COR_BRANCO)
            )
            self._worker.resposta_pronta.connect(
                lambda t: self._chat.adicionar_mensagem("Sirius", t, COR_AZUL)
            )
            self._worker.start()
            print("\033[92m[WALLPAPER]: Worker de voz ativo.\033[0m")
        except Exception as e:
            print(f"[WALLPAPER]: Worker falhou: {e}")

    def _on_estado(self, estado: str):
        self._esfera.set_estado(estado)
        self._hud.set_estado(estado)

    def _on_texto_manual(self, texto: str):
        if self._worker:
            self._worker.enviar_texto(f"sirius {texto}")

    def _falar_e_mostrar(self, texto: str):
        """Callback: fala em voz e mostra no chat overlay."""
        self._chat.adicionar_mensagem("Sirius", texto, COR_AZUL)
        # Chat aparece brevemente quando há mensagem proativa
        if not self._chat.isVisible():
            self._chat.abrir_animado()
            QTimer.singleShot(8000, self._chat.fechar_animado)

    def _aplicar_config(self, cfg: dict):
        """
        Aplica nova configuração em tempo real:
        - Muda monitor (reposiciona esfera + HUD)
        - Liga/desliga modo wallpaper
        - Atualiza comportamento de background
        """
        self._cfg = cfg
        salvar_config(cfg)

        novo_monitor = cfg.get("monitor_idx", 0)
        screen = _obter_geometria_desktop(novo_monitor)

        # Reposiciona a esfera no monitor correto
        self.setGeometry(screen)
        self._hud.posicionar(screen)
        self._chat._posicionar_em(screen)

        # Aplica modo wallpaper
        if cfg.get("modo_wallpaper", True):
            self._em_wallpaper = True
            self._timer_fundo.start(2000)
            QTimer.singleShot(200, self._fixar_workerw)
            print(f"[WALLPAPER]: Modo wallpaper → monitor {novo_monitor + 1}")
        else:
            # Modo janela flutuante — para o timer de reposicionamento
            self._em_wallpaper = False
            self._timer_fundo.stop()
            try:
                # Remove HWND_BOTTOM — volta a se comportar como janela normal
                HWND_NOTOPMOST = -2
                SWP_NOMOVE     = 0x0002
                SWP_NOSIZE     = 0x0001
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()), HWND_NOTOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                )
                self.setWindowFlag(Qt.WindowStaysOnBottomHint, False)
                self.show()
            except Exception:
                pass
            print(f"[WALLPAPER]: Modo janela flutuante → monitor {novo_monitor + 1}")

        print(f"[WALLPAPER]: Background={'ativo' if cfg.get('aberto_em_bg') else 'inativo'}")

    def closeEvent(self, event):
        """
        Shutdown em ordem segura para evitar:
          - GetDC failed: timers disparando após janela destruída
          - startTimer: event dispatcher destroyed: worker tentando
            emitir signals após o loop de eventos já ter terminado
        """
        # 1. Para o hook Win32 PRIMEIRO — ele acessa _hwnd_esfera
        _parar_hook_zorder()
        _destruir_proxy()

        # 2. Invalida o HWND global — hook thread para de usá-lo
        global _hwnd_esfera
        _hwnd_esfera = 0

        # 3. Para TODOS os QTimers imediatamente
        if hasattr(self, '_timer_fundo')  and self._timer_fundo.isActive():
            self._timer_fundo.stop()
        if hasattr(self, '_timer_hotkey') and self._timer_hotkey.isActive():
            self._timer_hotkey.stop()

        # 4. Para o timer interno da esfera (evita GetDC failed)
        if hasattr(self, '_esfera'):
            self._esfera._parar_timer()

        # 5. Para o worker de voz (join com timeout)
        if self._worker:
            self._worker.parar()

        # 6. Fecha sub-janelas (elas têm seus próprios timers)
        if hasattr(self, '_hud')  and self._hud:
            self._hud.close()
        if hasattr(self, '_chat') and self._chat:
            self._chat.close()

        # 7. Libera hotkey Win32
        try:
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), self._hotkey_id)
        except Exception:
            pass

        # 8. Salva estado
        if self._cerebro and hasattr(self._cerebro, "memoria") and self._cerebro.memoria:
            try:
                self._cerebro.memoria.salvar_estado("ultimo_modo", MODO_WALLPAPER)
            except Exception:
                pass

        event.accept()


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

def iniciar_wallpaper(cerebro=None, modo: str = None):
    """
    Inicializa o Sirius no modo especificado.
    Se modo=None, usa o modo salvo na config (padrão: wallpaper).
    Retorna (QApplication, janela) para o main chamar app.exec().
    """
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("S.I.R.I.U.S.")
    app.setQuitOnLastWindowClosed(False)

    # Lê config — respeita modo salvo se não foi especificado na linha de comando
    cfg = carregar_config()
    if modo is None:
        modo = MODO_WALLPAPER if cfg.get("modo_wallpaper", True) else MODO_ATIVO

    # Salva modo na memória do cerebro
    if cerebro and hasattr(cerebro, "memoria") and cerebro.memoria:
        try:
            cerebro.memoria.salvar_estado("ultimo_modo", modo)
            cerebro.memoria.salvar_estado(
                "monitor_idx", str(cfg.get("monitor_idx", 0)))
            print(f"\033[92m[WALLPAPER]: Modo '{modo}' salvo na memória.\033[0m")
        except Exception as e:
            print(f"[WALLPAPER]: Aviso ao salvar estado: {e}")

    if modo == MODO_WALLPAPER:
        janela = SiriusWallpaperWindow(cerebro=cerebro, config=cfg)
        janela.show()

    elif modo == MODO_FULLSCREEN:
        try:
            from interface import SiriusInterfaceMainWindow
            janela = SiriusInterfaceMainWindow(cerebro=cerebro)
            janela.showFullScreen()
        except ImportError:
            janela = SiriusWallpaperWindow(cerebro=cerebro)
            janela.showFullScreen()

    else:  # MODO_ATIVO
        try:
            from interface import SiriusInterfaceMainWindow
            janela = SiriusInterfaceMainWindow(cerebro=cerebro)
            janela.show()
        except ImportError:
            janela = SiriusWallpaperWindow(cerebro=cerebro)
            janela.show()

    return app, janela