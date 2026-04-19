"""
sirius_tts.py — Voz local do Sirius sem pagar API

Cascata de qualidade:
  1. Kokoro TTS  → voz neural natural em PT-BR (~50MB, local, gratuito)
  2. pyttsx3     → voz do Windows (SAPI5) — fallback sempre disponível

Instalação:
    pip install kokoro soundfile sounddevice

Uso pelo audio_handler.py:
    from sirius_tts import SiriusTTS
    tts = SiriusTTS()
    tts.falar("Oi chefia, tô ligado!")
"""

import os
import sys
import time
import threading
import tempfile

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)

CAMINHO_AUDIO  = os.path.join(diretorio_raiz, "data", "tts_cache")
os.makedirs(CAMINHO_AUDIO, exist_ok=True)


# ---------------------------------------------------------------------------
# Kokoro TTS — voz neural natural
# ---------------------------------------------------------------------------

class KokoroTTS:
    """
    Voz neural local usando Kokoro TTS.
    Qualidade muito superior ao pyttsx3, totalmente offline.

    Vozes PT-BR disponíveis:
        pf_dora  → feminina (padrão, melhor para PT)
        pm_alex  → masculina
        pm_santa → masculina alternativa

    pip install kokoro soundfile sounddevice
    """

    # Vozes em ordem de preferência para PT-BR
    VOZES_PTBR = ["pf_dora", "pm_alex", "pm_santa", "af_heart", "af_sky"]

    def __init__(self):
        self._pipeline  = None
        self._voz       = None
        self._disponivel = False
        self._lock       = threading.Lock()
        self._inicializar()

    def _inicializar(self):
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from kokoro import KPipeline
                self._pipeline   = KPipeline(lang_code="p")
            self._voz        = self.VOZES_PTBR[0]
            self._disponivel = True
            print(f"\033[92m[TTS]: Kokoro carregado — voz '{self._voz}' ativa.\033[0m")
        except ImportError:
            print("\033[33m[TTS]: Kokoro não instalado. pip install kokoro soundfile sounddevice\033[0m")
        except Exception as e:
            print(f"\033[33m[TTS]: Kokoro falhou ({e}) — usando pyttsx3.\033[0m")

    def falar(self, texto: str) -> bool:
        """Sintetiza e reproduz o texto. Retorna True se funcionou."""
        if not self._disponivel or not self._pipeline:
            return False

        with self._lock:
            try:
                import sounddevice as sd
                import numpy as np
                import warnings

                samples = []
                # Suprime warnings do PyTorch sobre dropout/weight_norm
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    generator = self._pipeline(
                        texto,
                        voice=self._voz,
                        speed=1.05,
                        split_pattern=r'\n+'
                    )
                    for _, _, audio in generator:
                        if audio is not None:
                            samples.append(audio)

                if not samples:
                    print("[TTS Kokoro]: Nenhum áudio gerado.")
                    return False

                audio_final = np.concatenate(samples)

                # Verifica se sounddevice tem dispositivo disponível
                try:
                    dispositivos = sd.query_devices()
                    if not dispositivos:
                        print("[TTS Kokoro]: Nenhum dispositivo de áudio.")
                        return False
                except Exception:
                    pass

                sd.play(audio_final, samplerate=24000, blocking=True)
                return True

            except Exception as e:
                print(f"\033[33m[TTS Kokoro]: Erro: {e}\033[0m")
                return False

    def salvar_wav(self, texto: str, caminho: str) -> bool:
        """Salva o áudio em arquivo WAV."""
        if not self._disponivel:
            return False
        try:
            import soundfile as sf
            import numpy as np

            samples   = []
            generator = self._pipeline(texto, voice=self._voz, speed=1.05)
            for _, _, audio in generator:
                if audio is not None:
                    samples.append(audio)

            if not samples:
                return False

            sf.write(caminho, np.concatenate(samples), 24000)
            return True
        except Exception as e:
            print(f"\033[33m[TTS Kokoro salvar]: {e}\033[0m")
            return False

    def mudar_voz(self, voz: str) -> bool:
        """Muda a voz ativa."""
        if not self._disponivel:
            return False
        self._voz = voz
        print(f"[TTS]: Voz alterada para '{voz}'")
        return True

    def listar_vozes(self) -> list[str]:
        return self.VOZES_PTBR

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# pyttsx3 TTS — fallback sempre disponível no Windows
# ---------------------------------------------------------------------------

