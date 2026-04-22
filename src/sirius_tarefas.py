"""
sirius_tarefas.py — Gerenciador de tarefas paralelas do Sirius

Permite que o Sirius execute múltiplas coisas ao mesmo tempo:

  PARALELO INTERNO (automático, sempre rodando em background):
    - Aprendizado autônomo (autodidata + leitor)
    - Monitor proativo (bateria, CPU, RAM, lembretes)
    - Wake word
    - Scheduler de retreino

  PARALELO SOLICITADO PELO USUÁRIO:
    "sirius, abre o chrome e pesquisa sobre python"
    → abre chrome E pesquisa ao mesmo tempo

    "sirius, manda mensagem pra mae e cria um arquivo de notas"
    → envia mensagem E cria arquivo ao mesmo tempo

    "sirius, reproduz música enquanto pesquisa sobre vulcões"
    → duas tarefas em paralelo com feedback de cada uma

  FILA DE TAREFAS (sequencial quando necessário):
    "sirius, primeiro fecha o discord, depois abre o chrome"
    → executa em ordem, uma após a outra

  CANCELAMENTO:
    "sirius, cancela a tarefa"
    "sirius, para tudo"
    "sirius, status das tarefas"

Integração no cerebro.py:
    from sirius_tarefas import GerenciadorTarefas
    self._tarefas = GerenciadorTarefas(cerebro=self)

    # Ao processar um comando com múltiplas ações:
    resultado = self._tarefas.processar_paralelo(comando, contexto)
"""

import os
import sys
import re
import time
import threading
import uuid
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)


# ---------------------------------------------------------------------------
# Estado e prioridade de tarefa
# ---------------------------------------------------------------------------

class EstadoTarefa(Enum):
    PENDENTE   = "pendente"
    RODANDO    = "rodando"
    CONCLUIDA  = "concluída"
    FALHOU     = "falhou"
    CANCELADA  = "cancelada"

class PrioridadeTarefa(Enum):
    ALTA   = 1   # comandos do usuário, alertas proativos
    NORMAL = 2   # pesquisas, geração de conteúdo
    BAIXA  = 3   # aprendizado autônomo, retreino


# ---------------------------------------------------------------------------
# Tarefa individual
# ---------------------------------------------------------------------------

class Tarefa:
    """
    Unidade de trabalho que pode rodar em paralelo.

    Cada tarefa tem:
    - Um ID único
    - Uma função a executar
    - Um callback de conclusão (opcional)
    - Estado e resultado
    """

    def __init__(self, nome: str, fn: Callable, args: tuple = (),
                 kwargs: dict = None, prioridade: PrioridadeTarefa = PrioridadeTarefa.NORMAL,
                 callback_fim: Callable = None, timeout: float = 60.0):
        self.id          = str(uuid.uuid4())[:8]
        self.nome        = nome
        self._fn         = fn
        self._args       = args
        self._kwargs     = kwargs or {}
        self.prioridade  = prioridade
        self.callback_fim = callback_fim
        self.timeout     = timeout

        self.estado      = EstadoTarefa.PENDENTE
        self.resultado   = None
        self.erro        = None
        self.criada_em   = datetime.now()
        self.iniciada_em : Optional[datetime] = None
        self.concluida_em: Optional[datetime] = None
        self._thread     : Optional[threading.Thread] = None
        self._cancelada  = threading.Event()

    def iniciar(self):
        """Lança a tarefa em uma thread própria."""
        self.estado      = EstadoTarefa.RODANDO
        self.iniciada_em = datetime.now()
        self._thread     = threading.Thread(
            target=self._executar,
            daemon=True,
            name=f"Tarefa-{self.id}-{self.nome[:20]}"
        )
        self._thread.start()

    def _executar(self):
        try:
            self.resultado    = self._fn(*self._args, **self._kwargs)
            if not self._cancelada.is_set():
                self.estado   = EstadoTarefa.CONCLUIDA
        except Exception as e:
            self.erro         = str(e)
            self.estado       = EstadoTarefa.FALHOU
            print(f"[TAREFA {self.id}]: Falhou — {e}")
        finally:
            self.concluida_em = datetime.now()
            if self.callback_fim and not self._cancelada.is_set():
                try:
                    self.callback_fim(self)
                except Exception:
                    pass

    def cancelar(self):
        self._cancelada.set()
        self.estado = EstadoTarefa.CANCELADA

    def aguardar(self, timeout: float = None) -> bool:
        """Aguarda a conclusão. Retorna True se concluiu, False se timeout."""
        if self._thread:
            self._thread.join(timeout=timeout or self.timeout)
        return self.estado == EstadoTarefa.CONCLUIDA

    @property
    def duracao(self) -> float:
        if self.iniciada_em and self.concluida_em:
            return (self.concluida_em - self.iniciada_em).total_seconds()
        if self.iniciada_em:
            return (datetime.now() - self.iniciada_em).total_seconds()
        return 0.0

    @property
    def esta_viva(self) -> bool:
        return self.estado in (EstadoTarefa.PENDENTE, EstadoTarefa.RODANDO)

    def __repr__(self):
        return f"Tarefa({self.nome!r}, {self.estado.value}, id={self.id})"


