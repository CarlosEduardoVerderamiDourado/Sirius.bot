import re
from memoria import SiriusMemory
from agente_sirius import SiriusAgente
from filtro_zoeiro import SiriusFiltro

class SiriusCerebro:
    def __init__(self):
        # 1. Memória SQL (SQLite) para Histórico e Dúvidas
        self.memoria = SiriusMemory()
        
        # 2. Filtro de Personalidade (O "tempero" do Sirius)
        self.filtro = SiriusFiltro()
        
        # 3. O Núcleo Híbrido (LangGraph + Gemini + Llama + FAISS)
        # O Agente agora centraliza o Consultor(FAISS), ControlePC e IAs
        self.agente = SiriusAgente()

    def processar(self, texto_usuario, forcar_processamento=False):
        """
        Ponto central de processamento. 
        texto_usuario: A frase vinda da interface ou do áudio.
        forcar_processamento: Se True, ignora a necessidade de falar 'Sirius'.
        """
        if isinstance(texto_usuario, list):
            texto_usuario = texto_usuario[0] if len(texto_usuario) > 0 else ""
        texto_lower = str(texto_usuario).lower().strip()
        
        # --- SISTEMA DE GATILHO (WAKE WORD) ---
        if "sirius" in texto_lower:
            # Remove a palavra "sirius" e pontuações próximas para não confundir o modelo
            comando = re.sub(r"[,!\.\s]*sirius[,!\.\s]*", " ", texto_lower).strip()
            if not comando:
                return "Diga lá, chefia. Tô ouvindo."
        elif forcar_processamento:
            comando = texto_lower
        else:
            # Se não chamou pelo nome e não forçou, o Sirius ignora (fica em standby)
            return None 

        print(f"\033[94m[CEREBRO]: Ativando Grafo de Decisão para: {comando}\033[0m")

        try:
            # --- O AGENTE DECIDE TUDO ---
            resposta_bruta = self.agente.executar(comando)
            
            # NOVIDADE: Limpeza imediata se for o dicionário do Gemini
            if isinstance(resposta_bruta, dict):
                resposta_bruta = resposta_bruta.get('text', str(resposta_bruta))
            
            # Se vier como lista, unifica
            if isinstance(resposta_bruta, list):
                resposta_bruta = " ".join(map(str, resposta_bruta))
            else:
                resposta_bruta = str(resposta_bruta)

            # --- TRATAMENTO DE FALHAS E DÚVIDAS ---
            # (O resto do seu código continua aqui...)

            # --- TRATAMENTO DE FALHAS E DÚVIDAS ---
            indicadores_falha = [
                "não sei", "não encontrei", "desculpe, mas não", 
                "não tenho acesso", "i don't know", "não pude determinar"
            ]
            
            # Agora o .lower() nunca mais vai dar erro de 'list object'
            if any(falha in resposta_bruta.lower() for falha in indicadores_falha):
                self.memoria.adicionar_duvida(comando)
                resposta_final = "Mano, não achei nada aqui agora. Já anotei no meu banco pra estudar depois!"
            else:
                # Aplica o seu filtro de personalidade (Zoeira)
                resposta_final = self.filtro.aplicar_zoeira(resposta_bruta)

            # --- SALVAMENTO NO HISTÓRICO (SQLite) ---
            # Aqui o SQLite recebe strings limpas, evitando o erro de 'binding parameter'
            self.memoria.salvar_historico(comando, resposta_final)
            
            # --- COLETA PARA TREINAMENTO DA REDE PRÓPRIA ---
            # Se a resposta foi boa (não caiu nos indicadores de falha),
            # salvamos como um 'exemplo mestre' para sua futura rede neural.
            if resposta_final != "Mano, não achei nada aqui agora. Já anotei no meu banco pra estudar depois!":
                # Criamos um dataset: Comando limpo -> Resposta da IA (sem a zoeira do filtro para não viciar o treino)
                self.memoria.salvar_amostra_treino(comando, resposta_bruta)
            
            return resposta_final

        except Exception as e:
            print(f"\033[91m[ERRO CEREBRO]: {e}\033[0m")
            return "Ih chefe, deu um curto aqui nos meus circuitos. Tenta de novo?"