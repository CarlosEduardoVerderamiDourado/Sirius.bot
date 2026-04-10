import time
import random
import os
import sys

# --- CONFIGURAÇÃO DE CAMINHOS ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__)) # /src
raiz_projeto = os.path.dirname(diretorio_atual) # Sobe para a Raiz

if diretorio_atual not in sys.path:
    sys.path.append(diretorio_atual)

# Agora os imports funcionam sem erro
from config.ia_local import SiriusLocal
from chatbot import SiriusChat 
from memoria import SiriusMemory

class SiriusExplorador:
    def __init__(self):
        self.ia_gemini = SiriusChat()
        self.ia_local = SiriusLocal()
        self.memoria = SiriusMemory() # A SiriusMemory já deve apontar para /data
        
    def iniciar_estudo_autonomo(self):
        print("[SIRIUS]: Iniciando ciclo de aprendizado autodidata...")
        
        # 1. Ele pega o último tema que aprendeu no DB para ter um ponto de partida
        ultimo_tema = self.memoria.obter_ultimo_tema_estudado() or "Tecnologia"
        
        while True:
            try:
                # 2. O Gemini gera uma 'Aula de Especialista' sobre um subtema derivado
                prompt = f"Com base no tema '{ultimo_tema}', escolha um conceito derivado aleatório e gere uma explicação técnica profunda. Formato: TEMA: [nome], CONTEUDO: [explicação]"
                aula_bruta = self.ia_gemini.responder(prompt, salvar_no_db=False)
                
                # 3. VALIDAÇÃO (O FILTRO)
                if self.validar_conhecimento(aula_bruta):
                    # 4. Salva na Memória de Longo Prazo
                    self.memoria.salvar_conhecimento_autonomo(aula_bruta)
                    print(f"[SIRIUS]: Novo conhecimento absorvido: {aula_bruta[:50]}...")
                    
                    # Atualiza o tema para o próximo salto
                    ultimo_tema = self.extrair_tema(aula_bruta)
                
                # Sleep para respeitar o limite da API e não fritar o PC
                time.sleep(60) 
                
            except Exception as e:
                print(f"[ERRO NO ESTUDO]: {e}")
                time.sleep(30)

    def validar_conhecimento(self, conteudo):
        # O Gemini atua como Juiz aqui
        prompt_juiz = f"Analise se este conteúdo é verídico e útil: {conteudo}. Responda apenas SIM ou NÃO."
        veredicto = self.ia_gemini.responder(prompt_juiz, salvar_no_db=False)
        return "SIM" in veredicto.upper()