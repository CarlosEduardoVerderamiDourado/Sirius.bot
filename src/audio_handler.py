import sys
import os
import re
import time
import tempfile
import requests
import pygame
import winsound
import numpy as np
from faster_whisper import WhisperModel

# --- LÓGICA DE CAMINHO ---
caminho_src = os.path.dirname(os.path.abspath(__file__))
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

class SiriusAudio:
    def __init__(self):
        self.api_key = ELEVENLABS_API_KEY
        self.voice_id = VOICE_ID
        
        # --- CONFIG FASTER-WHISPER ---
        # Alterado para 'cpu' para evitar o erro de cublas64_12.dll
        print("\033[93m[SIRIUS]: Carregando ouvidos (Faster-Whisper em CPU)...\033[0m")
        self.model_size = "base"
        self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        
        self.voice_id_windows = None
        self._configurar_voz_local()

    def _configurar_voz_local(self):
        import pyttsx3
        try:
            engine = pyttsx3.init()
            for v in engine.getProperty('voices'):
                if "Brazil" in v.name or "Portuguese" in v.name:
                    self.voice_id_windows = v.id
                    break
            del engine
        except: pass

    def limpar_texto(self, texto):
        # 1. Se for uma lista (comum em retornos de Agentes), extrai o primeiro item
        if isinstance(texto, list) and len(texto) > 0:
            texto = texto[0]

        # 2. Se for um dicionário (como o do seu log), pega apenas o valor da chave 'text'
        if isinstance(texto, dict):
            texto = texto.get('text', str(texto))

        # 3. Tratamento de String: Remove metadados que sobraram
        texto_final = str(texto)
        
        # Corta qualquer coisa que comece com 'extras': ou 'signature':
        for marcador in ["extras':", "signature':", "'type':"]:
            if marcador in texto_final:
                texto_final = texto_final.split(marcador)[0]

        # 4. Remove formatação Markdown (*, #, `, _) que a IA usa
        texto_final = re.sub(r'[\*\#\`\_]', '', texto_final)

        # 5. Limpeza de caracteres residuais de listas/dicionários convertidos
        caracteres_sujeira = ["[{", "}]", "{'text': '", '{"text": "', '"}', "'}", '["', '"]']
        for sujeira in caracteres_sujeira:
            texto_final = texto_final.replace(sujeira, "")

        return texto_final.strip()

    def falar(self, texto):
        texto_limpo = self.limpar_texto(texto)
        if not texto_limpo or len(texto_limpo) < 2: return
        
        sucesso = False
        if self.api_key and len(self.api_key) > 5:
            try:
                url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
                headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
                payload = {
                    "text": texto_limpo,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
                }
                response = requests.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    fd, temp_path = tempfile.mkstemp(suffix=".mp3")
                    with os.fdopen(fd, 'wb') as f: f.write(response.content)
                    pygame.mixer.music.load(temp_path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy(): time.sleep(0.1)
                    pygame.mixer.music.unload()
                    os.remove(temp_path)
                    sucesso = True
            except: pass

        if not sucesso: self._falar_windows(texto_limpo)

    def _falar_windows(self, t):
        import pyttsx3
        try:
            e = pyttsx3.init()
            if self.voice_id_windows: e.setProperty('voice', self.voice_id_windows)
            e.setProperty('rate', 180)
            e.say(t)
            e.runAndWait()
        except: pass

    def escutar_fluxo_continuo(self):
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300 # Sensibilidade ao barulho
        
        with sr.Microphone() as source:
            # Calibração rápida
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            
            try:
                # timeout=1 permite que a interface continue respondendo
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=10)
                
                wav_data = audio.get_wav_data()
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(wav_data)
                    tmp_name = f.name

                # Transcrição
                segments, _ = self.model.transcribe(tmp_name, language="pt")
                texto = "".join([s.text for s in segments]).lower().strip()
                
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)

                if texto:
                    print(f"\033[90m[DEBUG AUDIO]: '{texto}'\033[0m")
                    
                    # Lista de variações que o Whisper costuma entender para "Sirius"
                    gatilhos = ["sirius", "fírios", "fírius", "fídeos", "sírius", "fírio","serios","seídios","sídios"]
                    
                    # Verifica se qualquer uma das palavras gatilho está no texto
                    if any(g in texto for g in gatilhos):
                        winsound.Beep(1000, 150)
                        # Opcional: Substituir a variação pela palavra correta para o cérebro não se confundir
                        for g in gatilhos:
                            texto = re.sub(rf'\b{g}\b', 'sirius', texto)
                        return texto
                    
            except sr.WaitTimeoutError:
                return None
            except Exception as e:
                print(f"\033[31m[ERRO WHISPER]: {e}\033[0m")
                
        return None