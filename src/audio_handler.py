import sys
import os
import re
import time
import tempfile
import requests
import pygame
import sounddevice as sd
import speech_recognition as sr
import pyttsx3
import scipy.io.wavfile as wav
import winsound

# --- LÓGICA DE CAMINHO PARA IMPORTAÇÃO ---
caminho_src = os.path.dirname(os.path.abspath(__file__))
raiz_projeto = os.path.dirname(caminho_src)

if raiz_projeto not in sys.path:
    sys.path.append(raiz_projeto)

# Importa as configurações centralizadas
try:
    from config.config import ELEVENLABS_API_KEY, VOICE_ID
    print("\033[92m[Sucesso]: Configurações importadas!\033[0m")
except Exception as e:
    print(f"\033[31m[Erro]: Falha ao importar de config.config: {e}\033[0m")
    ELEVENLABS_API_KEY = None
    VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ" # Fallback para o Liam

class SiriusAudio:
    def __init__(self):
        self.api_key = ELEVENLABS_API_KEY
        self.voice_id = VOICE_ID
        
        # Inicializa o mixer do Pygame para áudio MP3
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        
        self.voice_id_windows = None
        self._configurar_voz_local()

    def _configurar_voz_local(self):
        """Configura o motor SAPI5 para caso a API da ElevenLabs falhe."""
        try:
            engine = pyttsx3.init()
            for v in engine.getProperty('voices'):
                if "Brazil" in v.name or "Portuguese" in v.name:
                    self.voice_id_windows = v.id
                    break
            del engine
        except:
            pass

    def limpar_texto(self, texto):
        """Limpa o texto para a IA não ler símbolos de Markdown ou metadados."""
        texto = re.sub(r'[\*\#\`\_]', '', str(texto))
        return " ".join(texto.split()).strip()

    def falar(self, texto):
        """Gera áudio via HTTP direto para evitar conflitos de biblioteca."""
        texto_limpo = self.limpar_texto(texto)
        if not texto_limpo: return

        print(f"\033[94mSirius:\033[0m {texto_limpo}")
        
        sucesso = False
        if self.api_key and isinstance(self.api_key, str):
            try:
                url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
                headers = {
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": self.api_key
                }
                payload = {
                    "text": texto_limpo,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
                }

                response = requests.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                        f.write(response.content)
                        temp_path = f.name
                    
                    pygame.mixer.music.load(temp_path)
                    pygame.mixer.music.play()
                    
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                    
                    pygame.mixer.music.unload()
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    sucesso = True
                else:
                    print(f"\033[33m[ElevenLabs]: Erro {response.status_code}. Verifique sua cota.\033[0m")
            except Exception as e:
                print(f"\033[31m[Erro de Conexão]: {e}\033[0m")

        if not sucesso:
            self._falar_windows(texto_limpo, "Voz IA indisponível")

    def _falar_windows(self, t, motivo=""):
        if motivo: print(f"\033[33m[Aviso]: {motivo}. Usando motor local.\033[0m")
        try:
            e = pyttsx3.init()
            if self.voice_id_windows:
                e.setProperty('voice', self.voice_id_windows)
            e.say(t)
            e.runAndWait()
            e.stop()
        except:
            pass

    # --- MÉTODOS DE ESCUTA REATIVADOS ---
    def aguardar_ativacao(self):
        """Escuta em standby até ouvir 'Sirius'."""
        fs = 44100
        seconds = 3 
        filename = 'trigger.wav'
        print("\033[90m[Standby: Diga 'Sirius']\033[0m")
        try:
            recording = sd.rec(int(seconds * fs), samplerate=fs, channels=1, dtype='int16')
            sd.wait()
            wav.write(filename, fs, recording)
            reconhecedor = sr.Recognizer()
            with sr.AudioFile(filename) as source:
                audio_data = reconhecedor.record(source)
                texto_ouvido = reconhecedor.recognize_google(audio_data, language='pt-BR').lower()
                if "sirius" in texto_ouvido:
                    winsound.Beep(1000, 150) 
                    return True
        except:
            pass
        finally:
            if os.path.exists(filename): os.remove(filename)
        return False

    def ouvir(self):
        """Capta o comando de voz do usuário."""
        fs = 44100  
        seconds = 7 
        filename = 'temp_audio_sirius.wav'
        try:
            sd.stop() 
            recording = sd.rec(int(seconds * fs), samplerate=fs, channels=1, dtype='int16')
            sd.wait()  
            wav.write(filename, fs, recording)
            reconhecedor = sr.Recognizer()
            with sr.AudioFile(filename) as source:
                audio_data = reconhecedor.record(source)
                return reconhecedor.recognize_google(audio_data, language='pt-BR')
        except:
            return None
        finally:
            if os.path.exists(filename): os.remove(filename)