# ---------------------------------------------------------------------------
# Detector de comandos paralelos
# ---------------------------------------------------------------------------

class DetectorParalelo:
    """
    Detecta quando um comando contém múltiplas ações a executar.

    Padrões reconhecidos:
      "X e Y"              → paralelo (exceto quando são partes de uma frase)
      "X enquanto Y"       → paralelo
      "X ao mesmo tempo que Y" → paralelo
      "primeiro X depois Y" → sequencial
      "X e depois Y"       → sequencial
      "X e então Y"        → sequencial
    """

    # Conectivos de paralelismo
    _PARALELO = [
        r"\s+e\s+(?:também\s+)?(?:abre?|fecha?|manda?|cria?|pesquisa?|reproduz|toca?|mostra?|calcula?|verifica?)",
        r"\s+enquanto\s+",
        r"\s+ao\s+mesmo\s+tempo\s+(?:que|em\s+que)\s+",
        r"\s+simultaneamente\s+",
        r"\s+junto\s+com\s+",
    ]

    # Conectivos de sequência
    _SEQUENCIAL = [
        r"\s+(?:e\s+)?depois\s+",
        r"\s+(?:e\s+)?então\s+",
        r"\s+(?:e\s+)?em\s+seguida\s+",
        r"\s+após\s+isso\s+",
        r"primeiro\s+.+?\s+depois\s+",
        r"\s+e\s+depois\s+",
    ]

    def analisar(self, texto: str) -> dict:
        """
        Retorna:
            {"tipo": "simples",     "partes": [texto]}
            {"tipo": "paralelo",    "partes": [a, b, ...]}
            {"tipo": "sequencial",  "partes": [a, b, ...]}
        """
        t = texto.lower().strip()

        # Verifica sequencial primeiro (mais específico)
        for padrao in self._SEQUENCIAL:
            m = re.search(padrao, t, re.IGNORECASE)
            if m:
                partes = self._dividir(texto, m.start(), m.end())
                if len(partes) >= 2 and all(len(p.strip()) > 5 for p in partes):
                    return {"tipo": "sequencial", "partes": partes}

        # Verifica paralelo
        for padrao in self._PARALELO:
            m = re.search(padrao, t, re.IGNORECASE)
            if m:
                partes = self._dividir(texto, m.start(), m.end())
                if len(partes) >= 2 and all(len(p.strip()) > 5 for p in partes):
                    return {"tipo": "paralelo", "partes": partes}

        return {"tipo": "simples", "partes": [texto]}

    def _dividir(self, texto: str, inicio: int, fim: int) -> list[str]:
        """Divide o texto em dois no ponto do conector."""
        parte1 = texto[:inicio].strip()
        parte2 = texto[fim:].strip()
        return [p for p in [parte1, parte2] if p]


# ---------------------------------------------------------------------------
# Gerenciador principal de tarefas
# ---------------------------------------------------------------------------

