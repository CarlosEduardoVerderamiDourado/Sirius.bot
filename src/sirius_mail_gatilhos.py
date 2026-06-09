"""
sirius_mail_gatilhos.py - Gatilhos de E-mail para o Sirius

Integra o SiriusEmailManager ao fluxo principal do Sirius (main.py) com:

  GATILHO 1 — Startup
      Ao iniciar, verifica e-mails em background e fala o resultado.

  GATILHO 2 — Texto livre
      Detecta intenção de e-mail na fala/digitação do usuário e responde.

Uso no main.py:
    from sirius_mail_gatilhos import SiriusMailGatilhos

    # Em SiriusAppPrincipal.__init__(), após _iniciar_servidor():
    self._mail = SiriusMailGatilhos(cerebro=self.cerebro, ui=self)
    self._mail.gatilho_startup()

    # Em enviar_texto_manual(), antes de passar para o cérebro:
    if self._mail.interceptar_texto(texto_original):
        return   # o gatilho já tratou e respondeu
"""

import re
import threading
import time
from typing import Optional, Callable

# ---------------------------------------------------------------------------
# Palavras-chave que indicam intenção de e-mail no texto livre
# ---------------------------------------------------------------------------

# O usuário quer SABER sobre e-mails (leitura)
_PADROES_LEITURA = [
    r"\bemail(s)?\b",
    r"\be[-\s]?mail(s)?\b",
    r"\bcaixa\s+(de\s+entrada|postal)\b",
    r"\bmensagem(ns)?\b",
    r"\bcorrei?o\b",
    r"\binbox\b",
    r"\bnão\s+li(do|da|dos|das)?\b",
    r"\btem\s+algo\b",
    r"\bchegou\s+algo\b",
    r"\balguma?\s+novidade\b",
]

# O usuário quer que o Sirius FALE sobre o assunto
_PADROES_FALA = [
    r"\bleia?\b",
    r"\bme\s+(conta|fala|diz)\b",
    r"\bmostr[ae]\b",
    r"\btem\b",
    r"\bcheg(ou|aram)\b",
    r"\bverifica?\b",
    r"\bconfira?\b",
    r"\bchec[ak]\b",
]

_RE_LEITURA = re.compile("|".join(_PADROES_LEITURA), re.IGNORECASE)
_RE_FALA    = re.compile("|".join(_PADROES_FALA),    re.IGNORECASE)


def _detectar_intencao_email(texto: str) -> bool:
    """
    Retorna True se o texto indica intenção relacionada a e-mail.

    Exemplos que ativam:
        "tem algum email?"
        "Sirius, verifica minha caixa de entrada"
        "chegou alguma mensagem?"
        "leia meus emails"
        "tem algo novo no correio?"
        "checa o inbox"

    Exemplos que NÃO ativam:
        "que horas são?"
        "me conta sobre python"       ← 'conta' sem e-mail junto não basta
    """
    tem_palavra_email = bool(_RE_LEITURA.search(texto))
    if not tem_palavra_email:
        return False

    # Se tem palavra de e-mail, qualquer frase já é suficiente para ativar.
    # O segundo padrão (_FALA) serve como reforço mas não é obrigatório.
    return True


# ---------------------------------------------------------------------------
# Classe principal de gatilhos
# ---------------------------------------------------------------------------

