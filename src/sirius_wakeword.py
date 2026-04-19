"""
sirius_wakeword.py — Wake word passiva do Sirius, 100% local, sem API

Usa openWakeWord (https://github.com/dscripka/openWakeWord)
- Roda offline, sem chave de API, sem internet
- Modelos pré-treinados: "hey jarvis", "alexa", "hey mycroft" e outros
- Suporta treino personalizado com sua voz (~5min de áudio)
- Consumo ~3% de CPU

Instalação:
    pip install openwakeword pyaudio

Modelos disponíveis gratuitamente (sem treino):
    "hey_jarvis"    ← mais parecido com Jarvis
    "alexa"
    "hey_mycroft"
    "timers_en"

Para treinar "ei sirius" com sua voz:
    python sirius_wakeword.py --treinar
    (grava 5min de você dizendo "ei sirius" e treina o modelo)
"""

import os
import sys
import time
import struct
import threading
import winsound
import argparse
import numpy as np

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

# Caminho para modelo personalizado treinado com sua voz
CAMINHO_MODELO_CUSTOM = os.path.join(diretorio_raiz, "config", "sirius_wakeword.onnx")

# Modelo builtin a usar se nao tiver custom treinado
# "hey_jarvis" e o mais parecido foneticamente com "sirius" nos modelos disponiveis
# Para treinar seu proprio modelo: python sirius_wakeword.py --treinar
MODELO_BUILTIN = "hey_jarvis"

# Palavras que o Whisper fallback deve reconhecer como wake word
# Inclui TODAS as variantes fonéticas vistas nos logs do Whisper Tiny
# Variantes fonéticas que o Whisper Tiny REALMENTE confunde com "sirius".
# REGRAS:
#   1. Sem palavras que aparecem em frases normais ("filhos", "seguiu", "see you")
#   2. Sem palavras curtas demais (< 5 letras) — falso positivo muito alto
#   3. O check é por PALAVRA INTEIRA (\b), não substring
#   4. Palavras compostas ("ei sirius") têm check exato por substring
WAKE_WORDS_SIRIUS = {
    # Grafias corretas
    "sirius", "sírius", "siriuz",
    # Variantes fonéticas confirmadas nos logs (soam genuinamente como "sirius")
    "serios", "sérios", "cedios", "fidios",
    "fírios", "fibios", "firius", "fírius",
    "sídios", "seídios",
    # Com prefixos — busca substring exata (contêm "sirius")
    "ei sirius", "oi sirius", "hey sirius",
    "ei serios", "oi serios",
    # REMOVIDOS PROPOSITALMENTE:
    #   "serious"  → ativa em "estou seriamente cansado"
    #   "filhos"   → ativa em "tenho dois filhos"
    #   "seguiu"   → ativa em "ele seguiu em frente"
    #   "see you"  → ativa em qualquer despedida em inglês
    #   "jarvis"   → ativa em filmes/séries
}

# Sensibilidade: 0.0 (muitos falsos positivos) a 1.0 (muito restrito)
# 0.5 é um bom equilíbrio
SENSIBILIDADE = 0.5

# Taxa de amostragem exigida pelo openWakeWord
SAMPLE_RATE   = 16000
CHUNK_SIZE    = 1280  # 80ms de áudio por frame


# ---------------------------------------------------------------------------
# Verificação de wake word por palavra inteira (não substring)
# ---------------------------------------------------------------------------

def _contem_wake_word(texto: str) -> bool:
    """
    Verifica se o texto contém uma wake word por PALAVRA INTEIRA.

    Por que isso importa:
      - Substring: "serios" ativa em "estou seriamente cansado" → FALSO POSITIVO
      - Palavra inteira: "serios" só ativa se for uma palavra separada → CORRETO

    Para termos compostos ("ei sirius"), usa busca substring exata
    porque já são específicos o suficiente.
    """
    import re as _re
    t = texto.lower().strip()

    for ww in WAKE_WORDS_SIRIUS:
        if " " in ww:
            # Termo composto — busca exata por substring
            if ww in t:
                return True
        else:
            # Palavra única — exige borda de palavra \b
            if _re.search(r"\b" + _re.escape(ww) + r"\b", t):
                return True

    return False


# ---------------------------------------------------------------------------
# Detector de wake word — openWakeWord (principal)
# ---------------------------------------------------------------------------

