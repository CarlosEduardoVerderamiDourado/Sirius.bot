import sys
import os
import threading
import base64
import time
import random

from duckduckgo_search import DDGS


# ---------------------------------------------------------------------------
# FASE DE CURA DINÂMICA (para .venv311 no Windows)
# ---------------------------------------------------------------------------

def aplicar_cura_sistema():
    """Registra diretórios de DLL necessários e valida o PyAudio."""
    try:
        import site
        for path in site.getsitepackages():
            if os.path.exists(path):
                os.add_dll_directory(path)
        bin_path = os.path.dirname(sys.executable)
        if os.path.exists(bin_path):
            os.add_dll_directory(bin_path)
        import pyaudio
        print(f"\033[92m[SISTEMA]: PyAudio validado com sucesso (V.{pyaudio.__version__})\033[0m")
    except Exception as e:
        print(f"\033[33m[AVISO]: Falha ao registrar diretórios de DLL: {e}\033[0m")

aplicar_cura_sistema()


# ---------------------------------------------------------------------------
# CORREÇÃO DE PATH
# ---------------------------------------------------------------------------

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
diretorio_src   = os.path.join(diretorio_atual, 'src')

for p in [diretorio_atual, diretorio_src]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# IMPORTS PRINCIPAIS
# ---------------------------------------------------------------------------

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle
from PySide6.QtGui import QIcon, QCloseEvent, QPixmap

try:
    from interface import SiriusInterfaceMainWindow
    from cerebro import SiriusCerebro
except ImportError as e:
    print(f"\033[31m[ERRO CRÍTICO]: Falha ao carregar módulos da /src: {e}\033[0m")
    sys.exit(1)

# Módulos opcionais — carregados com fallback gracioso
try:
    from sirius_treinador import SiriusTreinador
    _TREINADOR_DISPONIVEL = True
except ImportError:
    _TREINADOR_DISPONIVEL = False
    print("[AVISO]: SiriusTreinador não encontrado — retreino autônomo desabilitado.")

try:
    from config.config import LOGO_SIRIUS_B64
except Exception:
    LOGO_SIRIUS_B64 = None
    print("[AVISO]: Logo não encontrada em config/config.py")


# ---------------------------------------------------------------------------
# APRENDIZADO AUTÔNOMO EM BACKGROUND (fallback legado)
# ---------------------------------------------------------------------------

class SiriusSubconsciente:
    """
    Mineração periódica de conhecimento via DuckDuckGo.
    Utilizado apenas quando SiriusCoordenador/SiriusAutodidata não estão disponíveis.
    """

    def __init__(self, memoria_ref):
        self.memoria = memoria_ref
        self.interesses = [
            "competitivo de pokemon vgc 2026",
            "novidades tecnologias nasa 2026",
            "melhores praticas de python SOLID",
            "como treinar redes neurais locais",
            "historia da inteligencia artificial",
        ]

    def iniciar_estudos(self):
        while True:
            time.sleep(1200)  # a cada 20 minutos
            tema = random.choice(self.interesses)
            print(f"\n[SIRIUS SUBCONSCIENTE]: Expandindo conhecimento sobre '{tema}'...")
            try:
                with DDGS() as ddgs:
                    resultados = list(ddgs.text(tema, max_results=2))

                for res in resultados:
                    if isinstance(res, dict):
                        link  = res.get('href', 'Link indisponível')
                        corpo = res.get('body', 'Resumo indisponível')
                    elif isinstance(res, tuple):
                        link  = res[1] if len(res) > 1 else "Link indisponível"
                        corpo = res[2] if len(res) > 2 else "Resumo indisponível"
                    else:
                        continue

                    dados = f"Fonte: {link} | Resumo: {corpo}"
                    self.memoria.salvar_estudo_autonomo(tema, dados, tags="auto_learning")

                print(f"[SIRIUS SUBCONSCIENTE]: Estudo sobre '{tema}' concluído.")
            except Exception as e:
                print(f"[SIRIUS SUBCONSCIENTE]: Erro ao minerar: {e}")


# ---------------------------------------------------------------------------
# APLICAÇÃO PRINCIPAL
# ---------------------------------------------------------------------------

