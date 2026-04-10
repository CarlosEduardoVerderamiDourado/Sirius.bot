import time
import random
import threading
from duckduckgo_search import DDGS
from memoria import SiriusMemory # Importa sua classe de memória

class SiriusSubconsciente:
    def __init__(self):
        self.memoria = SiriusMemory()
        self.temas_de_interesse = [
            "competitivo de pokemon vgc 2026",
            "novidades python 3.14",
            "como criar redes neurais em c++",
            "tecnologias de propulsão espacial nasa",
            "melhores práticas de código limpo SOLID"
        ]

    def minerar_autonomamente(self):
        while True:
            # 1. Escolhe um tema aleatório
            tema = random.choice(self.temas_de_interesse)
            print(f"[SIRIUS]: Decidi estudar sobre: {tema}")

            try:
                # 2. Pesquisa na Web
                with DDGS() as ddgs:
                    resultados = [r for r in ddgs.text(tema, max_results=2)]
                
                if resultados:
                    from consultor import SiriusConsultor
                    consultor = SiriusConsultor() # Para injetar na memória vetorial
                    
                    for res in resultados:
                        conteudo_estudo = f"Fonte: {res['href']}\nConteúdo: {res['body']}"
                        
                        # 3. Salva no Banco de Treino (SQLite) para o futuro da RedeSirius
                        sucesso = self.memoria.salvar_estudo_autonomo(
                            tema=tema, 
                            conteudo=conteudo_estudo, 
                            tags="estudo_autonomo"
                        )
                        
                        if sucesso:
                            # Injeta também no FAISS para consulta em tempo real
                            consultor.injetar_conhecimento(
                                f"Conhecimento atualizado sobre {tema}: {res['body']}",
                                {"url": res['href'], "tipo": "estudo_autonomo"}
                            )
                            print(f"[SIRIUS]: Conhecimento sobre {tema} consolidado no SQLite e FAISS!")
                
            except Exception as e:
                print(f"[SIRIUS]: Tentei estudar, mas deu erro: {e}")

            # 4. Espera um tempo antes de estudar de novo (ex: 30 minutos)
            # Para testar agora, você pode baixar para 60 segundos
            time.sleep(1800) 

    def iniciar(self):
        # Roda em background para não travar o resto do sistema
        thread = threading.Thread(target=self.minerar_autonomamente, daemon=True)
        thread.start()

# Para usar no seu main_residente.py:
# sub = SiriusSubconsciente()
# sub.iniciar()