class SiriusMailGatilhos:
    """
    Gerencia os dois gatilhos de e-mail do Sirius:
      - Startup: verifica ao iniciar e fala o resultado
      - Texto:   intercepta frases do usuário sobre e-mail

    Args:
        cerebro:        instância de SiriusCerebro (para falar e memória)
        ui:             instância de SiriusAppPrincipal (para log na UI)
        user_id:        identificador do usuário (LGPD)
        falar_fn:       função alternativa de fala (opcional)
        log_sirius_fn:  função alternativa de log na UI (opcional)
    """

    def __init__(
        self,
        cerebro=None,
        ui=None,
        user_id: str = "carlos",
        falar_fn: Optional[Callable] = None,
        log_sirius_fn: Optional[Callable] = None,
    ):
        self.cerebro       = cerebro
        self.ui            = ui
        self.user_id       = user_id
        self._falar_fn     = falar_fn
        self._log_sirius_fn = log_sirius_fn

        self._manager      = None   # SiriusEmailManager criado sob demanda
        self._lock_manager = threading.Lock()
        self._startup_feito = False

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def gatilho_startup(self, atraso_segundos: float = 5.0):
        """
        GATILHO 1 — Startup

        Chame este método em SiriusAppPrincipal.__init__() após os
        subsistemas iniciarem. Aguarda `atraso_segundos` para não
        competir com a inicialização da UI e do áudio, depois verifica
        e-mails em background e fala o resultado.

        Args:
            atraso_segundos: tempo de espera antes de verificar (padrão 5s)
        """
        def _executar():
            time.sleep(atraso_segundos)
            print("[MAIL-GATILHO]: Executando verificação de startup...")
            self._verificar_e_falar(contexto="startup")
            self._startup_feito = True

        threading.Thread(target=_executar, daemon=True, name="mail-startup").start()

    def interceptar_texto(self, texto: str) -> bool:
        """
        GATILHO 2 — Texto livre

        Chame antes de passar o texto para cerebro.processar().
        Retorna True se o gatilho foi ativado (o caller deve fazer return).
        Retorna False se o texto não é sobre e-mail (continua normal).

        Args:
            texto: texto digitado ou falado pelo usuário (sem wake word)

        Exemplo em enviar_texto_manual():
            if self._mail.interceptar_texto(texto_original):
                return
        """
        if not _detectar_intencao_email(texto):
            return False

        print(f"[MAIL-GATILHO]: Intenção de e-mail detectada: '{texto}'")

        def _executar():
            self._verificar_e_falar(contexto="pergunta")

        threading.Thread(target=_executar, daemon=True, name="mail-pergunta").start()
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _obter_manager(self):
        """Cria o SiriusEmailManager uma única vez (lazy, thread-safe)."""
        with self._lock_manager:
            if self._manager is None:
                try:
                    from sirius_mail import SiriusEmailManager
                    memoria = getattr(self.cerebro, "memoria", None)
                    self._manager = SiriusEmailManager(
                        memoria=memoria,
                        user_id=self.user_id,
                    )
                except Exception as e:
                    print(f"[MAIL-GATILHO]: Erro ao criar SiriusEmailManager: {e}")
                    return None
            return self._manager

    def _verificar_e_falar(self, contexto: str = "geral"):
        """
        Conecta, processa e-mails, monta resposta e fala/exibe na UI.

        Args:
            contexto: "startup" ou "pergunta" — afeta o tom da resposta
        """
        manager = self._obter_manager()
        if not manager:
            self._falar("Não consegui acessar o gerenciador de e-mail.")
            return

        if not manager.conectar():
            self._falar(
                "Não consegui conectar ao servidor de e-mail. "
                "Verifique as credenciais no arquivo de configuração."
            )
            return

        try:
            resultado = manager.processar_emails()
            resposta  = self._montar_resposta(resultado, contexto)
            self._falar(resposta)
        finally:
            manager.desconectar()

    def _montar_resposta(self, resultado: dict, contexto: str) -> str:
        """
        Transforma o dict de resultado em uma frase natural para o Sirius falar.

        Exemplos de saída:
          Startup, sem e-mails:
            "Carlos, bom dia! Sua caixa de entrada está limpa. Nenhum e-mail não lido."

          Startup, com e-mails normais:
            "Carlos, você tem 2 e-mails não lidos. Nenhum parece urgente."

          Startup, com urgente:
            "Carlos, atenção! Você recebeu um e-mail importante de joao@empresa.com
             com o assunto Reunião amanhã. Veja assim que puder."

          Pergunta, sem e-mails:
            "Sua caixa está vazia. Nenhum e-mail não lido no momento."

          Pergunta, com e-mails:
            "Você tem 3 e-mails não lidos. O mais recente é de maria@gmail.com
             com o assunto Orçamento aprovado, prioridade alta."
        """
        emails          = resultado.get("emails", [])
        prioridade_max  = resultado.get("prioridade_maxima", "nenhuma")
        requer_atencao  = resultado.get("requer_interrupcao", False)
        quantidade      = len(emails)

        saudacao = "Carlos, " if contexto == "startup" else ""

        # ── Sem e-mails ──────────────────────────────────────────────
        if quantidade == 0:
            if contexto == "startup":
                return (
                    f"{saudacao}sua caixa de entrada está limpa. "
                    "Nenhum e-mail não lido. Pode focar no que está fazendo."
                )
            return "Sua caixa está vazia. Nenhum e-mail não lido no momento."

        # ── Com e-mails urgentes ──────────────────────────────────────
        if requer_atencao:
            urgente = max(emails, key=lambda e: e.get("score_prioridade", 0))
            remetente = urgente.get("remetente", "alguém")
            assunto   = urgente.get("assunto",   "sem assunto")
            resumo    = urgente.get("resumo",    "")

            frase = (
                f"{saudacao}atenção! Você recebeu um e-mail importante "
                f"de {remetente}, com o assunto: {assunto}."
            )
            if resumo:
                frase += f" Resumo: {resumo}"
            if quantidade > 1:
                frase += f" Além deste, há mais {quantidade - 1} e-mail(s) não lido(s)."
            return frase

        # ── Com e-mails normais ───────────────────────────────────────
        mais_recente = emails[-1]
        remetente = mais_recente.get("remetente", "alguém")
        assunto   = mais_recente.get("assunto",   "sem assunto")

        if quantidade == 1:
            return (
                f"{saudacao}você tem 1 e-mail não lido, "
                f"de {remetente}, assunto: {assunto}. "
                "Nenhum parece urgente."
            )

        return (
            f"{saudacao}você tem {quantidade} e-mails não lidos. "
            f"O mais recente é de {remetente}, assunto: {assunto}. "
            "Nenhum parece urgente."
        )

    def _falar(self, texto: str):
        """
        Fala o texto e exibe na UI do Sirius.

        Tenta na ordem:
          1. Função customizada passada no construtor
          2. cerebro → worker.audio.falar (caminho padrão do main.py)
          3. ui.log_sirius (só visual, sem áudio)
          4. print (fallback terminal)
        """
        print(f"[MAIL-GATILHO]: {texto}")

        # Exibe na UI (log visual)
        log_fn = self._log_sirius_fn
        if log_fn is None and self.ui and hasattr(self.ui, "log_sirius"):
            log_fn = self.ui.log_sirius
        if log_fn:
            try:
                log_fn(texto)
            except Exception as e:
                print(f"[MAIL-GATILHO]: Erro ao logar na UI: {e}")

        # Fala em áudio
        falar_fn = self._falar_fn
        if falar_fn is None:
            try:
                falar_fn = self.ui.worker.audio.falar
            except AttributeError:
                pass
        if falar_fn is None:
            try:
                falar_fn = self.cerebro.falar
            except AttributeError:
                pass

        if falar_fn:
            try:
                falar_fn(texto)
            except Exception as e:
                print(f"[MAIL-GATILHO]: Erro ao falar: {e}")