class PyttsxTTS:
    """Fallback usando voz do Windows (SAPI5). Sempre disponível."""

    def __init__(self):
        self._voice_id = None
        self._lock     = threading.Lock()
        self._configurar()

    def _configurar(self):
        try:
            import pyttsx3
            engine = pyttsx3.init()
            for v in engine.getProperty("voices"):
                if "Brazil" in v.name or "Portuguese" in v.name:
                    self._voice_id = v.id
                    break
            engine.stop()
        except Exception:
            pass

    def falar(self, texto: str) -> bool:
        with self._lock:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                if self._voice_id:
                    engine.setProperty("voice", self._voice_id)
                engine.setProperty("rate", 175)
                engine.setProperty("volume", 0.95)
                engine.say(texto)
                engine.runAndWait()
                engine.stop()
                return True
            except Exception as e:
                print(f"\033[33m[TTS pyttsx3]: {e}\033[0m")
                return False


# ---------------------------------------------------------------------------
# SiriusTTS — gerenciador principal com cascata
# ---------------------------------------------------------------------------

class SiriusTTS:
    """
    Gerenciador de TTS com cascata automática:
      1. Kokoro (neural, local, gratuito)
      2. pyttsx3 (SAPI5 Windows, sempre disponível)

    Uso:
        tts = SiriusTTS()
        tts.falar("Oi chefia!")
        tts.falar("Voz Kokoro", forcar_kokoro=True)
    """

    def __init__(self):
        self._kokoro  = KokoroTTS()
        self._pyttsx  = PyttsxTTS()
        self._modo    = "kokoro" if self._kokoro.disponivel else "pyttsx3"
        print(f"\033[94m[TTS]: Modo ativo: {self._modo}\033[0m")

    def falar(self, texto: str, forcar_kokoro: bool = False) -> bool:
        """Sintetiza o texto usando a melhor voz disponível."""
        if not texto or len(texto.strip()) < 2:
            return False

        # Tenta Kokoro primeiro
        if self._kokoro.disponivel:
            if self._kokoro.falar(texto):
                return True

        # Fallback pyttsx3
        return self._pyttsx.falar(texto)

    def mudar_voz(self, voz: str) -> str:
        """Muda a voz do Kokoro."""
        if self._kokoro.disponivel:
            self._kokoro.mudar_voz(voz)
            return f"Voz alterada para '{voz}'."
        return "Kokoro não disponível. Usando voz padrão do Windows."

    def listar_vozes(self) -> list[str]:
        return self._kokoro.listar_vozes()

    def status(self) -> dict:
        return {
            "kokoro_disponivel": self._kokoro.disponivel,
            "voz_kokoro":        self._kokoro._voz if self._kokoro.disponivel else None,
            "modo_ativo":        self._modo,
            "pyttsx3":           True,
        }

    @property
    def kokoro_disponivel(self) -> bool:
        return self._kokoro.disponivel


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_tts_instance = None

def get_tts() -> SiriusTTS:
    global _tts_instance
    if _tts_instance is None:
        _tts_instance = SiriusTTS()
    return _tts_instance


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--texto",  type=str, default="Oi chefia! Tô ligado e pronto pra ajudar.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--vozes",  action="store_true")
    parser.add_argument("--voz",    type=str, help="Muda a voz")
    args = parser.parse_args()

    tts = SiriusTTS()

    if args.status:
        s = tts.status()
        print("\n[TTS STATUS]")
        print(f"  Kokoro:  {'✓ ' + s['voz_kokoro'] if s['kokoro_disponivel'] else '✗ pip install kokoro soundfile sounddevice'}")
        print(f"  pyttsx3: ✓ sempre disponível")
        print(f"  Modo:    {s['modo_ativo']}\n")

    if args.vozes:
        print("Vozes PT-BR disponíveis:", tts.listar_vozes())

    if args.voz:
        print(tts.mudar_voz(args.voz))

    print(f"Falando: '{args.texto}'")
    tts.falar(args.texto)