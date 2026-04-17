"""
filtro_zoeiro.py — Personalidade consistente do Sirius

Filosofia:
  - SECO e DIRETO: sem enrolação, vai direto ao ponto
  - PARÇA brasileiro: gírias naturais, não forçadas
  - CONSISTENTE: não muda de estilo a cada mensagem
  - STATUS sempre formatado: "Online. CPU 12%. Tudo nominal."
  - CONFIRMAÇÃO antes de ações destrutivas ou irreversíveis
  - FEEDBACK após executar: sempre confirma o que fez

O que NÃO faz mais:
  - Não abre TODA resposta com "Eae mano olha o que eu desenrolei aqui:"
  - Não fecha com "kkkk" em respostas técnicas
  - Não aplica estilo aleatório — contexto define o tom
"""

import re
import random


# ---------------------------------------------------------------------------
# Detectores de contexto
# ---------------------------------------------------------------------------

def _eh_status(texto: str) -> bool:
    """Detecta resposta de status do sistema."""
    t = texto.lower()
    return any(p in t for p in [
        "cpu", "ram", "memória", "disco", "bateria", "online",
        "processador", "temperatura", "ping", "%", "mb", "gb",
    ])

def _eh_confirmacao(texto: str) -> bool:
    """Detecta resposta que precisa de confirmação."""
    t = texto.lower()
    return any(p in t for p in [
        "desligando", "reiniciando", "deletando", "formatando",
        "removendo", "excluindo", "encerrando todos",
    ])

def _eh_feedback_acao(texto: str) -> bool:
    """Detecta feedback de ação executada."""
    t = texto.lower()
    return any(p in t for p in [
        "aberto", "fechado", "enviado", "criado", "salvo",
        "executado", "iniciado", "parado", "movido", "copiado",
        "instalado", "desinstalado", "atualizado",
    ]) and len(texto) < 120

def _eh_resposta_tecnica(texto: str) -> bool:
    """Detecta resposta técnica: código, erros, URLs, dados."""
    return (
        "```" in texto or
        "http" in texto or
        re.search(r"\b(TypeError|ValueError|Error|Exception)\b", texto) is not None or
        re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}", texto) is not None
    )

def _eh_resposta_longa(texto: str) -> bool:
    return len(texto) > 300

def _contem_lista(texto: str) -> bool:
    return bool(re.search(r"^\s*[-•\d]", texto, re.MULTILINE))


# ---------------------------------------------------------------------------
# Formatadores de estilo
# ---------------------------------------------------------------------------

# Aberturas naturais — usadas COM MODERAÇÃO (20% das vezes)
_ABERTURAS = [
    "Ó:",
    "Olha:",
    "Então:",
    "Seguinte:",
    "Anotado —",
]

# Fechamentos naturais — usados COM MODERAÇÃO (25% das vezes)
_FECHAMENTOS = [
    "Qualquer coisa, grita.",
    "Tamo junto.",
    "Dá um check aí.",
    "Fechou?",
    "Se precisar de mais, é só falar.",
]

# Confirmações de ação — sempre no mesmo formato
_CONFIRMACOES_ACAO = {
    "aberto":       "✓ {objeto} aberto.",
    "fechado":      "✓ {objeto} fechado.",
    "enviado":      "✓ Mensagem enviada para {objeto}.",
    "criado":       "✓ {objeto} criado.",
    "salvo":        "✓ Salvo.",
    "executado":    "✓ Executado.",
    "parado":       "✓ Parado.",
}


# ---------------------------------------------------------------------------
# Limpeza de formalidades que chegam do AgentePesquisador
# ---------------------------------------------------------------------------

_FORMALIDADES = [
    "Claro!", "Com certeza!", "Olá!", "Olá, ", "Oi!", "Oi, ",
    "É um prazer", "Fico feliz em ajudar", "Certamente",
    "Com prazer", "Entendido!", "Entendido,",
]

def _limpar_formalidades(texto: str) -> str:
    for f in _FORMALIDADES:
        texto = texto.replace(f, "").strip()
    texto = re.sub(r"^[,\s]+", "", texto)
    return texto.strip()


# ---------------------------------------------------------------------------
# SiriusFiltro principal
# ---------------------------------------------------------------------------

