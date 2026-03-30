import sys
import os
import threading
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle
from PySide6.QtGui import QIcon, QCloseEvent

# Importando suas classes originais
from interface import SiriusInterfaceMainWindow
from chatbot import SiriusChat
from audio_handler import SiriusAudio

class SiriusAppPrincipal(SiriusInterfaceMainWindow):
    def __init__(self):
        # 1. Inicializa a interface principal
        super().__init__() 
        
        # 2. Instancia o "Cérebro" e a "Voz" para este processo
        self.chat = SiriusChat()
        self.audio = SiriusAudio()
        self.ativo = True

        # --- CONFIGURAÇÃO DA LOGO E CAMINHOS ---
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))
        caminho_logo = os.path.join(diretorio_atual, "img", "logo_sirius.png")
        
        if os.path.exists(caminho_logo):
            self.setWindowIcon(QIcon(caminho_logo))
        
        # --- CONFIGURAR SYSTEM TRAY (BANDEJA) ---
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(caminho_logo):
            self.tray.setIcon(QIcon(caminho_logo))
        else:
            self.tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

        menu = QMenu()
        menu.addAction("Abrir Sirius", self.showNormal)
        menu.addSeparator()
        menu.addAction("Sair Completamente", self.sair_total)
        
        self.tray.setContextMenu(menu)
        self.tray.show()
        self.tray.activated.connect(self.on_tray_icon_activated)

    # --- CONEXÃO DA INTELIGÊNCIA ---
    def processar_resposta_ia(self, texto_resposta):
        """
        Sobrescreve o método da interface.py. 
        Aqui é onde o texto da IA vira voz e aparece no console.
        """
        if not texto_resposta:
            return

        # 1. Faz o Sirius falar
        # Usamos uma thread para o áudio não travar a animação da esfera
        threading.Thread(target=self.audio.falar, args=(texto_resposta,), daemon=True).start()
        
        # 2. Mostra no log do terminal (como você viu antes)
        print(f"\n[Sirius]: {texto_resposta}")

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        """Esconde a janela mas mantém o processo vivo"""
        if self.ativo:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "S.I.R.I.U.S.",
                "Continuo ativo e ouvindo em segundo plano.",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

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