class SiriusAppPrincipal(SiriusInterfaceMainWindow):
    """
    Ponto de entrada da aplicação S.I.R.I.U.S.

    Responsabilidades:
    - Inicializar o cérebro e os subsistemas de aprendizado autônomo.
    - Configurar ícone e bandeja do sistema.
    - Delegar toda a captura/reprodução de áudio ao SiriusWorker (dentro da interface),
      evitando o bug de duplo loop que trava o microfone.
    """

    def __init__(self, cerebro=None):
        super().__init__()
        self.cerebro = cerebro or SiriusCerebro()
        self.ativo   = True

        self._iniciar_aprendizado_autonomo()
        self._iniciar_treinador_autonomo()

        self.icone_sirius = self._obter_icone_sirius()
        self.setWindowIcon(self.icone_sirius)
        self._configurar_bandeja()
        self._iniciar_servidor()

    # ------------------------------------------------------------------
    # Inicialização de subsistemas
    # ------------------------------------------------------------------

    def _iniciar_aprendizado_autonomo(self):
        """
        Tenta iniciar o SiriusCoordenador (abordagem moderna).
        Em caso de falha, recorre ao SiriusAutodidata e, por último,
        ao SiriusSubconsciente legado.
        """
        # Tentativa 1: Coordenador (gerencia Autodidata + Leitor juntos)
        try:
            from sirius_coordenador import SiriusCoordenador
            self._coordenador = SiriusCoordenador(memoria=self.cerebro.memoria)
            self._coordenador.iniciar()
            print("[MAIN]: SiriusCoordenador iniciado com sucesso.")
            return
        except Exception as e:
            print(f"[MAIN]: SiriusCoordenador indisponível: {e}")

        # Tentativa 2: Autodidata standalone (Wikipedia + Web + auto-diálogo)
        try:
            from sirius_autodidata import SiriusAutodidata
            self._autodidata = SiriusAutodidata(
                memoria=self.cerebro.memoria,
                cerebro=self.cerebro,
            )
            self._autodidata.iniciar()
            print("[MAIN]: SiriusAutodidata iniciado com sucesso.")
            return
        except Exception as e:
            print(f"[MAIN]: SiriusAutodidata indisponível: {e}")

        # Fallback final: subconsciente legado
        print("[MAIN]: Usando SiriusSubconsciente (fallback legado).")
        self._subconsciente = SiriusSubconsciente(self.cerebro.memoria)
        threading.Thread(target=self._subconsciente.iniciar_estudos, daemon=True).start()

    def _iniciar_treinador_autonomo(self):
        """Inicia o retreino periódico das redes neurais (a cada 2 horas)."""
        if not _TREINADOR_DISPONIVEL:
            return
        try:
            self._treinador = SiriusTreinador()
            self._treinador.iniciar_ciclo_autonomo(intervalo_horas=2.0)
            print("[MAIN]: SiriusTreinador iniciado (ciclo de 2h).")
        except Exception as e:
            print(f"[MAIN]: Falha ao iniciar SiriusTreinador: {e}")

    def _iniciar_servidor(self):
        """
        Inicia o servidor REST + WebSocket em thread daemon.
        Acessível em http://SEU_IP:5000 de qualquer dispositivo na rede.
        """
        try:
            from sirius_server import iniciar_servidor
            iniciar_servidor(
                cerebro=self.cerebro,
                host="0.0.0.0",
                porta=5000,
                em_thread=True,   # não bloqueia a UI
            )
        except ImportError:
            print("[MAIN]: sirius_server.py não encontrado — servidor desativado.")
            print("        pip install fastapi uvicorn")
        except Exception as e:
            print(f"[MAIN]: Servidor não iniciou: {e}")

    # ------------------------------------------------------------------
    # Interface e bandeja
    # ------------------------------------------------------------------

    def _obter_icone_sirius(self) -> QIcon:
        try:
            img_data = base64.b64decode(LOGO_SIRIUS_B64)
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)
            if not pixmap.isNull():
                return QIcon(pixmap)
        except Exception:
            pass
        return self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def _configurar_bandeja(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.icone_sirius)
        self.tray.setToolTip("S.I.R.I.U.S. - Sistema Ativo")

        menu = QMenu()
        menu.addAction("Abrir Interface", self.showNormal)
        menu.addSeparator()
        menu.addAction("Sair do Sirius", self.sair_total)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_icon_activated)
        self.tray.show()

    # ------------------------------------------------------------------
    # Processamento de texto manual (digitado na UI)
    # ------------------------------------------------------------------

    def enviar_texto_manual(self):
        """Processa texto digitado na interface gráfica."""
        texto_original = self.input_texto.text().strip()
        if not texto_original:
            return

        self.input_texto.clear()
        self.log_usuario(texto_original)

        # Garante wake word para que o cérebro processe corretamente
        if "sirius" not in texto_original.lower():
            texto_para_processar = f"Sirius, {texto_original}"
        else:
            texto_para_processar = texto_original

        def _thread_resposta():
            resposta_final = self.cerebro.processar(texto_para_processar)
            if resposta_final:
                self.log_sirius(resposta_final)
                # Áudio delegado ao worker da interface (evita conflito de microfone)
                self.worker.audio.falar(resposta_final)
                print(f"\n[SIRIUS TEXTO]: {resposta_final}")

        threading.Thread(target=_thread_resposta, daemon=True).start()

    # ------------------------------------------------------------------
    # Eventos da janela e bandeja
    # ------------------------------------------------------------------

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()

    def closeEvent(self, event: QCloseEvent):
        """Minimiza para a bandeja em vez de fechar."""
        if self.ativo:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "S.I.R.I.U.S.",
                "Operando em segundo plano.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )

    def sair_total(self):
        """Encerra completamente a aplicação."""
        self.ativo = False
        self.worker.rodando = False  # Para o SiriusWorker corretamente
        self.tray.hide()
        QApplication.quit()
        os._exit(0)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Instancia o cerebro antecipadamente para ler o último modo
    cerebro_temp = SiriusCerebro()

    # Lê o último modo usado da memória
    ultimo_modo = None
    if hasattr(cerebro_temp, 'memoria') and cerebro_temp.memoria:
        try:
            ultimo_modo = cerebro_temp.memoria.carregar_estado("ultimo_modo")
        except Exception:
            pass

    print(f"\033[94m[MAIN]: Último modo registrado: "
          f"{ultimo_modo or 'nenhum (primeiro boot)'}\033[0m")

    if ultimo_modo == "wallpaper":
        # Último uso foi como wallpaper — lança sirius_wallpaper.py
        print("\033[92m[MAIN]: Restaurando modo wallpaper...\033[0m")
        import subprocess
        wallpaper_path = os.path.join(diretorio_src, "sirius_wallpaper.py")
        if os.path.exists(wallpaper_path):
            subprocess.Popen(
                [sys.executable, wallpaper_path],
                cwd=diretorio_src
            )
            sys.exit(0)
        else:
            print("[MAIN]: sirius_wallpaper.py não encontrado — abrindo modo janela.")

    # Modo janela normal (padrão ou fallback)
    sirius = SiriusAppPrincipal(cerebro=cerebro_temp)
    sirius.show()
    sys.exit(app.exec())