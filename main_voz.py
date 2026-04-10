import os
import sys

# --- FIX: RESOLVE O ERRO DE DPI ---
os.environ["QT_GLES_SELECT"] = "desktop"
os.environ["QT_PA_PLATFORM"] = "windows:dpiawareness=0"

import time
from dotenv import load_dotenv

# --- CORREÇÃO DE PATH DINÂMICA ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__)) # /src
raiz_projeto = os.path.dirname(diretorio_atual) # Sobe para a Raiz

# Garante que o Python encontre os módulos locais na /src
if diretorio_atual not in sys.path:
    sys.path.append(diretorio_atual)

# --- PUXA O .ENV DA PASTA CONFIG ---
caminho_env = os.path.join(raiz_projeto, "config", ".env")
load_dotenv(caminho_env)

# Agora os imports funcionam porque o sys.path foi ajustado
from cerebro import SiriusCerebro
from audio_handler import SiriusAudio

def iniciar_sirius_voz():
    # 1. Inicializa o áudio (Faster-Whisper é carregado aqui)
    try:
        sirius_voz = SiriusAudio()
    except Exception as e:
        print(f"\033[31m[FALHA CRÍTICA]: Dispositivo de áudio não encontrado: {e}\033[0m")
        return

    # 2. Inicializa o cérebro
    cerebro = SiriusCerebro()
    
    print("\n" + "="*45)
    print("      SIRIUS OS - AGENTE INTELIGENTE INICIADO")
    print("      Voz: Faster-Whisper | Cérebro: Ativado")
    print("="*45)
    
    sirius_voz.falar("Opa! Sistema operacional carregado. Qual a missão de hoje?")

    while True:
        try:
            # 3. Escuta usando o modelo Faster-Whisper local
            user_input = sirius_voz.escutar_fluxo_continuo()
            
            if not user_input or user_input.strip() == "":
                continue 

            print(f"\n\033[96m[VOCÊ]:\033[0m {user_input}")

            # 4. Comandos de interrupção imediata
            if any(cmd in user_input.lower() for cmd in ["sair", "tchau", "encerrar", "desligar"]):
                sirius_voz.falar("Fechando os sistemas. Até mais!")
                break
                
            # 5. PROCESSAMENTO VIA CÉREBRO (Aqui entra o LangChain no futuro)
            try:
                # O Cérebro processa e retorna a resposta com a personalidade do Sirius
                resposta_final = cerebro.processar(user_input)
                
                if resposta_final:
                    # O print já acontece dentro do cerebro.py ou audio_handler.falar
                    sirius_voz.falar(resposta_final)
                
                # Pequena pausa para evitar que ele se ouça falando
                time.sleep(0.3)

            except Exception as e:
                if "429" in str(e):
                    msg_erro = "Google me bloqueou por excesso de requisições. Dá um tempinho!"
                else:
                    msg_erro = "Minha rede neural deu um nó aqui. Pode repetir?"
                    print(f"\033[31m[ERRO]: {e}\033[0m")
                
                sirius_voz.falar(msg_erro)
                continue

        except KeyboardInterrupt:
            print("\n\n[SIRIUS]: Desligamento forçado via teclado.")
            break
        except Exception as e:
            print(f"\n[ERRO NO LOOP DE VOZ]: {e}")
            time.sleep(1)
            continue 

if __name__ == "__main__":
    iniciar_sirius_voz()