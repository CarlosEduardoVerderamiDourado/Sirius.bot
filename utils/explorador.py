import os
import sys
import time
import re
import random
from googlesearch import search 
import trafilatura 

# --- BLOCO OBRIGATÓRIO PARA RESOLVER CAMINHOS ---
diretorio_atual = os.path.dirname(os.path.abspath(__file__)) # /src
raiz_projeto = os.path.dirname(diretorio_atual) # Sobe para a Raiz

if raiz_projeto not in sys.path:
    sys.path.insert(0, raiz_projeto)
if os.path.join(raiz_projeto, 'src') not in sys.path:
    sys.path.insert(0, os.path.join(raiz_projeto, 'src'))

from memoria import SiriusMemory
from neuronio import SiriusNeuronio 
from chatbot import SiriusChat # Usaremos para extrair próximos temas
# -----------------------------------------------

class SiriusExplorador:
    def __init__(self):
        self.memoria = SiriusMemory()
        self.neuronio = SiriusNeuronio()
        self.ia = SiriusChat()
        self.intervalo_estudo = 300 
        
        # Sementes para quando a fila estiver vazia
        self.areas_mestre = [
            "Inteligência Artificial", "História do Brasil", "Mecânica Quântica", 
            "Arquitetura de Software", "Grandes Civilizações", "Biologia Molecular",
            "Exploração Espacial", "Filosofia Moderna", "Cibersegurança"
        ]

    def extrair_proximos_temas(self, texto_minerado):
        """Usa a IA para descobrir 3 novos temas baseados na leitura atual"""
        print("[EXPLORADOR]: Identificando novas conexões de conhecimento...")
        prompt = f"""
        Com base neste texto, extraia 3 assuntos específicos e fascinantes relacionados.
        Responda APENAS os nomes dos temas separados por vírgula.
        TEXTO: {texto_minerado[:1200]}
        """
        try:
            resposta = self.ia.responder(prompt, salvar_no_db=False)
            temas = [t.strip() for t in resposta.split(",")]
            for t in temas:
                if len(t) < 50: # Evita frases longas
                    self.memoria.adicionar_duvida(t) # Adiciona na fila de estudo
                    print(f"\033[96m[DESCOBERTA]:\033[0m Novo interesse mapeado: {t}")
        except:
            pass

    def minerar_web(self, tema):
        print(f"\033[94m[MINERADOR]:\033[0m Pesquisando: {tema}")
        links_encontrados = []
        try:
            for resultado in search(tema, num_results=2, lang="pt"):
                links_encontrados.append(resultado)
        except Exception as e:
            print(f"\033[31m[ERRO BUSCA]:\033[0m {e}")
            return False

        conteudo_total = ""
        for url in links_encontrados:
            try:
                downloaded = trafilatura.fetch_url(url)
                conteudo_limpo = trafilatura.extract(downloaded)

                if conteudo_limpo and len(conteudo_limpo) > 500:
                    if not self.neuronio.verificar_se_ja_sabe(conteudo_limpo):
                        self.memoria.salvar_estudo_autonomo(tema, conteudo_limpo, tags="autodidata")
                        conteudo_total += conteudo_limpo + "\n"
                        print(f"\033[92m[SUCESSO]:\033[0m Absorvido de {url}")
            except:
                continue
        
        # Se minerou algo novo, extrai os próximos temas para o ciclo infinito
        if conteudo_total:
            self.extrair_proximos_temas(conteudo_total)
            return True
        return False

    def iniciar_ciclo(self):
        print("\n" + "="*60)
        print("\033[94m[SIRIUS EXPLORADOR]: Modo Explorador Infinito Ativado\033[0m")
        print("="*60 + "\n")
        
        while True:
            try:
                # 1. Tenta pegar uma dúvida sua ou um tema descoberto
                tema = self.memoria.buscar_duvida_pendente() 
                
                # 2. Se não houver nada na fila, usa uma semente aleatória para recomeçar
                if not tema:
                    tema = random.choice(self.areas_mestre)
                    print(f"\033[90m[SIRIUS]: Fila vazia. Iniciando nova expedição: {tema}\033[0m")
                else:
                    print(f"\033[95m[SIRIUS]:\033[0m Próximo alvo: {tema}")

                # Executa a mineração
                conseguiu = self.minerar_web(tema)
                
                # Sempre marca como resolvido para a fila andar
                self.memoria.marcar_duvida_como_resolvida(tema)

            except Exception as e:
                print(f"\033[31m[ERRO EXPLORADOR]:\033[0m {e}")

            print(f"\n[AGUARDANDO]: {self.intervalo_estudo/60} min para o próximo salto...")
            time.sleep(self.intervalo_estudo)

if __name__ == "__main__":
    explorador = SiriusExplorador()
    explorador.iniciar_ciclo()