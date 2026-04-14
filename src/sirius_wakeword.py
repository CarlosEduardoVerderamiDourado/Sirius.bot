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
# Variantes fonéticas REAIS de "sirius" para o Whisper fallback.
# IMPORTANTE: nao coloque palavras comuns aqui (ex: "filhos", "seguiu")
# pois o match é feito por substring e causam falsos positivos.
# Só inclua variantes que soam MUITO parecidas com "sirius".
WAKE_WORDS_SIRIUS = {
    # Corretas
    "sirius", "sírius", "siriuz",
    # Variantes fonéticas confirmadas do Whisper Tiny
    "serios", "sérios", "cedios", "fidios",
    "fírios", "fibios", "firius", "fírius",
    "sídios", "seídios",
    # Com prefixos — palavras compostas que incluem "sirius"
    "ei sirius", "oi sirius", "hey sirius",
    "ei serios", "oi serios",
}

# Palavras removidas propositalmente para evitar falsos positivos:
#   "serious"  → ativa em "estou seriamente cansado"
#   "filhos"   → ativa em "tenho dois filhos"
#   "seguiu"   → ativa em "ele seguiu em frente"
#   "see you"  → ativa em frases em inglês comuns
#   "jarvis"   → muito genérico, ativa em conteúdo de filmes/séries
#   "hey jarvis" → idem

# Sensibilidade: 0.0 (muitos falsos positivos) a 1.0 (muito restrito)
# 0.5 é um bom equilíbrio
SENSIBILIDADE = 0.5

