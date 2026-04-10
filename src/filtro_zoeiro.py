import random

class SiriusFiltro:
    def __init__(self):
        # Frases no estilo "parça" pra abrir a conversa
        self.aberturas_parca = [
            "Eae mano, olha o que eu desenrolei aqui:",
            "Papo reto, a fita é a seguinte:",
            "Dá um confere no que eu achei pra vc:",
            "Mano, o Carlos me deu a letra e eu busquei isso aqui:",
            "Seguinte, se liga nessa fita:",
            "Ó o que apareceu nos meus circuitos, vê aí:"
        ]

        # Comentários pra fechar o papo de forma natural
        self.fechamentos_parca = [
            "Vê se faz sentido aí, kkkk.",
            "Qualquer coisa dá um salve!",
            "Se não for isso, a gente caça de novo, vamo que vamo.",
            "Fechou? Se precisar de mais é só gritar.",
            "É isso, mano. Dá um check aí!",
            "Tamo junto, qualquer fita me avisa."
        ]

    def aplicar_zoeira(self, texto):
        if "```" in texto or "Erro" in texto or len(texto) < 15:
            return texto
    # ... resto do código
            
        # 2. Sorteio de estilo (pra não ficar repetitivo)
        sorteio = random.random()

        # Remove formalidades que a IA as vezes coloca sem querer
        texto = texto.replace("Claro!", "").replace("Com certeza!", "").strip()

        if sorteio < 0.3: # Só o começo
            return f"{random.choice(self.aberturas_parca)}\n\n{texto}"
        
        elif sorteio < 0.6: # Só o final
            return f"{texto}\n\n{random.choice(self.fechamentos_parca)}"
        
        elif sorteio < 0.8: # Início e Fim (Combo completo)
            return f"{random.choice(self.aberturas_parca)}\n\n{texto}\n\n{random.choice(self.fechamentos_parca)}"
        
        else: # Deixa o texto puro (confia no System Prompt do chatbot)
            return texto