"""
sirius_camera.py — Visão por câmera do Sirius

Capacidades:
  - Identificação facial (quem está na frente da câmera)
  - Detecção de objetos e cenas
  - Leitura de QR code e código de barras
  - Captura de foto / gravação de vídeo
  - Identificação de emoções (expressão facial)
  - Troca automática de conta pelo rosto (integração com sirius_contas.py)

Tecnologia (100% local, sem API):
  opencv-python  → captura de câmera, detecção de faces (Haar Cascade + DNN)
  face_recognition → identificação facial (dlib)
  pyzbar         → leitura de QR code e códigos de barras
  deepface       → emoções e análise facial (opcional)

Instalação mínima (detecção básica):
    pip install opencv-python
    pip install opencv-contrib-python    # para trackers extras

Instalação completa (identificação facial):
    pip install face_recognition         # requer dlib → cmake + Visual Studio
    pip install deepface                 # emoções (opcional, pesado ~500MB)

Instalação QR/barcode:
    pip install pyzbar
    # Windows: baixar DLL zlibwapi.dll se necessário

Comandos de voz:
  "sirius, abre a câmera"              → mostra feed ao vivo
  "sirius, tira uma foto"              → captura e salva frame
  "sirius, quem está na minha frente"  → identifica pessoa pela câmera
  "sirius, quantas pessoas tem aqui"   → conta faces detectadas
  "sirius, lê o qr code"               → lê QR/barcode na câmera
  "sirius, qual minha expressão"        → detecta emoção
  "sirius, cadastra meu rosto"         → registra face para a conta ativa
  "sirius, apaga meu rosto"            → remove cadastro facial
  "sirius, status da câmera"           → info sobre câmeras disponíveis

Integração com cerebro.py:
  from sirius_camera import SiriusCamera
  self._camera = SiriusCamera(contas=self._contas)
  # No processar():
  if self._camera.e_comando_camera(comando):
      return self._camera.processar_comando(comando)
"""

import os
import sys
import re
import time
import json
import threading
import unicodedata
from typing import Optional, Callable
from pathlib import Path

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
CAMINHO_FOTOS  = os.path.join(CAMINHO_DATA, "camera", "fotos")
CAMINHO_ROSTOS = os.path.join(CAMINHO_DATA, "camera", "rostos")

for _p in [CAMINHO_FOTOS, CAMINHO_ROSTOS]:
    os.makedirs(_p, exist_ok=True)


def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Detecção de dependências
# ---------------------------------------------------------------------------

def _verificar_deps() -> dict:
    status = {"opencv": False, "face_recognition": False,
              "pyzbar": False, "deepface": False}
    try:
        import cv2
        status["opencv"] = True
    except ImportError:
        pass
    try:
        import face_recognition
        status["face_recognition"] = True
    except ImportError:
        pass
    try:
        import pyzbar
        status["pyzbar"] = True
    except ImportError:
        pass
    try:
        import deepface
        status["deepface"] = True
    except ImportError:
        pass
    return status


# ---------------------------------------------------------------------------
# Capturador de câmera — gerencia o dispositivo
# ---------------------------------------------------------------------------

