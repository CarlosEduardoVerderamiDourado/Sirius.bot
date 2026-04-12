import sys
import os
import re
import time
import threading
import tempfile
import requests
import pygame
import winsound
import numpy as np
from faster_whisper import WhisperModel

# --- LÓGICA DE CAMINHO ---
caminho_src  = os.path.dirname(os.path.abspath(__file__))
raiz_projeto = os.path.dirname(caminho_src)
if raiz_projeto not in sys.path:
    sys.path.append(raiz_projeto)

try:
    from config.config import ELEVENLABS_API_KEY, VOICE_ID
    print("\033[92m[Sucesso]: Configurações importadas!\033[0m")
except Exception as e:
    print(f"\033[31m[Erro]: Falha ao importar config: {e}\033[0m")
    ELEVENLABS_API_KEY = None
    VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ"

# Variantes fonéticas da wake word
WAKE_WORD_VARIANTES = {
    "fírios", "fírius", "fídeos", "sírius", "fírio",
    "serios", "seídios", "sídios", "sirius"
}


class SiriusAudio:
    def __init__(self):
        self.api_key  = ELEVENLABS_API_KEY
        self.voice_id = VOICE_ID

        print("\033[93m[SIRIUS]: Carregando ouvidos (Faster-Whisper em CPU)...\033[0m")
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        # ✅ Flag de mute — bloqueia escuta enquanto Sirius fala
        self._falando       = False
        self._lock_tts      = threading.Lock()  # evita pyttsx3 em paralelo

        self._voice_id_windows = None
        self._configurar_voz_local()

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def _configurar_voz_local(self):
        import pyttsx3
        try:
            engine = pyttsx3.init()
            for v in engine.getProperty('voices'):
                if "Brazil" in v.name or "Portuguese" in v.name:
                    self._voice_id_windows = v.id
                    break
            engine.stop()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Limpeza de texto
    # -----------------------------------------------------------------------

    def limpar_texto(self, texto) -> str:
        if isinstance(texto, list) and texto:
            texto = texto[0]
        if isinstance(texto, dict):
            texto = texto.get('text', str(texto))

        texto_final = str(texto)

        for marcador in ["extras':", "signature':", "'type':"]:
            if marcador in texto_final:
                texto_final = texto_final.split(marcador)[0]

        texto_final = re.sub(r'[\*\#\`\_]', '', texto_final)

        for sujeira in ["[{", "}]", "{'text': '", '{"text": "', '"}', "'}", '["', '"]']:
            texto_final = texto_final.replace(sujeira, "")

        return texto_final.strip()

    # -----------------------------------------------------------------------
    # TTS — ElevenLabs com fallback para pyttsx3
    # -----------------------------------------------------------------------

    def falar(self, texto: str):
        texto_limpo = self.limpar_texto(texto)
        if not texto_limpo or len(texto_limpo) < 2:
            return

        print(f"\033[92m[SIRIUS]:\033[0m {texto_limpo}")

        # ✅ Ativa mute do microfone antes de falar
        self._falando = True
        try:
            if not self._falar_elevenlabs(texto_limpo):
                self._falar_windows(texto_limpo)
        finally:
            # ✅ Sempre desativa mute ao terminar, mesmo se der erro
            self._falando = False

    def _falar_elevenlabs(self, texto: str) -> bool:
        if not self.api_key or len(self.api_key) <= 5:
            return False
        try:
            url     = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
            headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
            payload = {
                "text": texto,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
            }
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code != 200:
                return False

            fd, temp_path = tempfile.mkstemp(suffix=".mp3")
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)

            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
            pygame.mixer.music.unload()
            os.remove(temp_path)
            return True

        except Exception as e:
            print(f"\033[33m[TTS ELEVENLABS]: Falha — {e}\033[0m")
            return False

    def _falar_windows(self, texto: str):
        # ✅ Lock garante que só uma thread usa pyttsx3 por vez
        with self._lock_tts:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                if self._voice_id_windows:
                    engine.setProperty('voice', self._voice_id_windows)
                engine.setProperty('rate', 180)
                engine.say(texto)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                print(f"\033[33m[TTS LOCAL]: Falha — {e}\033[0m")

    # -----------------------------------------------------------------------
    # STT — Faster-Whisper
    # -----------------------------------------------------------------------

    def _normalizar_wake_word(self, texto: str) -> tuple[str, bool]:
        tinha_wake_word = False
        for variante in WAKE_WORD_VARIANTES:
            if variante in texto:
                tinha_wake_word = True
                texto = re.sub(rf'\b{re.escape(variante)}\b', 'sirius', texto)
        return texto, tinha_wake_word

    def escutar_fluxo_continuo(self) -> str | None:
        # ✅ Não escuta enquanto o Sirius estiver falando
        if self._falando:
            time.sleep(0.1)
            return None

        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300

        try:
            microfone = sr.Microphone()
            with microfone as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)

                # ✅ Verifica novamente após calibração (fala pode ter começado)
                if self._falando:
                    return None

                try:
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=8)
                except sr.WaitTimeoutError:
                    return None

            # ✅ Descarta áudio capturado se o Sirius começou a falar durante a escuta
            if self._falando:
                return None

            wav_data = audio.get_wav_data()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_data)
                tmp_path = f.name

            try:
                segments, _ = self.model.transcribe(tmp_path, language="pt")
                texto = "".join(s.text for s in segments).lower().strip()
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if not texto:
                return None

            print(f"\033[90m[DEBUG AUDIO]: '{texto}'\033[0m")

            texto, tinha_wake_word = self._normalizar_wake_word(texto)
            if tinha_wake_word:
                winsound.Beep(1000, 150)

            return texto

        except (OSError, AttributeError):
            print("\033[33m[SISTEMA]: Microfone não detectado ou ocupado. Retentando...\033[0m")
            time.sleep(1)
            return None
        except Exception as e:
            if "NoneType" not in str(e):
                print(f"\033[31m[ERRO INTERNO AUDIO]: {e}\033[0m")
            return None