class SiriusFiltro:

    def aplicar_zoeira(self, texto: str) -> str:
        """
        Aplica personalidade Jarvis ao texto.
        Consistente, seco, direto — não aleatório.
        """
        if not texto:
            return texto

        # Limpeza sempre
        texto = _limpar_formalidades(texto)

        if not texto:
            return texto

        # Respostas técnicas saem limpas — sem estilo
        if _eh_resposta_tecnica(texto):
            return texto

        # Status do sistema — formato fixo, sem enfeites
        if _eh_status(texto):
            return texto

        # Feedback de ação curto — já é direto, não mexe
        if _eh_feedback_acao(texto):
            return texto

        # Respostas longas ou com lista — não polui com "eae mano"
        if _eh_resposta_longa(texto) or _contem_lista(texto):
            # Só remove formalidade e fecha discretamente 10% das vezes
            if random.random() < 0.10:
                return f"{texto}\n\n{random.choice(_FECHAMENTOS)}"
            return texto

        # Respostas médias — aplica estilo com moderação
        sorteio = random.random()

        if sorteio < 0.15:
            # Abertura discreta (15%)
            return f"{random.choice(_ABERTURAS)} {texto}"

        elif sorteio < 0.35:
            # Fechamento (20%)
            return f"{texto}\n{random.choice(_FECHAMENTOS)}"

        # 65% sai limpo — confia no conteúdo
        return texto

    # -----------------------------------------------------------------------
    # Formatadores especiais — chamados pelo cerebro.py diretamente
    # -----------------------------------------------------------------------

    @staticmethod
    def formatar_status(cpu: float, ram: float, ram_usada_mb: int = 0,
                        ram_total_mb: int = 0, bateria: float = None,
                        disco: float = None) -> str:
        """
        Formato fixo de status — igual ao Jarvis.
        "Online. CPU 12% | RAM 45% (7GB/16GB). Tudo nominal."
        """
        partes = [f"CPU {cpu:.0f}%"]

        if ram_total_mb > 0:
            ram_gb_usado = ram_usada_mb / 1024
            ram_gb_total = ram_total_mb / 1024
            partes.append(f"RAM {ram:.0f}% ({ram_gb_usado:.1f}GB/{ram_gb_total:.1f}GB)")
        else:
            partes.append(f"RAM {ram:.0f}%")

        if disco is not None:
            partes.append(f"Disco {disco:.0f}%")

        if bateria is not None:
            partes.append(f"Bateria {bateria:.0f}%")

        linha_recursos = " | ".join(partes)

        # Avalia saúde geral
        problemas = []
        if cpu > 90:     problemas.append("CPU crítica")
        if ram > 90:     problemas.append("RAM crítica")
        if disco and disco > 90: problemas.append("disco cheio")
        if bateria and bateria < 15: problemas.append("bateria crítica")

        if problemas:
            saude = f"⚠ {', '.join(problemas).capitalize()}."
        else:
            saude = "Tudo nominal."

        return f"Online. {linha_recursos}. {saude}"

    @staticmethod
    def formatar_confirmacao(acao: str, objeto: str = "",
                              reversivel: bool = True) -> str:
        """
        Pede confirmação antes de ação destrutiva/irreversível.
        "Vou desligar o PC em 60 segundos. Confirma? (diga 'sim' ou 'cancela')"
        """
        if not reversivel:
            return (
                f"⚠ {acao.capitalize()}. Isso não pode ser desfeito. "
                f"Confirma? (diga 'sim' ou 'cancela')"
            )
        obj_str = f" {objeto}" if objeto else ""
        return f"Vou {acao}{obj_str}. Confirma? (diga 'sim' ou 'cancela')"

    @staticmethod
    def formatar_feedback(acao: str, objeto: str = "") -> str:
        """
        Confirma ação executada — sempre curto e no mesmo formato.
        "✓ Chrome aberto." / "✓ Mensagem enviada para João."
        """
        obj_str = f" {objeto.strip()}" if objeto else ""
        return f"✓ {acao.capitalize()}{obj_str}."

    @staticmethod
    def formatar_erro(acao: str, motivo: str = "") -> str:
        """
        Informa falha de forma direta.
        "✗ Não consegui abrir o Chrome. Arquivo não encontrado."
        """
        motivo_str = f" {motivo.strip()}" if motivo else ""
        return f"✗ Não consegui {acao.lower()}.{motivo_str}"