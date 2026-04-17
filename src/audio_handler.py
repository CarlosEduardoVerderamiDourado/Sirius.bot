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

# Caminho do som de ativação estilo Jarvis
_CAMINHO_SOM_ATIVACAO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "sirius_ativacao.wav"
)


def _tocar_som_ativacao():
    """
    Toca o som de ativação do Sirius.
    Prioridade: sirius_ativacao.wav (estilo Jarvis) → winsound.Beep (fallback)
    """
    try:
        if os.path.exists(_CAMINHO_SOM_ATIVACAO):
            import pygame as _pg
            if not _pg.mixer.get_init():
                _pg.mixer.init()
            _pg.mixer.Sound(_CAMINHO_SOM_ATIVACAO).play()
            return
    except Exception:
        pass
    # Fallback — beep duplo simples
    try:
        winsound.Beep(700,  80)
        time.sleep(0.04)
        winsound.Beep(1100, 130)
    except Exception:
        pass



def _eh_transcricao_ruim(texto: str) -> bool:
    """
    Detecta transcrições que são eco, repetição ou ruído do microfone.
    Evita salvar lixo no banco e processar comandos fantasmas.
    """
    import re as _re
    from collections import Counter as _Counter

    t = texto.strip().lower()

    # Muito curto
    if len(t.split()) < 2:
        return True

    # Só pontuação ou reticências
    if _re.match(r'^[.\s…,!?]+$', t):
        return True

    palavras = t.split()

    # Mais de 50% das palavras são iguais = eco
    if len(palavras) >= 4:
        freq      = _Counter(palavras)
        mais_freq = freq.most_common(1)[0][1]
        if mais_freq / len(palavras) > 0.5:
            return True

    # Frases repetidas = eco do TTS
    sentencas = [s.strip() for s in _re.split(r'[.!?]', t) if len(s.strip()) > 3]
    if len(sentencas) >= 2:
        unicas = set(sentencas)
        if len(unicas) < len(sentencas) * 0.6:
            return True

    # Padrões de ruído
    padroes = [
        r'^(um,?\s*){3,}',
        r'^(é,?\s*){3,}',
        r'(.{4,})\1{2,}',
    ]
    for p in padroes:
        if _re.search(p, t):
            return True

    return False

class SiriusAudio:
    def __init__(self, usar_wakeword: bool = True, picovoice_key: str = None):
        self.api_key  = ELEVENLABS_API_KEY
        self.voice_id = VOICE_ID

        print("\033[93m[SIRIUS]: Carregando ouvidos (Faster-Whisper em CPU)...\033[0m")
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        # Flag de mute — bloqueia escuta enquanto Sirius fala
        self._falando       = False
        self._lock_tts      = threading.Lock()

        # Flag de wake word — quando True, o próximo escutar_fluxo_continuo
        # captura o comando mesmo sem "sirius" no texto
        self._wake_ativada  = threading.Event()
        self._wakeword      = None

        self._voice_id_windows = None
        self._configurar_voz_local()

        # Inicia wake word em background
        # So ativa se openwakeword estiver instalado (evita conflito com speech_recognition)
        if usar_wakeword:
            try:
                import openwakeword  # testa se esta instalado
                self._iniciar_wakeword(picovoice_key)
            except ImportError:
                print("\033[33m[AUDIO]: openwakeword nao instalado — wake word desabilitada.")
                print("  Para ativar: pip install openwakeword\033[0m")
                self._wakeword = None

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

    def _iniciar_wakeword(self, picovoice_key: str = None):
        """Inicializa detecção passiva de wake word."""
        try:
            from sirius_wakeword import SiriusWakeWord
            self._wakeword = SiriusWakeWord(
                callback_ativado=self._ao_detectar_wake_word
            )
            self._wakeword.iniciar()
        except Exception as e:
            print(f"\033[33m[AUDIO]: Wake word não disponível: {e}\033[0m")
            self._wakeword = None

    def _ao_detectar_wake_word(self):
        """
        Callback chamado pelo SiriusWakeWord quando detecta a wake word.
        Sinaliza ao escutar_fluxo_continuo que o próximo áudio é um comando.
        """
        if not self._falando:
            self._wake_ativada.set()
            print("\033[94m[AUDIO]: Wake word detectada — aguardando comando...\033[0m")

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

        # Cascata: ElevenLabs → Kokoro → pyttsx3
        self._falando = True
        try:
            if not self._falar_elevenlabs(texto_limpo):
                if not self._falar_kokoro(texto_limpo):
                    self._falar_windows(texto_limpo)
        finally:
            self._falando = False


    def _falar_kokoro(self, texto: str) -> bool:
        """Voz neural local Kokoro — gratuita, alta qualidade, sem internet."""
        try:
            from sirius_tts import get_tts
            tts = get_tts()
            if tts.kokoro_disponivel:
                return tts.falar(texto)
            return False
        except Exception:
            return False

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
        # Não escuta enquanto o Sirius estiver falando
        if self._falando:
            time.sleep(0.1)
            return None

        # ── MODO WAKE WORD ATIVO ─────────────────────────────────────────
        # A wake word já segura o microfone em loop próprio (pyaudio direto).
        # NÃO abrimos outro stream aqui — só capturamos depois do beep.
        if self._wakeword is not None and self._wakeword._rodando:
            if not self._wake_ativada.is_set():
                time.sleep(0.05)   # aguarda sem tocar no mic
                return None
            self._wake_ativada.clear()
            return self._capturar_comando_pos_wakeword()

        # ── SEM WAKE WORD: escuta contínua normal ────────────────────────
        return self._escutar_sem_wakeword()

    def _capturar_comando_pos_wakeword(self) -> str | None:
        """Abre o mic UMA VEZ para capturar o comando após o beep da wake word."""
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.2)
                if self._falando:
                    return None
                try:
                    audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
                except sr.WaitTimeoutError:
                    return None

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
                try: os.remove(tmp_path)
                except: pass

            if not texto or _eh_transcricao_ruim(texto):
                return None

            print(f"\033[90m[DEBUG AUDIO]: (pos wakeword) '{texto}'\033[0m")
            texto, tinha = self._normalizar_wake_word(texto)
            if tinha:
                _tocar_som_ativacao()
            if "sirius" not in texto:
                texto = f"sirius {texto}"
            return texto

        except (OSError, AttributeError):
            time.sleep(1)
            return None
        except Exception as e:
            if "NoneType" not in str(e):
                print(f"\033[31m[ERRO AUDIO]: {e}\033[0m")
            return None

    def _escutar_sem_wakeword(self) -> str | None:
        """Escuta continua quando nao ha wake word ativa (fallback)."""
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                if self._falando:
                    return None
                try:
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=8)
                except sr.WaitTimeoutError:
                    return None

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
                try: os.remove(tmp_path)
                except: pass

            if not texto or _eh_transcricao_ruim(texto):
                return None

            print(f"\033[90m[DEBUG AUDIO]: '{texto}'\033[0m")
            texto, tinha = self._normalizar_wake_word(texto)
            if tinha:
                _tocar_som_ativacao()
            return texto

        except (OSError, AttributeError):
            time.sleep(1)
            return None
        except Exception as e:
            if "NoneType" not in str(e):
                print(f"\033[31m[ERRO INTERNO AUDIO]: {e}\033[0m")
            return None