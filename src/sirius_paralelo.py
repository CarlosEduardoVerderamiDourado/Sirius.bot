"""
sirius_paralelo.py — Execução paralela de tarefas do Sirius (versão unificada)

Combina o melhor de sirius_paralelo.py e sirius_tarefas.py:

  DO sirius_paralelo.py (usuário):
    + Pipeline com passagem de resultado entre etapas
    + GrupoParalelo com executar_background()
    + Resposta IMEDIATA ao usuário + notificação posterior por voz
    + Cancelar tarefa por número ou ID
    + Detecção por múltiplos verbos (_VERBOS_ACAO)
    + background() como API pública explícita
    + registrar_callbacks() público

  DO sirius_tarefas.py (nosso):
    + PrioridadeTarefa (ALTA / NORMAL / BAIXA)
    + MAX_PARALELAS — limite de tarefas simultâneas do usuário
    + Limpeza periódica automática de tarefas antigas (5min)
    + _processar_acao_simples — evita recursão infinita no cerebro

  TIPOS DE EXECUÇÃO:
  ─────────────────────────────────────────────────────────────
  background  → roda em paralelo, sem bloquear a conversa
  paralelo    → múltiplas tarefas independentes ao mesmo tempo
  pipeline    → sequência de etapas passando resultado entre si

  EXEMPLOS DE USO POR VOZ:
  ─────────────────────────────────────────────────────────────
  "abre o chrome e o spotify ao mesmo tempo"
  "pesquisa sobre python e sobre machine learning"
  "baixa o arquivo e quando terminar abre ele"
  "primeiro fecha o discord, depois abre o chrome"
  "me avisa quando terminar de indexar"

  COMANDOS DE CONTROLE:
  ─────────────────────────────────────────────────────────────
  "o que está rodando?"         → lista tarefas ativas
  "cancela a tarefa 2"          → cancela tarefa por número
  "cancela tudo"                → cancela todas as tarefas

  INTEGRAÇÃO NO cerebro.py:
  ─────────────────────────────────────────────────────────────
    self._paralelo = SiriusParalelo(callback_log=..., callback_falar=...)

    if self._paralelo.e_comando_paralelo(comando):
        return self._paralelo.processar_comando(comando)

    if self._paralelo.detectar_paralelismo(comando):
        return self._paralelo.processar_paralelo(comando, self)

    self._paralelo.background(
        fn=lambda: pesquisar(tema),
        nome="Pesquisa Python",
        callback=lambda r: audio.falar(f"Pronto: {r[:80]}")
    )
"""

import os
import sys
import re
import time
import threading
import uuid
import unicodedata
from typing import Callable, Optional, Any
from enum import Enum
from datetime import datetime

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)


def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EstadoTarefa(Enum):
    PENDENTE  = "pendente"
    RODANDO   = "rodando"
    CONCLUIDA = "concluída"
    FALHOU    = "falhou"
    CANCELADA = "cancelada"

class PrioridadeTarefa(Enum):
    ALTA   = 1   # comandos diretos do usuário
    NORMAL = 2   # pesquisas, geração de conteúdo
    BAIXA  = 3   # aprendizado autônomo, retreino


# ---------------------------------------------------------------------------
# Tarefa — unidade básica de trabalho
# ---------------------------------------------------------------------------

