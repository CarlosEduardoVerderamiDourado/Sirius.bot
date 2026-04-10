import sys
import os
import threading
import base64
import time
import random

from duckduckgo_search import DDGS  # <--- ADICIONE ESTA LINHA

# --- FASE DE CURA DINÂMICA (PARA .VENV311) ---
def aplicar_cura_sistema():
    try:
        import site
        # Mapeia os caminhos de pacotes do seu ambiente virtual atual (.venv311)
        for path in site.getsitepackages():
            if os.path.exists(path):
                os.add_dll_directory(path)
        
        # Mapeia o diretório onde o executável do Python reside
        bin_path = os.path.dirname(sys.executable)
        if os.path.exists(bin_path):
            os.add_dll_directory(bin_path)
            
        # Tenta validar o PyAudio internamente para garantir que a DLL carregou
        import pyaudio
        print(f"\033[92m[SISTEMA]: PyAudio validado com sucesso (V.{pyaudio.__version__})\033[0m")
    except Exception as e:
        print(f"\033[33m[AVISO]: Falha ao registrar diretórios de DLL: {e}\033[0m")

aplicar_cura_sistema()

# --- CORREÇÃO DE PATH PARA IMPORTAÇÃO DE MÓDULOS LOCAIS ---
# --- CORREÇÃO DE PATH PARA IMPORTAÇÃO DE MÓDULOS LOCAIS ---
# Este bloco garante que o Python olhe para a pasta src/ e config/
# --- CORREÇÃO DE PATH PARA IMPORTAÇÃO DE MÓDULOS LOCAIS ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__)) # Raiz (onde está o main)
diretorio_src = os.path.join(diretorio_atual, 'src')

# Garante que o Python olhe na Raiz e na pasta /src
caminhos_necessarios = [diretorio_atual, diretorio_src]
for p in caminhos_necessarios:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p) # insert(0) dá prioridade aos seus arquivos

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle
from PySide6.QtGui import QIcon, QCloseEvent, QPixmap

# Agora o Python busca direto na /src
try:
    from interface import SiriusInterfaceMainWindow
    from cerebro import SiriusCerebro 
    from audio_handler import SiriusAudio
except ImportError as e:
    print(f"\033[31m[ERRO CRÍTICO]: Falha ao carregar módulos da /src: {e}\033[0m")
    # Aqui é bom parar o script, senão o erro de 'NameError' volta
    sys.exit(1)

# Ajuste do Import da Logo (Busca na pasta /config da raiz)
try:
    from config.config import LOGO_SIRIUS_B64
except Exception:
    LOGO_SIRIUS_B64 = None 
    print("[AVISO]: Logo não encontrada em config/config.py")

# --- CLASSE DE APRENDIZADO AUTÔNOMO ---
class SiriusSubconsciente:
    def __init__(self, memoria_ref):
        self.memoria = memoria_ref
        self.interesses = [
            "competitivo de pokemon vgc 2026",
            "novidades tecnologias nasa 2026",
            "melhores praticas de python SOLID",
            "como treinar redes neurais locais",
            "historia da inteligencia artificial noir"
        ]

    def iniciar_estudos(self):
        while True:
            time.sleep(1200) # 20 minutos
            tema = random.choice(self.interesses)
            print(f"\n[SIRIUS SUBCONSCIENTE]: Expandindo conhecimento sobre '{tema}'...")
            try:
                with DDGS() as ddgs:
                    resultados = [r for r in ddgs.text(tema, max_results=2)]
                if resultados:
                    for res in resultados:
                        dados = f"Fonte: {res['href']} | Resumo: {res['body']}"
                        self.memoria.salvar_estudo_autonomo(tema, dados, tags="auto_learning")
                    print(f"[SIRIUS SUBCONSCIENTE]: Estudo sobre '{tema}' concluído.")
            except Exception as e:
                print(f"[SIRIUS SUBCONSCIENTE]: Erro ao minerar: {e}")

