"""
filtro_zoeiro.py — Personalidade do Sirius

Referência: J.A.R.V.I.S. do Iron Man.
  - Seco e preciso. Vai direto ao ponto, sem enrolação.
  - Tom profissional com leve ironia quando conveniente.
  - Nunca bajulador, nunca grita, nunca usa "kkk".
  - Confirma o que foi feito. Alerta quando tem problema.
  - Trata o usuário como "chefe" (não "mano", não "parceiro").
  - Não começa toda resposta com a mesma frase.

Jarvis NÃO diz:
  "Eae mano olha o que eu desenrolei aqui:"
  "Papo reto, a fita é a seguinte:"
  "Vê se faz sentido aí, kkkk."

Jarvis DIZ:
  "Prontamente, chefe."
  "Identificado. Aqui está:"
  "Feito."
  "Como solicitado."
  "Registrado."
"""

import re
import random


def _eh_status(texto):
    t = texto.lower()
    return any(p in t for p in [
        "cpu", "ram", "memória", "disco", "bateria", "online",
        "processador", "%", "mb", "gb", "nominal", "operacional",
    ])

def _eh_feedback_acao(texto):
    return (
        texto.startswith("✓") or texto.startswith("✗") or texto.startswith("⚠") or
        (len(texto) < 80 and any(p in texto.lower() for p in [
            "aberto", "fechado", "enviado", "criado", "salvo",
            "executado", "iniciado", "parado", "cancelado",
        ]))
    )

def _eh_resposta_tecnica(texto):
    return (
        "```" in texto or
        re.search(r"https?://", texto) is not None or
        re.search(r"\b(TypeError|ValueError|Error|Exception|Traceback)\b", texto) is not None
    )

def _eh_pergunta_sem_resposta(texto):
    t = texto.lower()
    return any(p in t for p in [
        "ainda não sei", "não sei responder", "já anotei",
        "não encontrei", "não tenho informação",
    ])

def _eh_saudacao(texto):
    t = texto.lower().strip()
    return any(t.startswith(p) for p in [
        "bom dia", "boa tarde", "boa noite", "oi!", "olá",
        "e aí", "tudo certo", "tudo bom",
    ])

def _eh_longa(texto):
    return len(texto) > 280

def _tem_lista(texto):
    return bool(re.search(r"^\s*[-•\d]", texto, re.MULTILINE))


_REMOVER = [
    "Claro!", "Claro, ", "Com certeza!", "Com certeza, ",
    "Olá!", "Olá, ", "Oi!", "Oi, ",
    "É um prazer", "Fico feliz em ajudar", "Certamente!",
    "Certamente, ", "Com prazer!", "Com prazer, ",
    "Entendido!", "Ótima pergunta!", "Excelente pergunta!",
    "Eae mano", "Eae", "Parça", "parceiro",
    "Papo reto, a fita é a seguinte:",
    "Ó o que apareceu nos meus circuitos",
    "Vê se faz sentido aí, kkkk.",
    "Tamo junto, qualquer fita me avisa.",
    "Se não for isso, a gente caça de novo, vamo que vamo.",
    "É isso, mano. Dá um check aí!",
    "Seguinte, se liga nessa fita:",
]

def _limpar(texto):
    for frase in _REMOVER:
        texto = texto.replace(frase, "").strip()
    texto = re.sub(r"^[,\.!\s]+", "", texto)
    texto = re.sub(r"\bk{2,}\b\.?", "", texto, flags=re.IGNORECASE)
    return texto.strip()


_PREFIXOS_INFO = [
    "Aqui está:",
    "Como solicitado:",
    "Identificado —",
    "Encontrei o seguinte:",
    "",
    "",
    "",
    "",
    "",
    "",
]

_SEM_RESPOSTA = [
    "Não tenho dados suficientes sobre isso no momento, chefe. Já estou pesquisando.",
    "Ainda não tenho uma resposta precisa. Registrei para pesquisar.",
    "Informação insuficiente no momento. Já anotei.",
]

