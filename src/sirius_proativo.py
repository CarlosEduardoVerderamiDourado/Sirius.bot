"""
sirius_proativo.py — Monitor Proativo do S.I.R.I.U.S. v5.2
============================================================
Módulo autônomo que roda em background e alerta o Carlos sobre:

  • Lembretes agendados
  • Hardware crítico (CPU, RAM, disco, bateria)
  • Hora (início/fim de expediente, pausa, meia-noite)
  • Processos pesados
  • Clima (requer requests + API key)
  • Briefing matinal
  • Contexto de trabalho via SiriusFoco (opcional)
  • OCR automático de erros em tela (contexto DESENVOLVIMENTO)

Ciclos de verificação:
  A cada 30s  → lembretes, hardware, hora, foco
  A cada 2min → processos pesados
  A cada 5min → clima
  A cada 3min → OCR automático (só em DESENVOLVIMENTO)

Integração no main_residente.py / cerebro.py:
    from sirius_proativo import SiriusProativo

    proativo = SiriusProativo(
        callback_falar  = worker.audio.falar,  # ou qualquer fn(str)
        callback_log    = self.log_sirius,
        cerebro         = self.cerebro,        # opcional
    )
    proativo.iniciar()   # inicia thread daemon

    # Para agendar lembretes pelo cerebro:
    proativo.agendar_lembrete("reunião às 15h", minutos=60)

Dependências:
    obrigatórias:  nenhuma (fallback puro Python)
    opcionais:     psutil (hardware), requests (clima)
"""

from __future__ import annotations

import os
import re
import sys
import time
import threading
import sqlite3
from datetime import datetime, timedelta
from typing import Callable, Optional

# ── Paths ──────────────────────────────────────────────────────────────────── #
_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
_DIR_DATA = os.path.join(_DIR_RAIZ, "data")
os.makedirs(_DIR_DATA, exist_ok=True)

if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

# ── psutil (opcional) ──────────────────────────────────────────────────────── #
try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

# ── requests (opcional — clima) ────────────────────────────────────────────── #
try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


# =============================================================================
# Sub-monitores — cada um tem .verificar() → list[str]
# =============================================================================

class _MonitorSistema:
    """Hardware: CPU, RAM, disco, bateria."""

    # Limiares de alerta
    CPU_CRITICO  = 92    # %
    RAM_CRITICO  = 90    # %
    DISCO_CRITICO = 90   # %
    BAT_BAIXA    = 15    # %
    BAT_CRITICA  = 5     # %

    def __init__(self):
        self._ultimo_cpu_alerta  = 0.0
        self._ultimo_ram_alerta  = 0.0
        self._ultimo_bat_alerta  = 0.0
        self._intervalo_alerta   = 300   # não repetir alerta por 5 minutos

    def verificar(self) -> list[str]:
        if not _PSUTIL_OK:
            return []

        alertas = []
        agora   = time.time()

        # CPU
        try:
            cpu = psutil.cpu_percent(interval=0.3)
            if cpu > self.CPU_CRITICO and agora - self._ultimo_cpu_alerta > self._intervalo_alerta:
                self._ultimo_cpu_alerta = agora
                alertas.append(f"⚠ CPU em {cpu:.0f}% — verifique processos pesados.")
        except Exception:
            pass

        # RAM
        try:
            ram = psutil.virtual_memory()
            if ram.percent > self.RAM_CRITICO and agora - self._ultimo_ram_alerta > self._intervalo_alerta:
                self._ultimo_ram_alerta = agora
                livre_mb = ram.available // 1024 ** 2
                alertas.append(
                    f"⚠ RAM em {ram.percent:.0f}% — apenas {livre_mb} MB livres."
                )
        except Exception:
            pass

        # Disco (raiz)
        try:
            disco = psutil.disk_usage("/")
            if disco.percent > self.DISCO_CRITICO:
                alertas.append(
                    f"⚠ Disco em {disco.percent:.0f}% — "
                    f"{disco.free // 1024**3} GB livres."
                )
        except Exception:
            pass

        # Bateria
        try:
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged:
                if bat.percent <= self.BAT_CRITICA and agora - self._ultimo_bat_alerta > 120:
                    self._ultimo_bat_alerta = agora
                    alertas.append(
                        f"🔋 Bateria crítica: {bat.percent:.0f}%! Conecte o carregador agora."
                    )
                elif bat.percent <= self.BAT_BAIXA and agora - self._ultimo_bat_alerta > self._intervalo_alerta:
                    self._ultimo_bat_alerta = agora
                    alertas.append(
                        f"🔋 Bateria baixa: {bat.percent:.0f}%. Carregue em breve."
                    )
        except Exception:
            pass

        return alertas