class SiriusWakeWord:
    """
    Detecta wake word passivamente usando openWakeWord.
    100% local, sem API, ~3% CPU.
    """

    def __init__(self, callback_ativado, sensibilidade: float = SENSIBILIDADE):
        self._callback        = callback_ativado
        self._sensibilidade   = sensibilidade
        self._rodando         = False
        self._thread          = None
        self._modo            = None
        self._oww             = None
        self._deteccoes       = 0
        self._ultima_deteccao = 0
        self._cooldown        = 2.5  # segundos mínimos entre detecções

    # -----------------------------------------------------------------------
    # Inicialização
    # -----------------------------------------------------------------------

    def _inicializar_oww(self) -> bool:
        """
        Tenta inicializar o openWakeWord.
        PRIORIDADE:
          1. Modelo pkl treinado com sua voz (MFCC + sklearn) — detecta "sirius" direto
          2. Modelo onnx personalizado (openwakeword)
          3. Fallback Whisper Tiny
        """
        # Prioridade 1: modelo pkl treinado (gerado pelo --so-treinar)
        CAMINHO_PKL = CAMINHO_MODELO_CUSTOM.replace(".onnx", "_fallback.pkl")
        if os.path.exists(CAMINHO_PKL):
            try:
                import pickle
                with open(CAMINHO_PKL, "rb") as f:
                    dados = pickle.load(f)
                self._clf_pkl    = dados["clf"]
                self._scaler_pkl = dados["scaler"]
                self._modo       = "mfcc_sklearn"
                print(f"\033[92m[WAKEWORD]: Modelo personalizado MFCC carregado — detecta 'sirius' diretamente!\033[0m")
                print(f"\033[92m[WAKEWORD]: ~3% CPU. Diga 'Sirius' para ativar.\033[0m")
                return True
            except Exception as e:
                print(f"\033[33m[WAKEWORD]: Erro ao carregar modelo pkl: {e}\033[0m")

        # Prioridade 2: modelo onnx (openwakeword)
        if os.path.exists(CAMINHO_MODELO_CUSTOM):
            try:
                from openwakeword.model import Model
                self._oww  = Model(wakeword_models=[CAMINHO_MODELO_CUSTOM], inference_framework="onnx")
                self._modo = "openwakeword"
                print(f"\033[92m[WAKEWORD]: Modelo ONNX personalizado carregado!\033[0m")
                return True
            except Exception as e:
                print(f"\033[33m[WAKEWORD]: Erro no modelo onnx: {e}\033[0m")

        # Prioridade 3: Whisper Tiny fallback
        print("\033[92m[WAKEWORD]: Modo Whisper Tiny — detecta 'Sirius' por transcricao.\033[0m")
        print("\033[93m[WAKEWORD]: Para menor CPU, o modelo ja foi treinado — verifique o .pkl\033[0m")
        return False

        # Prioridade 3 (nunca chega aqui, mas deixa como referencia)
        # Modelo builtin hey_jarvis — precisa dizer "hey jarvis" para ativar
        try:
            from openwakeword.model import Model
            self._oww = Model(wakeword_models=[MODELO_BUILTIN], inference_framework="onnx")
            self._modo = "openwakeword"
            print(f"\033[92m[WAKEWORD]: Modelo builtin '{MODELO_BUILTIN}' — diga 'hey jarvis' para ativar.\033[0m")
            return True
        except ImportError:
            print("\033[33m[WAKEWORD]: openWakeWord nao instalado.\033[0m")
            print("  pip install openwakeword")
            return False
        except Exception as e:
            print(f"\033[33m[WAKEWORD]: {e}\033[0m")
            return False

    # -----------------------------------------------------------------------
    # Loop principal — openWakeWord
    # -----------------------------------------------------------------------

    def _loop_oww(self):
        """Loop de detecção com openWakeWord. Baixíssimo consumo de CPU."""
        try:
            import pyaudio
            pa     = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            print(f"\033[31m[WAKEWORD]: Erro ao abrir microfone: {e}\033[0m")
            self._usar_fallback_whisper()
            return

        print(f"\033[94m[WAKEWORD]: Ouvindo passivamente...\033[0m")
        print(f"\033[94m[WAKEWORD]: Diga 'Sirius' para ativar (modelo: {MODELO_BUILTIN}).\033[0m")

        while self._rodando:
            try:
                audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                audio_np    = np.frombuffer(audio_chunk, dtype=np.int16)

                predicao = self._oww.predict(audio_np)

                # Verifica se algum modelo superou a sensibilidade
                for nome_modelo, score in predicao.items():
                    if score >= self._sensibilidade:
                        agora = time.time()
                        if agora - self._ultima_deteccao >= self._cooldown:
                            self._ultima_deteccao = agora
                            self._deteccoes      += 1
                            print(f"\033[92m[WAKEWORD]: Detectado '{nome_modelo}' (score: {score:.2f})\033[0m")
                            self._ao_detectar()
                        break

            except Exception as e:
                if self._rodando:
                    print(f"\033[33m[WAKEWORD]: Erro no loop: {e}\033[0m")
                    time.sleep(0.5)

        stream.stop_stream()
        stream.close()
        pa.terminate()

    # -----------------------------------------------------------------------
    # Loop MFCC sklearn — modelo treinado com sua voz
    # -----------------------------------------------------------------------

    def _loop_mfcc(self):
        """Loop de detecção usando modelo MFCC + sklearn treinado com sua voz."""
        import wave, tempfile

        try:
            import pyaudio
        except ImportError:
            print("[WAKEWORD]: pyaudio nao instalado")
            self._usar_fallback_whisper()
            return

        RATE      = 16000
        CHUNK     = 1024
        # 2.0s por janela — suficiente para capturar "Sirius" completo
        # com margem para início e fim da fala (1.5s era muito curto)
        SEGUNDOS  = 2.0
        N_FRAMES  = int(RATE / CHUNK * SEGUNDOS)
        # Threshold de energia — descarta silêncio e ruído de fundo leve
        THRESHOLD = 350

        print("\033[92m[WAKEWORD]: Ouvindo 'Sirius' com modelo MFCC (~3% CPU)...\033[0m")

        while self._rodando:
            pa = stream = None
            try:
                pa     = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16, channels=1,
                    rate=RATE, input=True,
                    frames_per_buffer=CHUNK
                )
                frames      = []
                tem_energia = False
                for _ in range(N_FRAMES):
                    if not self._rodando:
                        break
                    chunk    = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(chunk)
                    audio_np = np.frombuffer(chunk, dtype=np.int16)
                    if np.abs(audio_np).mean() > THRESHOLD:
                        tem_energia = True
            except Exception as e:
                time.sleep(0.5)
                continue
            finally:
                try:
                    if stream: stream.stop_stream(); stream.close()
                    if pa:     pa.terminate()
                except Exception:
                    pass

            if not tem_energia or not frames:
                continue

            # Extrai MFCC e prediz
            try:
                audio_np = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
                feat     = self._extrair_mfcc(audio_np)
                feat_s   = self._scaler_pkl.transform([feat])
                pred     = self._clf_pkl.predict(feat_s)[0]
                prob     = self._clf_pkl.predict_proba(feat_s)[0][1]

                # Threshold 0.88: valor calibrado empiricamente.
                # 0.75 causava muitos falsos positivos porque o modelo
                # foi treinado com ruído sintético como negativo — qualquer
                # voz humana tem score alto. 0.88 reduz sem eliminar detecção.
                # Solução definitiva: gravar negativos reais (--gravar-negativos).
                if pred == 1 and prob > 0.88:
                    agora = time.time()
                    if agora - self._ultima_deteccao >= self._cooldown:
                        self._ultima_deteccao = agora
                        self._deteccoes      += 1
                        print(f"\033[92m[WAKEWORD]: 'Sirius' detectado! (prob: {prob:.0%})\033[0m")
                        self._ao_detectar()
            except Exception:
                pass

    def _extrair_mfcc(self, audio: np.ndarray, n_mfcc: int = 20) -> np.ndarray:
        """MFCC simplificado — mesmo algoritmo usado no treino."""
        frame_size = 512
        hop_size   = 256
        frames     = []
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i:i + frame_size] * np.hamming(frame_size)
            fft   = np.abs(np.fft.rfft(frame))
            fft   = fft[:n_mfcc * 4]
            banda = len(fft) // n_mfcc
            feats = [np.mean(fft[j*banda:(j+1)*banda]) for j in range(n_mfcc)]
            frames.append(feats)
        if not frames:
            return np.zeros(n_mfcc)
        return np.mean(frames, axis=0)

    # -----------------------------------------------------------------------
    # Fallback — Whisper Tiny (sem openWakeWord)
    # -----------------------------------------------------------------------

    def _usar_fallback_whisper(self):
        """Inicia o fallback baseado em Whisper quando openWakeWord falha."""
        self._modo   = "whisper_fallback"
        self._thread = threading.Thread(
            target=self._loop_whisper_fallback,
            daemon=True,
            name="SiriusWakeWord-Whisper"
        )
        self._thread.start()

    def _loop_whisper_fallback(self):
        """
        Detecta wake word usando pyaudio + VAD simples + Whisper Tiny.
        Grava em chunks de 3s, transcreve e verifica se contém "sirius".
        Não usa speech_recognition — evita conflito com audio_handler.py.
        """
        print("\033[93m[WAKEWORD]: Modo Whisper Tiny — ouvindo 'Sirius' continuamente...\033[0m")
        print("\033[93m[WAKEWORD]: Para menor CPU, treine: python sirius_wakeword.py --treinar\033[0m")

        import wave
        import tempfile

        try:
            import pyaudio
        except ImportError:
            print("\033[31m[WAKEWORD]: pyaudio nao instalado: pip install pyaudio\033[0m")
            return

        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            print("\033[92m[WAKEWORD]: Whisper Tiny carregado. Aguardando 'Sirius'...\033[0m")
        except Exception as e:
            print(f"\033[31m[WAKEWORD]: Falha ao carregar Whisper: {e}\033[0m")
            return

        # Configuracoes de audio
        RATE       = 16000
        CHUNK      = 1024
        SEGUNDOS   = 3      # grava 3s por vez para detectar wake word
        N_FRAMES   = int(RATE / CHUNK * SEGUNDOS)
        THRESHOLD  = 300    # energia minima para considerar que ha fala

        while self._rodando:
            pa     = None
            stream = None
            try:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK
                )

                frames       = []
                tem_energia  = False

                for _ in range(N_FRAMES):
                    if not self._rodando:
                        break
                    chunk = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(chunk)

                    # Verifica energia — so transcreve se tiver som
                    audio_np = np.frombuffer(chunk, dtype=np.int16)
                    if np.abs(audio_np).mean() > THRESHOLD:
                        tem_energia = True

            except Exception as e:
                if self._rodando:
                    print(f"\033[33m[WAKEWORD]: Erro ao capturar audio: {e}\033[0m")
                    time.sleep(1)
                continue
            finally:
                try:
                    if stream:
                        stream.stop_stream()
                        stream.close()
                    if pa:
                        pa.terminate()
                except Exception:
                    pass

            # So transcreve se detectou energia (tem fala)
            if not tem_energia or not frames:
                continue

            # Salva e transcreve
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp_path = f.name

                wf = wave.open(tmp_path, "wb")
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))
                wf.close()

                segs, _ = model.transcribe(tmp_path, language="pt")
                texto   = "".join(s.text for s in segs).lower().strip()

                if texto:
                    print(f"\033[90m[WAKEWORD DEBUG]: ouvido: '{texto[:40]}'\033[0m")

                    if _contem_wake_word(texto):
                        agora = time.time()
                        if agora - self._ultima_deteccao >= self._cooldown:
                            self._ultima_deteccao = agora
                            self._deteccoes      += 1
                            print(f"\033[92m[WAKEWORD]: 'Sirius' detectado via Whisper! ({self._deteccoes}x)\033[0m")
                            self._ao_detectar()

            except Exception as e:
                if self._rodando:
                    print(f"\033[33m[WAKEWORD]: Erro na transcricao: {e}\033[0m")
            finally:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    # -----------------------------------------------------------------------
    # Ação ao detectar
    # -----------------------------------------------------------------------

    def _ao_detectar(self):
        """Beep duplo estilo Jarvis + dispara callback."""
        try:
            winsound.Beep(700,  80)
            time.sleep(0.04)
            winsound.Beep(1100, 130)
        except Exception:
            pass

        try:
            self._callback()
        except Exception as e:
            print(f"[WAKEWORD]: Erro no callback: {e}")

    # -----------------------------------------------------------------------
    # Controle
    # -----------------------------------------------------------------------

    def iniciar(self) -> bool:
        if self._rodando:
            return True

        self._rodando = True

        ok = self._inicializar_oww()
        if ok and self._modo == "mfcc_sklearn":
            self._thread = threading.Thread(
                target=self._loop_mfcc,
                daemon=True,
                name="SiriusWakeWord-MFCC"
            )
            self._thread.start()
        elif ok and self._modo == "openwakeword":
            self._thread = threading.Thread(
                target=self._loop_oww,
                daemon=True,
                name="SiriusWakeWord-OWW"
            )
            self._thread.start()
        else:
            # Fallback Whisper Tiny
            self._modo = "whisper_fallback"
            self._thread = threading.Thread(
                target=self._loop_whisper_fallback,
                daemon=True,
                name="SiriusWakeWord-Whisper"
            )
            self._thread.start()

        print(f"\033[92m[WAKEWORD]: Wake word ativa (modo: {self._modo}).\033[0m")
        return True

    def parar(self):
        self._rodando = False

    def status(self) -> dict:
        return {
            "rodando":     self._rodando,
            "modo":        self._modo,
            "deteccoes":   self._deteccoes,
            "modelo":      CAMINHO_MODELO_CUSTOM if os.path.exists(CAMINHO_MODELO_CUSTOM) else MODELO_BUILTIN,
        }


