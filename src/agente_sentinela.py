"""
agente_sentinela.py — Ponte Hardware → Cérebro do S.I.R.I.U.S.
===============================================================

Fluxo de dados:
  MonitorSistema / MonitorProcessos
       │  threshold atingido
       ▼
  EventoHardware  ──►  queue.Queue  (non-blocking put)
                              │
                    SiriusCerebro._thread_sentinela
                              │  .get(timeout=1)
                              ▼
                    analisar_crise(evento)
                    ├─ psutil: snapshots de processos
                    ├─ SiriusNeuronio: pontuação de relevância
                    └─ resposta Jarvis personalizada
                              │
                    callback_falar / proativo._disparar

Integração (cerebro.py):
    from agente_sentinela import AgenteSentinela, EventoHardware
    self._sentinela = AgenteSentinela(cerebro=self)
    self._sentinela.iniciar()
    # repasse a fila ao SiriusProativo:
    self._proativo.registrar_fila_sentinela(self._sentinela.fila)

Integração (sirius_proativo.py):
    self._fila_sentinela: queue.Queue | None = None

    def registrar_fila_sentinela(self, fila: queue.Queue):
        self._fila_sentinela = fila

    # Em MonitorSistema.verificar():
    #   em vez de só append(str) → também chama _emitir()
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# Tipos de evento (token enviado da thread do proativo → thread do cérebro)
# ─────────────────────────────────────────────────────────────────────────────

EVENTO_RAM_ALTA         = "ALTA_CARGA_RAM"
EVENTO_CPU_ALTA         = "ALTA_CARGA_CPU"
EVENTO_CPU_RAM_ALTA     = "ALTA_CARGA_CPU_RAM"   # CPU e RAM simultaneamente
EVENTO_DISCO_CHEIO      = "DISCO_QUASE_CHEIO"
EVENTO_BATERIA_CRITICA  = "BATERIA_CRITICA"
EVENTO_BATERIA_BAIXA    = "BATERIA_BAIXA"
EVENTO_PROC_PESADO      = "PROCESSO_PESADO_DETECTADO"


@dataclass
class ProcessoInfo:
    """Snapshot leve de um processo relevante."""
    nome:        str
    pid:         int
    cpu_pct:     float
    ram_mb:      float
    ram_pct:     float
    essencial:   Optional[bool] = None   # preenchido pelo analisador
    score:       float = 0.0             # score de "não-essencialidade"


@dataclass
class EventoHardware:
    """
    Token enviado pela thread de monitoramento para a thread do cérebro.
    Carrega métricas brutas + snapshot de processos para análise contextual.
    """
    tipo:           str                    # uma das constantes EVENTO_*
    ts:             float = field(default_factory=time.time)

    # Métricas de sistema no momento do disparo
    ram_pct:        float = 0.0
    ram_livre_gb:   float = 0.0
    cpu_pct:        float = 0.0
    disco_pct:      float = 0.0
    disco_livre_gb: float = 0.0
    bateria_pct:    float = 0.0
    bateria_mins:   int   = 0

    # Processos ativos no momento do evento (top 10)
    processos:      List[ProcessoInfo] = field(default_factory=list)

    # Contexto extra (ex: jogo detectado, contexto da sessão)
    contexto:       Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Classificador de processos (heurística + histórico do neurônio)
# ─────────────────────────────────────────────────────────────────────────────

# Processos que NUNCA devem ser encerrados pelo Sirius
_PROCESSOS_PROTEGIDOS = frozenset({
    # Sistema operacional
    "system", "systemd", "init", "kernel", "kworker", "ksoftirqd",
    "kthreadd", "migration", "rcu_sched", "irq", "watchdog",
    # Windows essencial
    "svchost.exe", "lsass.exe", "csrss.exe", "smss.exe", "wininit.exe",
    "services.exe", "winlogon.exe", "ntoskrnl.exe", "explorer.exe",
    "dwm.exe", "conhost.exe", "taskhostw.exe", "audiodg.exe",
    # Sirius
    "python.exe", "python3.exe", "python", "python3",
    # Antivírus / segurança
    "msmpeng.exe", "mssense.exe", "mbam.exe", "avp.exe",
})

# Processos claramente não-essenciais (candidatos a encerramento)
_PROCESSOS_NAO_ESSENCIAIS = frozenset({
    "chrome.exe", "chrome", "chromium", "firefox.exe", "firefox",
    "msedge.exe", "opera.exe", "brave.exe",
    "discord.exe", "discord",
    "slack.exe", "slack",
    "teams.exe", "teams",
    "onedrive.exe", "dropbox.exe",
    "steam.exe", "epicgameslauncher.exe",
    "spotifywebhelper.exe", "spotify.exe", "spotify",
    "zoom.exe",
    "skype.exe",
    "obs64.exe", "obs.exe",
    "update.exe", "updater.exe",
    "backgroundtaskhost.exe",
    "searchindexer.exe",
    "wsappx", "fontdrvhost.exe",
})


def _coletar_processos(top_n: int = 12) -> List[ProcessoInfo]:
    """
    Captura snapshot dos top_n processos por uso de RAM.
    Roda em < 200ms na maioria dos sistemas.
    """
    try:
        import psutil
    except ImportError:
        return []

    procs: List[ProcessoInfo] = []
    for proc in psutil.process_iter(
        ["name", "pid", "cpu_percent", "memory_info", "memory_percent"]
    ):
        try:
            info  = proc.info
            nome  = (info.get("name") or "").lower()
            if not nome or nome in {"idle", ""}:
                continue
            ram_b = info["memory_info"].rss if info.get("memory_info") else 0
            procs.append(
                ProcessoInfo(
                    nome     = info.get("name") or nome,
                    pid      = info["pid"],
                    cpu_pct  = info.get("cpu_percent") or 0.0,
                    ram_mb   = ram_b / (1024 ** 2),
                    ram_pct  = info.get("memory_percent") or 0.0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda p: p.ram_mb, reverse=True)
    return procs[:top_n]


def _score_nao_essencial(proc: ProcessoInfo, historico_nomes: set) -> float:
    """
    Retorna um score de 0.0 (totalmente essencial) a 1.0 (claramente dispensável).
    Considera: lista estática + histórico de uso + consumo de recursos.
    """
    nome_lower = proc.nome.lower()

    if nome_lower in _PROCESSOS_PROTEGIDOS:
        return 0.0

    score = 0.0

    if nome_lower in _PROCESSOS_NAO_ESSENCIAIS:
        score += 0.55

    # Se o usuário nunca mencionou este processo → mais dispensável
    if nome_lower not in historico_nomes:
        score += 0.15

    # Penaliza processos com alto consumo de RAM sem ser protegido
    if proc.ram_mb > 500:
        score += 0.20
    elif proc.ram_mb > 200:
        score += 0.10

    # Bônus se CPU também está alta
    if proc.cpu_pct > 30:
        score += 0.10

    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Detector de contexto — tenta identificar o que o usuário está fazendo
# ─────────────────────────────────────────────────────────────────────────────

_JOGOS = frozenset({
    "minecraft", "javaw.exe", "league of legends", "leagueclient.exe",
    "csgo.exe", "cs2.exe", "valorant.exe", "riotclientservices.exe",
    "steam.exe", "steamwebhelper.exe", "fortnite.exe",
    "r5apex.exe", "epicgameslauncher.exe", "gta5.exe",
    "witcher3.exe", "eldenring.exe", "cyberpunk2077.exe",
})

_CRIATIVO = frozenset({
    "premiere.exe", "after effects.exe", "photoshop.exe", "illustrator.exe",
    "audacity.exe", "davinci resolve.exe", "resolve.exe",
    "obs64.exe", "obs.exe", "blender.exe",
})

_TRABALHO = frozenset({
    "code.exe", "code", "pycharm64.exe", "idea64.exe",
    "eclipse.exe", "netbeans64.exe", "androidstudio64.exe",
    "excel.exe", "winword.exe", "powerpnt.exe",
    "putty.exe", "wt.exe", "windowsterminal.exe",
})


def _detectar_contexto(processos: List[ProcessoInfo]) -> Dict[str, Any]:
    """Infere o que o usuário está fazendo baseado nos processos ativos."""
    nomes = {p.nome.lower() for p in processos}
    ctx: Dict[str, Any] = {}

    jogo_ativo = nomes & _JOGOS
    if jogo_ativo:
        ctx["atividade"] = "jogo"
        ctx["jogo"]      = next(iter(jogo_ativo))

    elif nomes & _CRIATIVO:
        ctx["atividade"] = "criacao"
    elif nomes & _TRABALHO:
        ctx["atividade"] = "trabalho"
    else:
        ctx["atividade"] = "geral"

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# AgenteSentinela — consome a fila e gera respostas inteligentes
# ─────────────────────────────────────────────────────────────────────────────

class AgenteSentinela:
    """
    Consome EventoHardware da fila e gera respostas contextuais usando o
    neurônio do Sirius. Roda em thread daemon separada para não bloquear
    nem a thread do proativo nem a thread principal do cérebro.

    Uso em cerebro.py:
        self._sentinela = AgenteSentinela(cerebro=self)
        self._sentinela.iniciar()
        self._proativo.registrar_fila_sentinela(self._sentinela.fila)
    """

    # Cooldown entre análises do mesmo tipo de evento (evita spam)
    _COOLDOWN: Dict[str, int] = {
        EVENTO_RAM_ALTA:        600,    # 10 min
        EVENTO_CPU_ALTA:        600,
        EVENTO_CPU_RAM_ALTA:    900,
        EVENTO_DISCO_CHEIO:    3600,
        EVENTO_BATERIA_CRITICA: 300,
        EVENTO_BATERIA_BAIXA:   900,
        EVENTO_PROC_PESADO:     900,
    }

    def __init__(self, cerebro=None):
        """
        cerebro: instância de SiriusCerebro.
                 Se None, o sentinela ainda funciona mas usa respostas
                 padrão sem passar pelo neurônio.
        """
        self._cerebro    = cerebro
        self.fila: queue.Queue[EventoHardware] = queue.Queue(maxsize=32)
        self._rodando    = False
        self._thread: Optional[threading.Thread] = None
        self._ultimos: Dict[str, float] = {}   # tipo → timestamp última análise

    # ──────────────────────────────────────────────────────────────────────
    # Controle de ciclo de vida
    # ──────────────────────────────────────────────────────────────────────

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name="AgenteSentinela",
        )
        self._thread.start()
        print("\033[92m[SENTINELA]: Agente Sentinela ativo — monitorando hardware.\033[0m")

    def parar(self):
        self._rodando = False
        # Libera o .get() bloqueante
        try:
            self.fila.put_nowait(None)
        except queue.Full:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Loop principal (thread daemon)
    # ──────────────────────────────────────────────────────────────────────

    def _loop(self):
        print("\033[94m[SENTINELA]: Thread de análise de hardware iniciada.\033[0m")
        while self._rodando:
            try:
                evento = self.fila.get(timeout=2.0)
            except queue.Empty:
                continue

            # Sinal de parada
            if evento is None:
                break

            # Verifica cooldown por tipo
            agora = time.time()
            ultimo = self._ultimos.get(evento.tipo, 0.0)
            cooldown = self._COOLDOWN.get(evento.tipo, 600)
            if agora - ultimo < cooldown:
                print(
                    f"\033[90m[SENTINELA]: Evento {evento.tipo} ignorado "
                    f"(cooldown: {int(cooldown - (agora - ultimo))}s restantes)\033[0m"
                )
                continue

            self._ultimos[evento.tipo] = agora

            # Análise em thread separada para não bloquear a fila
            threading.Thread(
                target=self._analisar_e_reagir,
                args=(evento,),
                daemon=True,
            ).start()

    # ──────────────────────────────────────────────────────────────────────
    # Análise + reação
    # ──────────────────────────────────────────────────────────────────────

    def _analisar_e_reagir(self, evento: EventoHardware):
        """Ponto de entrada da análise. Chama analisar_crise e dispara o alerta."""
        try:
            print(f"\033[93m[SENTINELA]: Analisando evento {evento.tipo}...\033[0m")
            resposta = self.analisar_crise(evento)
            if resposta:
                self._disparar(resposta)
        except Exception as e:
            print(f"\033[91m[SENTINELA]: Erro ao analisar evento: {e}\033[0m")

    def analisar_crise(self, evento: EventoHardware) -> Optional[str]:
        """
        Núcleo analítico do Sentinela.

        Fluxo:
          1. Coleta snapshot de processos (se não veio no evento)
          2. Classifica processos (essencial / não-essencial) via heurística + neurônio
          3. Detecta contexto (jogo, trabalho, criação)
          4. Gera sugestão Jarvis personalizada

        Retorna a string de resposta ou None se não há sugestão relevante.
        """
        # ── 1. Garante snapshot de processos ─────────────────────────────
        if not evento.processos:
            evento.processos = _coletar_processos()

        if not evento.processos:
            return self._resposta_padrao(evento)

        # ── 2. Detecta contexto da sessão ─────────────────────────────────
        if not evento.contexto:
            evento.contexto = _detectar_contexto(evento.processos)

        # ── 3. Obtém histórico de nomes do usuário ─────────────────────────
        historico_nomes = self._obter_historico_nomes()

        # ── 4. Classifica processos por score de não-essencialidade ────────
        for proc in evento.processos:
            proc.score      = _score_nao_essencial(proc, historico_nomes)
            proc.essencial  = proc.score < 0.4

        # ── 5. Filtra candidatos a encerramento ────────────────────────────
        candidatos = sorted(
            [p for p in evento.processos if not p.essencial and p.ram_mb > 50],
            key=lambda p: p.score,
            reverse=True,
        )[:3]

        # ── 6. Tenta usar o neurônio para refinar a análise ───────────────
        contexto_ia = self._consultar_neuronio(evento, candidatos)

        # ── 7. Constrói resposta Jarvis ────────────────────────────────────
        return self._construir_resposta(evento, candidatos, contexto_ia)

    def _obter_historico_nomes(self) -> set:
        """Extrai nomes de programas mencionados pelo usuário no histórico."""
        nomes = set()
        try:
            if self._cerebro and hasattr(self._cerebro, "memoria"):
                hist = self._cerebro.memoria.obter_historico(limite=50)
                for entrada in hist:
                    texto = str(entrada.get("usuario", "")).lower()
                    # Heurística: palavras de 3+ chars que podem ser nomes de app
                    for palavra in texto.split():
                        if len(palavra) >= 3:
                            nomes.add(palavra)
        except Exception:
            pass
        return nomes

    def _consultar_neuronio(
        self,
        evento:     EventoHardware,
        candidatos: List[ProcessoInfo],
    ) -> Optional[str]:
        """
        Usa o SiriusNeuronio para classificar os candidatos e gerar
        uma análise de texto curta.

        Retorna None se o neurônio não estiver disponível.
        """
        if not self._cerebro:
            return None

        try:
            neuronio = getattr(self._cerebro, "neuronio", None)
            if not neuronio:
                return None

            nomes_cands = ", ".join(p.nome for p in candidatos) if candidatos else "nenhum"
            atividade   = evento.contexto.get("atividade", "geral")
            jogo        = evento.contexto.get("jogo", "")

            prompt = (
                f"O sistema está com RAM em {evento.ram_pct:.0f}% e CPU em {evento.cpu_pct:.0f}%. "
                f"Atividade atual do usuário: {atividade}"
                + (f" ({jogo})" if jogo else "")
                + f". Processos que consomem mais memória: {nomes_cands}. "
                "Responda de forma muito curta (1 frase) qual processo é menos importante "
                "para a atividade atual e por que."
            )

            resultado = neuronio.responder(prompt)
            if resultado and len(resultado) > 10 and "nao sei" not in resultado.lower():
                return resultado.strip()[:200]

        except Exception as e:
            print(f"\033[90m[SENTINELA]: Neurônio indisponível para análise: {e}\033[0m")

        return None

    def _construir_resposta(
        self,
        evento:     EventoHardware,
        candidatos: List[ProcessoInfo],
        analise_ia: Optional[str],
    ) -> Optional[str]:
        """
        Constrói a resposta final no tom Jarvis.
        Combina métricas + análise de IA + sugestão de ação.
        """
        atividade = evento.contexto.get("atividade", "geral")
        jogo      = evento.contexto.get("jogo", "")

        # ── Prefixo contextual ─────────────────────────────────────────────
        if atividade == "jogo":
            jogo_nome = jogo.replace(".exe", "").title()
            prefixo   = f"Chefia, detectei que você está jogando {jogo_nome}."
        elif atividade == "criacao":
            prefixo = "Chefia, detectei que você está em modo criativo."
        elif atividade == "trabalho":
            prefixo = "Chefia, enquanto você trabalha,"
        else:
            prefixo = "Chefia,"

        # ── Diagnóstico de recursos ────────────────────────────────────────
        if evento.tipo in (EVENTO_RAM_ALTA, EVENTO_CPU_RAM_ALTA):
            metricas = (
                f"a RAM está em {evento.ram_pct:.0f}% "
                f"({evento.ram_livre_gb:.1f} GB livres)"
            )
            if evento.cpu_pct > 0:
                metricas += f" e a CPU em {evento.cpu_pct:.0f}%"
        elif evento.tipo == EVENTO_CPU_ALTA:
            metricas = f"a CPU está em {evento.cpu_pct:.0f}%"
        elif evento.tipo == EVENTO_DISCO_CHEIO:
            metricas = (
                f"o disco está em {evento.disco_pct:.0f}% "
                f"({evento.disco_livre_gb:.1f} GB livres)"
            )
        elif evento.tipo == EVENTO_BATERIA_CRITICA:
            metricas = f"a bateria está crítica — {evento.bateria_pct:.0f}%"
        elif evento.tipo == EVENTO_BATERIA_BAIXA:
            metricas = (
                f"a bateria está em {evento.bateria_pct:.0f}%"
                + (f" (~{evento.bateria_mins} min restantes)" if evento.bateria_mins > 0 else "")
            )
        else:
            metricas = "o sistema está sob alta carga"

        # ── Culpados ───────────────────────────────────────────────────────
        if candidatos:
            # Formata os 2 maiores culpados
            top = candidatos[:2]
            culpados_str = " e ".join(
                f"{p.nome.replace('.exe','').title()} ({p.ram_mb:.0f} MB)"
                for p in top
            )
            culpa = f" Detectei que {culpados_str} está consumindo memória em background."
        else:
            culpa = ""

        # ── Análise do neurônio ────────────────────────────────────────────
        ia_str = f" {analise_ia}" if analise_ia else ""

        # ── Oferta de ação ─────────────────────────────────────────────────
        if candidatos and evento.tipo in (
            EVENTO_RAM_ALTA, EVENTO_CPU_RAM_ALTA, EVENTO_PROC_PESADO
        ):
            alvo = candidatos[0].nome.replace(".exe", "").title()
            oferta = (
                f" Quer que eu encerre os processos de background do {alvo} "
                "para dar fôlego ao servidor?"
            )
        elif evento.tipo == EVENTO_DISCO_CHEIO:
            oferta = " Quer que eu analise o disco e sugira o que pode ser removido?"
        elif evento.tipo in (EVENTO_BATERIA_CRITICA, EVENTO_BATERIA_BAIXA):
            oferta = " Conecte o carregador. Posso suspender tarefas pesadas para economizar."
        else:
            oferta = ""

        resposta = f"{prefixo} {metricas}.{culpa}{ia_str}{oferta}"
        return resposta.strip()

    def _resposta_padrao(self, evento: EventoHardware) -> str:
        """Resposta simples quando não há dados de processos."""
        mapa = {
            EVENTO_RAM_ALTA:        f"⚠ RAM em {evento.ram_pct:.0f}% — sistema sobrecarregado.",
            EVENTO_CPU_ALTA:        f"⚠ CPU em {evento.cpu_pct:.0f}% — processamento elevado.",
            EVENTO_CPU_RAM_ALTA:    f"⚠ CPU {evento.cpu_pct:.0f}% e RAM {evento.ram_pct:.0f}% — sistema no limite.",
            EVENTO_DISCO_CHEIO:     f"⚠ Disco em {evento.disco_pct:.0f}% — espaço crítico.",
            EVENTO_BATERIA_CRITICA: f"⚠ Bateria crítica — {evento.bateria_pct:.0f}%! Conecte o carregador.",
            EVENTO_BATERIA_BAIXA:   f"⚠ Bateria em {evento.bateria_pct:.0f}%.",
            EVENTO_PROC_PESADO:     "⚠ Processo pesado detectado em background.",
        }
        return mapa.get(evento.tipo, "⚠ Alerta de hardware detectado.")

    # ──────────────────────────────────────────────────────────────────────
    # Disparo da resposta
    # ──────────────────────────────────────────────────────────────────────

    def _disparar(self, resposta: str):
        """Envia a resposta ao callback de fala e ao log."""
        print(f"\n\033[93m[SENTINELA]: {resposta}\033[0m")

        # Tenta usar o callback de fala do cérebro
        try:
            if self._cerebro:
                cb_falar = getattr(self._cerebro, "_callback_falar", None)
                if cb_falar:
                    cb_falar(resposta)
                    return

                cb_log = getattr(self._cerebro, "_callback_log", None)
                if cb_log:
                    cb_log(f"⚙ {resposta}")

        except Exception as e:
            print(f"[SENTINELA]: Erro ao disparar resposta: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # API para que o SiriusProativo emita eventos
    # ──────────────────────────────────────────────────────────────────────

    def emitir(self, evento: EventoHardware) -> bool:
        """
        Coloca o evento na fila sem bloquear.
        Retorna True se inserido, False se a fila estiver cheia.
        """
        try:
            self.fila.put_nowait(evento)
            return True
        except queue.Full:
            print(
                f"\033[90m[SENTINELA]: Fila cheia — evento {evento.tipo} descartado.\033[0m"
            )
            return False