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
    VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ" # Fallback

class SiriusAudio:
    def __init__(self):
        self.api_key = ELEVENLABS_API_KEY
        self.voice_id = VOICE_ID
        
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
        """
        FILTRO CRÍTICO: Extrai apenas a fala humana da resposta da IA.
        Remove metadados, assinaturas e símbolos de Markdown.
        """
        texto_final = ""

        # Caso 1: Se o texto vier como a lista de dicionários do log
        if isinstance(texto, list):
            for item in texto:
                if isinstance(item, dict) and 'text' in item:
                    texto_final = item['text']
                    break
        # Caso 2: Se vier como string direta
        else:
            texto_final = str(texto)

        # Remove a assinatura caso ela tenha vazado para a string
        if "extras': {'signature':" in texto_final:
            texto_final = texto_final.split("extras':")[0]

        # Limpeza de caracteres especiais e Markdown
        texto_final = re.sub(r'[\*\#\`\_]', '', texto_final)
        
        # Remove colchetes ou chaves que sobraram da conversão bruta
        texto_final = texto_final.replace("[{", "").replace("}]", "").strip()

        return " ".join(texto_final.split()).strip()

    def falar(self, texto):
        """Gera áudio via HTTP direto filtrando o lixo da IA."""
        texto_limpo = self.limpar_texto(texto)
        
        # Se após a limpeza não sobrar texto real, cancela a fala
        if not texto_limpo or len(texto_limpo) < 2: 
            return

        print(f"\033[94mSirius:\033[0m {texto_limpo}")
        
        sucesso = False
        # Verificação básica de segurança na API Key
        if self.api_key and isinstance(self.api_key, str) and len(self.api_key) > 5:
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
                    # Usando mkstemp para garantir que o Windows não bloqueie o arquivo
                    fd, temp_path = tempfile.mkstemp(suffix=".mp3")
                    try:
                        with os.fdopen(fd, 'wb') as f:
                            f.write(response.content)
                        
                        pygame.mixer.music.load(temp_path)
                        pygame.mixer.music.play()
                        
                        while pygame.mixer.music.get_busy():
                            time.sleep(0.1)
                        
                        pygame.mixer.music.unload()
                        sucesso = True
                    finally:
                        if os.path.exists(temp_path):
                            try: os.remove(temp_path)
                            except: pass
                else:
                    # Agora detalha o erro da ElevenLabs (ajuda no debug do 401)
                    print(f"\033[33m[ElevenLabs]: Erro {response.status_code}. {response.text}\033[0m")
            except Exception as e:
                print(f"\033[31m[Erro de Conexão]: {e}\033[0m")

        if not sucesso:
            # Se falhar, garante que o mixer está livre antes de usar a voz local
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except:
                pass
            self._falar_windows(texto_limpo, "Voz IA indisponível")

    def _falar_windows(self, t, motivo=""):
        if motivo: print(f"\033[33m[Aviso]: {motivo}. Usando motor local.\033[0m")
        try:
            # Recriar o engine evita que o motor SAPI5 "engasgue" no Windows
            e = pyttsx3.init()
            if self.voice_id_windows:
                e.setProperty('voice', self.voice_id_windows)
            e.setProperty('rate', 180) # Velocidade levemente mais rápida/natural
            e.say(t)
            e.runAndWait()
            e.stop()
            del e # Limpa o motor da memória
        except:
            pass

    def aguardar_ativacao(self):
        """Escuta em standby até ouvir 'Sirius'."""
        fs = 44100
        seconds = 5 
        filename = 'trigger.wav'
        print("\033[90m[Standby: Diga 'Sirius']\033[0m")
        try:
            sd.stop() # Garante que o microfone não esteja ocupado
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
            if os.path.exists(filename): 
                try: os.remove(filename)
                except: pass
        return False

    def ouvir(self):
        """Capta o comando de voz do usuário."""
        fs = 44100  
        seconds = 10 
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
            if os.path.exists(filename): 
                try: os.remove(filename)
                except: pass