class Tarefa:
    """Unidade de trabalho que pode rodar em paralelo."""

    def __init__(self, fn: Callable, nome: str = "",
                 callback: Callable = None, timeout: float = 120.0,
                 prioridade: PrioridadeTarefa = PrioridadeTarefa.NORMAL):
        self.id           = str(uuid.uuid4())[:8]
        self.fn           = fn
        self.nome         = nome or f"Tarefa {self.id}"
        self.callback     = callback
        self.timeout      = timeout
        self.prioridade   = prioridade
        self.estado       = EstadoTarefa.PENDENTE
        self.resultado    = None
        self.erro         = None
        self.criada_em    = datetime.now()
        self.iniciada_em  : Optional[datetime] = None
        self.concluida_em : Optional[datetime] = None
        self._thread      : Optional[threading.Thread] = None
        self._cancelar    = threading.Event()

    def _executar(self):
        self.estado      = EstadoTarefa.RODANDO
        self.iniciada_em = datetime.now()
        try:
            self.resultado = self.fn()
            if not self._cancelar.is_set():
                self.estado = EstadoTarefa.CONCLUIDA
        except Exception as e:
            self.erro   = str(e)
            self.estado = EstadoTarefa.FALHOU
            print(f"[PARALELO]: Tarefa '{self.nome}' falhou: {e}")
        finally:
            self.concluida_em = datetime.now()
            if self.callback and not self._cancelar.is_set():
                try:
                    self.callback(self.resultado)
                except Exception as ce:
                    print(f"[PARALELO]: Callback de '{self.nome}' falhou: {ce}")

    def iniciar(self):
        self._thread = threading.Thread(
            target=self._executar,
            daemon=True,
            name=f"SiriusTarefa-{self.id}"
        )
        self._thread.start()

    def cancelar(self):
        self._cancelar.set()
        self.estado = EstadoTarefa.CANCELADA

    def aguardar(self, timeout: float = None) -> Any:
        if self._thread:
            self._thread.join(timeout=timeout or self.timeout)
        return self.resultado

    @property
    def ativa(self) -> bool:
        return self.estado in (EstadoTarefa.PENDENTE, EstadoTarefa.RODANDO)

    @property
    def duracao_s(self) -> float:
        if self.iniciada_em is None:
            return 0.0
        fim = self.concluida_em or datetime.now()
        return (fim - self.iniciada_em).total_seconds()

    def __str__(self):
        return f"[{self.id}] {self.nome} ({self.estado.value})"


# ---------------------------------------------------------------------------
# GrupoParalelo — várias tarefas independentes ao mesmo tempo
# ---------------------------------------------------------------------------

class GrupoParalelo:
    """
    Executa múltiplas tarefas independentes em paralelo.
    Exemplo: "abre o chrome e o spotify"
    """

    def __init__(self, tarefas: list[Tarefa], callback_grupo: Callable = None):
        self.tarefas        = tarefas
        self.callback_grupo = callback_grupo  # chamado quando TODAS terminam

    def executar(self) -> list[Any]:
        """Dispara todas em paralelo e aguarda os resultados."""
        for t in self.tarefas:
            t.iniciar()
        resultados = [t.aguardar() for t in self.tarefas]
        if self.callback_grupo:
            try:
                self.callback_grupo(resultados)
            except Exception as e:
                print(f"[PARALELO]: Callback do grupo falhou: {e}")
        return resultados

    def executar_background(self):
        """Dispara tudo e retorna imediatamente — não bloqueia."""
        threading.Thread(target=self.executar, daemon=True,
                         name="SiriusGrupo").start()


