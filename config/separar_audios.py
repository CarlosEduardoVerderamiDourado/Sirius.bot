import os
import wave
import struct

def cortar_wav_simples(arquivo_entrada, pasta_destino):
    if not os.path.exists(pasta_destino):
        os.makedirs(pasta_destino)
        print(f"✅ Pasta criada: {pasta_destino}")

    try:
        with wave.open(arquivo_entrada, 'rb') as w:
            params = w.getparams()
            framerate = w.getframerate()
            n_frames_per_chunk = int(framerate * 1.5) # 1.5 segundos
            
            print(f"⏳ Lendo {arquivo_entrada}...")
            count = 0
            while True:
                frames = w.readframes(n_frames_per_chunk)
                if not frames or len(frames) < n_frames_per_chunk:
                    break
                
                nome_saida = os.path.join(pasta_destino, f"real_neg_{count}.wav")
                with wave.open(nome_saida, 'wb') as out:
                    out.setparams(params)
                    out.writeframes(frames)
                count += 1
            
            print(f"🚀 Sucesso! {count} amostras criadas em {pasta_destino}")
            print("Agora rode: python src/sirius_wakeword.py --so-treinar")

    except Exception as e:
        print(f"❌ Erro: {e}")
        print("Certifique-se de que o arquivo 'negativos.wav' está na mesma pasta do script.")

# Configuração
arquivo = "negativos.wav" 
destino = os.path.join("config", "treino_wakeword", "negativos")

cortar_wav_simples(arquivo, destino)