"""
sirius_wallpaper.py — Sirius como papel de parede real do Windows

Como funciona de verdade (mesma técnica do Wallpaper Engine):
  1. Encontra o processo Progman (dono do desktop)
  2. Envia mensagem 0x052C → Windows cria o WorkerW atrás dos ícones
  3. SetParent(nossa_janela → WorkerW) → ficamos ATRÁS dos ícones
  4. Ajuste de estilos Win32 (WS_CHILD, remove WS_POPUP) → integração real

Arquitetura de janelas:
  ┌─────────────────────────────────────────────┐
  │  Taskbar / Alt+Tab / janelas normais         │ ← camada normal
  ├─────────────────────────────────────────────┤
  │  HUDStatus  (Tool + StaysOnTop + NoFocus)   │ ← sempre visível
  │  ChatOverlay (Tool + StaysOnTop, escondido) │ ← aparece ao ativar
  ├─────────────────────────────────────────────┤
  │  Ícones do desktop                           │ ← WorkerW
  ├─────────────────────────────────────────────┤
  │  SiriusWallpaperWindow  ← AQUI (WorkerW)    │ ← esfera Jarvis
  └─────────────────────────────────────────────┘

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
import ctypes
import ctypes.wintypes
import math
import random
import threading
import time
from datetime import datetime

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QLineEdit, QPushButton, QSizePolicy, QFrame,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QPoint, QSize,
    QPropertyAnimation, QEasingCurve, QRect,
)
from PySide6.QtGui import QFont, QColor, QCursor, QKeySequence, QShortcut
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

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
# Win32 — funções auxiliares para fixar no fundo
# ---------------------------------------------------------------------------

def _fixar_workerw(hwnd_qt: int) -> bool:
    """
    Embute a janela Qt no WorkerW do Windows (atrás dos ícones do desktop).
    Retorna True se conseguiu fixar de verdade.

    Passos:
      1. FindWindow("Progman") → janela pai do desktop
      2. SendMessageTimeout(0x052C) → cria o WorkerW se não existir
      3. EnumWindows → encontra o HWND do WorkerW
      4. SetParent(nossa_janela, workerw) → embute de verdade
      5. Ajusta estilos Win32 para remover decorações de janela filha
    """
    try:
        user32  = ctypes.windll.user32
        WS_CHILD         = 0x40000000
        WS_POPUP         = 0x80000000
        GWL_STYLE        = -16
        GWL_EXSTYLE      = -20
        WS_EX_APPWINDOW  = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080

        # 1. Progman
        progman = user32.FindWindowW("Progman", None)
        if not progman:
            print("[WALLPAPER]: Progman não encontrado.")
            return False

        # 2. Cria WorkerW
        user32.SendMessageTimeoutW(
            progman, 0x052C, 0, 0, 0, 2000,
            ctypes.byref(ctypes.c_ulong())
        )

        # 3. Encontra WorkerW
        workerw = ctypes.c_void_p(0)

        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _enum(hwnd_w, _):
            shell = user32.FindWindowExW(hwnd_w, None, "SHELLDLL_DefView", None)
            if shell:
                workerw.value = user32.FindWindowExW(None, hwnd_w, "WorkerW", None)
            return True

        user32.EnumWindows(EnumProc(_enum), None)

        if not workerw.value:
            # Tenta encontrar via Progman diretamente (Windows 11)
            workerw.value = user32.FindWindowExW(progman, None, "WorkerW", None)

        if not workerw.value:
            print("[WALLPAPER]: WorkerW não encontrado — esfera flutuante.")
            return False

        # 4. SetParent
        user32.SetParent(hwnd_qt, workerw.value)

        # 5. Ajusta estilos — transforma em janela filho (remove WS_POPUP)
        estilo = user32.GetWindowLongPtrW(hwnd_qt, GWL_STYLE)
        estilo = (estilo & ~WS_POPUP) | WS_CHILD
        user32.SetWindowLongPtrW(hwnd_qt, GWL_STYLE, estilo)

        # Remove da taskbar e Alt+Tab
        ex = user32.GetWindowLongPtrW(hwnd_qt, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        user32.SetWindowLongPtrW(hwnd_qt, GWL_EXSTYLE, ex)

        print("\033[92m[WALLPAPER]: ✓ Esfera fixada no fundo do desktop (WorkerW).\033[0m")
        return True

    except Exception as e:
        print(f"[WALLPAPER]: Erro ao fixar no fundo: {e}")
        return False


def _registrar_hotkey_global(hwnd: int, id_hotkey: int,
                              mod: int, vk: int) -> bool:
    """Registra hotkey global via Win32 RegisterHotKey."""
    try:
        return bool(ctypes.windll.user32.RegisterHotKey(hwnd, id_hotkey, mod, vk))
    except Exception:
        return False


def _obter_geometria_desktop() -> QRect:
    """Retorna o retângulo do desktop primário."""
    screen = QApplication.primaryScreen()
    return screen.geometry()


# ---------------------------------------------------------------------------
# Esfera 3D OpenGL (reutilizada do interface.py)
# ---------------------------------------------------------------------------

class SiriusNexus3DView(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(QSize(200, 200))
        self.rotation      = 0.0
        self.pulse         = 0.0
        self._estado       = ESTADO_STANDBY
        self._falando      = False
        self.COR_NEON_AZUL = (93/255, 226/255, 255/255)
        self.COR_BRANCO    = (1.0, 1.0, 1.0)
        self.COR_VERDE     = (0.0, 1.0, 0.53)
        self.COR_AMARELO   = (1.0, 0.84, 0.0)

        self.pontos_plexus = []
        self.pontos_nucleo = []

        for _ in range(1200):
            phi      = random.uniform(0, 2 * math.pi)
            costheta = random.uniform(-1, 1)
            theta    = math.acos(costheta)
            r        = 2.4
            x = r * math.sin(theta) * math.cos(phi)
            y = r * math.sin(theta) * math.sin(phi)
            z = r * math.cos(theta)
            self.pontos_plexus.append({"pos": [x, y, z], "orig": [x, y, z]})

        for _ in range(500):
            r2       = random.uniform(0, 0.6)
            phi      = random.uniform(0, 2 * math.pi)
            costheta = random.uniform(-1, 1)
            theta    = math.acos(costheta)
            self.pontos_nucleo.append([
                r2 * math.sin(theta) * math.cos(phi),
                r2 * math.sin(theta) * math.sin(phi),
                r2 * math.cos(theta),
            ])

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animar)
        self._timer.start(16)

    def set_estado(self, estado: str):
        self._estado  = estado
        self._falando = estado == ESTADO_FALANDO
        self.update()

    def _cor_estado(self):
        return {
            ESTADO_STANDBY:     self.COR_NEON_AZUL,
            ESTADO_OUVINDO:     self.COR_VERDE,
            ESTADO_PROCESSANDO: self.COR_AMARELO,
            ESTADO_FALANDO:     self.COR_BRANCO,
        }.get(self._estado, self.COR_NEON_AZUL)

    def _animar(self):
        vel = 1.2 if self._falando else (0.8 if self._estado == ESTADO_OUVINDO else 0.4)
        self.rotation += vel
        self.pulse    += 0.08
        amp    = 0.15 if self._falando else 0.03
        factor = 1.0 + math.sin(self.pulse) * amp
        for p in self.pontos_plexus:
            p["pos"] = [v * factor for v in p["orig"]]
        self.update()

    def initializeGL(self):
        glClearColor(0.0, 0.0, 0.0, 0.0)   # fundo transparente
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -9.0)

        cor = self._cor_estado()

        glPushMatrix()
        glRotatef(self.rotation, 0, 1, 0)
        glRotatef(self.rotation * 0.5, 1, 0, 0)

        glBegin(GL_LINES)
        for i in range(0, len(self.pontos_plexus), 12):
            p1 = self.pontos_plexus[i]["pos"]
            for j in range(i + 1, min(i + 30, len(self.pontos_plexus))):
                p2   = self.pontos_plexus[j]["pos"]
                dist = math.dist(p1, p2)
                if dist < 0.8:
                    alpha = (1.0 - dist / 0.8) * (0.4 if self._falando else 0.18)
                    glColor4f(*cor, alpha)
                    glVertex3f(*p1)
                    glVertex3f(*p2)
        glEnd()

        glPointSize(2.0)
        glBegin(GL_POINTS)
        for p in self.pontos_plexus:
            glColor4f(*cor, 0.5)
            glVertex3f(*p["pos"])
        glEnd()
        glPopMatrix()

        glBegin(GL_POINTS)
        for p in self.pontos_nucleo:
            glColor4f(*cor, random.uniform(0.4, 0.9))
            glVertex3f(*p)
        glEnd()

    def resizeGL(self, w, h):
        glViewport(0, 0, w, max(h, 1))
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, w / max(h, 1), 0.1, 50.0)
        glMatrixMode(GL_MODELVIEW)


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

        self._timer = QTimer(self)
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

        layout.addWidget(self.lbl_hora)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_cpu)
        layout.addWidget(self.lbl_ram)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_estado)
        layout.addWidget(self._sep())
        layout.addWidget(self.btn_chat)

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
        x = screen.right()  - self.width()  - 24
        y = screen.bottom() - self.height() - 60
        self.move(x, y)

    def abrir_animado(self):
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
        self.rodando = False
        self.quit()
        self.wait()


# ---------------------------------------------------------------------------
# Janela principal de wallpaper
# ---------------------------------------------------------------------------

class SiriusWallpaperWindow(QMainWindow):
    """
    Janela que exibe a esfera 3D como papel de parede real.
    É embebida no WorkerW do Windows (atrás dos ícones).
    Toda a interação acontece via HUD e ChatOverlay separados.
    """

    def __init__(self, cerebro=None):
        super().__init__()
        self._cerebro = cerebro
        self._worker  = None

        screen = _obter_geometria_desktop()

        # Flags para compatibilidade com SetParent / WorkerW
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("SIRIUS_WALLPAPER")
        self.setGeometry(screen)

        # Esfera 3D — ocupa a tela inteira
        self._esfera = SiriusNexus3DView()
        self._esfera.setAttribute(Qt.WA_TranslucentBackground)
        self.setCentralWidget(self._esfera)

        # HUD — janela separada, sempre no topo
        self._hud = HUDStatus()
        self._hud.posicionar(screen)
        self._hud.show()

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

        self._hotkey_id = 1

    def _fixar_workerw(self):
        hwnd = int(self.winId())
        ok   = _fixar_workerw(hwnd)
        if not ok:
            # Fallback: fica só com StaysOnBottom sem WorkerW
            self.setWindowFlag(Qt.WindowStaysOnBottomHint, True)
            self.show()

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

    def closeEvent(self, event):
        try:
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), self._hotkey_id)
        except Exception:
            pass
        if self._worker:
            self._worker.parar()
        self._hud.close()
        self._chat.close()
        event.accept()


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

def iniciar_wallpaper(cerebro=None, modo: str = MODO_WALLPAPER):
    """
    Inicializa o Sirius no modo especificado.
    Retorna (QApplication, janela) para o main chamar app.exec().
    """
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("S.I.R.I.U.S.")
    app.setQuitOnLastWindowClosed(False)

    if modo == MODO_WALLPAPER:
        janela = SiriusWallpaperWindow(cerebro=cerebro)
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