# Taxa de amostragem exigida pelo openWakeWord
SAMPLE_RATE   = 16000
CHUNK_SIZE    = 1280  # 80ms de áudio por frame

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
        SEGUNDOS  = 1.5
        N_FRAMES  = int(RATE / CHUNK * SEGUNDOS)
        THRESHOLD = 300

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

                # Threshold alto (0.92) para evitar falsos positivos.
                # O modelo MFCC foi treinado com fala sintetica como negativo —
                # isso causa falsos positivos com voz humana real.
                # Solucao definitiva: grave negativos reais (veja --so-treinar).
                if pred == 1 and prob > 0.92:
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

                    # Verifica palavra INTEIRA — evita falsos positivos por substring
                    def _contem_wake_word(t, wake_words):
                        import re as _re
                        for ww in wake_words:
                            if " " in ww:
                                if ww in t:
                                    return True, ww
                            else:
                                if _re.search(r"\b" + _re.escape(ww) + r"\b", t):
                                    return True, ww
                        return False, None

                    detectou, palavra = _contem_wake_word(texto, WAKE_WORDS_SIRIUS)
                    if detectou:
                        agora = time.time()
                        if agora - self._ultima_deteccao >= self._cooldown:
                            self._ultima_deteccao = agora
                            self._deteccoes      += 1
                            print(f"\033[92m[WAKEWORD]: 'Sirius' detectado via '{palavra}'! ({self._deteccoes}x)\033[0m")
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
        """Grava N repetições de cada frase de ativação."""
        try:
            import pyaudio
            import wave
        except ImportError:
            print("pip install pyaudio")
            return False

        pa = pyaudio.PyAudio()
        print(f"\n{'='*50}")
        print("  GRAVAÇÃO DE WAKE WORD PERSONALIZADA")
        print(f"{'='*50}")
        print(f"  Vamos gravar {n_repeticoes} repetições de cada frase.")
        print(f"  Fale naturalmente, em volume normal.\n")

        total_gravados = 0
        for frase in self.FRASES:
            print(f"\n  Frase: '{frase}'")
            for i in range(n_repeticoes):
                input(f"    [{i+1}/{n_repeticoes}] Pressione Enter e diga '{frase}'... ")

                stream = pa.open(
                    format=pyaudio.paInt16, channels=1,
                    rate=SAMPLE_RATE, input=True,
                    frames_per_buffer=CHUNK_SIZE
                )

                frames = []
                # Grava 1.5 segundos
                for _ in range(int(SAMPLE_RATE / CHUNK_SIZE * 1.5)):
                    frames.append(stream.read(CHUNK_SIZE, exception_on_overflow=False))
                stream.stop_stream()
                stream.close()

                nome_arquivo = os.path.join(
                    self.caminho_audio,
                    f"{frase.replace(' ', '_')}_{i:03d}.wav"
                )
                with wave.open(nome_arquivo, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(b"".join(frames))

                total_gravados += 1
                print(f"    ✓ Gravado")

        pa.terminate()
        print(f"\n  {total_gravados} amostras gravadas em {self.caminho_audio}")
        return True

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
        Treino MFCC + sklearn com amostras negativas REAIS de fala.

        O problema do treino anterior: negativos eram ruído puro (0.01 amplitude).
        Qualquer fala real tem muito mais energia → sempre classificava como positivo.

        Solução: gerar negativos com características de fala real (energia, variação
        espectral) ou gravar o usuário dizendo palavras aleatórias.
        """
        print("[TREINO]: Usando método MFCC + sklearn com negativos realistas...")
        try:
            import numpy as np
            import wave
            import pickle
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import cross_val_score
        except ImportError as e:
            print(f"pip install scikit-learn: {e}")
            return False

        def extrair_mfcc_simples(audio: np.ndarray, n_mfcc: int = 20) -> np.ndarray:
            """MFCC simplificado sem librosa."""
            frame_size = 512
            hop_size   = 256
            frames = []
            for i in range(0, len(audio) - frame_size, hop_size):
                frame = audio[i:i + frame_size] * np.hamming(frame_size)
                fft   = np.abs(np.fft.rfft(frame))
                fft   = fft[:n_mfcc * 4]
                banda = max(1, len(fft) // n_mfcc)
                feats = [np.mean(fft[j*banda:(j+1)*banda]) for j in range(n_mfcc)]
                frames.append(feats)
            if not frames:
                return np.zeros(n_mfcc)
            return np.mean(frames, axis=0)

        def gerar_fala_sintetica(duracao_s: float = 1.5) -> np.ndarray:
            """
            Gera áudio sintético com características de fala humana.
            Muito melhor que ruído branco como negativo.
            """
            n = int(SAMPLE_RATE * duracao_s)
            # Formantes típicos de vogais (Hz)
            formantes = [
                (800,  150),   # F1 vogal aberta
                (1200, 200),   # F2
                (2500, 300),   # F3
                (350,  100),   # vogal fechada
                (1800, 250),
                (2800, 350),
            ]
            t   = np.linspace(0, duracao_s, n)
            sig = np.zeros(n, dtype=np.float32)

            # Escolhe 3 índices aleatórios da lista de formantes
            indices_aleatorios = np.random.choice(len(formantes), size=3, replace=False)
            
            for idx in indices_aleatorios:
                f, b = formantes[idx]  # Acessa a frequência e banda usando o índice
                
                # Oscilação com envelope de amplitude (simula sílaba)
                envelope = np.exp(-np.random.uniform(0.5, 3.0) * t)
                envelope *= np.random.uniform(0.3, 1.0)
                sig += envelope * np.sin(2 * np.pi * f * t + np.random.uniform(0, 2*np.pi)).astype(np.float32)

            # Adiciona ruído de fundo leve
            sig += np.random.randn(n).astype(np.float32) * 0.05

            # Normaliza para amplitude similar à fala real
            sig = sig / (np.abs(sig).max() + 1e-8) * np.random.uniform(0.3, 0.8)
            return sig

        def gerar_negativo_transiente(duracao_s: float = 1.5) -> np.ndarray:
            """Gera transiente (porta batendo, teclado, etc.) como negativo."""
            n   = int(SAMPLE_RATE * duracao_s)
            sig = np.zeros(n, dtype=np.float32)
            # Alguns picos aleatórios
            for _ in range(np.random.randint(1, 5)):
                pos = np.random.randint(0, n // 2)
                dur = np.random.randint(100, 2000)
                amp = np.random.uniform(0.1, 0.6)
                sig[pos:pos+dur] += amp * np.exp(-np.linspace(0, 5, dur)) * np.random.randn(dur).astype(np.float32)
            return sig

        # ── Positivos: suas gravações de "ei sirius"
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

        if len(X_pos) < 5:
            print("[TREINO]: Amostras positivas insuficientes.")
            return False

        n_pos = len(X_pos)
        print(f"[TREINO]: {n_pos} amostras positivas extraídas.")

        # Verifica se ha negativos reais — melhora muito a precisao
        pasta_neg = os.path.join(self.caminho_audio, "negativos")
        n_neg_reais = 0
        if os.path.exists(pasta_neg):
            n_neg_reais = sum(1 for f in os.listdir(pasta_neg) if f.endswith(".wav"))
        if n_neg_reais == 0:
            print()
            print("\033[93m[TREINO]: DICA IMPORTANTE para evitar falsos positivos:")
            print(f"  Crie a pasta: {pasta_neg}")
            print("  Grave voce falando qualquer coisa MENOS 'sirius'")
            print("  Ex: 'abre o chrome', 'tudo bem', 'que horas sao', 'boa tarde'")
            print("  Formato: .wav, 16000Hz, mono, ~2-3s cada")
            print("  Quanto mais negativos reais, menos falsos positivos!")
            print("  Retreine depois: python sirius_wakeword.py --so-treinar\033[0m")
            print()
        else:
            print(f"[TREINO]: {n_neg_reais} negativos reais encontrados.")

        # ── Negativos: mix de fala sintética + transientes + silêncio
        X_neg = []

        # 60% fala sintética (o maior problema era esse)
        n_fala = int(n_pos * 3 * 0.6)
        for _ in range(n_fala):
            audio = gerar_fala_sintetica()
            X_neg.append(extrair_mfcc_simples(audio))

        # 25% transientes
        n_trans = int(n_pos * 3 * 0.25)
        for _ in range(n_trans):
            audio = gerar_negativo_transiente()
            X_neg.append(extrair_mfcc_simples(audio))

        # 15% silêncio / ruído muito baixo
        n_sil = int(n_pos * 3 * 0.15)
        for _ in range(n_sil):
            audio = np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.005
            X_neg.append(extrair_mfcc_simples(audio))

        # Negativos de arquivo se existirem (pasta negativos/)
        pasta_neg = os.path.join(self.caminho_audio, "negativos")
        if os.path.exists(pasta_neg):
            for f in os.listdir(pasta_neg):
                if f.endswith(".wav"):
                    try:
                        with wave.open(os.path.join(pasta_neg, f), 'rb') as wf:
                            raw = wf.readframes(wf.getnframes())
                        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        X_neg.append(extrair_mfcc_simples(audio))
                    except Exception:
                        continue

        print(f"[TREINO]: {len(X_neg)} amostras negativas geradas ({n_fala} fala sintética, {n_trans} transientes, {n_sil} silêncio).")

        X = np.array(X_pos + X_neg)
        y = np.array([1] * len(X_pos) + [0] * len(X_neg))

        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X)

        # Treina com regularização forte (C baixo) para evitar overfitting
        # C=0.3 — regularizacao forte para evitar overfitting em negativos sinteticos.
        # Quanto menor o C, mais conservador o modelo (menos falsos positivos).
        # Se ainda tiver falsos positivos, grave negativos reais:
        #   pasta: config/wakeword_audio/negativos/
        #   coloque .wav de voce falando qualquer coisa menos "sirius"
        clf = LogisticRegression(max_iter=1000, C=0.3, class_weight="balanced")
        clf.fit(X_s, y)

        # Cross-validation para ver se generaliza
        try:
            scores = cross_val_score(clf, X_s, y, cv=3, scoring="f1")
            print(f"[TREINO]: F1 (cross-val): {scores.mean():.1%} ± {scores.std():.1%}")
            if scores.mean() < 0.5:
                print("[TREINO]: ⚠ F1 baixo — modelo pode não generalizar bem.")
                print("[TREINO]: Dica: grave amostras negativas reais (veja abaixo).")
        except Exception:
            pass

        acuracia = clf.score(X_s, y)
        print(f"[TREINO]: Acurácia no treino: {acuracia:.1%}")

        # Salva
        modelo_pkl = self.caminho_modelo.replace(".onnx", "_fallback.pkl")
        with open(modelo_pkl, "wb") as f:
            pickle.dump({"clf": clf, "scaler": scaler}, f)

        flag_path = self.caminho_modelo.replace(".onnx", ".pkl_mode")
        open(flag_path, "w").close()

        print(f"\033[92m[TREINO]: Modelo salvo: {modelo_pkl}\033[0m")
        print()
        print("💡 Para melhorar ainda mais, grave negativos reais:")
        print(f"   Crie a pasta: {pasta_neg}")
        print("   Coloque .wav de você falando qualquer coisa MENOS 'sirius'")
        print("   (ex: 'abre o chrome', 'tudo bem', 'que horas são')")
        print("   Depois retreine: python src/sirius_wakeword.py --so-treinar")
        return True

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sirius Wake Word")
    parser.add_argument("--treinar",     action="store_true", help="Grava amostras E treina")
    parser.add_argument("--so-treinar",  action="store_true", help="Só treina com amostras já gravadas (não regrava)")
    parser.add_argument("--gravar",      action="store_true", help="Só grava as amostras")
    parser.add_argument("--testar",      action="store_true", help="Testa a detecção")
    parser.add_argument("--repeticoes",  type=int, default=50, help="Amostras por frase")
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

    elif args.testar:
        print("Testando wake word. Ctrl+C para parar.")
        def _cb():
            print("✓ Wake word detectada!")

        ww = SiriusWakeWord(callback_ativado=_cb)
        ww.iniciar()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ww.parar()
            print(f"\nDetecções: {ww._deteccoes}")

    else:
        parser.print_help()