_FECHAMENTOS_RAROS = [
    "Precisa de mais alguma coisa, chefe?",
    "Há mais algo?",
    "Posso ajudar com mais alguma coisa?",
]


class SiriusFiltro:

    def aplicar_zoeira(self, texto: str) -> str:
        if not texto or len(texto) < 3:
            return texto

        texto = _limpar(texto)
        if not texto:
            return texto

        # Casos que saem sem modificação
        if _eh_feedback_acao(texto):
            return texto
        if _eh_status(texto) and texto.startswith("Online"):
            return texto
        if _eh_resposta_tecnica(texto):
            return texto
        if _eh_saudacao(texto):
            return texto

        # "Não sei" — substitui por versão Jarvis
        if _eh_pergunta_sem_resposta(texto):
            return random.choice(_SEM_RESPOSTA)

        # Respostas longas/lista — fechamento raramente
        if _eh_longa(texto) or _tem_lista(texto):
            if random.random() < 0.12:
                return f"{texto}\n\n{random.choice(_FECHAMENTOS_RAROS)}"
            return texto

        # Respostas médias
        # 70% sai limpo, 20% com prefixo, 10% com fechamento
        sorteio = random.random()

        if sorteio < 0.20:
            prefixo = random.choice(_PREFIXOS_INFO)
            if prefixo and not texto.lower().startswith(prefixo.lower().rstrip(":")):
                sep = " " if prefixo.endswith((":","—")) else " "
                return f"{prefixo}{sep}{texto}"

        elif sorteio < 0.30:
            return f"{texto}\n\n{random.choice(_FECHAMENTOS_RAROS)}"

        return texto

    @staticmethod
    def formatar_status(cpu: float, ram: float,
                        ram_usada_mb: int = 0, ram_total_mb: int = 0,
                        bateria: float = None, disco: float = None) -> str:
        partes = [f"CPU {cpu:.0f}%"]
        if ram_total_mb > 0:
            u = ram_usada_mb / 1024
            t = ram_total_mb / 1024
            partes.append(f"RAM {ram:.0f}% ({u:.1f}GB/{t:.1f}GB)")
        else:
            partes.append(f"RAM {ram:.0f}%")
        if disco is not None:
            partes.append(f"Disco {disco:.0f}%")
        if bateria is not None:
            partes.append(f"Bateria {bateria:.0f}%")

        recursos = " | ".join(partes)
        alertas = []
        if cpu > 90:                            alertas.append("CPU crítica")
        if ram > 90:                            alertas.append("RAM crítica")
        if disco and disco > 90:                alertas.append("disco quase cheio")
        if bateria is not None and bateria < 15: alertas.append("bateria crítica")

        saude = f"⚠ {', '.join(alertas).capitalize()}." if alertas else "Tudo nominal."
        return f"Online. {recursos}. {saude}"

    @staticmethod
    def formatar_confirmacao(acao: str, objeto: str = "",
                              reversivel: bool = True) -> str:
        obj = f" {objeto.strip()}" if objeto else ""
        if not reversivel:
            return f"⚠ {acao.capitalize()}{obj}. Isso não pode ser desfeito. Confirma? (sim / cancela)"
        return f"{acao.capitalize()}{obj}. Confirma? (sim / cancela)"

    @staticmethod
    def formatar_feedback(acao: str, objeto: str = "") -> str:
        obj = f" {objeto.strip()}" if objeto else ""
        a = acao[0].upper() + acao[1:] if acao else acao
        return f"✓ {a}{obj}."

    @staticmethod
    def formatar_erro(acao: str, motivo: str = "") -> str:
        a = acao[0].lower() + acao[1:] if acao else acao
        m = f" {motivo.strip()}" if motivo else ""
        return f"✗ Não consegui {a}.{m}"

    @staticmethod
    def formatar_alerta(mensagem: str, nivel: str = "aviso") -> str:
        icone = {"info": "ℹ", "aviso": "⚠", "critico": "🔴"}.get(nivel, "⚠")
        return f"{icone} {mensagem}"