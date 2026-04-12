import os
import sys

# --- FIX: RESOLVE O ERRO DE DPI ---
os.environ["QT_GLES_SELECT"] = "desktop"
os.environ["QT_PA_PLATFORM"] = "windows:dpiawareness=0"

import time
from dotenv import load_dotenv

# --- CORREÇÃO DE PATH DINÂMICA ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
raiz_projeto    = os.path.dirname(diretorio_atual)

if diretorio_atual not in sys.path:
    sys.path.append(diretorio_atual)

# --- PUXA O .ENV DA PASTA CONFIG ---
caminho_env = os.path.join(raiz_projeto, "config", ".env")
load_dotenv(caminho_env)

from cerebro import SiriusCerebro
from audio_handler import SiriusAudio

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

WAKE_WORDS      = ["sirius"]
CMDS_ENCERRAR   = ["sair", "tchau", "encerrar", "desligar sirius"]
MAX_ERROS_AUDIO = 5   # reinicia o loop após muitos erros consecutivos


def _contem(texto: str, palavras: list[str]) -> bool:
    return any(p in texto.lower() for p in palavras)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def iniciar_sirius_voz():
    # 1. Inicializa áudio
    try:
        sirius_voz = SiriusAudio()
    except Exception as e:
        print(f"\033[31m[FALHA CRÍTICA]: Dispositivo de áudio não encontrado: {e}\033[0m")
        return

    # 2. Inicializa cérebro
    cerebro = SiriusCerebro()

    print("\n" + "=" * 50)
    print("      SIRIUS OS - AGENTE INTELIGENTE INICIADO")
    print("      Voz: Faster-Whisper  |  Cérebro: Ativado")
    print("=" * 50 + "\n")

    sirius_voz.falar("Opa! Sistema operacional carregado. Qual a missão de hoje?")

    erros_consecutivos = 0

    while True:
        try:
            # 3. Escuta contínua
            user_input = sirius_voz.escutar_fluxo_continuo()

            if not user_input or not user_input.strip():
                continue

            erros_consecutivos = 0  # reseta contador ao receber áudio válido
            texto_lower = user_input.lower().strip()

            print(f"\n\033[96m[VOCÊ]:\033[0m {user_input}")

            # 4. Encerramento por voz
            if _contem(texto_lower, CMDS_ENCERRAR):
                sirius_voz.falar("Fechando os sistemas. Até mais, chefia!")
                break

            # 5. Decide se processa com ou sem wake word
            tem_wake_word     = _contem(texto_lower, WAKE_WORDS)
            forcar            = not tem_wake_word  # sem "sirius" → força processamento direto

            # 6. Processa via cérebro
            resposta = cerebro.processar(user_input, forcar_processamento=forcar)

            if resposta:
                print(f"\033[92m[SIRIUS]:\033[0m {resposta}")
                sirius_voz.falar(resposta)

            time.sleep(0.2)

        except KeyboardInterrupt:
            print("\n\n[SIRIUS]: Desligamento forçado via teclado.")
            sirius_voz.falar("Até mais!")
            break

        except AttributeError as e:
            # Microfone retornou None — falha de hardware transitória
            erros_consecutivos += 1
            print(f"\033[33m[AVISO AUDIO]: {e} (erro {erros_consecutivos}/{MAX_ERROS_AUDIO})\033[0m")
            if erros_consecutivos >= MAX_ERROS_AUDIO:
                print("\033[31m[SIRIUS]: Muitos erros de áudio. Reiniciando dispositivo...\033[0m")
                try:
                    sirius_voz = SiriusAudio()
                    erros_consecutivos = 0
                except Exception:
                    print("\033[31m[SIRIUS]: Falha ao reiniciar áudio. Encerrando.\033[0m")
                    break
            time.sleep(1)

        except Exception as e:
            erros_consecutivos += 1
            print(f"\033[31m[ERRO NO LOOP]: {type(e).__name__} - {e}\033[0m")
            time.sleep(2)


if __name__ == "__main__":
    iniciar_sirius_voz()