class CapturadorCamera:
    """
    Gerencia abertura/fechamento da câmera OpenCV.
    Mantém a câmera aberta enquanto houver operações em andamento,
    liberando após timeout de inatividade.
    """

    def __init__(self, indice: int = 0, timeout_idle: float = 30.0):
        self._indice      = indice
        self._cap         = None
        self._lock        = threading.Lock()
        self._ultimo_uso  = 0.0
        self._timeout     = timeout_idle
        self._disponivel  = False
        self._thread_guard = None
        self._rodando     = False

        self._verificar_disponibilidade()

    def _verificar_disponibilidade(self):
        try:
            import cv2
            cap = cv2.VideoCapture(self._indice, cv2.CAP_DSHOW)
            if cap.isOpened():
                self._disponivel = True
                cap.release()
                print(f"\033[92m[CAMERA]: Câmera {self._indice} disponível.\033[0m")
            else:
                print(f"\033[33m[CAMERA]: Câmera {self._indice} não encontrada.\033[0m")
        except ImportError:
            print("\033[33m[CAMERA]: opencv-python não instalado. pip install opencv-python\033[0m")
        except Exception as e:
            print(f"\033[33m[CAMERA]: Erro ao verificar câmera: {e}\033[0m")

    def abrir(self) -> bool:
        if not self._disponivel:
            return False
        with self._lock:
            if self._cap and self._cap.isOpened():
                self._ultimo_uso = time.time()
                return True
            try:
                import cv2
                self._cap = cv2.VideoCapture(self._indice, cv2.CAP_DSHOW)
                # Resolução balanceada — qualidade boa sem lag
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._cap.set(cv2.CAP_PROP_FPS, 30)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # buffer mínimo = frames frescos
                if self._cap.isOpened():
                    self._ultimo_uso = time.time()
                    self._iniciar_guard()
                    return True
                return False
            except Exception as e:
                print(f"[CAMERA]: Erro ao abrir: {e}")
                return False

    def capturar_frame(self):
        """Captura um frame. Retorna (ok, frame) ou (False, None)."""
        if not self.abrir():
            return False, None
        with self._lock:
            self._ultimo_uso = time.time()
            if self._cap and self._cap.isOpened():
                # Descarta frames antigos do buffer
                for _ in range(2):
                    self._cap.grab()
                ok, frame = self._cap.read()
                return ok, frame
        return False, None

    def fechar(self):
        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None
        self._rodando = False

    def _iniciar_guard(self):
        """Thread que fecha a câmera após timeout de inatividade."""
        if self._rodando:
            return
        self._rodando = True

        def _guard():
            while self._rodando:
                time.sleep(5)
                with self._lock:
                    if (self._cap and self._cap.isOpened() and
                            time.time() - self._ultimo_uso > self._timeout):
                        self._cap.release()
                        self._cap = None
                        self._rodando = False
                        print("[CAMERA]: Câmera fechada por inatividade.")
                        return

        self._thread_guard = threading.Thread(target=_guard, daemon=True, name="CameraGuard")
        self._thread_guard.start()

    def listar_cameras() -> list[int]:
        """Descobre índices de câmeras disponíveis (0-4)."""
        try:
            import cv2
            disponiveis = []
            for i in range(5):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    disponiveis.append(i)
                    cap.release()
            return disponiveis
        except Exception:
            return []

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# Detector de faces — Haar Cascade (leve) + DNN (preciso)
# ---------------------------------------------------------------------------

class DetectorFaces:
    """
    Detecta faces em frames com OpenCV.
    Usa Haar Cascade por padrão (rápido, sem instalação extra).
    Se o modelo DNN estiver disponível, usa ele (mais preciso).
    """

    def __init__(self):
        self._cascade  = None
        self._net      = None
        self._inicializar()

    def _inicializar(self):
        try:
            import cv2
            # Haar Cascade — sempre disponível com opencv
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._cascade = cv2.CascadeClassifier(cascade_path)
            print("\033[92m[CAMERA]: Detector de faces (Haar) ativo.\033[0m")
        except Exception as e:
            print(f"[CAMERA]: Detector de faces indisponível: {e}")

    def detectar(self, frame) -> list[tuple]:
        """
        Detecta faces no frame.
        Retorna lista de (x, y, largura, altura) de cada face.
        """
        if self._cascade is None or frame is None:
            return []
        try:
            import cv2
            cinza   = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces   = self._cascade.detectMultiScale(
                cinza,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(40, 40),
                flags=cv2.CASCADE_SCALE_IMAGE
            )
            if len(faces) == 0:
                return []
            return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]
        except Exception:
            return []

    def recortar_face(self, frame, bbox: tuple, margem: float = 0.2):
        """Recorta a região da face com margem."""
        try:
            x, y, w, h  = bbox
            h_img, w_img = frame.shape[:2]
            mg_x = int(w * margem)
            mg_y = int(h * margem)
            x1   = max(0, x - mg_x)
            y1   = max(0, y - mg_y)
            x2   = min(w_img, x + w + mg_x)
            y2   = min(h_img, y + h + mg_y)
            return frame[y1:y2, x1:x2]
        except Exception:
            return frame


# ---------------------------------------------------------------------------
# Identificador facial — face_recognition (dlib)
# ---------------------------------------------------------------------------