class SiriusAppPrincipal(SiriusInterfaceMainWindow):
    def __init__(self):
        super().__init__() 
        self.cerebro = SiriusCerebro()
        self.audio = SiriusAudio()
        self.ativo = True

        # Inicia Aprendizado Autônomo
        self.subconsciente = SiriusSubconsciente(self.cerebro.memoria)
        threading.Thread(target=self.subconsciente.iniciar_estudos, daemon=True).start()

        # Inicia a Vigília de áudio (Ouvindo o microfone em segundo plano)
        threading.Thread(target=self.iniciar_vigilia, daemon=True).start()

        self.icone_sirius = self.obter_icone_sirius()
        self.setWindowIcon(self.icone_sirius)
        self.configurar_bandeja()

    # --- MÉTODO DE VIGÍLIA ---
    def iniciar_vigilia(self):
        """Loop eterno para captar o áudio do ambiente usando Faster-Whisper"""
        print("\033[94m[SIRIUS]: Vigília de áudio iniciada. Estou ouvindo...\033[0m")
        while self.ativo:
            try:
                # Chama a captação contínua do audio_handler
                texto_ouvido = self.audio.escutar_fluxo_continuo()
                
                if texto_ouvido:
                    print(f"\033[92m[OUVIDO]:\033[0m {texto_ouvido}")
                    self.processar_voz_direta(texto_ouvido)
            except Exception as e:
                print(f"\033[31m[ERRO VIGÍLIA]: {e}\033[0m")
                time.sleep(2) # Pausa curta antes de tentar novamente em caso de erro
            
            time.sleep(0.1)

    def processar_voz_direta(self, texto):
        """Processa a voz captada sem interferir no input de texto manual"""
        def thread_resposta():
            # O cérebro verifica se a palavra-chave 'Sirius' foi dita
            resposta_final = self.cerebro.processar(texto)
            if resposta_final:
                # Registra na interface e fala pelo áudio configurado
                self.log_sirius(resposta_final)
                self.audio.falar(resposta_final)
                print(f"\n[SIRIUS VOZ]: {resposta_final}")

        threading.Thread(target=thread_resposta, daemon=True).start()

    def obter_icone_sirius(self):
        try:
            img_data = base64.b64decode(LOGO_SIRIUS_B64)
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)
            return QIcon(pixmap) if not pixmap.isNull() else self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        except:
            return self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def configurar_bandeja(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.icone_sirius)
        self.tray.setToolTip("S.I.R.I.U.S. - Sistema Ativo")
        menu = QMenu()
        menu.addAction("Abrir Interface", self.showNormal)
        menu.addSeparator()
        menu.addAction("Sair do Sirius", self.sair_total)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_icon_activated)
        self.tray.show()

    def enviar_texto_manual(self):
        """Processa o texto da caixa de entrada da interface gráfica"""
        texto_original = self.input_texto.text().strip()
        if not texto_original: return
        
        self.input_texto.clear()
        self.log_usuario(texto_original) 

        # Adiciona o gatilho internamente se não houver no texto manual
        if "sirius" not in texto_original.lower():
            texto_para_processar = f"Sirius, {texto_original}"
        else:
            texto_para_processar = texto_original

        def thread_resposta():
            resposta_final = self.cerebro.processar(texto_para_processar)
            if resposta_final:
                self.log_sirius(resposta_final)
                self.audio.falar(resposta_final)
                print(f"\n[SIRIUS TEXTO]: {resposta_final}")

        threading.Thread(target=thread_resposta, daemon=True).start()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()

    def closeEvent(self, event: QCloseEvent):
        if self.ativo:
            event.ignore()
            self.hide()
            self.tray.showMessage("S.I.R.I.U.S.", "Operando em segundo plano.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def sair_total(self):
        self.ativo = False
        self.tray.hide()
        QApplication.quit()
        os._exit(0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    sirius = SiriusAppPrincipal()
    sirius.show()
    sys.exit(app.exec())