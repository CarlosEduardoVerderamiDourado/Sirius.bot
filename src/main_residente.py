import sys
import os
import threading
import base64  # Necessário para decodificar a string da imagem
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle
from PySide6.QtGui import QIcon, QCloseEvent, QPixmap # Adicionado QPixmap para processar a imagem

# Importando suas classes originais
from interface import SiriusInterfaceMainWindow
from chatbot import SiriusChat
from audio_handler import SiriusAudio

# Ajuste do Import: Entra na PASTA config e importa do ARQUIVO config.py
try:
    from config.config import LOGO_SIRIUS_B64
except ImportError:
    # Caso o Python não encontre a pasta no PATH
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from config.config import LOGO_SIRIUS_B64

class SiriusAppPrincipal(SiriusInterfaceMainWindow):
    def __init__(self):
        # 1. Inicializa a interface principal
        super().__init__() 
        
        # 2. Instancia o "Cérebro" e a "Voz" para este processo
        self.chat = SiriusChat()
        self.audio = SiriusAudio()
        self.ativo = True

        # --- CONFIGURAÇÃO DA LOGO VIA BASE64 ---
        # Obtemos o ícone uma única vez para usar na janela e na bandeja
        self.icone_sirius = self.obter_icone_sirius()
        self.setWindowIcon(self.icone_sirius)
        
        # --- CONFIGURAR SYSTEM TRAY (BANDEJA) ---
        self.configurar_bandeja()

    def obter_icone_sirius(self):
        """Converte a string Base64 do arquivo config/config.py em um QIcon"""
        try:
            # Transforma o texto em bytes de imagem
            img_data = base64.b64decode(LOGO_SIRIUS_B64)
            pixmap = QPixmap()
            # Carrega a imagem direto da memória
            pixmap.loadFromData(img_data)
            
            if pixmap.isNull():
                raise ValueError("Pixmap está vazio. Verifique o código Base64.")
                
            return QIcon(pixmap)
        except Exception as e:
            print(f"Erro ao carregar ícone embutido: {e}")
            # Se falhar, usa um ícone padrão do sistema (computador) para não ficar invisível
            return self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def configurar_bandeja(self):
        """Inicializa e exibe o ícone na bandeja do sistema"""
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.icone_sirius)
        self.tray.setToolTip("S.I.R.I.U.S. - Assistente Ativo")

        # Menu da bandeja
        menu = QMenu()
        menu.addAction("Abrir Sirius", self.showNormal)
        menu.addSeparator()
        menu.addAction("Sair Completamente", self.sair_total)
        
        self.tray.setContextMenu(menu)
        
        # Conecta o clique no ícone
        self.tray.activated.connect(self.on_tray_icon_activated)
        
        # EXIBE o ícone (Obrigatório para aparecer)
        self.tray.show()

    # --- CONEXÃO DA INTELIGÊNCIA ---
    def processar_resposta_ia(self, texto_resposta):
        """
        Sobrescreve o método da interface.py. 
        Aqui é onde o texto da IA vira voz e aparece no console.
        """
        if not texto_resposta:
            return

        # 1. Faz o Sirius falar em segundo plano
        threading.Thread(target=self.audio.falar, args=(texto_resposta,), daemon=True).start()
        
        # 2. Mostra no log do terminal
        print(f"\n[Sirius]: {texto_resposta}")

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        """Esconde a janela mas mantém o processo vivo na bandeja"""
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
    
    # Impede que o app feche quando a janela principal for escondida
    app.setQuitOnLastWindowClosed(False)
    
    sirius = SiriusAppPrincipal()
    sirius.show()
    
    sys.exit(app.exec())