class IdentificadorFacial:
    """
    Identifica quem está na câmera comparando com rostos cadastrados.
    Usa a biblioteca face_recognition (baseada em dlib — 99.38% de precisão).

    Instalação:
        pip install face_recognition
        # Windows: pode precisar de cmake e Visual Studio Build Tools
        # Alternativa sem compilar: pip install face_recognition --no-build-isolation
    """

    THRESHOLD = 0.50   # distância máxima para considerar match (0=idêntico, 1=diferente)

    def __init__(self):
        self._disponivel = False
        self._encodings: dict[str, list] = {}  # conta_id → lista de encodings
        self._meta: dict[str, str] = {}         # conta_id → nome
        self._lock = threading.Lock()
        self._verificar()
        self._carregar_rostos()

    def _verificar(self):
        try:
            import face_recognition
            self._disponivel = True
            print("\033[92m[CAMERA]: face_recognition ativo — identificação facial disponível.\033[0m")
        except ImportError:
            print("\033[33m[CAMERA]: face_recognition não instalado.")
            print("  pip install face_recognition\033[0m")

    def _carregar_rostos(self):
        """Carrega encodings salvos em disco."""
        if not self._disponivel:
            return
        meta_path = os.path.join(CAMINHO_ROSTOS, "meta.json")
        if os.path.exists(meta_path):
            try:
                import numpy as np
                with open(meta_path) as f:
                    self._meta = json.load(f)
                for conta_id, nome in self._meta.items():
                    enc_path = os.path.join(CAMINHO_ROSTOS, f"{conta_id}.npy")
                    if os.path.exists(enc_path):
                        self._encodings[conta_id] = list(np.load(enc_path, allow_pickle=True))
                n = sum(len(v) for v in self._encodings.values())
                if n > 0:
                    print(f"\033[92m[CAMERA]: {len(self._encodings)} rosto(s) carregado(s) ({n} amostras).\033[0m")
            except Exception as e:
                print(f"[CAMERA]: Erro ao carregar rostos: {e}")

    def _salvar_rostos(self):
        try:
            import numpy as np
            meta_path = os.path.join(CAMINHO_ROSTOS, "meta.json")
            with open(meta_path, "w") as f:
                json.dump(self._meta, f)
            for conta_id, encodings in self._encodings.items():
                enc_path = os.path.join(CAMINHO_ROSTOS, f"{conta_id}.npy")
                np.save(enc_path, np.array(encodings))
        except Exception as e:
            print(f"[CAMERA]: Erro ao salvar rostos: {e}")

    def extrair_encoding(self, frame) -> Optional[object]:
        """Extrai o encoding facial do primeiro rosto encontrado no frame."""
        if not self._disponivel:
            return None
        try:
            import face_recognition
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locais = face_recognition.face_locations(rgb, model="hog")
            if not locais:
                return None
            encs = face_recognition.face_encodings(rgb, locais)
            return encs[0] if encs else None
        except Exception:
            return None

    def identificar(self, frame) -> Optional[tuple[str, str, float]]:
        """
        Identifica quem está no frame.
        Retorna (conta_id, nome, confiança) ou None se não reconheceu.
        """
        if not self._disponivel or not self._encodings:
            return None
        enc = self.extrair_encoding(frame)
        if enc is None:
            return None

        melhor_id    = None
        melhor_nome  = None
        melhor_dist  = self.THRESHOLD

        with self._lock:
            for conta_id, encs_lista in self._encodings.items():
                if not encs_lista:
                    continue
                import face_recognition
                dists = face_recognition.face_distance(encs_lista, enc)
                dist_min = float(min(dists))
                if dist_min < melhor_dist:
                    melhor_dist = dist_min
                    melhor_id   = conta_id
                    melhor_nome = self._meta.get(conta_id, "?")

        if melhor_id:
            confianca = 1.0 - melhor_dist  # 0.5 dist → 50% conf
            return melhor_id, melhor_nome, confianca
        return None

    def cadastrar(self, conta_id: str, nome: str, frames: list,
                  callback_status: Callable = None) -> int:
        """
        Cadastra rostos de uma lista de frames.
        Retorna número de amostras válidas extraídas.
        """
        if not self._disponivel:
            if callback_status:
                callback_status("face_recognition não instalado. pip install face_recognition")
            return 0

        novos_encs = []
        for frame in frames:
            enc = self.extrair_encoding(frame)
            if enc is not None:
                novos_encs.append(enc)

        if not novos_encs:
            if callback_status:
                callback_status("Nenhum rosto detectado nos frames. Tente em melhor iluminação.")
            return 0

        with self._lock:
            if conta_id not in self._encodings:
                self._encodings[conta_id] = []
            self._encodings[conta_id].extend(novos_encs)
            self._meta[conta_id] = nome
            self._salvar_rostos()

        if callback_status:
            callback_status(f"✓ {len(novos_encs)} amostras cadastradas para {nome}.")
        return len(novos_encs)

    def remover(self, conta_id: str) -> bool:
        with self._lock:
            if conta_id in self._encodings:
                del self._encodings[conta_id]
                self._meta.pop(conta_id, None)
                enc_path = os.path.join(CAMINHO_ROSTOS, f"{conta_id}.npy")
                try:
                    os.remove(enc_path)
                except Exception:
                    pass
                self._salvar_rostos()
                return True
        return False

    @property
    def disponivel(self) -> bool:
        return self._disponivel

    @property
    def tem_rostos(self) -> bool:
        return bool(self._encodings)


