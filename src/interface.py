import sys
import math
import random
import threading
import os
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, 
                                QLabel, QFrame, QLineEdit, QPushButton, QTextEdit)
from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

# --- INTEGRAÇÃO COM O CÉREBRO REAL ---
try:
    caminho_atual = os.path.dirname(os.path.abspath(__file__))
    if caminho_atual not in sys.path:
        sys.path.append(caminho_atual)
    
    from chatbot import sirius_com_memoria
except ImportError as e:
    print(f"\033[31m[Erro]: Falha ao carregar o cérebro: {e}\033[0m")
    sirius_com_memoria = None

from audio_handler import SiriusAudio

class SiriusWorker(QThread):
    comando_detectado = Signal(str)
    resposta_pronta = Signal(str)
    status_fala = Signal(bool) 

    def __init__(self, audio_sys):
        super().__init__()
        self.audio = audio_sys
        self.comando_manual = None
        self.lock = threading.Lock()
        self.rodando = True
        self.modo_voz_ativo = True 

    def enviar_comando_texto(self, texto):
        with self.lock:
            self.comando_manual = texto

    def run(self):
        while self.rodando:
            fala_usuario = None
            
            # 1. VERIFICA COMANDO MANUAL (TEXTO)
            with self.lock:
                if self.comando_manual:
                    fala_usuario = self.comando_manual
                    self.comando_manual = None
            
            # 2. SE NÃO HÁ TEXTO E VOZ ATIVA: AGUARDA PALAVRA DE ATIVAÇÃO
            if not fala_usuario and self.modo_voz_ativo:
                if self.audio.aguardar_ativacao():
                    # Esfera pisca para indicar que começou a ouvir
                    self.status_fala.emit(True) 
                    time.sleep(0.3)
                    self.status_fala.emit(False)
                    
                    fala_usuario = self.audio.ouvir()

            # 3. PROCESSA A ENTRADA
            if fala_usuario and str(fala_usuario).strip():
                comando_str = str(fala_usuario).strip()
                self.comando_detectado.emit(comando_str)
                
                try:
                    if sirius_com_memoria:
                        config = {"configurable": {"session_id": "sessao_sirius_final"}}
                        resultado = sirius_com_memoria.invoke({"input": comando_str}, config=config)
                        
                        conteudo = getattr(resultado, 'content', resultado)
                        
                        if isinstance(conteudo, list):
                            resposta_texto = ""
                            for item in conteudo:
                                if isinstance(item, dict) and 'text' in item:
                                    resposta_texto = item['text']
                                    break
                        else:
                            resposta_texto = str(conteudo)

                        if "extras':" in resposta_texto:
                            resposta_texto = resposta_texto.split("extras':")[0]
                        
                        resposta_texto = resposta_texto.replace("[{", "").replace("}]", "").strip()
                        
                    else:
                        resposta_texto = "Conexão com o núcleo neural perdida."
                        
                except Exception as e:
                    resposta_texto = "Atingi meu limite de processamento." if "429" in str(e) else f"Erro: {e}"
                
                if resposta_texto:
                    self.resposta_pronta.emit(str(resposta_texto))
                    
                    self.status_fala.emit(True)
                    try:
                        self.audio.falar(resposta_texto)
                    finally:
                        self.status_fala.emit(False)

            time.sleep(0.1)

# --- VISUALIZAÇÃO 3D ---
class SiriusNexus3DView(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(QSize(600, 400))
        self.rotation = 0
        self.pulse = 0
        self._esta_falando = False 

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)

        self.COR_NEON_AZUL = (93/255, 226/255, 255/255)
        self.COR_BRANCO = (1.0, 1.0, 1.0)
        self.pontos_plexus = []
        self.pontos_nucleo = []
        
        for _ in range(1200):
            phi = random.uniform(0, 2 * math.pi)
            costheta = random.uniform(-1, 1)
            theta = math.acos(costheta)
            r = 2.4 
            x = r * math.sin(theta) * math.cos(phi)
            y = r * math.sin(theta) * math.sin(phi)
            z = r * math.cos(theta)
            self.pontos_plexus.append({'pos': [x, y, z], 'orig': [x, y, z]})

        for _ in range(500):
            r_core = random.uniform(0, 0.6)
            phi = random.uniform(0, 2 * math.pi)
            costheta = random.uniform(-1, 1)
            theta = math.acos(costheta)
            cx = r_core * math.sin(theta) * math.cos(phi)
            cy = r_core * math.sin(theta) * math.sin(phi)
            cz = r_core * math.cos(theta)
            self.pontos_nucleo.append([cx, cy, cz])

    @property
    def esta_falando(self): return self._esta_falando
    
    @esta_falando.setter
    def esta_falando(self, valor):
        self._esta_falando = valor
        self.update()

    def update_animation(self):
        vel_rotacao = 1.2 if self._esta_falando else 0.4
        self.rotation += vel_rotacao
        self.pulse += 0.08
        amplitude = 0.15 if self._esta_falando else 0.03
        factor = (1.0 + math.sin(self.pulse) * amplitude)
        for p in self.pontos_plexus:
            p['pos'] = [v * factor for v in p['orig']]
        self.update()

    def initializeGL(self):
        glClearColor(0.0, 0.0, 0.0, 1.0) 
        glEnable(GL_DEPTH_TEST); glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT); glLoadIdentity(); glTranslatef(0.0, 0.0, -9.0)
        glPushMatrix()
        glRotatef(self.rotation, 0, 1, 0); glRotatef(self.rotation * 0.5, 1, 0, 0)
        
        cor_atual = self.COR_BRANCO if self._esta_falando else self.COR_NEON_AZUL
        
        glBegin(GL_LINES)
        for i in range(0, len(self.pontos_plexus), 12): 
            p1 = self.pontos_plexus[i]['pos']
            for j in range(i + 1, i + 30):
                if j < len(self.pontos_plexus):
                    p2 = self.pontos_plexus[j]['pos']
                    dist = math.dist(p1, p2)
                    if dist < 0.8:
                        alpha = (1.0 - dist/0.8) * (0.4 if self._esta_falando else 0.18)
                        glColor4f(*cor_atual, alpha); glVertex3f(*p1); glVertex3f(*p2)
        glEnd()
        
        glPointSize(2.0); glBegin(GL_POINTS)
        for p in self.pontos_plexus: glColor4f(*cor_atual, 0.5); glVertex3f(*p['pos'])
        glEnd(); glPopMatrix()

        glBegin(GL_POINTS)
        for p_core in self.pontos_nucleo:
            glColor4f(*cor_atual, random.uniform(0.4, 0.9)); glVertex3f(*p_core)
        glEnd()

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h); glMatrixMode(GL_PROJECTION); glLoadIdentity(); gluPerspective(45, (w/h), 0.1, 50.0); glMatrixMode(GL_MODELVIEW)

