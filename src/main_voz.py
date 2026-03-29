import time
import os
from dotenv import load_dotenv
from chatbot import sirius_com_memoria  # Importa a chain configurada
from audio_handler import SiriusAudio

# Carrega as chaves do arquivo .env
load_dotenv()

def iniciar_sirius_voz():
    sirius_voz = SiriusAudio()
    
    # Boas-vindas inicial
    sirius_voz.falar("Opa bom! qual a boa de hoje.")

    while True:
        try:
            # 1. Tenta ouvir (Configurado para 6 segundos no audio_handler)
            user_input = sirius_voz.ouvir()
            
            # 2. Se o retorno for None (silêncio ou erro de rede), tenta de novo
            if user_input is None:
                print("... (Silêncio detectado ou voz não entendida) ...")
                continue 

            # 3. Comandos de saída
            if user_input.lower() in ["sair", "tchau", "encerrar", "parar"]:
                sirius_voz.falar("Desligando os motores.")
                break
                
            # 4. Chama o Gemini com tratamento de erro de servidor (503/429)
            try:
                config = {"configurable": {"session_id": "sessao_voz_facu"}}
                resposta = sirius_com_memoria.invoke({"input": user_input}, config=config)
                
                # 5. Extrai o texto da resposta
                if isinstance(resposta.content, list):
                    texto_final = resposta.content[0].get('text', '')
                else:
                    texto_final = resposta.content
                
                # 6. Sirius fala a resposta (já com o filtro de asteriscos)
                sirius_voz.falar(texto_final)
                
                # 7. Pausa para o Windows liberar o hardware de som
                time.sleep(1.0)

            except Exception as e:
                # Trata especificamente o erro de servidor lotado ou cota
                if "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e):
                    print("\n\033[91m[AVISO]: Servidor instável ou lotado. Tentando reconectar...\033[0m")
                    sirius_voz.falar("Opa, meu cérebro na nuvem deu um soluço. Espera um pouquinho que eu já volto.")
                    time.sleep(4)
                    continue 
                
                # Caso seja outro erro dentro do invoke
                print(f"\n[ERRO NO GEMINI]: {e}")
                continue

        except Exception as e:
            # Erro geral no loop (microfone, etc)
            print(f"\n[ERRO NO LOOP]: {e}")
            time.sleep(2)
            continue 

if __name__ == "__main__":
    iniciar_sirius_voz()