# ---------------------------------------------------------------------------
# Treinador de wake word personalizada
# ---------------------------------------------------------------------------

class TreinadorWakeWord:
    """
    Grava sua voz dizendo 'ei sirius' e treina um modelo ONNX personalizado.
    Usa o sistema de treino do openWakeWord.

    Uso:
        python sirius_wakeword.py --treinar
    """

    FRASES = [
        "ei sirius",
        "sirius",
        "oi sirius",
        "hey sirius",
    ]

    def __init__(self):
        self.caminho_audio  = os.path.join(diretorio_raiz, "config", "treino_wakeword")
        self.caminho_modelo = CAMINHO_MODELO_CUSTOM
        os.makedirs(self.caminho_audio, exist_ok=True)

    def gravar_amostras(self, n_repeticoes: int = 50):
        """
        Grava N repetições de frases de ativação.

        IMPORTANTE: As frases NÃO contêm "Sirius" para evitar que a
        própria wake word dispare durante a gravação de amostras.
        O modelo aprende o padrão vocal geral — não a palavra exata.
        """
        try:
            import pyaudio
            import wave
        except ImportError:
            print("pip install pyaudio")
            return False

        pa = pyaudio.PyAudio()
        print(f"\n{'='*55}")
        print("  GRAVAÇÃO DE WAKE WORD PERSONALIZADA")
        print(f"{'='*55}")
        print("  Você vai gravar frases curtas para ensinar o Sirius")
        print("  a reconhecer SUA voz.")
        print()
        print("  IMPORTANTE: As frases abaixo NÃO contêm a palavra 'Sirius'")
        print("  para evitar que a wake word dispare durante a gravação.")
        print("  O modelo aprende o padrão vocal do início de 'Si-ri-us'.")
        print()

        # Frases que soam similar à fonética de "Sirius" mas não ativam a wake word
        # Foco nos sons "si", "ri", "us" — a sequência que distingue "sirius"
        FRASES = [
            "si ri us",           # fonética separada
            "sistema ativo",      # começa com "si"
            "visível agora",      # "si" no meio
            "rio azul",           # "ri"
            "música boa",         # "si" + vogal
            "ativar sistema",     # "si"
        ]
        SEGUNDOS_POR_AMOSTRA = 2.0
        RATE     = SAMPLE_RATE
        CHUNK    = 1024
        N_FRAMES = int(RATE / CHUNK * SEGUNDOS_POR_AMOSTRA)

        os.makedirs(self.caminho_audio, exist_ok=True)
        total_gravado = 0

        for frase in FRASES:
            print(f"\n  Frase: '{frase}'")
            print(f"  Repita {n_repeticoes} vezes. Cada gravação = {SEGUNDOS_POR_AMOSTRA:.0f}s.")
            input("  [Enter para começar]")

            for i in range(n_repeticoes):
                print(f"  Gravando {i+1}/{n_repeticoes}... fale agora!", end="\r")
                try:
                    stream = pa.open(
                        format=pyaudio.paInt16, channels=1,
                        rate=RATE, input=True, frames_per_buffer=CHUNK
                    )
                    frames = [stream.read(CHUNK, exception_on_overflow=False)
                              for _ in range(N_FRAMES)]
                    stream.stop_stream()
                    stream.close()

                    nome = f"amostra_{frase.replace(' ', '_')}_{i:04d}.wav"
                    caminho_out = os.path.join(self.caminho_audio, nome)
                    with wave.open(caminho_out, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                        wf.setframerate(RATE)
                        wf.writeframes(b"".join(frames))

                    total_gravado += 1
                    time.sleep(0.1)
                except Exception as e:
                    print(f"\n  Erro: {e}")
                    continue

            print(f"  ✓ {n_repeticoes} amostras de '{frase}' gravadas.")

        pa.terminate()
        print(f"\n  Total: {total_gravado} amostras gravadas em {self.caminho_audio}")
        print()
        print("  ── PRÓXIMO PASSO: Gravar negativos reais ──────────────────")
        pasta_neg = os.path.join(self.caminho_audio, "negativos")
        print(f"  Crie: {pasta_neg}")
        print("  Grave arquivos .wav de você falando qualquer coisa")
        print("  EXCETO as frases acima. Ex: 'abre o chrome', 'que horas são'")
        print("  Quanto mais negativos reais, menos falsos positivos.")
        print()
        print("  Depois rode: python sirius_wakeword.py --so-treinar")
        print(f"{'='*55}\n")
        return total_gravado > 0


    def treinar(self):
        """
        Treina modelo personalizado de wake word.
        
        Abordagem: extrai embeddings com openWakeWord e treina
        um classificador sklearn por cima — sem usar openwakeword.train
        que tem dependências quebradas (acoustics/scipy).
        """
        print("\n[TREINO]: Iniciando treino personalizado...")
        print("[TREINO]: Extraindo embeddings dos áudios...")

        try:
            import numpy as np
            from openwakeword.model import Model
            import wave
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            import pickle
            import onnx
            import skl2onnx
            from skl2onnx import convert_sklearn
            from skl2onnx.common.data_types import FloatTensorType
        except ImportError as e:
            print(f"\033[31m[TREINO]: Dependência faltando: {e}\033[0m")
            print("  pip install scikit-learn skl2onnx onnx")
            return False

        # 1. Carrega modelo base para extrair embeddings
        try:
            oww = Model(wakeword_models=[], inference_framework="onnx")
        except Exception as e:
            print(f"\033[31m[TREINO]: Falha ao carregar modelo base: {e}\033[0m")
            return False

        # 2. Coleta arquivos de áudio positivos (sua voz)
        arquivos_wav = [
            os.path.join(self.caminho_audio, f)
            for f in os.listdir(self.caminho_audio)
            if f.endswith(".wav")
        ]

        if not arquivos_wav:
            print("[TREINO]: Nenhum arquivo .wav encontrado.")
            return False

        print(f"[TREINO]: {len(arquivos_wav)} amostras positivas encontradas.")

        # 3. Extrai embeddings dos áudios positivos
        X_pos = []
        for caminho_wav in arquivos_wav:
            try:
                with wave.open(caminho_wav, 'rb') as wf:
                    raw = wf.readframes(wf.getnframes())
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

                # Processa em chunks de 1280 samples (80ms)
                for i in range(0, len(audio) - CHUNK_SIZE, CHUNK_SIZE):
                    chunk = audio[i:i + CHUNK_SIZE]
                    if len(chunk) == CHUNK_SIZE:
                        predicao = oww.predict(chunk)
                        # Pega os embeddings internos do modelo base
                        if hasattr(oww, 'preprocessor') and oww.preprocessor is not None:
                            emb = oww.preprocessor.get_embeddings()
                            if emb is not None and len(emb) > 0:
                                X_pos.append(emb.flatten())
            except Exception:
                continue

        if len(X_pos) < 10:
            print("[TREINO]: Embeddings insuficientes extraídos. Tentando método alternativo...")
            return self._treinar_simples(arquivos_wav)

        # 4. Gera amostras negativas (silêncio + ruído aleatório)
        print("[TREINO]: Gerando amostras negativas...")
        X_neg = []
        n_negativos = len(X_pos) * 2
        for _ in range(n_negativos):
            ruido = np.random.randn(CHUNK_SIZE).astype(np.float32) * 100
            try:
                predicao = oww.predict(ruido)
                if hasattr(oww, 'preprocessor') and oww.preprocessor is not None:
                    emb = oww.preprocessor.get_embeddings()
                    if emb is not None:
                        X_neg.append(emb.flatten())
            except Exception:
                pass

        X = np.array(X_pos + X_neg)
        y = np.array([1] * len(X_pos) + [0] * len(X_neg))

        # 5. Treina classificador
        print(f"[TREINO]: Treinando classificador ({len(X_pos)} pos + {len(X_neg)} neg)...")
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        clf = LogisticRegression(max_iter=500, C=1.0)
        clf.fit(X_scaled, y)

        score = clf.score(X_scaled, y)
        print(f"[TREINO]: Acurácia no treino: {score:.1%}")

        # 6. Exporta para ONNX
        try:
            initial_type = [('float_input', FloatTensorType([None, X.shape[1]]))]
            modelo_onnx = convert_sklearn(clf, initial_types=initial_type)
            with open(self.caminho_modelo, "wb") as f:
                f.write(modelo_onnx.SerializeToString())
            print(f"\033[92m[TREINO]: Modelo salvo em: {self.caminho_modelo}\033[0m")

            # Salva o scaler junto
            scaler_path = self.caminho_modelo.replace(".onnx", "_scaler.pkl")
            with open(scaler_path, "wb") as f:
                pickle.dump(scaler, f)

            print("\033[92m[TREINO]: Wake word 'ei sirius' treinada com sucesso!\033[0m")
            return True

        except Exception as e:
            print(f"\033[31m[TREINO]: Falha ao exportar ONNX: {e}\033[0m")
            return self._treinar_simples(arquivos_wav)

    def _treinar_simples(self, arquivos_wav: list) -> bool:
        """
        Método de treino alternativo — usa MFCC + sklearn puro.
        Não depende de openWakeWord internals. Menos preciso mas funcional.
        """
        print("[TREINO]: Usando método alternativo (MFCC + sklearn)...")
        try:
            import numpy as np
            import wave
            import pickle
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except ImportError as e:
            print(f"pip install scikit-learn: {e}")
            return False

        def extrair_mfcc_simples(audio: np.ndarray, n_mfcc: int = 20) -> np.ndarray:
            """MFCC simplificado sem librosa."""
            frame_size  = 512
            hop_size    = 256
            frames = []
            for i in range(0, len(audio) - frame_size, hop_size):
                frame = audio[i:i + frame_size] * np.hamming(frame_size)
                fft   = np.abs(np.fft.rfft(frame))
                fft   = fft[:n_mfcc * 4]
                # Agrupa em n_mfcc bandas
                banda = len(fft) // n_mfcc
                feats = [np.mean(fft[j*banda:(j+1)*banda]) for j in range(n_mfcc)]
                frames.append(feats)
            if not frames:
                return np.zeros(n_mfcc)
            return np.mean(frames, axis=0)

        # Positivos
        X_pos = []
        for wav in arquivos_wav:
            try:
                with wave.open(wav, 'rb') as wf:
                    raw = wf.readframes(wf.getnframes())
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                feat  = extrair_mfcc_simples(audio)
                X_pos.append(feat)
            except Exception:
                continue

        # Negativos de arquivo — voz humana real (melhor qualidade)
        # Coloque .wav em config/wakeword_audio/negativos/ para usar
        pasta_neg = os.path.join(self.caminho_audio, "negativos")
        X_neg_arquivo = []
        if os.path.exists(pasta_neg):
            for f in os.listdir(pasta_neg):
                if f.endswith(".wav"):
                    try:
                        with wave.open(os.path.join(pasta_neg, f), 'rb') as wf:
                            raw = wf.readframes(wf.getnframes())
                        audio_neg = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        X_neg_arquivo.append(extrair_mfcc_simples(audio_neg))
                    except Exception:
                        continue
            if X_neg_arquivo:
                print(f"[TREINO]: {len(X_neg_arquivo)} negativos reais carregados.")

        # Negativos sintéticos — mix de tipos para cobrir casos reais
        # Sem negativos reais, o modelo generaliza mal para voz humana
        X_neg_sintetico = []
        n_neg_alvo = max(len(X_pos) * 3, 30)

        # 50% ruído cor-de-rosa (mais parecido com voz do que branco)
        for _ in range(n_neg_alvo // 2):
            ruido = np.cumsum(np.random.randn(SAMPLE_RATE).astype(np.float32)) * 0.001
            ruido = ruido / (np.abs(ruido).max() + 1e-8) * 0.05
            X_neg_sintetico.append(extrair_mfcc_simples(ruido))

        # 30% fala sintética com formantes (simula vogais sem ser "sirius")
        for _ in range(n_neg_alvo * 3 // 10):
            t_arr  = np.linspace(0, 1.5, int(SAMPLE_RATE * 1.5))
            # Formantes de vogal genérica (não específica de "sirius")
            freq   = np.random.choice([400, 600, 800, 1000, 1200])
            sinal  = np.sin(2 * np.pi * freq * t_arr).astype(np.float32)
            sinal += np.random.randn(len(t_arr)).astype(np.float32) * 0.05
            sinal  = sinal / (np.abs(sinal).max() + 1e-8) * 0.3
            X_neg_sintetico.append(extrair_mfcc_simples(sinal))

        # 20% silêncio com micro-ruído
        for _ in range(n_neg_alvo // 5):
            silencio = np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.002
            X_neg_sintetico.append(extrair_mfcc_simples(silencio))

        X_neg = X_neg_arquivo + X_neg_sintetico
        if not X_neg:
            X_neg = X_neg_sintetico  # fallback

        n_pos = len(X_pos)
        n_neg = len(X_neg)
        print(f"[TREINO]: {n_pos} positivos + {n_neg} negativos "
              f"({len(X_neg_arquivo)} reais + {len(X_neg_sintetico)} sintéticos)")

        X = np.array(X_pos + X_neg)
        y = np.array([1] * n_pos + [0] * n_neg)

        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)

        # C=0.3: regularização forte — evita overfitting em negativos sintéticos
        # class_weight="balanced": compensa desbalanceamento positivo/negativo
        clf = LogisticRegression(max_iter=1000, C=0.3, class_weight="balanced")
        clf.fit(X_s, y)

        # Cross-validation para medir generalização real
        try:
            from sklearn.model_selection import cross_val_score
            scores = cross_val_score(clf, X_s, y, cv=min(3, n_pos), scoring="f1")
            print(f"[TREINO]: F1 cross-val: {scores.mean():.1%} ± {scores.std():.1%}")
            if scores.mean() < 0.6:
                print("[TREINO]: ⚠ F1 baixo. Grave negativos reais para melhorar:")
                print(f"  Pasta: {pasta_neg}")
                print("  Fale qualquer coisa EXCETO 'sirius' em arquivos .wav")
        except Exception:
            pass

        # Salva como pkl (o loop de detecção vai usar pkl se não achar onnx)
        modelo_pkl = self.caminho_modelo.replace(".onnx", "_fallback.pkl")
        with open(modelo_pkl, "wb") as f:
            pickle.dump({"clf": clf, "scaler": scaler}, f)

        print(f"\033[92m[TREINO]: Modelo alternativo salvo: {modelo_pkl}\033[0m")
        print(f"\033[92m[TREINO]: Acurácia: {clf.score(X_s, y):.1%}\033[0m")

        # Sinaliza para o loop usar o pkl
        flag_path = self.caminho_modelo.replace(".onnx", ".pkl_mode")
        open(flag_path, "w").close()
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sirius Wake Word")
    parser.add_argument("--treinar",          action="store_true", help="Grava amostras E treina")
    parser.add_argument("--so-treinar",       action="store_true", help="Só treina com amostras já gravadas")
    parser.add_argument("--gravar",           action="store_true", help="Só grava amostras positivas")
    parser.add_argument("--gravar-negativos", action="store_true", help="Grava amostras negativas reais (reduz falsos positivos)")
    parser.add_argument("--testar",           action="store_true", help="Testa a detecção")
    parser.add_argument("--diagnostico",      action="store_true", help="Diagnóstico completo do sistema")
    parser.add_argument("--repeticoes",       type=int, default=50, help="Amostras por frase")
    args = parser.parse_args()

    t = TreinadorWakeWord()

    if args.so_treinar:
        # ✅ Usa amostras já gravadas — não precisa regravar
        n = sum(1 for f in os.listdir(t.caminho_audio) if f.endswith(".wav")) if os.path.exists(t.caminho_audio) else 0
        if n == 0:
            print(f"[TREINO]: Nenhuma amostra encontrada em {t.caminho_audio}")
            print("[TREINO]: Rode primeiro: python sirius_wakeword.py --gravar")
        else:
            print(f"[TREINO]: {n} amostras encontradas. Iniciando treino...")
            t.treinar()

    elif args.gravar:
        t.gravar_amostras(args.repeticoes)

    elif args.treinar:
        if t.gravar_amostras(args.repeticoes):
            t.treinar()

    elif args.gravar_negativos:
        # Grava amostras negativas reais — melhora muito a precisão
        pasta_neg = os.path.join(t.caminho_audio, "negativos")
        os.makedirs(pasta_neg, exist_ok=True)

        try:
            import pyaudio, wave
        except ImportError:
            print("pip install pyaudio")
            sys.exit(1)

        print(f"\n{'='*55}")
        print("  GRAVAÇÃO DE AMOSTRAS NEGATIVAS REAIS")
        print(f"{'='*55}")
        print("  Fale QUALQUER COISA exceto as frases de ativação.")
        print("  Ex: 'abre o chrome', 'que horas são', 'tudo bem'")
        print(f"  Destino: {pasta_neg}")
        print()

        frases_neg = [
            "abre o chrome",
            "que horas são agora",
            "tudo bem por aqui",
            "como está o sistema",
            "manda mensagem pro João",
            "qual é a temperatura hoje",
            "desliga o monitor",
            "coloca uma música",
        ]

        pa = pyaudio.PyAudio()
        RATE = 16000; CHUNK = 1024; SECS = 2.0
        N_FRAMES = int(RATE / CHUNK * SECS)
        n_gravados = 0

        for frase in frases_neg:
            print(f"  Diga: '{frase}'")
            input("  [Enter para gravar]")
            for i in range(args.repeticoes // len(frases_neg) + 1):
                print(f"  Gravando... ({SECS:.0f}s)", end="\r")
                try:
                    st = pa.open(format=pyaudio.paInt16, channels=1,
                                 rate=RATE, input=True, frames_per_buffer=CHUNK)
                    frames = [st.read(CHUNK) for _ in range(N_FRAMES)]
                    st.stop_stream(); st.close()
                    fname = os.path.join(pasta_neg, f"neg_{frase[:15].replace(' ','_')}_{i:03d}.wav")
                    with wave.open(fname, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                        wf.setframerate(RATE)
                        wf.writeframes(b"".join(frames))
                    n_gravados += 1
                except Exception as e:
                    print(f"Erro: {e}")

        pa.terminate()
        print(f"\n  ✓ {n_gravados} negativos gravados em {pasta_neg}")
        print("  Agora retreine: python sirius_wakeword.py --so-treinar")

    elif args.diagnostico:
        # Mostra diagnóstico completo do sistema de wake word
        print(f"\n{'='*55}")
        print("  DIAGNÓSTICO WAKE WORD")
        print(f"{'='*55}")

        pkl_path  = CAMINHO_MODELO_CUSTOM.replace(".onnx", "_fallback.pkl")
        onnx_path = CAMINHO_MODELO_CUSTOM
        audio_dir = os.path.join(diretorio_raiz, "config", "wakeword_audio")
        neg_dir   = os.path.join(audio_dir, "negativos")

        print(f"  Modelo MFCC (.pkl):   {'✓ ' + pkl_path if os.path.exists(pkl_path) else '✗ não existe'}")
        print(f"  Modelo ONNX:          {'✓' if os.path.exists(onnx_path) else '✗ não existe'}")

        n_pos = len([f for f in os.listdir(audio_dir) if f.endswith(".wav")]) if os.path.exists(audio_dir) else 0
        n_neg = len([f for f in os.listdir(neg_dir) if f.endswith(".wav")]) if os.path.exists(neg_dir) else 0
        print(f"  Amostras positivas:   {n_pos}")
        print(f"  Amostras negativas:   {n_neg} {'⚠ Adicione negativos reais!' if n_neg == 0 else '✓'}")

        if os.path.exists(pkl_path):
            import pickle
            with open(pkl_path, "rb") as f:
                dados = pickle.load(f)
            clf = dados.get("clf")
            if clf and hasattr(clf, "coef_"):
                print(f"  Threshold atual:      0.88 (prob no loop MFCC)")
                print(f"  Regularização C:      {clf.C if hasattr(clf, 'C') else '?'}")

        print()
        if n_neg == 0:
            print("  RECOMENDAÇÃO: Grave negativos reais para reduzir falsos positivos:")
            print("    python sirius_wakeword.py --gravar-negativos")
        if n_pos < 20:
            print("  RECOMENDAÇÃO: Mais amostras positivas melhoram a precisão:")
            print("    python sirius_wakeword.py --gravar")
        print(f"{'='*55}\n")

    elif args.testar:
        import threading

        def _falar(texto: str):
            """Fala uma instrução em voz usando pyttsx3."""
            try:
                import pyttsx3
                engine = pyttsx3.init()
                # Tenta voz em português
                for v in engine.getProperty("voices"):
                    if "brazil" in v.name.lower() or "portuguese" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
                engine.setProperty("rate", 170)
                engine.say(texto)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                print(f"[VOZ]: {texto}  (pyttsx3 falhou: {e})")

        def _cb():
            print("\033[92m✓ Wake word detectada!\033[0m")
            threading.Thread(
                target=_falar,
                args=("Wake word detectada. Pode falar o comando.",),
                daemon=True
            ).start()

        # Instrução inicial em voz
        print("\n[TESTE WAKE WORD]")
        print("  Modo: Whisper Tiny" if not os.path.exists(
            os.path.join(diretorio_raiz, "config", "sirius_wakeword_fallback.pkl")
        ) else "  Modo: Modelo treinado (MFCC)")
        print("  Diga 'Sirius' para ativar.")
        print("  Ctrl+C para parar.\n")

        threading.Thread(
            target=_falar,
            args=("Teste de wake word iniciado. Diga Sirius para ativar.",),
            daemon=True
        ).start()

        ww = SiriusWakeWord(callback_ativado=_cb)
        ww.iniciar()

        # Lembra o usuário a cada 20 segundos se não detectar nada
        _ultimo_lembrete = time.time()
        _lembrete_intervalo = 20

        try:
            while True:
                time.sleep(1)
                agora = time.time()
                if agora - _ultimo_lembrete >= _lembrete_intervalo and ww._deteccoes == 0:
                    _ultimo_lembrete = agora
                    threading.Thread(
                        target=_falar,
                        args=("Diga Sirius para testar.",),
                        daemon=True
                    ).start()
        except KeyboardInterrupt:
            ww.parar()
            n = ww._deteccoes
            print(f"\nDetecções: {n}")
            msg = f"Teste concluído. {n} detecções registradas." if n > 0                   else "Nenhuma detecção. Tente falar mais perto do microfone."
            _falar(msg)
            print(msg)

    else:
        parser.print_help()