# ---------------------------------------------------------------------------
# Leitor de QR code e código de barras
# ---------------------------------------------------------------------------

class LeitorQR:
    """
    Lê QR codes e códigos de barras via câmera.
    Usa pyzbar (gratuito, sem API).

    pip install pyzbar
    """

    def __init__(self):
        self._disponivel = False
        try:
            import pyzbar.pyzbar
            self._disponivel = True
            print("\033[92m[CAMERA]: Leitor QR/Barcode ativo.\033[0m")
        except ImportError:
            print("\033[33m[CAMERA]: pyzbar não instalado. pip install pyzbar\033[0m")

    def ler_frame(self, frame) -> list[dict]:
        """
        Lê todos os códigos no frame.
        Retorna lista de {'tipo': 'QRCODE'/'EAN13'/..., 'dados': '...'}
        """
        if not self._disponivel or frame is None:
            return []
        try:
            import cv2
            from pyzbar import pyzbar
            cinza    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            codigos  = pyzbar.decode(cinza)
            return [
                {
                    "tipo":  codigo.type,
                    "dados": codigo.data.decode("utf-8", errors="replace"),
                    "bbox":  codigo.rect,
                }
                for codigo in codigos
            ]
        except Exception:
            return []

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# Analisador de expressões faciais (opcional — DeepFace)
# ---------------------------------------------------------------------------

class AnalisadorExpressao:
    """
    Detecta emoção facial (feliz, triste, raiva, surpreso, neutro...).
    Usa DeepFace (opcional — ~500MB de modelos).

    pip install deepface
    """

    _EMOCOES_PT = {
        "happy":     "feliz",
        "sad":       "triste",
        "angry":     "irritado",
        "surprise":  "surpreso",
        "fear":      "com medo",
        "disgust":   "com nojo",
        "neutral":   "neutro",
    }

    def __init__(self):
        self._disponivel = False
        try:
            import deepface
            self._disponivel = True
            print("\033[92m[CAMERA]: DeepFace ativo — análise de expressões disponível.\033[0m")
        except ImportError:
            pass

    def analisar(self, frame) -> Optional[dict]:
        """
        Analisa expressão facial no frame.
        Retorna {'emocao': 'feliz', 'confianca': 0.92} ou None.
        """
        if not self._disponivel or frame is None:
            return None
        try:
            import cv2
            from deepface import DeepFace
            rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            resultado = DeepFace.analyze(
                rgb,
                actions=["emotion"],
                enforce_detection=False,
                silent=True,
            )
            if resultado and isinstance(resultado, list):
                dados    = resultado[0]
                emocao_en = dados.get("dominant_emotion", "neutral")
                emocao_pt = self._EMOCOES_PT.get(emocao_en, emocao_en)
                confianca = dados.get("emotion", {}).get(emocao_en, 0) / 100.0
                return {"emocao": emocao_pt, "emocao_en": emocao_en, "confianca": confianca}
        except Exception:
            pass
        return None

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# SiriusCamera — motor principal
# ---------------------------------------------------------------------------

