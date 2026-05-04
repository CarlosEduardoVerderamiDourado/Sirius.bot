import sys
import math
import random
import threading
import os
import time
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
    QWidget, QLabel, QFrame, QLineEdit, QPushButton,
    QTextEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

caminho_atual = os.path.dirname(os.path.abspath(__file__))
if caminho_atual not in sys.path:
    sys.path.append(caminho_atual)

from audio_handler import SiriusAudio

COR_FUNDO        = "#000000"
COR_AZUL_NEON    = "#5DE2FF"
COR_AZUL_ESCURO  = "#0a1a2a"
COR_VERDE        = "#00FF88"
COR_BRANCO       = "#FFFFFF"
COR_AMARELO      = "#FFD700"
COR_CINZA        = "#2a3a4a"
COR_TEXTO_HORA   = "#4a7a9a"

ESTADO_STANDBY     = "STANDBY"
ESTADO_OUVINDO     = "OUVINDO"
ESTADO_PROCESSANDO = "PROCESSANDO"
ESTADO_FALANDO     = "FALANDO"


class SiriusWorker(QThread):
    comando_detectado = Signal(str)
    resposta_pronta   = Signal(str)
    estado_mudou      = Signal(str)

    def __init__(self, audio_sys, cerebro):
        super().__init__()
        self.audio          = audio_sys
        self.cerebro        = cerebro
        self.comando_manual = None
        self.lock           = threading.Lock()
        self.rodando        = True
        self.modo_voz_ativo = True

    def enviar_comando_texto(self, texto: str):
        with self.lock:
            self.comando_manual = texto

    def run(self):
        print("\033[94m[WORKER]: Núcleo de processamento ativo.\033[0m")
        while self.rodando:
            fala_usuario = None
            with self.lock:
                if self.comando_manual:
                    fala_usuario        = self.comando_manual
                    self.comando_manual = None

            if not fala_usuario and self.modo_voz_ativo:
                self.estado_mudou.emit(ESTADO_OUVINDO)
                try:
                    resultado_voz = self.audio.escutar_fluxo_continuo()
                    if resultado_voz:
                        fala_usuario = resultado_voz
                    else:
                        self.estado_mudou.emit(ESTADO_STANDBY)
                except Exception as e:
                    print(f"[ERRO ÁUDIO]: {e}")
                    self.estado_mudou.emit(ESTADO_STANDBY)
                    time.sleep(1)
                    continue

            if fala_usuario:
                comando_str = str(fala_usuario).strip()
                if not comando_str:
                    time.sleep(0.01)
                    continue
                self.estado_mudou.emit(ESTADO_PROCESSANDO)
                self.comando_detectado.emit(comando_str)
                tem_wake_word = "sirius" in comando_str.lower()
                resposta = self.cerebro.processar(
                    comando_str,
                    forcar_processamento=not tem_wake_word
                )
                if resposta:
                    self.resposta_pronta.emit(str(resposta))
                    self.estado_mudou.emit(ESTADO_FALANDO)
                    try:
                        self.audio.falar(resposta)
                    finally:
                        self.estado_mudou.emit(ESTADO_STANDBY)
                else:
                    self.estado_mudou.emit(ESTADO_STANDBY)
            time.sleep(0.01)

    def parar(self):
        self.rodando = False
        self.quit()
        self.wait()