class GerenciadorTarefas:
    """
    Orquestra a execução paralela e sequencial de tarefas no Sirius.

    Responsabilidades:
    1. Detectar quando um comando tem múltiplas ações
    2. Criar e executar tarefas em paralelo ou sequência
    3. Coletar resultados e montar resposta unificada
    4. Controlar limite de tarefas simultâneas
    5. Permitir cancelamento de tarefas em andamento
    """

    MAX_PARALELAS = 4   # máximo de tarefas do usuário ao mesmo tempo

    def __init__(self, cerebro=None, callback_log: Callable = None):
        self._cerebro      = cerebro
        self._callback_log = callback_log
        self._tarefas      : dict[str, Tarefa] = {}   # id → tarefa
        self._lock         = threading.Lock()
        self._detector     = DetectorParalelo()

        # Limpeza periódica de tarefas antigas
        threading.Thread(target=self._limpar_antigas, daemon=True).start()

    # -----------------------------------------------------------------------
    # API principal — chamado pelo cerebro.py
    # -----------------------------------------------------------------------

    def processar(self, comando: str, executar_fn: Callable) -> str | None:
        """
        Analisa o comando. Se tiver múltiplas ações, executa em paralelo/sequência.
        Se for simples, retorna None (cerebro processa normalmente).

        executar_fn: função do cerebro que processa um comando simples
                     Assinatura: fn(texto) → str
        """
        analise = self._detector.analisar(comando)

        if analise["tipo"] == "simples":
            return None   # deixa o cerebro processar normalmente

        partes = analise["partes"]
        modo   = analise["tipo"]

        print(f"\033[94m[TAREFAS]: {modo.upper()} — {len(partes)} ações detectadas\033[0m")
        for i, p in enumerate(partes, 1):
            print(f"  [{i}] {p}")

        if modo == "paralelo":
            return self._executar_paralelo(partes, executar_fn)
        else:
            return self._executar_sequencial(partes, executar_fn)

    # -----------------------------------------------------------------------
    # Execução paralela
    # -----------------------------------------------------------------------

    def _executar_paralelo(self, partes: list[str], executar_fn: Callable) -> str:
        """
        Executa todas as partes simultaneamente e aguarda todas terminarem.
        Retorna resposta unificada.
        """
        # Verifica limite
        ativas = sum(1 for t in self._tarefas.values() if t.esta_viva)
        if ativas + len(partes) > self.MAX_PARALELAS:
            return (
                f"Já tenho {ativas} tarefa(s) rodando, chefe. "
                f"Máximo é {self.MAX_PARALELAS} simultâneas. "
                "Use 'status das tarefas' para ver o que está rodando."
            )

        tarefas_criadas = []
        for parte in partes:
            t = Tarefa(
                nome        = parte[:40],
                fn          = executar_fn,
                args        = (parte,),
                prioridade  = PrioridadeTarefa.ALTA,
                timeout     = 30.0,
            )
            with self._lock:
                self._tarefas[t.id] = t
            t.iniciar()
            tarefas_criadas.append(t)

        # Aguarda todas (máx 30s)
        for t in tarefas_criadas:
            t.aguardar(timeout=30.0)

        # Monta resposta
        return self._montar_resposta_paralela(tarefas_criadas)

    def _montar_resposta_paralela(self, tarefas: list[Tarefa]) -> str:
        resultados = []
        for t in tarefas:
            if t.estado == EstadoTarefa.CONCLUIDA and t.resultado:
                resultados.append(f"✓ {t.resultado}")
            elif t.estado == EstadoTarefa.FALHOU:
                resultados.append(f"✗ '{t.nome[:30]}' falhou: {t.erro}")
            else:
                resultados.append(f"✓ '{t.nome[:30]}' concluído.")
        return "\n".join(resultados)

    # -----------------------------------------------------------------------
    # Execução sequencial
    # -----------------------------------------------------------------------

    def _executar_sequencial(self, partes: list[str], executar_fn: Callable) -> str:
        """
        Executa as partes em ordem, uma após a outra.
        Retorna resposta unificada após todas concluírem.
        """
        resultados = []
        for i, parte in enumerate(partes, 1):
            print(f"\033[94m[TAREFAS]: Executando [{i}/{len(partes)}]: {parte[:50]}\033[0m")
            try:
                resultado = executar_fn(parte)
                resultados.append(f"✓ [{i}] {resultado or parte[:40]}")
            except Exception as e:
                resultados.append(f"✗ [{i}] Falhou: {e}")
        return "\n".join(resultados)

    # -----------------------------------------------------------------------
    # Tarefas em background — lançadas e esquecidas (fire & forget)
    # -----------------------------------------------------------------------

    def lancar_background(self, nome: str, fn: Callable, args: tuple = (),
                           kwargs: dict = None,
                           prioridade: PrioridadeTarefa = PrioridadeTarefa.NORMAL,
                           callback_fim: Callable = None) -> Tarefa:
        """
        Lança uma tarefa em background sem bloquear.
        Usada internamente pelo scheduler, agentes, coordenador, etc.
        """
        t = Tarefa(
            nome         = nome,
            fn           = fn,
            args         = args,
            kwargs       = kwargs,
            prioridade   = prioridade,
            callback_fim = callback_fim,
        )
        with self._lock:
            self._tarefas[t.id] = t
        t.iniciar()
        return t

    # -----------------------------------------------------------------------
    # Cancelamento
    # -----------------------------------------------------------------------

    def cancelar_todas(self) -> str:
        """Cancela todas as tarefas do usuário em andamento."""
        canceladas = 0
        with self._lock:
            for t in self._tarefas.values():
                if t.esta_viva and t.prioridade == PrioridadeTarefa.ALTA:
                    t.cancelar()
                    canceladas += 1
        if canceladas == 0:
            return "Nenhuma tarefa ativa para cancelar, chefe."
        return f"✓ {canceladas} tarefa(s) cancelada(s)."

    def cancelar_ultima(self) -> str:
        """Cancela a tarefa do usuário mais recente."""
        with self._lock:
            ativas = [
                t for t in self._tarefas.values()
                if t.esta_viva and t.prioridade == PrioridadeTarefa.ALTA
            ]
        if not ativas:
            return "Nenhuma tarefa ativa para cancelar, chefe."
        ultima = max(ativas, key=lambda t: t.criada_em)
        ultima.cancelar()
        return f"✓ Tarefa '{ultima.nome[:40]}' cancelada."

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    def status(self) -> str:
        """Retorna status formatado de todas as tarefas recentes."""
        with self._lock:
            recentes = sorted(
                self._tarefas.values(),
                key=lambda t: t.criada_em,
                reverse=True
            )[:8]

        if not recentes:
            return "Nenhuma tarefa registrada, chefe."

        linhas = ["Tarefas recentes:"]
        for t in recentes:
            icone = {
                EstadoTarefa.PENDENTE:  "⏳",
                EstadoTarefa.RODANDO:   "▶",
                EstadoTarefa.CONCLUIDA: "✓",
                EstadoTarefa.FALHOU:    "✗",
                EstadoTarefa.CANCELADA: "○",
            }.get(t.estado, "?")
            dur = f" {t.duracao:.1f}s" if t.iniciada_em else ""
            linhas.append(f"  {icone} [{t.id}] {t.nome[:40]}{dur}")

        ativas = sum(1 for t in self._tarefas.values() if t.esta_viva)
        if ativas:
            linhas.append(f"\n  {ativas} tarefa(s) em execução agora.")

        return "\n".join(linhas)

    def n_ativas(self) -> int:
        return sum(1 for t in self._tarefas.values() if t.esta_viva)

    # -----------------------------------------------------------------------
    # Limpeza periódica
    # -----------------------------------------------------------------------

    def _limpar_antigas(self):
        """Remove tarefas concluídas com mais de 10 minutos."""
        while True:
            time.sleep(300)
            agora = datetime.now()
            with self._lock:
                ids_remover = [
                    id_ for id_, t in self._tarefas.items()
                    if not t.esta_viva and
                    t.concluida_em and
                    (agora - t.concluida_em).total_seconds() > 600
                ]
                for id_ in ids_remover:
                    del self._tarefas[id_]

    # -----------------------------------------------------------------------
    # Comandos de voz
    # -----------------------------------------------------------------------

    _TRIGGERS_STATUS = {
        "status das tarefas", "status de tarefas", "o que esta rodando",
        "o que voce esta fazendo", "quantas tarefas", "tarefas ativas",
        "o que ta rodando", "mostra tarefas",
    }
    _TRIGGERS_CANCELAR_TUDO = {
        "cancela tudo", "para tudo", "cancela todas as tarefas",
        "para todas as tarefas", "interrompe tudo",
    }
    _TRIGGERS_CANCELAR_ULT = {
        "cancela a tarefa", "cancela essa tarefa", "para a tarefa",
        "interrompe a tarefa", "cancela o ultimo",
    }

    def e_comando_tarefa(self, texto: str) -> bool:
        t = texto.lower()
        return (
            any(tr in t for tr in self._TRIGGERS_STATUS) or
            any(tr in t for tr in self._TRIGGERS_CANCELAR_TUDO) or
            any(tr in t for tr in self._TRIGGERS_CANCELAR_ULT)
        )

    def processar_comando(self, texto: str) -> str:
        t = texto.lower()
        if any(tr in t for tr in self._TRIGGERS_STATUS):
            return self.status()
        if any(tr in t for tr in self._TRIGGERS_CANCELAR_TUDO):
            return self.cancelar_todas()
        if any(tr in t for tr in self._TRIGGERS_CANCELAR_ULT):
            return self.cancelar_ultima()
        return "Comando de tarefa não reconhecido."