class _MonitorProcessos:
    """Processos com alto consumo de CPU/RAM."""

    CPU_PROC_CRITICO = 80   # % por processo
    RAM_PROC_CRITICO = 1024  # MB por processo

    def verificar(self) -> list[str]:
        if not _PSUTIL_OK:
            return []

        alertas = []
        try:
            for proc in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
                try:
                    cpu = proc.info["cpu_percent"] or 0
                    mem = (proc.info["memory_info"].rss // 1024 ** 2) if proc.info["memory_info"] else 0
                    nome = proc.info["name"] or "?"

                    if cpu > self.CPU_PROC_CRITICO:
                        alertas.append(
                            f"⚠ Processo '{nome}' consumindo {cpu:.0f}% de CPU."
                        )
                    elif mem > self.RAM_PROC_CRITICO:
                        alertas.append(
                            f"⚠ Processo '{nome}' usando {mem} MB de RAM."
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        return alertas[:2]  # no máximo 2 alertas de processo por ciclo


class _MonitorHora:
    """Alertas baseados em hora do dia."""

    def __init__(self):
        self._hora_inicio_expediente  = 9
        self._hora_fim_expediente     = 18
        self._ultimo_alerta_hora: dict[str, float] = {}
        self._intervalo = 3600   # 1h entre alertas do mesmo tipo

    def _pode_alertar(self, chave: str) -> bool:
        agora = time.time()
        if agora - self._ultimo_alerta_hora.get(chave, 0) > self._intervalo:
            self._ultimo_alerta_hora[chave] = agora
            return True
        return False

    def verificar(self) -> list[str]:
        agora = datetime.now()
        h, m  = agora.hour, agora.minute
        alertas = []

        # Início de expediente
        if h == self._hora_inicio_expediente and m < 30:
            if self._pode_alertar("inicio"):
                alertas.append("☀ Bom dia, chefia. Expediente iniciado.")

        # Fim de expediente
        elif h == self._hora_fim_expediente and m < 30:
            if self._pode_alertar("fim"):
                alertas.append("🌙 São 18h. Hora de encerrar o expediente.")

        # Pausa sugerida (a cada 2h de expediente)
        elif 9 < h < 18 and h % 2 == 0 and m < 5:
            if self._pode_alertar(f"pausa_{h}"):
                alertas.append(
                    f"⏸ {h}h — hora de uma pausa rápida, chefia."
                )

        # Meia-noite / madrugada
        elif h == 0 and m < 5:
            if self._pode_alertar("meia_noite"):
                alertas.append("🌙 Meia-noite. Considere descansar, chefia.")

        return alertas


class _MonitorFoco:
    """Monitora foco e produtividade — placeholder simples sem SiriusFoco."""

    def __init__(self):
        self._ultimo = 0.0
        self._intervalo = 1800   # 30min

    def verificar(self) -> list[str]:
        # Lógica de foco delegada ao módulo SiriusFoco quando disponível.
        # Este monitor só emite alertas de produtividade genéricos.
        agora = time.time()
        if agora - self._ultimo < self._intervalo:
            return []
        self._ultimo = agora

        h = datetime.now().hour
        if 9 <= h <= 18:
            return []   # durante expediente, deixa o SiriusFoco cuidar

        return []


class _MonitorClima:
    """Consulta clima via OpenWeatherMap (requer API key)."""

    _API_URL = "https://api.openweathermap.org/data/2.5/weather"

    def __init__(self):
        self._api_key    = os.environ.get("OPENWEATHER_API_KEY", "")
        self._cidade     = os.environ.get("SIRIUS_CIDADE", "São Paulo")
        self._ultimo     = 0.0
        self._intervalo  = 300   # 5min

    def verificar(self) -> list[str]:
        if not _REQUESTS_OK or not self._api_key:
            return []

        agora = time.time()
        if agora - self._ultimo < self._intervalo:
            return []
        self._ultimo = agora

        try:
            resp = _requests.get(
                self._API_URL,
                params={"q": self._cidade, "appid": self._api_key,
                        "units": "metric", "lang": "pt_br"},
                timeout=5,
            )
            if resp.status_code == 200:
                data  = resp.json()
                temp  = data["main"]["temp"]
                desc  = data["weather"][0]["description"]
                return [f"🌤 {self._cidade}: {temp:.0f}°C, {desc}."]
        except Exception:
            pass

        return []


class _BriefingMatinal:
    """Dispara um briefing de bom dia uma vez por dia."""

    def __init__(self, cerebro=None):
        self._cerebro      = cerebro
        self._ultimo_dia   = -1
        self._hora_briefing = 9   # às 9h

    def deve_disparar(self) -> bool:
        agora = datetime.now()
        return (
            agora.hour == self._hora_briefing
            and agora.day != self._ultimo_dia
        )

    def marcar_disparado(self):
        self._ultimo_dia = datetime.now().day

    def gerar(self) -> str:
        agora    = datetime.now()
        dia_sem  = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
        dia_nome = dia_sem[agora.weekday()]
        data_str = agora.strftime("%d/%m/%Y")
        return (
            f"Bom dia, chefia. Hoje é {dia_nome}, {data_str}. "
            "Sistema operacional, memória estável. Pronto para começar."
        )


# =============================================================================
# Banco de lembretes (SQLite)
# =============================================================================

_DB_LEMBRETES = os.path.join(_DIR_DATA, "sirius_lembretes.db")

_SQL_LEMBRETES = """
CREATE TABLE IF NOT EXISTS lembretes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    DEFAULT 'admin',
    texto       TEXT    NOT NULL,
    disparar_em DATETIME NOT NULL,
    recorrente  INTEGER DEFAULT 0,
    intervalo_m INTEGER DEFAULT 0,
    disparado   INTEGER DEFAULT 0,
    criado_em   DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


class _GerenciadorLembretes:
    """Persiste e verifica lembretes agendados."""

    def __init__(self):
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(_SQL_LEMBRETES)

    def _connect(self):
        conn = sqlite3.connect(_DB_LEMBRETES, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def agendar(
        self,
        texto:       str,
        minutos:     int  = 30,
        user_id:     str  = "admin",
        recorrente:  bool = False,
        intervalo_m: int  = 0,
    ) -> bool:
        """Agenda um lembrete para daqui a `minutos` minutos."""
        disparar_em = datetime.now() + timedelta(minutes=minutos)
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO lembretes "
                        "(user_id, texto, disparar_em, recorrente, intervalo_m) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            user_id,
                            texto.strip(),
                            disparar_em.strftime("%Y-%m-%d %H:%M:%S"),
                            int(recorrente),
                            intervalo_m,
                        ),
                    )
            return True
        except Exception as e:
            print(f"[PROATIVO]: Erro ao agendar lembrete: {e}")
            return False

    def verificar(self, user_id: Optional[str] = None) -> list[str]:
        """Retorna lembretes que estão na hora e os marca como disparados."""
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alertas = []
        try:
            with self._lock:
                with self._connect() as conn:
                    where = "disparar_em <= ? AND disparado = 0"
                    params: list = [agora]
                    if user_id:
                        where += " AND user_id = ?"
                        params.append(user_id)
                    rows = conn.execute(
                        f"SELECT id, texto, recorrente, intervalo_m FROM lembretes "
                        f"WHERE {where}",
                        params,
                    ).fetchall()

                    for row_id, texto, recorrente, intervalo_m in rows:
                        alertas.append(f"⏰ Lembrete: {texto}")
                        if recorrente and intervalo_m > 0:
                            nova = (
                                datetime.now() + timedelta(minutes=intervalo_m)
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            conn.execute(
                                "UPDATE lembretes SET disparar_em = ? WHERE id = ?",
                                (nova, row_id),
                            )
                        else:
                            conn.execute(
                                "UPDATE lembretes SET disparado = 1 WHERE id = ?",
                                (row_id,),
                            )

        except Exception as e:
            print(f"[PROATIVO]: Erro ao verificar lembretes: {e}")

        return alertas

    def listar(self, user_id: str = "admin") -> list[dict]:
        """Retorna lembretes futuros não disparados."""
        try:
            agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT id, texto, disparar_em FROM lembretes "
                        "WHERE user_id = ? AND disparado = 0 AND disparar_em > ? "
                        "ORDER BY disparar_em",
                        (user_id, agora),
                    ).fetchall()
            return [{"id": r[0], "texto": r[1], "em": r[2]} for r in rows]
        except Exception:
            return []

    def cancelar(self, lembrete_id: int) -> bool:
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE lembretes SET disparado = 1 WHERE id = ?",
                        (lembrete_id,),
                    )
            return True
        except Exception:
            return False


# =============================================================================
# SiriusProativo — classe principal
# =============================================================================

class SiriusProativo:
    """
    Monitor proativo do S.I.R.I.U.S.

    Roda em background e dispara alertas via callback_falar.

    Uso mínimo:
        proativo = SiriusProativo(callback_falar=falar)
        proativo.iniciar()

    Uso completo (integrado ao cerebro):
        proativo = SiriusProativo(
            callback_falar = worker.audio.falar,
            callback_log   = self.log_sirius,
            cerebro        = self.cerebro,
        )
        proativo.iniciar()
        proativo.agendar_lembrete("revisar PR", minutos=60)
    """

    def __init__(
        self,
        callback_falar:  Optional[Callable[[str], None]] = None,
        callback_log:    Optional[Callable[[str], None]] = None,
        cerebro=None,
        user_id: str = "admin",
    ):
        self._falar      = callback_falar
        self._log        = callback_log
        self._cerebro    = cerebro
        self._user_id    = user_id
        self._rodando    = False
        self._thread: Optional[threading.Thread] = None
        self._lock       = threading.Lock()

        # Sub-monitores
        self._monitor_sistema   = _MonitorSistema()
        self._monitor_processos = _MonitorProcessos()
        self._monitor_hora      = _MonitorHora()
        self._monitor_foco_base = _MonitorFoco()
        self._monitor_clima     = _MonitorClima()
        self._lembretes         = _GerenciadorLembretes()
        self._briefing          = _BriefingMatinal(cerebro=cerebro)

        # SiriusFoco (opcional — context-awareness)
        self._foco = None
        try:
            from sirius_foco import SiriusFoco
            self._foco = SiriusFoco(callback_alerta=self._disparar)
            print("\033[92m[PROATIVO]: SiriusFoco integrado.\033[0m")
        except Exception as e:
            print(f"[PROATIVO]: SiriusFoco indisponível: {e}")

        print("\033[94m[PROATIVO]: SiriusProativo inicializado.\033[0m")

    # =========================================================================
    # API pública
    # =========================================================================

    def iniciar(self):
        """Inicia o monitor em thread daemon."""
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name="SiriusProativo",
        )
        self._thread.start()
        print("\033[92m[PROATIVO]: Monitor ativo em background.\033[0m")

    def parar(self):
        """Para o loop de monitoramento."""
        self._rodando = False
        print("[PROATIVO]: Monitor encerrado.")

    def agendar_lembrete(
        self,
        texto:       str,
        minutos:     int  = 30,
        recorrente:  bool = False,
        intervalo_m: int  = 0,
    ) -> str:
        """
        Agenda um lembrete.
        Retorna string de confirmação para o Sirius falar.
        """
        ok = self._lembretes.agendar(
            texto       = texto,
            minutos     = minutos,
            user_id     = self._user_id,
            recorrente  = recorrente,
            intervalo_m = intervalo_m,
        )
        if ok:
            em = (datetime.now() + timedelta(minutes=minutos)).strftime("%H:%M")
            return f"✓ Lembrete agendado para {em}: '{texto}'."
        return "⚠ Não foi possível agendar o lembrete."

    def listar_lembretes(self) -> str:
        """Retorna string formatada com lembretes pendentes."""
        items = self._lembretes.listar(self._user_id)
        if not items:
            return "Nenhum lembrete agendado, chefia."
        linhas = ["Lembretes pendentes:"]
        for i in items:
            linhas.append(f"  [{i['id']}] {i['texto']} — às {i['em'][11:16]}")
        return "\n".join(linhas)

    def cancelar_lembrete(self, lembrete_id: int) -> str:
        ok = self._lembretes.cancelar(lembrete_id)
        return f"✓ Lembrete {lembrete_id} cancelado." if ok else "⚠ Lembrete não encontrado."

    def e_comando_proativo(self, texto: str) -> bool:
        """Retorna True se o texto é um comando de gerenciamento de lembretes."""
        t = texto.lower()
        return any(p in t for p in [
            "lembra", "lembrete", "me avisa", "me lembra",
            "meus lembretes", "lista lembretes", "cancela lembrete",
            "cancela o lembrete",
        ])

    def processar_comando(self, texto: str) -> str:
        """Processa comandos de lembretes — chamado pelo cerebro.py."""
        t = texto.lower()

        # Listar
        if any(p in t for p in ["meus lembretes", "lista lembretes", "quais lembretes"]):
            return self.listar_lembretes()

        # Cancelar por ID
        m = re.search(r"cancela(?:\s+o)?\s+lembrete\s+(\d+)", t)
        if m:
            return self.cancelar_lembrete(int(m.group(1)))

        # Agendar — "me lembra X em Y minutos" ou "lembra de X daqui a Y horas"
        m_min = re.search(
            r"(?:lembra|me avisa|me lembra)\s+(?:de\s+)?(.+?)\s+"
            r"(?:em|daqui\s+a)\s+(\d+)\s+(minutos?|horas?)",
            t,
        )
        if m_min:
            texto_lembrete = m_min.group(1).strip()
            valor          = int(m_min.group(2))
            unidade        = m_min.group(3)
            minutos        = valor * 60 if "hora" in unidade else valor
            return self.agendar_lembrete(texto_lembrete, minutos=minutos)

        # Agendamento simples sem tempo — padrão 30 minutos
        m_simples = re.search(
            r"(?:lembra|me avisa|me lembra)\s+(?:de\s+)?(.+)", t
        )
        if m_simples:
            return self.agendar_lembrete(m_simples.group(1).strip(), minutos=30)

        return "Não entendi o lembrete. Tente: 'me lembra de reunião em 60 minutos'."

    # =========================================================================
    # Loop principal
    # =========================================================================

    def _loop(self):
        """
        Loop de monitoramento com integração ao SiriusFoco.

        Ciclos:
          A cada 30s  → lembretes, hardware, hora, foco
          A cada 2min (ciclo % 4)  → processos pesados
          A cada 5min (ciclo % 10) → clima
          A cada 3min (ciclo % 6)  → OCR automático (só em DESENVOLVIMENTO)
        """
        print("\033[94m[PROATIVO]: Loop iniciado.\033[0m")

        ciclo        = 0
        ts_ocr       = 0.0
        ctx_anterior = None
        OCR_INTERVALO = 180

        while self._rodando:
            time.sleep(30)
            ciclo += 1
            alertas: list[str] = []

            # ── 1. Lembretes ──────────────────────────────────────────────────
            try:
                alertas += self._lembretes.verificar(self._user_id)
            except Exception as e:
                print(f"[PROATIVO]: Erro em lembretes: {e}")

            # ── 2. Hardware ───────────────────────────────────────────────────
            try:
                alertas += self._monitor_sistema.verificar()
            except Exception as e:
                print(f"[PROATIVO]: Erro em hardware: {e}")

            # ── 3. Hora ───────────────────────────────────────────────────────
            try:
                alertas += self._monitor_hora.verificar()
            except Exception as e:
                print(f"[PROATIVO]: Erro em hora: {e}")

            # ── 4. Processos (a cada 2 min) ───────────────────────────────────
            if ciclo % 4 == 0:
                try:
                    alertas += self._monitor_processos.verificar()
                except Exception as e:
                    print(f"[PROATIVO]: Erro em processos: {e}")

            # ── 5. Clima (a cada 5 min) ───────────────────────────────────────
            if ciclo % 10 == 0:
                try:
                    alertas += self._monitor_clima.verificar()
                except Exception as e:
                    print(f"[PROATIVO]: Erro em clima: {e}")

            # ── 6. SiriusFoco (context-awareness) ─────────────────────────────
            if self._foco is not None:
                try:
                    ctx_atual = self._foco.obter_contexto_atual()

                    # Sugestão proativa ao mudar de contexto
                    if ctx_atual != ctx_anterior:
                        ctx_anterior = ctx_atual
                        sug = self._foco.verificar_sugestao_proativa()
                        if sug:
                            alertas.append(sug)
                            print(
                                f"\033[93m[PROATIVO FOCO]: ({ctx_atual}) {sug}\033[0m"
                            )

                    # OCR automático de erros (só em DESENVOLVIMENTO)
                    agora = time.time()
                    if (
                        ctx_atual == "DESENVOLVIMENTO"
                        and agora - ts_ocr >= OCR_INTERVALO
                    ):
                        ts_ocr = agora
                        threading.Thread(
                            target=self._verificar_erros_ocr,
                            daemon=True,
                            name="SiriusProativo-OCR",
                        ).start()

                except Exception as e:
                    print(f"[PROATIVO]: Erro no SiriusFoco: {e}")

            # ── 7. Briefing matinal ────────────────────────────────────────────
            try:
                if self._briefing.deve_disparar():
                    self._briefing.marcar_disparado()
                    threading.Thread(
                        target=self._disparar_briefing,
                        daemon=True,
                    ).start()
                    continue   # não empilha alertas com briefing
            except Exception as e:
                print(f"[PROATIVO]: Erro em briefing: {e}")

            # ── 8. Dispara alertas acumulados ─────────────────────────────────
            for msg in alertas:
                threading.Thread(
                    target=self._disparar, args=(msg,), daemon=True
                ).start()
                time.sleep(2)   # espaça alertas para não sobrecarregar TTS

    # =========================================================================
    # Helpers internos
    # =========================================================================

    def _disparar(self, mensagem: str):
        """Envia um alerta via callback_falar e callback_log."""
        if not mensagem or not mensagem.strip():
            return
        mensagem = mensagem.strip()
        print(f"\033[93m[PROATIVO]: {mensagem}\033[0m")

        if self._log:
            try:
                self._log(mensagem)
            except Exception:
                pass

        if self._falar:
            try:
                self._falar(mensagem)
            except Exception as e:
                print(f"[PROATIVO]: Erro ao falar: {e}")

    def _disparar_briefing(self):
        """Gera e dispara o briefing matinal."""
        try:
            texto = self._briefing.gerar()

            # Enriquece com dados do cerebro se disponível
            if self._cerebro and hasattr(self._cerebro, "processar"):
                try:
                    extra = self._cerebro.processar(
                        "resumo rápido do dia — lembretes e tarefas"
                    )
                    if extra and len(extra) < 200:
                        texto += f" {extra}"
                except Exception:
                    pass

            self._disparar(texto)
        except Exception as e:
            print(f"[PROATIVO]: Erro no briefing: {e}")

    def _verificar_lembretes(self) -> list[str]:
        """Wrapper público — usado quando SiriusProativo é subclassado."""
        return self._lembretes.verificar(self._user_id)

    def _verificar_erros_ocr(self):
        """OCR automático de erros na tela (contexto DESENVOLVIMENTO)."""
        if not self._foco:
            return
        try:
            resultado = self._foco.detectar_erros_na_tela()
            if resultado:
                print(f"\033[93m[PROATIVO OCR]: {resultado}\033[0m")
                self._disparar(resultado)
        except Exception as e:
            print(f"[PROATIVO]: Erro no OCR: {e}")


# =============================================================================
# Singleton global
# =============================================================================

_proativo_instance: Optional[SiriusProativo] = None
_proativo_lock = threading.Lock()


def get_proativo(
    callback_falar: Optional[Callable] = None,
    callback_log:   Optional[Callable] = None,
    cerebro=None,
) -> SiriusProativo:
    """Retorna a instância singleton do SiriusProativo."""
    global _proativo_instance
    with _proativo_lock:
        if _proativo_instance is None:
            _proativo_instance = SiriusProativo(
                callback_falar = callback_falar,
                callback_log   = callback_log,
                cerebro        = cerebro,
            )
    return _proativo_instance


# =============================================================================
# Standalone — smoke test
# =============================================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  SiriusProativo — Teste Standalone")
    print("=" * 55)

    def _falar(txt):
        print(f"  🔊 {txt}")

    p = SiriusProativo(callback_falar=_falar)

    print("\n[1] Agendando lembrete em 1 minuto...")
    print(" ", p.agendar_lembrete("teste de lembrete", minutos=1))

    print("\n[2] Listando lembretes...")
    print(" ", p.listar_lembretes())

    print("\n[3] Hardware...")
    alertas = p._monitor_sistema.verificar()
    print(f"  Alertas: {alertas or '(nenhum)'}")

    print("\n[4] Hora...")
    alertas = p._monitor_hora.verificar()
    print(f"  Alertas: {alertas or '(nenhum)'}")

    print("\n[5] Iniciando monitor por 90s (aguarde lembretes)...")
    p.iniciar()

    try:
        time.sleep(90)
    except KeyboardInterrupt:
        pass

    p.parar()
    print("\n✓ Teste concluído.")