class SiriusNexus3DView(QOpenGLWidget):
    _CORES = {
        ESTADO_STANDBY:     (93/255, 226/255, 255/255),
        ESTADO_OUVINDO:     (0/255,  255/255, 136/255),
        ESTADO_PROCESSANDO: (255/255, 215/255, 0/255),
        ESTADO_FALANDO:     (1.0,    1.0,     1.0),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(QSize(580, 360))
        self._estado   = ESTADO_STANDBY
        self._cor      = self._CORES[ESTADO_STANDBY]
        self._cor_alvo = self._cor
        self.rotation  = 0.0
        self.rotation2 = 0.0
        self.pulse     = 0.0

        self.pontos_plexus = []
        for _ in range(900):
            phi      = random.uniform(0, 2 * math.pi)
            costheta = random.uniform(-1, 1)
            theta    = math.acos(costheta)
            r        = 2.2
            x = r * math.sin(theta) * math.cos(phi)
            y = r * math.sin(theta) * math.sin(phi)
            z = r * math.cos(theta)
            self.pontos_plexus.append({"pos": [x,y,z], "orig": [x,y,z]})

        self.pontos_nucleo = []
        for _ in range(300):
            r_c = random.uniform(0.0, 0.5)
            phi = random.uniform(0, 2 * math.pi)
            cos = random.uniform(-1, 1)
            the = math.acos(cos)
            self.pontos_nucleo.append([
                r_c*math.sin(the)*math.cos(phi),
                r_c*math.sin(the)*math.sin(phi),
                r_c*math.cos(the),
            ])

        N = 120
        self.anel1 = [(2.8*math.cos(2*math.pi*i/N), 2.8*math.sin(2*math.pi*i/N), 0.0) for i in range(N)]
        self.anel2 = [(2.6*math.cos(2*math.pi*i/N), 0.0, 2.6*math.sin(2*math.pi*i/N)) for i in range(N)]

        self.timer = QTimer()
        self.timer.timeout.connect(self._animar)
        self.timer.start(16)

    def set_estado(self, estado: str):
        self._estado   = estado
        self._cor_alvo = self._CORES.get(estado, self._CORES[ESTADO_STANDBY])

    @property
    def esta_falando(self):
        return self._estado == ESTADO_FALANDO

    @esta_falando.setter
    def esta_falando(self, v):
        self.set_estado(ESTADO_FALANDO if v else ESTADO_STANDBY)

    def _animar(self):
        self._cor = (
            self._cor[0] + (self._cor_alvo[0] - self._cor[0]) * 0.05,
            self._cor[1] + (self._cor_alvo[1] - self._cor[1]) * 0.05,
            self._cor[2] + (self._cor_alvo[2] - self._cor[2]) * 0.05,
        )
        vel = {ESTADO_STANDBY:0.3, ESTADO_OUVINDO:0.8, ESTADO_PROCESSANDO:1.5, ESTADO_FALANDO:1.2}.get(self._estado, 0.3)
        self.rotation  += vel
        self.rotation2 -= vel * 0.7
        self.pulse     += 0.06
        amp = {ESTADO_STANDBY:0.02, ESTADO_OUVINDO:0.06, ESTADO_PROCESSANDO:0.08, ESTADO_FALANDO:0.12}.get(self._estado, 0.02)
        factor = 1.0 + math.sin(self.pulse) * amp
        for p in self.pontos_plexus:
            p["pos"] = [v * factor for v in p["orig"]]
        self.update()

    def initializeGL(self):
        glClearColor(0,0,0,1)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0,0,-9)
        cor = self._cor

        glPushMatrix()
        glRotatef(self.rotation, 0, 1, 0)
        glRotatef(self.rotation * 0.4, 1, 0, 0)
        glBegin(GL_LINES)
        for i in range(0, len(self.pontos_plexus), 10):
            p1 = self.pontos_plexus[i]["pos"]
            for j in range(i+1, min(i+25, len(self.pontos_plexus))):
                p2   = self.pontos_plexus[j]["pos"]
                dist = math.dist(p1, p2)
                if dist < 0.75:
                    alpha = (1.0 - dist/0.75) * 0.22
                    glColor4f(*cor, alpha)
                    glVertex3f(*p1)
                    glVertex3f(*p2)
        glEnd()
        glPointSize(1.8)
        glBegin(GL_POINTS)
        for p in self.pontos_plexus:
            glColor4f(*cor, 0.45)
            glVertex3f(*p["pos"])
        glEnd()
        glPopMatrix()

        glPushMatrix()
        glRotatef(self.rotation * 1.2, 0, 0, 1)
        glRotatef(25, 1, 0, 0)
        glLineWidth(1.5)
        glBegin(GL_LINE_LOOP)
        for pt in self.anel1:
            glColor4f(*cor, 0.5 + 0.2*math.sin(self.pulse))
            glVertex3f(*pt)
        glEnd()
        glLineWidth(1.0)
        glPopMatrix()

        glPushMatrix()
        glRotatef(self.rotation2, 1, 0, 0)
        glRotatef(60, 0, 1, 0)
        glBegin(GL_LINE_LOOP)
        for pt in self.anel2:
            glColor4f(*cor, 0.35 + 0.1*math.cos(self.pulse*1.3))
            glVertex3f(*pt)
        glEnd()
        glPopMatrix()

        pulso = 0.6 + 0.3*abs(math.sin(self.pulse*1.5))
        glPointSize(2.5)
        glBegin(GL_POINTS)
        for p in self.pontos_nucleo:
            b = random.uniform(pulso-0.1, pulso+0.1)
            glColor4f(*cor, b)
            glVertex3f(*p)
        glEnd()

    def resizeGL(self, w, h):
        if h == 0: h = 1
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, w/h, 0.1, 50.0)
        glMatrixMode(GL_MODELVIEW)