# --- JANELA PRINCIPAL ---
class SiriusInterfaceMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("S.I.R.I.U.S. - Absolute Black Edition")
        self.resize(600, 800)
        self.setStyleSheet("background-color: #000000;")

        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout_master = QVBoxLayout(self.main_widget)
        self.layout_master.setContentsMargins(0,0,0,0)

        self.core_view = SiriusNexus3DView()
        self.layout_master.addWidget(self.core_view)

        self.btn_toggle = QPushButton("TEXTO", self.core_view)
        self.btn_toggle.setGeometry(480, 20, 100, 35)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setStyleSheet("""
            QPushButton { 
                background: rgba(0,0,0,180); 
                color: #5DE2FF; 
                border: 1px solid #5DE2FF; 
                border-radius: 8px; 
                font-weight: bold; 
            }
            QPushButton:hover { background: rgba(93, 226, 255, 0.2); }
        """)
        self.btn_toggle.clicked.connect(self.alternar_interface)

        self.hud_chat = QFrame()
        self.hud_chat.setFixedHeight(300)
        self.hud_chat.setStyleSheet("""
            QFrame { 
                background-color: rgba(5, 5, 5, 240); 
                border-top: 2px solid #5DE2FF; 
                border-top-left-radius: 30px; 
                border-top-right-radius: 30px; 
            }
        """)
        self.hud_chat.setVisible(False)
        self.layout_master.addWidget(self.hud_chat)

        layout_chat = QVBoxLayout(self.hud_chat)
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setStyleSheet("background: transparent; color: #5DE2FF; border: none; font-size: 14px; font-family: 'Consolas';")
        
        self.input_texto = QLineEdit()
        self.input_texto.setPlaceholderText("Digite seu comando...")
        self.input_texto.setStyleSheet("""
            QLineEdit { 
                background: rgba(93, 226, 255, 0.05); 
                border: 1px solid #5DE2FF; 
                border-radius: 15px; 
                color: white; 
                padding: 12px; 
            }
        """)
        self.input_texto.returnPressed.connect(self.enviar_texto_manual)
        
        layout_chat.addWidget(self.chat_history)
        layout_chat.addWidget(self.input_texto)

        self.worker = SiriusWorker(SiriusAudio())
        self.worker.comando_detectado.connect(self.log_usuario)
        self.worker.resposta_pronta.connect(self.log_sirius)
        self.worker.status_fala.connect(self.set_fala_view)
        self.worker.start()

    def set_fala_view(self, status):
        self.core_view.esta_falando = status

    # --- CORREÇÃO DO BUG DE ALTERNÂNCIA ---
    def alternar_interface(self):
        painel_visivel = self.hud_chat.isVisible()
        nova_visibilidade = not painel_visivel
        
        self.hud_chat.setVisible(nova_visibilidade)
        self.worker.modo_voz_ativo = not nova_visibilidade
        
        # Reset de estado ao voltar para voz
        if not nova_visibilidade: 
            with self.worker.lock:
                self.worker.comando_manual = None
            print("\033[94m[Sistema]: Modo Voz Reativado.\033[0m")
        
        self.btn_toggle.setText("VOZ" if nova_visibilidade else "TEXTO")
        if nova_visibilidade: 
            self.input_texto.setFocus()

    def enviar_texto_manual(self):
        t = self.input_texto.text()
        if t.strip():
            self.worker.enviar_comando_texto(t)
            self.input_texto.clear()
            self.input_texto.setFocus() # Mantém o foco para agilizar o uso

    def log_usuario(self, t):
        self.chat_history.append(f"<p style='color:white; margin: 5px;'><b>👤 Você:</b> {t}</p>")
        self.chat_history.verticalScrollBar().setValue(self.chat_history.verticalScrollBar().maximum())

    def log_sirius(self, t):
        self.chat_history.append(f"<p style='color:#5DE2FF; margin: 5px;'><b>🤖 SIRIUS:</b> {t}</p>")
        self.chat_history.verticalScrollBar().setValue(self.chat_history.verticalScrollBar().maximum())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SiriusInterfaceMainWindow()
    window.show()
    sys.exit(app.exec())