# ---------------------------------------------------------------------------
# Pipeline — sequência de etapas com passagem de resultado
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Executa etapas em sequência, passando o resultado de uma para a próxima.
    Exemplo: "baixa o arquivo e quando terminar abre ele"
    """

    def __init__(self, etapas: list[Callable], nomes: list[str] = None,
                 callback_final: Callable = None):
        self.etapas         = etapas
        self.nomes          = nomes or [f"Etapa {i+1}" for i in range(len(etapas))]
        self.callback_final = callback_final
        self._cancelar      = threading.Event()

    def executar(self, valor_inicial: Any = None) -> Any:
        """Executa cada etapa passando o resultado para a próxima."""
        resultado = valor_inicial
        for i, (fn, nome) in enumerate(zip(self.etapas, self.nomes)):
            if self._cancelar.is_set():
                print(f"[PIPELINE]: Cancelado em '{nome}'.")
                return None
            print(f"[PIPELINE]: Etapa {i+1}/{len(self.etapas)}: {nome}...")
            try:
                resultado = fn(resultado) if resultado is not None else fn()
            except Exception as e:
                print(f"[PIPELINE]: Etapa '{nome}' falhou: {e}")
                return None

        if self.callback_final:
            try:
                self.callback_final(resultado)
            except Exception:
                pass
        return resultado

    def executar_background(self, valor_inicial: Any = None):
        threading.Thread(target=self.executar, args=(valor_inicial,),
                         daemon=True, name="SiriusPipeline").start()

    def cancelar(self):
        self._cancelar.set()


# ---------------------------------------------------------------------------
# Detector de paralelismo
# ---------------------------------------------------------------------------

# Conectivos que indicam paralelismo (ao mesmo tempo)
_PARALELO_TRIGGERS = {
    "e ao mesmo tempo", "ao mesmo tempo", "simultaneamente",
    "em paralelo", "junto com", "ao mesmo momento",
    "e tambem", "e também", "alem disso", "além disso",
    " e abrir ", " e fechar ", " e pesquisar ", " e abra ", " e feche ",
}

# Conectivos que indicam pipeline (sequencial dependente)
_PIPELINE_TRIGGERS = {
    "e quando terminar", "depois abre", "e depois", "e em seguida",
    "assim que terminar", "quando concluir", "primeiro", "depois que",
    "e então", "e entao", "em seguida",
}

# Verbos de ação — "abre o chrome e o spotify" → dois verbos implícitos
_VERBOS_ACAO = {
    "abre", "abrir", "fecha", "fechar", "pesquisa", "pesquisar",
    "baixa", "baixar", "copia", "copiar", "move", "mover",
    "instala", "instalar", "executa", "executar", "toca", "tocar",
    "reproduz", "reproduzir", "cria", "criar", "salva", "salvar",
}


def detectar_tipo_execucao(texto: str) -> str | None:
    """
    Retorna 'paralelo' | 'pipeline' | None.
    Pipeline tem prioridade — é mais específico.
    """
    t = _norm(texto)

    if any(trigger in t for trigger in _PIPELINE_TRIGGERS):
        return "pipeline"

    if any(trigger in t for trigger in _PARALELO_TRIGGERS):
        return "paralelo"

    # Múltiplos verbos de ação na mesma frase
    verbos = [v for v in _VERBOS_ACAO if re.search(r'\b' + v + r'\b', t)]
    if len(verbos) >= 2:
        return "paralelo"

    return None


def extrair_sub_comandos(texto: str, tipo: str) -> list[str]:
    """
    Divide o comando em sub-comandos.
    "abre o chrome e o spotify" → ["abre o chrome", "abre o spotify"]
    "primeiro fecha, depois abre" → ["fecha", "abre"]
    """
    t = texto.lower().strip()

    if tipo == "pipeline":
        for trigger in sorted(_PIPELINE_TRIGGERS, key=len, reverse=True):
            if trigger in t:
                partes = t.split(trigger, 1)
                return [p.strip() for p in partes if p.strip()]
        return [t]

    if tipo == "paralelo":
        # Substitui todos os triggers por marcador
        for trigger in sorted(_PARALELO_TRIGGERS, key=len, reverse=True):
            if trigger in t:
                t = t.replace(trigger, " __SPLIT__ ")

        partes = [p.strip() for p in t.split("__SPLIT__") if p.strip()]
        if len(partes) >= 2:
            return partes

        # Tenta dividir por " e " antes de verbos de ação
        partes = re.split(
            r'\s+e\s+(?=' + "|".join(_VERBOS_ACAO) + r'\b)',
            t
        )
        if len(partes) >= 2:
            return [p.strip() for p in partes]

    return [texto]


# ---------------------------------------------------------------------------
# SiriusParalelo — motor central (versão unificada)
# ---------------------------------------------------------------------------

class SiriusParalelo:
    """
    Gerencia todas as tarefas paralelas do Sirius.

    Responsabilidades:
    1. Detectar paralelismo em comandos de voz
    2. Executar múltiplas ações simultâneas ou em pipeline
    3. Notificar o usuário quando tarefas terminam
    4. Controlar limite de tarefas simultâneas (MAX_PARALELAS)
    5. Permitir cancelamento por número ou ID
    6. Limpeza automática de tarefas antigas
    """

    MAX_PARALELAS = 4   # máximo de tarefas de alta prioridade simultâneas

    def __init__(self, callback_log: Callable = None,
                 callback_falar: Callable = None):
        self._log      = callback_log
        self._falar    = callback_falar
        self._tarefas  : dict[str, Tarefa] = {}
        self._lock     = threading.Lock()

        # Limpeza automática a cada 5 minutos
        threading.Thread(target=self._loop_limpeza,
                         daemon=True, name="SiriusParaleloLimpeza").start()

    def registrar_callbacks(self, callback_log: Callable = None,
                             callback_falar: Callable = None):
        if callback_log:   self._log   = callback_log
        if callback_falar: self._falar = callback_falar

    # -----------------------------------------------------------------------
    # API pública — chamada pelo cerebro.py e outros módulos
    # -----------------------------------------------------------------------

    def background(self, fn: Callable, nome: str = "",
                   callback: Callable = None, timeout: float = 120.0,
                   prioridade: PrioridadeTarefa = PrioridadeTarefa.NORMAL) -> Tarefa:
        """
        Submete tarefa para rodar em background sem bloquear a conversa.
        Retorna imediatamente com o objeto Tarefa.
        """
        tarefa = Tarefa(fn, nome, callback, timeout, prioridade)
        with self._lock:
            self._tarefas[tarefa.id] = tarefa
        tarefa.iniciar()
        print(f"\033[94m[PARALELO]: '{nome}' iniciada (id={tarefa.id}).\033[0m")
        return tarefa

    def paralelo(self, fns_nomes: list[tuple[Callable, str]],
                 callback_grupo: Callable = None) -> GrupoParalelo:
        """
        Executa múltiplas funções em paralelo.
        fns_nomes: [(fn1, "nome1"), (fn2, "nome2"), ...]
        """
        tarefas = []
        for fn, nome in fns_nomes:
            t = Tarefa(fn, nome, prioridade=PrioridadeTarefa.ALTA)
            with self._lock:
                self._tarefas[t.id] = t
            tarefas.append(t)

        grupo = GrupoParalelo(tarefas, callback_grupo)
        grupo.executar_background()
        print(f"\033[94m[PARALELO]: Grupo → {[n for _, n in fns_nomes]}\033[0m")
        return grupo

    def pipeline(self, etapas: list[tuple[Callable, str]],
                 callback_final: Callable = None,
                 valor_inicial: Any = None) -> Pipeline:
        """
        Executa etapas em sequência, passando resultado entre elas.
        etapas: [(fn1, "nome1"), (fn2, "nome2"), ...]
        """
        fns   = [fn for fn, _ in etapas]
        nomes = [n for _, n in etapas]
        p = Pipeline(fns, nomes, callback_final)
        p.executar_background(valor_inicial)
        print(f"\033[94m[PARALELO]: Pipeline → {nomes}\033[0m")
        return p

    # -----------------------------------------------------------------------
    # Processamento de comandos de voz com paralelismo
    # -----------------------------------------------------------------------

    def detectar_paralelismo(self, texto: str) -> bool:
        return detectar_tipo_execucao(texto) is not None

    def processar_paralelo(self, comando: str, cerebro) -> str:
        """
        Divide o comando em sub-tarefas e executa.
        Retorna resposta IMEDIATA ao usuário.
        Notifica via voz quando tudo terminar.
        """
        tipo         = detectar_tipo_execucao(comando)
        sub_comandos = extrair_sub_comandos(comando, tipo)

        if len(sub_comandos) < 2:
            return cerebro.processar(comando)

        # Verifica limite de tarefas simultâneas
        n_ativas = sum(
            1 for t in self._tarefas.values()
            if t.ativa and t.prioridade == PrioridadeTarefa.ALTA
        )
        if n_ativas + len(sub_comandos) > self.MAX_PARALELAS:
            return (
                f"Já tenho {n_ativas} tarefa(s) ativa(s), chefe. "
                f"Limite é {self.MAX_PARALELAS} simultâneas. "
                "Use 'o que está rodando' para ver."
            )

        if tipo == "pipeline":
            return self._executar_pipeline(sub_comandos, cerebro)
        else:
            return self._executar_paralelo(sub_comandos, cerebro)

    def _executar_paralelo(self, sub_comandos: list[str], cerebro) -> str:
        """
        Dispara sub-comandos em paralelo.
        Retorna confirmação IMEDIATA.
        Fala o resultado quando todas terminarem.
        """
        resultados = {}
        lock_res   = threading.Lock()

        def _fazer(cmd, idx):
            try:
                r = cerebro._processar_acao_simples(cmd)
                if r is None:
                    r = cerebro.processar(f"sirius {cmd}", forcar_processamento=True)
                with lock_res:
                    resultados[idx] = r
            except Exception as e:
                with lock_res:
                    resultados[idx] = f"✗ Erro: {e}"

        threads = []
        for i, cmd in enumerate(sub_comandos):
            t = threading.Thread(target=_fazer, args=(cmd, i), daemon=True)
            t.start()
            threads.append(t)

            # Registra no controle de tarefas
            tarefa = Tarefa(
                fn=lambda c=cmd: cerebro._processar_acao_simples(c),
                nome=cmd[:40],
                prioridade=PrioridadeTarefa.ALTA
            )
            with self._lock:
                self._tarefas[tarefa.id] = tarefa
            tarefa.estado = EstadoTarefa.RODANDO
            tarefa.iniciada_em = datetime.now()

        # Resposta imediata
        nomes = [c[:30] for c in sub_comandos]
        resposta_imediata = (
            f"Executando {len(sub_comandos)} tarefas em paralelo: "
            + " | ".join(nomes) + "."
        )

        # Aguarda em background e notifica
        def _aguardar():
            for t in threads:
                t.join(timeout=60)
            msgs = []
            for i, cmd in enumerate(sub_comandos):
                r = resultados.get(i, "")
                msgs.append(f"• {cmd[:25]}: {str(r)[:80]}" if r else f"• {cmd[:25]}: feito")

            if self._falar:
                resumo = "Tarefas concluídas. " + ", ".join(
                    f"{c[:20]}: feito" for c in sub_comandos
                )
                try:
                    self._falar(resumo)
                except Exception:
                    pass
            if self._log and msgs:
                try:
                    self._log("✓ Paralelo concluído:\n" + "\n".join(msgs))
                except Exception:
                    pass

        threading.Thread(target=_aguardar, daemon=True).start()
        return resposta_imediata

    def _executar_pipeline(self, sub_comandos: list[str], cerebro) -> str:
        """
        Executa sub-comandos em sequência.
        Retorna confirmação IMEDIATA.
        Notifica após cada etapa.
        """
        def _run():
            for i, cmd in enumerate(sub_comandos):
                print(f"[PARALELO]: Pipeline etapa {i+1}/{len(sub_comandos)}: '{cmd}'")
                try:
                    r = cerebro._processar_acao_simples(cmd)
                    if r is None:
                        r = cerebro.processar(f"sirius {cmd}", forcar_processamento=True)
                    if r and self._log:
                        try:
                            self._log(f"✓ Etapa {i+1}: {r[:100]}")
                        except Exception:
                            pass
                    time.sleep(0.3)
                except Exception as e:
                    print(f"[PARALELO]: Pipeline etapa {i+1} falhou: {e}")
                    break

            if self._falar:
                try:
                    self._falar("Sequência concluída, chefe.")
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True).start()

        nomes = [c[:25] for c in sub_comandos]
        return (
            f"Iniciando sequência de {len(sub_comandos)} etapas: "
            + " → ".join(nomes) + "."
        )

    # -----------------------------------------------------------------------
    # Gerenciamento de tarefas
    # -----------------------------------------------------------------------

    def listar_ativas(self) -> list[Tarefa]:
        with self._lock:
            return [t for t in self._tarefas.values() if t.ativa]

    def cancelar_por_id_ou_numero(self, referencia: str) -> str:
        """Cancela tarefa pelo ID ou pelo número na lista de ativas."""
        ativas = self.listar_ativas()

        # Tenta por número
        if referencia.isdigit():
            idx = int(referencia) - 1
            if 0 <= idx < len(ativas):
                ativas[idx].cancelar()
                return f"✓ Tarefa '{ativas[idx].nome}' cancelada."
            return f"Tarefa número {referencia} não encontrada."

        # Tenta por ID parcial
        with self._lock:
            for t in self._tarefas.values():
                if t.id.startswith(referencia):
                    t.cancelar()
                    return f"✓ Tarefa '{t.nome}' cancelada."

        return f"Tarefa '{referencia}' não encontrada."

    def cancelar_todas(self) -> int:
        with self._lock:
            ativas = [t for t in self._tarefas.values() if t.ativa]
        for t in ativas:
            t.cancelar()
        return len(ativas)

    def n_ativas(self) -> int:
        return sum(1 for t in self._tarefas.values() if t.ativa)

    def status(self) -> dict:
        with self._lock:
            ativas     = [t for t in self._tarefas.values() if t.ativa]
            concluidas = [t for t in self._tarefas.values()
                          if t.estado == EstadoTarefa.CONCLUIDA]
            falhas     = [t for t in self._tarefas.values()
                          if t.estado == EstadoTarefa.FALHOU]
        return {
            "tarefas_ativas":     len(ativas),
            "tarefas_concluidas": len(concluidas),
            "tarefas_falhas":     len(falhas),
            "nomes_ativos":       [t.nome for t in ativas],
        }

    # -----------------------------------------------------------------------
    # Limpeza automática de tarefas antigas
    # -----------------------------------------------------------------------

    def _loop_limpeza(self):
        while True:
            time.sleep(300)   # a cada 5 minutos
            agora = datetime.now()
            with self._lock:
                ids_remover = [
                    tid for tid, t in self._tarefas.items()
                    if not t.ativa and
                    t.concluida_em and
                    (agora - t.concluida_em).total_seconds() > 300
                ]
                for tid in ids_remover:
                    del self._tarefas[tid]

    # -----------------------------------------------------------------------
    # Comandos de voz para gerenciar tarefas
    # -----------------------------------------------------------------------

    _TRIGGERS_STATUS = {
        "o que esta rodando", "o que está rodando",
        "tarefas ativas", "tarefas em andamento",
        "quantas tarefas", "processos ativos",
        "o que voce esta fazendo", "o que você está fazendo",
        "status das tarefas", "lista tarefas",
        "o que ta rodando", "mostra tarefas",
    }
    _TRIGGERS_CANCELAR = {
        "cancela a tarefa", "cancela tarefa",
        "para a tarefa", "mata a tarefa",
        "cancela tudo", "para tudo",
        "cancela todos os processos", "para todos os processos",
        "interrompe tudo", "interrompe a tarefa",
    }

    def e_comando_paralelo(self, texto: str) -> bool:
        t = _norm(texto)
        return (
            any(tr in t for tr in self._TRIGGERS_STATUS) or
            any(tr in t for tr in self._TRIGGERS_CANCELAR)
        )

    def processar_comando(self, texto: str) -> str:
        t = _norm(texto)

        # Cancela tudo
        if any(p in t for p in ["cancela tudo", "para tudo", "cancela todos",
                                  "interrompe tudo"]):
            n = self.cancelar_todas()
            return f"✓ {n} tarefa(s) cancelada(s)." if n else "Nenhuma tarefa ativa."

        # Cancela por número ou ID
        m = re.search(r"(?:cancela|para|mata|interrompe)\s+(?:a\s+)?tarefa\s+(\w+)", t)
        if m:
            return self.cancelar_por_id_ou_numero(m.group(1))

        # Lista ativas
        if any(p in t for p in self._TRIGGERS_STATUS):
            ativas = self.listar_ativas()
            if not ativas:
                return "Nenhuma tarefa rodando no momento, chefe."
            linhas = [f"Tarefas ativas ({len(ativas)}):"]
            for i, tarefa in enumerate(ativas, 1):
                dur = f"{tarefa.duracao_s:.0f}s" if tarefa.iniciada_em else "pendente"
                linhas.append(f"  {i}. [{tarefa.id}] {tarefa.nome} — {dur}")
            return "\n".join(linhas)

        return "Comando de tarefa não reconhecido."