class BarraStatus(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(f"QFrame {{ background: rgba(0,10,20,220); border-bottom: 1px solid {COR_CINZA}; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(20)
        fonte = QFont("Consolas", 9)

        lbl_nome = QLabel("S.I.R.I.U.S.")
        lbl_nome.setFont(QFont("Consolas", 9, QFont.Bold))
        lbl_nome.setStyleSheet(f"color: {COR_AZUL_NEON}; letter-spacing: 3px;")

        self.lbl_cpu   = self._lbl(fonte, "CPU --")
        self.lbl_ram   = self._lbl(fonte, "RAM --")
        self.lbl_hora  = self._lbl(fonte, "--:--:--")
        self.lbl_conta = self._lbl(fonte, "")
        self.lbl_estado = QLabel(ESTADO_STANDBY)
        self.lbl_estado.setFont(QFont("Consolas", 8, QFont.Bold))
        self.lbl_estado.setStyleSheet(f"color: {COR_AZUL_NEON}; letter-spacing: 2px;")

        layout.addWidget(lbl_nome)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_cpu)
        layout.addWidget(self.lbl_ram)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_conta)
        layout.addStretch()
        layout.addWidget(self.lbl_hora)
        layout.addWidget(self._sep())
        layout.addWidget(self.lbl_estado)

        t = QTimer()
        t.timeout.connect(self._atualizar)
        t.start(1000)
        self._timer = t
        self._atualizar()

    def _lbl(self, fonte, txt):
        l = QLabel(txt)
        l.setFont(fonte)
        l.setStyleSheet(f"color: {COR_TEXTO_HORA};")
        return l

    def _sep(self):
        s = QLabel("│")
        s.setStyleSheet(f"color: {COR_CINZA};")
        return s

    def _atualizar(self):
        self.lbl_hora.setText(datetime.now().strftime("%H:%M:%S"))
        try:
            import psutil
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            self.lbl_cpu.setText(f"CPU {cpu:.0f}%")
            self.lbl_ram.setText(f"RAM {ram:.0f}%")
            self.lbl_cpu.setStyleSheet(f"color: {COR_AMARELO if cpu>80 else COR_TEXTO_HORA};")
            self.lbl_ram.setStyleSheet(f"color: {COR_AMARELO if ram>85 else COR_TEXTO_HORA};")
        except Exception:
            pass

    def set_estado(self, estado: str):
        cores = {ESTADO_STANDBY:COR_AZUL_NEON, ESTADO_OUVINDO:COR_VERDE,
                 ESTADO_PROCESSANDO:COR_AMARELO, ESTADO_FALANDO:COR_BRANCO}
        cor = cores.get(estado, COR_AZUL_NEON)
        self.lbl_estado.setText(estado)
        self.lbl_estado.setStyleSheet(f"color: {cor}; letter-spacing: 2px;")

    def set_conta(self, nome: str):
        if nome and nome != "chefia":
            self.lbl_conta.setText(f"● {nome.upper()}")
            self.lbl_conta.setStyleSheet(f"color: {COR_VERDE};")
        else:
            self.lbl_conta.setText("")