class SiriusCamera:
    """
    Sistema de visão por câmera do Sirius.

    Integração no cerebro.py (_inicializar_modulos):
        try:
            from sirius_camera import SiriusCamera
            self._camera = SiriusCamera(contas=self._contas)
            print("[CEREBRO]: Sistema de câmera ativo.")
        except Exception as e:
            self._camera = None

    No processar(), antes do classificador:
        if self._camera and self._camera.e_comando_camera(comando):
            resp = self._camera.processar_comando(
                comando,
                callback_falar=getattr(self, '_callback_falar', None)
            )
            if resp:
                self._adicionar_contexto("assistant", resp)
                self.memoria.salvar_historico(comando, resp)
                return resp
    """

    def __init__(self, indice_camera: int = 0, contas=None,
                 callback_troca_conta: Callable = None):
        self._contas              = contas
        self._callback_troca      = callback_troca_conta
        self._capturador          = CapturadorCamera(indice_camera)
        self._detector_faces      = DetectorFaces()
        self._identificador       = IdentificadorFacial()
        self._leitor_qr           = LeitorQR()
        self._expressao           = AnalisadorExpressao()
        self._lock                = threading.Lock()

        # Cooldown de identificação — não identifica a cada frame
        self._ultimo_id_conta = ""
        self._ultimo_id_ts    = 0.0
        self._COOLDOWN_ID     = 8.0    # segundos

        # Monitor passivo em background (identifica quem está na câmera)
        self._monitor_ativo   = False
        self._thread_monitor  = None

    # -----------------------------------------------------------------------
    # API pública — chamados pelo cerebro.py
    # -----------------------------------------------------------------------

    def tirar_foto(self, nome: str = "") -> Optional[str]:
        """Captura um frame e salva em disco. Retorna caminho ou None."""
        ok, frame = self._capturador.capturar_frame()
        if not ok or frame is None:
            return None
        try:
            import cv2
            ts     = int(time.time())
            nome_f = re.sub(r"[^\w]", "_", nome) + f"_{ts}.jpg" if nome else f"foto_{ts}.jpg"
            caminho = os.path.join(CAMINHO_FOTOS, nome_f)
            cv2.imwrite(caminho, frame)
            print(f"[CAMERA]: Foto salva em {caminho}")
            return caminho
        except Exception as e:
            print(f"[CAMERA]: Erro ao salvar foto: {e}")
            return None

    def quem_esta_na_frente(self) -> str:
        """Identifica quem está na câmera agora."""
        ok, frame = self._capturador.capturar_frame()
        if not ok or frame is None:
            return "Não consigo acessar a câmera agora."

        faces = self._detector_faces.detectar(frame)
        if not faces:
            return "Não detectei nenhuma pessoa na câmera."

        if len(faces) > 1:
            return f"Tem {len(faces)} pessoas na câmera. Identificação funciona melhor com uma pessoa só."

        if not self._identificador.disponivel:
            return (
                f"Detectei 1 pessoa, mas face_recognition não está instalado. "
                "Para identificar: pip install face_recognition"
            )

        if not self._identificador.tem_rostos:
            return (
                "Detectei uma pessoa, mas nenhum rosto está cadastrado ainda. "
                "Diz: 'sirius, cadastra meu rosto' para começar."
            )

        resultado = self._identificador.identificar(frame)
        if resultado:
            conta_id, nome, conf = resultado
            pct = int(conf * 100)
            return f"Reconheci {nome} com {pct}% de confiança."
        return "Tem uma pessoa na câmera, mas não reconheci quem é."

    def contar_pessoas(self) -> str:
        """Conta quantas pessoas estão visíveis."""
        ok, frame = self._capturador.capturar_frame()
        if not ok or frame is None:
            return "Câmera indisponível."
        faces = self._detector_faces.detectar(frame)
        n = len(faces)
        if n == 0:
            return "Nenhuma pessoa detectada na câmera agora."
        if n == 1:
            return "Tem 1 pessoa na câmera."
        return f"Tem {n} pessoas na câmera agora."

    def ler_qr(self, timeout: float = 10.0) -> str:
        """
        Tenta ler um QR code ou código de barras por até `timeout` segundos.
        """
        if not self._leitor_qr.disponivel:
            return "pyzbar não instalado. pip install pyzbar"
        if not self._capturador.disponivel:
            return "Câmera indisponível."

        print(f"[CAMERA]: Procurando QR code por {timeout}s...")
        t_inicio = time.time()
        while time.time() - t_inicio < timeout:
            ok, frame = self._capturador.capturar_frame()
            if ok and frame is not None:
                codigos = self._leitor_qr.ler_frame(frame)
                if codigos:
                    c = codigos[0]
                    tipo   = c["tipo"]
                    dados  = c["dados"]
                    print(f"[CAMERA]: {tipo} lido → '{dados[:60]}'")
                    return f"{tipo} detectado: {dados}"
            time.sleep(0.1)

        return "Nenhum QR code ou código de barras detectado. Aproxime o código da câmera."

    def analisar_expressao(self) -> str:
        """Detecta a expressão facial atual."""
        ok, frame = self._capturador.capturar_frame()
        if not ok or frame is None:
            return "Câmera indisponível."

        faces = self._detector_faces.detectar(frame)
        if not faces:
            return "Não detectei nenhuma face para analisar a expressão."

        if not self._expressao.disponivel:
            return (
                "DeepFace não instalado. Para analisar expressões: pip install deepface"
            )

        resultado = self._expressao.analisar(frame)
        if resultado:
            emocao = resultado["emocao"]
            conf   = int(resultado["confianca"] * 100)
            return f"Você parece estar {emocao} ({conf}% de confiança)."
        return "Não consegui analisar a expressão agora."

    def cadastrar_rosto(self, conta_id: str, conta_nome: str,
                        n_frames: int = 20,
                        callback_status: Callable = None) -> str:
        """
        Captura múltiplos frames e cadastra o rosto da conta.
        """
        if not self._identificador.disponivel:
            return "face_recognition não instalado. pip install face_recognition"
        if not self._capturador.disponivel:
            return "Câmera indisponível."

        def _falar(msg):
            print(f"[CAMERA]: {msg}")
            if callback_status:
                try:
                    callback_status(msg)
                except Exception:
                    pass

        def _gravar():
            _falar(
                f"Vou capturar seu rosto, {conta_nome}. "
                "Olhe diretamente para a câmera e vire levemente a cabeça para os lados."
            )
            time.sleep(1.5)

            frames_validos = []
            angulos        = ["frente", "levemente pra direita", "levemente pra esquerda",
                              "levemente pra cima", "frente novamente"]

            for i, angulo in enumerate(angulos):
                _falar(f"Agora olhe {angulo}. ({i+1}/{len(angulos)})")
                time.sleep(0.8)

                capturados = 0
                tentativas = 0
                while capturados < 4 and tentativas < 20:
                    ok, frame = self._capturador.capturar_frame()
                    if ok and frame is not None:
                        faces = self._detector_faces.detectar(frame)
                        if faces:
                            frames_validos.append(frame.copy())
                            capturados += 1
                    tentativas += 1
                    time.sleep(0.1)

            n = self._identificador.cadastrar(
                conta_id, conta_nome, frames_validos,
                callback_status=_falar
            )
            if n >= 5:
                _falar(
                    f"Perfeito! Rosto de {conta_nome} cadastrado com {n} amostras. "
                    "Agora vou te reconhecer automaticamente pela câmera."
                )
            else:
                _falar(f"Consegui {n} amostras. Tente novamente com melhor iluminação.")

        threading.Thread(target=_gravar, daemon=True).start()
        return f"Iniciando cadastro de rosto para {conta_nome}. Olhe para a câmera!"

    def remover_rosto(self, conta_id: str, conta_nome: str) -> str:
        ok = self._identificador.remover(conta_id)
        if ok:
            return f"✓ Rosto de {conta_nome} removido."
        return f"Nenhum rosto cadastrado para {conta_nome}."

    # -----------------------------------------------------------------------
    # Monitor passivo — identifica rostos em background
    # -----------------------------------------------------------------------

    def iniciar_monitor(self):
        """
        Inicia monitoramento passivo da câmera.
        Quando identifica uma pessoa diferente da conta ativa, troca automaticamente.
        """
        if self._monitor_ativo or not self._identificador.tem_rostos:
            return
        self._monitor_ativo = True
        self._thread_monitor = threading.Thread(
            target=self._loop_monitor, daemon=True, name="CameraMonitor"
        )
        self._thread_monitor.start()
        print("\033[92m[CAMERA]: Monitor passivo ativo — identificação facial em background.\033[0m")

    def parar_monitor(self):
        self._monitor_ativo = False

    def _loop_monitor(self):
        """Loop de monitoramento — verifica rosto a cada 3 segundos."""
        while self._monitor_ativo:
            time.sleep(3)

            # Cooldown — não verifica se acabou de identificar
            if time.time() - self._ultimo_id_ts < self._COOLDOWN_ID:
                continue

            ok, frame = self._capturador.capturar_frame()
            if not ok or frame is None:
                continue

            faces = self._detector_faces.detectar(frame)
            if not faces:
                continue

            resultado = self._identificador.identificar(frame)
            if not resultado:
                continue

            conta_id, nome, conf = resultado
            if conf < 0.60:  # só troca com boa confiança
                continue

            self._ultimo_id_ts    = time.time()
            self._ultimo_id_conta = conta_id

            print(f"\033[94m[CAMERA]: {nome} detectado (conf={conf:.0%}).\033[0m")

            # Verifica se é uma conta diferente da ativa
            if self._contas and hasattr(self._contas, "conta_ativa"):
                conta_atual = self._contas.conta_ativa
                if conta_atual and conta_atual.id != conta_id:
                    # Troca de conta pelo rosto
                    if self._callback_troca:
                        self._callback_troca(conta_id, nome, conf)

    # -----------------------------------------------------------------------
    # Comandos de voz
    # -----------------------------------------------------------------------

    _TRIGGERS_CAMERA = frozenset({
        # Foto
        "tira uma foto", "tirar uma foto", "tira foto", "foto agora",
        "captura uma foto", "faz uma foto", "fotografa",
        # Quem está
        "quem esta na minha frente", "quem esta ai", "quem voce ve",
        "quem tem na camera", "identifica quem esta", "reconhece quem esta",
        "quem sou eu", "me identifica", "me reconhece",
        # Contar
        "quantas pessoas", "quantas pessoas tem", "conta as pessoas",
        "tem alguem na camera",
        # QR
        "le o qr", "lê o qr", "ler qr", "escaneia qr",
        "le o qr code", "lê o qr code", "le o codigo",
        "escaneia o codigo", "lê o código", "qr code",
        "le o codigo de barras", "lê o código de barras",
        # Expressão
        "qual minha expressao", "qual é minha expressão", "como estou",
        "analisa minha expressao", "que expressao estou",
        # Cadastro
        "cadastra meu rosto", "cadastrar meu rosto", "registra meu rosto",
        "salva meu rosto", "aprende meu rosto",
        "apaga meu rosto", "remove meu rosto", "deleta meu rosto",
        # Status
        "status da camera", "status da câmera", "camera disponivel",
        "câmera disponível", "tem camera", "tem câmera",
        # Geral
        "abre a camera", "abre a câmera", "liga a camera", "liga a câmera",
    })

    def e_comando_camera(self, texto: str) -> bool:
        t = _norm(texto)
        return any(tr in t for tr in self._TRIGGERS_CAMERA)

    def processar_comando(self, texto: str, conta_ativa=None,
                           callback_falar: Callable = None) -> str:
        t = _norm(texto)

        # ── Status ────────────────────────────────────────────────────────
        if any(p in t for p in ["status da camera", "status da câmera",
                                 "camera disponivel", "tem camera"]):
            return self._status_texto()

        # ── Foto ──────────────────────────────────────────────────────────
        if any(p in t for p in ["tira uma foto", "tira foto", "foto agora",
                                  "captura uma foto", "faz uma foto", "fotografa"]):
            caminho = self.tirar_foto()
            if caminho:
                nome_arq = os.path.basename(caminho)
                return f"✓ Foto salva: {nome_arq}"
            return "✗ Não consegui tirar a foto. Verifique se a câmera está conectada."

        # ── Quem está ─────────────────────────────────────────────────────
        if any(p in t for p in ["quem esta", "quem voce ve", "quem tem na camera",
                                  "identifica quem", "reconhece quem", "me identifica",
                                  "me reconhece"]):
            return self.quem_esta_na_frente()

        # ── Contar ────────────────────────────────────────────────────────
        if any(p in t for p in ["quantas pessoas", "conta as pessoas", "tem alguem"]):
            return self.contar_pessoas()

        # ── QR code ───────────────────────────────────────────────────────
        if any(p in t for p in ["qr", "codigo de barras", "código de barras",
                                  "escaneia", "le o codigo", "lê o código"]):
            return self.ler_qr()

        # ── Expressão ─────────────────────────────────────────────────────
        if any(p in t for p in ["expressao", "expressão", "como estou", "que expressao"]):
            return self.analisar_expressao()

        # ── Cadastrar rosto ───────────────────────────────────────────────
        if any(p in t for p in ["cadastra meu rosto", "registra meu rosto",
                                  "salva meu rosto", "aprende meu rosto"]):
            if not conta_ativa:
                return "Nenhuma conta ativa para cadastrar o rosto."
            return self.cadastrar_rosto(
                conta_ativa.id, conta_ativa.nome,
                callback_status=callback_falar
            )

        # ── Remover rosto ─────────────────────────────────────────────────
        if any(p in t for p in ["apaga meu rosto", "remove meu rosto", "deleta meu rosto"]):
            if not conta_ativa:
                return "Nenhuma conta ativa para remover o rosto."
            return self.remover_rosto(conta_ativa.id, conta_ativa.nome)

        # ── Abrir câmera ──────────────────────────────────────────────────
        if any(p in t for p in ["abre a camera", "abre a câmera", "liga a camera"]):
            if self._capturador.abrir():
                return "Câmera ativa. Pode mandar o comando."
            return "✗ Não consegui acessar a câmera."

        return "Comando de câmera não reconhecido."

    def _status_texto(self) -> str:
        deps  = _verificar_deps()
        linhas = ["Status da câmera:"]
        linhas.append(f"  Câmera:            {'✓ disponível' if self._capturador.disponivel else '✗ não encontrada'}")
        linhas.append(f"  Detecção de faces: {'✓ Haar Cascade' if self._detector_faces._cascade else '✗ OpenCV erro'}")
        linhas.append(f"  Identificação:     {'✓ face_recognition' if deps['face_recognition'] else '✗ pip install face_recognition'}")
        linhas.append(f"  QR/Barcode:        {'✓ pyzbar' if deps['pyzbar'] else '✗ pip install pyzbar'}")
        linhas.append(f"  Expressões:        {'✓ deepface' if deps['deepface'] else '✗ pip install deepface (opcional)'}")
        n_rostos = sum(len(v) for v in self._identificador._encodings.values())
        n_pessoas = len(self._identificador._encodings)
        linhas.append(f"  Rostos cadastrados: {n_pessoas} pessoa(s), {n_rostos} amostra(s)")
        return "\n".join(linhas)

    def status(self) -> dict:
        deps = _verificar_deps()
        return {
            "camera_disponivel":      self._capturador.disponivel,
            "face_recognition":       deps["face_recognition"],
            "pyzbar":                 deps["pyzbar"],
            "deepface":               deps["deepface"],
            "rostos_cadastrados":     len(self._identificador._encodings),
            "monitor_ativo":          self._monitor_ativo,
        }


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_camera_instance: Optional[SiriusCamera] = None

def get_camera(contas=None) -> SiriusCamera:
    global _camera_instance
    if _camera_instance is None:
        _camera_instance = SiriusCamera(contas=contas)
    return _camera_instance


# ---------------------------------------------------------------------------
# Standalone — testa a câmera
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Testa o SiriusCamera")
    parser.add_argument("--status",  action="store_true")
    parser.add_argument("--foto",    action="store_true")
    parser.add_argument("--quem",    action="store_true")
    parser.add_argument("--qr",      action="store_true")
    parser.add_argument("--expressao", action="store_true")
    args = parser.parse_args()

    cam = SiriusCamera()

    if args.status or not any([args.foto, args.quem, args.qr, args.expressao]):
        print(cam._status_texto())

    if args.foto:
        p = cam.tirar_foto("teste")
        print(f"Foto: {p}")

    if args.quem:
        print(cam.quem_esta_na_frente())

    if args.qr:
        print(cam.ler_qr(timeout=15))

    if args.expressao:
        print(cam.analisar_expressao())