class IndicadorEstado(QLabel):
    _TEXTOS = {
        ESTADO_STANDBY:     ("AGUARDANDO", COR_AZUL_NEON),
        ESTADO_OUVINDO:     ("● OUVINDO",  COR_VERDE),
        ESTADO_PROCESSANDO: ("◆ PROCESSANDO", COR_AMARELO),
        ESTADO_FALANDO:     ("▶ FALANDO",  COR_BRANCO),
    }
    def __init__(self, parent=None):
        super().__init__("AGUARDANDO", parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Consolas", 11, QFont.Bold))
        self.setStyleSheet(f"color: {COR_AZUL_NEON}; letter-spacing: 6px; background: transparent;")
        self.setFixedHeight(30)

    def set_estado(self, estado: str):
        texto, cor = self._TEXTOS.get(estado, ("AGUARDANDO", COR_AZUL_NEON))
        self.setText(texto)
        self.setStyleSheet(f"color: {cor}; letter-spacing: 6px; background: transparent;")


class PainelChat(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: rgba(2,12,24,235); border-top: 1px solid {COR_CINZA}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setFont(QFont("Consolas", 11))
        self.chat.setStyleSheet(f"""
            QTextEdit {{ background: transparent; border: none; color: {COR_AZUL_NEON}; }}
            QScrollBar:vertical {{ background: #0a1a2a; width: 6px; border-radius: 3px; }}
            QScrollBar::handle:vertical {{ background: {COR_CINZA}; border-radius: 3px; }}
        """)
        self.chat.setMinimumHeight(110)

        linha = QHBoxLayout()
        linha.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Comando ou mensagem...")
        self.input.setFont(QFont("Consolas", 11))
        self.input.setStyleSheet(f"""
            QLineEdit {{ background: rgba(93,226,255,0.04); border: 1px solid {COR_CINZA};
                         border-radius: 6px; color: white; padding: 8px 14px; }}
            QLineEdit:focus {{ border: 1px solid {COR_AZUL_NEON}; background: rgba(93,226,255,0.07); }}
        """)
        self.btn_enviar = QPushButton("ENVIAR")
        self.btn_enviar.setFixedSize(80, 36)
        self.btn_enviar.setFont(QFont("Consolas", 9, QFont.Bold))
        self.btn_enviar.setCursor(Qt.PointingHandCursor)
        self.btn_enviar.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {COR_AZUL_NEON};
                           border: 1px solid {COR_AZUL_NEON}; border-radius: 6px; letter-spacing: 1px; }}
            QPushButton:hover {{ background: rgba(93,226,255,0.12); }}
        """)
        linha.addWidget(self.input)
        linha.addWidget(self.btn_enviar)
        layout.addWidget(self.chat)
        layout.addLayout(linha)

    def _fmt_hora(self):
        return datetime.now().strftime("%H:%M")

    def log_usuario(self, txt: str):
        self.chat.append(
            f"<p style=\'margin:3px 0; font-family:Consolas;\'>"
            f"<span style=\'color:{COR_TEXTO_HORA};\'>[{self._fmt_hora()}]</span> "
            f"<span style=\'color:white; font-weight:bold;\'>▷ VOCÊ:</span> "
            f"<span style=\'color:white;\'>{txt}</span></p>"
        )
        self._scroll()

    def log_sirius(self, txt: str):
        self.chat.append(
            f"<p style=\'margin:3px 0; font-family:Consolas;\'>"
            f"<span style=\'color:{COR_TEXTO_HORA};\'>[{self._fmt_hora()}]</span> "
            f"<span style=\'color:{COR_AZUL_NEON}; font-weight:bold;\'>◆ SIRIUS:</span> "
            f"<span style=\'color:{COR_AZUL_NEON};\'>{txt}</span></p>"
        )
        self._scroll()

    def log_sistema(self, txt: str):
        self.chat.append(
            f"<p style=\'margin:3px 0; font-family:Consolas;\'>"
            f"<span style=\'color:{COR_TEXTO_HORA};\'>[{self._fmt_hora()}]</span> "
            f"<span style=\'color:{COR_AMARELO};\'>{txt}</span></p>"
        )
        self._scroll()

    def _scroll(self):
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())


# BotaoMic removido — mic sempre ativo


class SiriusInterfaceMainWindow(QMainWindow):
    _log_sistema_signal = Signal(str)

    def __init__(self, cerebro=None):
        super().__init__()
        if cerebro is None:
            from cerebro import SiriusCerebro
            cerebro = SiriusCerebro()
        self._cerebro = cerebro

        self.setWindowTitle("S.I.R.I.U.S.")
        self.resize(640, 820)
        self.setMinimumSize(560, 700)
        self.setStyleSheet(f"background-color: {COR_FUNDO};")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.barra_status = BarraStatus()
        root.addWidget(self.barra_status)

        self.core_view = SiriusNexus3DView()
        self.core_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.core_view)

        self.indicador = IndicadorEstado()
        root.addWidget(self.indicador)

        linha_ctrl = QWidget()
        linha_ctrl.setFixedHeight(48)
        linha_ctrl.setStyleSheet("background: rgba(0,10,20,200);")
        lc = QHBoxLayout(linha_ctrl)
        lc.setContentsMargins(16, 6, 16, 6)
        lc.setSpacing(8)

        # Botão de mostrar/esconder teclado (input)
        self.btn_teclado = QPushButton("⌨  TECLADO")
        self.btn_teclado.setFixedSize(120, 36)
        self.btn_teclado.setFont(QFont("Consolas", 9, QFont.Bold))
        self.btn_teclado.setCursor(Qt.PointingHandCursor)
        self.btn_teclado.setCheckable(True)
        self.btn_teclado.setChecked(True)
        self.btn_teclado.toggled.connect(self._toggle_teclado)
        self.btn_teclado.setStyleSheet(f"""
            QPushButton {{ background: rgba(93,226,255,0.06); color: {COR_AZUL_NEON};
                           border: 1px solid {COR_AZUL_NEON}; border-radius: 6px; letter-spacing: 1px; }}
            QPushButton:hover {{ background: rgba(93,226,255,0.15); }}
            QPushButton:checked {{ background: rgba(93,226,255,0.06); }}
            QPushButton:!checked {{ color: {COR_CINZA}; border-color: {COR_CINZA};
                                    background: transparent; }}
        """)

        # Botão WALLPAPER — ativa/desativa modo papel de parede
        self.btn_wallpaper = QPushButton("□  WALLPAPER")
        self.btn_wallpaper.setFixedSize(130, 36)
        self.btn_wallpaper.setFont(QFont("Consolas", 9, QFont.Bold))
        self.btn_wallpaper.setCursor(Qt.PointingHandCursor)
        self.btn_wallpaper.setCheckable(True)
        self.btn_wallpaper.setChecked(False)
        self.btn_wallpaper.toggled.connect(self._toggle_wallpaper)
        self.btn_wallpaper.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COR_CINZA};
                border: 1px solid {COR_CINZA};
                border-radius: 6px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                color: {COR_AZUL_NEON};
                border-color: {COR_AZUL_NEON};
            }}
            QPushButton:checked {{
                background: rgba(0,255,136,0.08);
                color: {COR_VERDE};
                border: 1px solid {COR_VERDE};
            }}
        """)

        btn_limpar = QPushButton("LIMPAR")
        btn_limpar.setFixedSize(80, 36)
        btn_limpar.setFont(QFont("Consolas", 9))
        btn_limpar.setCursor(Qt.PointingHandCursor)
        btn_limpar.clicked.connect(lambda: self.painel_chat.chat.clear())
        btn_limpar.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {COR_CINZA};
                           border: 1px solid {COR_CINZA}; border-radius: 6px; }}
            QPushButton:hover {{ color: {COR_AZUL_NEON}; border-color: {COR_AZUL_NEON}; }}
        """)

        lc.addWidget(self.btn_teclado)
        lc.addWidget(self.btn_wallpaper)
        lc.addStretch()
        lc.addWidget(btn_limpar)
        root.addWidget(linha_ctrl)

        # Estado interno do modo wallpaper
        self._proc_wallpaper = None
        self._timer_fundo    = QTimer(self)
        self._timer_fundo.timeout.connect(self._manter_no_fundo)

        self.painel_chat = PainelChat()
        self.painel_chat.setFixedHeight(280)
        self.painel_chat.input.returnPressed.connect(self._enviar_texto)
        self.painel_chat.btn_enviar.clicked.connect(self._enviar_texto)
        root.addWidget(self.painel_chat)

        self.worker = SiriusWorker(SiriusAudio(), self._cerebro)
        self.worker.comando_detectado.connect(self.painel_chat.log_usuario)
        self.worker.resposta_pronta.connect(self.painel_chat.log_sirius)
        self.worker.estado_mudou.connect(self._on_estado)
        self.worker.start()

        self._log_sistema_signal.connect(self.painel_chat.log_sistema)

        if hasattr(self._cerebro, "_proativo") and self._cerebro._proativo:
            self._cerebro._proativo.registrar_callback(
                callback_falar=self.worker.audio.falar,
                callback_log=self._log_sistema_signal.emit,
            )
        if hasattr(self._cerebro, "registrar_callback"):
            self._cerebro.registrar_callback(
                callback_falar=self.worker.audio.falar,
                callback_log=self._log_sistema_signal.emit,
            )

        QTimer.singleShot(800, self._boas_vindas)
        QTimer.singleShot(500, self._atualizar_conta)

    def _on_estado(self, estado: str):
        self.core_view.set_estado(estado)
        self.barra_status.set_estado(estado)
        self.indicador.set_estado(estado)

    def _enviar_texto(self):
        t = self.painel_chat.input.text().strip()
        if t:
            self.worker.enviar_comando_texto(t)
            self.painel_chat.input.clear()
            self.painel_chat.input.setFocus()

    def _atualizar_conta(self):
        try:
            if hasattr(self._cerebro, "_sessao") and self._cerebro._sessao:
                nome = self._cerebro._sessao.nome_usuario
            elif hasattr(self._cerebro, "_perfil") and self._cerebro._perfil:
                nome = self._cerebro._perfil.get("nome", "")
            else:
                nome = ""
            self.barra_status.set_conta(nome)
        except Exception:
            pass

    def _boas_vindas(self):
        h = datetime.now().hour
        s = "Bom dia" if h < 12 else "Boa tarde" if h < 18 else "Boa noite"
        self.painel_chat.log_sistema(f"◆ Sistemas operacionais. {s}. Aguardando comandos.")

    # compatibilidade legado
    def log_sirius(self, t: str):   self.painel_chat.log_sirius(t)
    def log_usuario(self, t: str):  self.painel_chat.log_usuario(t)
    def set_fala_view(self, v: bool): self._on_estado(ESTADO_FALANDO if v else ESTADO_STANDBY)

    def _toggle_teclado(self, visivel: bool):
        """Mostra/esconde a linha de input."""
        self.painel_chat.input.setVisible(visivel)
        self.painel_chat.btn_enviar.setVisible(visivel)
        self.painel_chat.setFixedHeight(280 if visivel else 210)
        self.btn_teclado.setText("⌨  TECLADO" if visivel else "⌨  OCULTO")

    def _toggle_wallpaper(self, ativar: bool):
        """
        Ativa/desativa modo papel de parede.
        Quando ativo: lança sirius_wallpaper.py como processo separado
        e minimiza esta janela.
        Quando inativo: encerra o processo do wallpaper.
        """
        if ativar:
            self._lancar_wallpaper()
        else:
            self._encerrar_wallpaper()

    def _lancar_wallpaper(self):
        """Lança sirius_wallpaper.py em processo separado."""
        # Encerra instância anterior se houver
        self._encerrar_wallpaper()

        # Localiza sirius_wallpaper.py no mesmo diretório (src/)
        src_dir  = os.path.dirname(os.path.abspath(__file__))
        wallpaper = os.path.join(src_dir, "sirius_wallpaper.py")

        if not os.path.exists(wallpaper):
            self.painel_chat.log_sistema(
                f"✗ sirius_wallpaper.py não encontrado em {src_dir}"
            )
            self.btn_wallpaper.setChecked(False)
            return

        try:
            self._proc_wallpaper = subprocess.Popen(
                [sys.executable, wallpaper],
                cwd=src_dir,
            )
            self.btn_wallpaper.setText("■  WALLPAPER")
            self.painel_chat.log_sistema(
                "◆ Modo wallpaper iniciado. "
                "Duplo clique na esfera para abrir o chat."
            )
            print(f"\033[92m[INTERFACE]: sirius_wallpaper.py lançado "
                  f"(pid={self._proc_wallpaper.pid}).\033[0m")

            # Salva o modo na memória do cerebro
            self._salvar_modo_memoria("wallpaper")

            # Timer que monitora se o processo ainda está vivo
            self._timer_fundo.start(3000)

        except Exception as e:
            self.painel_chat.log_sistema(f"✗ Erro ao iniciar wallpaper: {e}")
            self.btn_wallpaper.setChecked(False)
            self._proc_wallpaper = None

    def _encerrar_wallpaper(self):
        """Encerra o processo do wallpaper se estiver rodando."""
        proc = getattr(self, '_proc_wallpaper', None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
                print("\033[93m[INTERFACE]: sirius_wallpaper.py encerrado.\033[0m")
            except Exception:
                proc.kill()
        self._proc_wallpaper = None
        self._salvar_modo_memoria("janela")

    def _manter_no_fundo(self):
        """
        Timer 3s — verifica se o processo wallpaper ainda está rodando.
        Se encerrou externamente, desativa o botão.
        """
        proc = getattr(self, '_proc_wallpaper', None)
        if proc and proc.poll() is not None:
            # Processo encerrou sozinho
            self._proc_wallpaper = None
            self._timer_fundo.stop()
            self.btn_wallpaper.setChecked(False)
            self.btn_wallpaper.setText("□  WALLPAPER")


    def _salvar_modo_memoria(self, modo: str):
        """Persiste o último modo usado na memória do cerebro."""
        try:
            if hasattr(self._cerebro, 'memoria') and self._cerebro.memoria:
                self._cerebro.memoria.salvar_estado("ultimo_modo", modo)
        except Exception:
            pass

    def closeEvent(self, event):
        self._timer_fundo.stop()
        self._encerrar_wallpaper()
        self.worker.parar()
        event.accept()


if __name__ == "__main__":
    app    = QApplication(sys.argv)
    window = SiriusInterfaceMainWindow()
    window.show()
    sys.exit(app.exec())