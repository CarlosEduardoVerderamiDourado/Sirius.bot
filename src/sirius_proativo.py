"""
sirius_proativo.py — Proatividade completa estilo Jarvis

O Sirius fala sem ser perguntado quando algo importante acontece:

  LEMBRETES  → "me lembra às 15h de tomar remédio"
  BATERIA    → "Chefia, bateria em 15%! Conecta o carregador."
  CPU/RAM    → "Memória em 92%, vai travar não."
  CLIMA      → "Bom dia! Hoje em Guarulhos: 28°C, chance de chuva 70%."
  HORA       → Bom dia / boa tarde / boa noite automático (1x por período)

Integração com cerebro.py (já feita):
    self._proativo = SiriusProativo()
    self._proativo.iniciar()
    self._proativo.registrar_callback(audio.falar, interface.log_sirius)
"""

import os
import sys
import re
import time
import json
import threading
from datetime import datetime, timedelta

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_LEMBRETES = os.path.join(diretorio_raiz, "data", "lembretes.json")

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES       = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
               "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _periodo_do_dia(hora: int = None) -> str:
    h = hora if hora is not None else datetime.now().hour
    if 5  <= h < 12: return "manhã"
    if 12 <= h < 18: return "tarde"
    if 18 <= h < 23: return "noite"
    return "madrugada"


def _saudacao() -> str:
    return {"manhã": "Bom dia", "tarde": "Boa tarde",
            "noite": "Boa noite", "madrugada": "Boa madrugada"}[_periodo_do_dia()]


def _extrair_horario(texto: str):
    t = texto.lower()
    m = re.search(r"(?:daqui|em)\s+(?:a\s+)?(\d+)\s*min(?:utos?)?", t)
    if m:
        alvo = datetime.now() + timedelta(minutes=int(m.group(1)))
        return (alvo.hour, alvo.minute)
    m = re.search(r"em\s+(\d+)\s*hora(?:s)?", t)
    if m:
        alvo = datetime.now() + timedelta(hours=int(m.group(1)))
        return (alvo.hour, alvo.minute)
    m = re.search(r"(\d{1,2})[h:](\d{2})", t)
    if m:
        return _ajustar(int(m.group(1)), int(m.group(2)), t)
    m = re.search(r"(\d{1,2})\s*h(?:oras?)?(?!\d)", t)
    if m:
        return _ajustar(int(m.group(1)), 0, t)
    m = re.search(r"(?:as?|às?)\s+(\d{1,2})(?:\s|$)", t)
    if m:
        return _ajustar(int(m.group(1)), 0, t)
    return None


def _ajustar(h, mn, texto):
    if any(p in texto for p in ["tarde", "noite"]):
        if h < 12: h += 12
    elif any(p in texto for p in ["manhã", "manha"]):
        if h == 12: h = 0
    elif h < 7:
        h += 12
    return (h % 24, mn)


def _extrair_descricao(texto: str) -> str:
    t = texto.lower()
    for r_ in [r"sirius,?\s*", r"me lembra\s*(?:de\s*)?", r"lembra\s*(?:de\s*)?",
               r"agenda(?:r)?\s*(?:um?\s*)?lembrete\s*(?:para\s*)?",
               r"cria\s*(?:um?\s*)?lembrete\s*(?:para\s*)?",
               r"bota\s*(?:um?\s*)?lembrete\s*(?:para\s*)?",
               r"coloca\s*(?:um?\s*)?lembrete\s*(?:para\s*)?",
               r"me avisa\s*(?:de\s*)?"]:
        t = re.sub(r_, "", t).strip()
    for p in [r"(?:às?|as?|para\s+as?)\s+\d{1,2}[h:]\d{2}",
              r"(?:às?|as?|para\s+as?)\s+\d{1,2}\s*h(?:oras?)?",
              r"(?:às?|as?|para\s+as?)\s+\d{1,2}(?:\s|$)",
              r"daqui\s+(?:a\s+)?\d+\s*min(?:utos?)?",
              r"em\s+\d+\s*(?:minutos?|horas?)",
              r"\d{1,2}[h:]\d{2}", r"da\s+(?:manhã|manha|tarde|noite)"]:
        t = re.sub(p, "", t).strip()
    t = re.sub(r"^(de|para|que|em|a|o|um|uma)\s+", "", t).strip()
    return re.sub(r"\s+", " ", t).strip() or "Lembrete"


# ---------------------------------------------------------------------------
# Lembrete
# ---------------------------------------------------------------------------

class Lembrete:
    def __init__(self, hora, minuto, descricao, repetir=False):
        self.hora      = hora
        self.minuto    = minuto
        self.descricao = descricao
        self.repetir   = repetir
        self.disparado = False
        self.criado_em = datetime.now().isoformat()

    def deve_disparar(self) -> bool:
        if self.disparado and not self.repetir:
            return False
        agora  = datetime.now()
        target = agora.replace(hour=self.hora, minute=self.minuto, second=0, microsecond=0)
        return abs((agora - target).total_seconds()) <= 30

    def mensagem(self) -> str:
        return f"🔔 Lembrete, chefe: {self.descricao}."

    def to_dict(self):
        return {"hora": self.hora, "minuto": self.minuto,
                "descricao": self.descricao, "repetir": self.repetir,
                "disparado": self.disparado, "criado_em": self.criado_em}

    @classmethod
    def from_dict(cls, d):
        l = cls(d["hora"], d["minuto"], d["descricao"], d.get("repetir", False))
        l.disparado = d.get("disparado", False)
        return l

    def __str__(self):
        rep = " (todo dia)" if self.repetir else ""
        return f"{self.hora:02d}:{self.minuto:02d} — {self.descricao}{rep}"


# ---------------------------------------------------------------------------
# Monitor de sistema
# ---------------------------------------------------------------------------

class MonitorSistema:
    def __init__(self):
        self._ultimo: dict[str, float] = {}
        self._COOLDOWN = {
            "bateria_critica": 600,
            "bateria_baixa":   1800,
            "ram_alta":        3600,
            "cpu_alta":        3600,
            "disco_cheio":     7200,
        }

    def _pode(self, tipo: str) -> bool:
        agora = time.time()
        if agora - self._ultimo.get(tipo, 0) >= self._COOLDOWN[tipo]:
            self._ultimo[tipo] = agora
            return True
        return False

    def verificar(self) -> list[str]:
        """Monitora recursos do sistema e retorna alertas no tom Jarvis."""
        alertas = []
        try:
            import psutil

            # --- Bateria ---
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged:
                pct = bat.percent
                if pct <= 10 and self._pode("bateria_critica"):
                    alertas.append(
                        f"⚠ Bateria crítica — {pct:.0f}%. "
                        "Conecte o carregador imediatamente, chefe."
                    )
                elif pct <= 20 and self._pode("bateria_baixa"):
                    mins = int(bat.secsleft / 60) if bat.secsleft and bat.secsleft > 0 else 0
                    tempo = f" Autonomia restante: ~{mins} minutos." if mins > 5 else ""
                    alertas.append(
                        f"⚠ Bateria em {pct:.0f}%.{tempo} Recomendo conectar o carregador."
                    )

            # --- RAM ---
            mem = psutil.virtual_memory()
            if mem.percent >= 90 and self._pode("ram_alta"):
                livre_gb = mem.available / (1024 ** 3)
                alertas.append(
                    f"⚠ Memória em {mem.percent:.0f}% — apenas {livre_gb:.1f} GB disponíveis. "
                    "Considere fechar aplicações pesadas."
                )

            # --- CPU ---
            cpu = psutil.cpu_percent(interval=0.5)
            if cpu >= 95 and self._pode("cpu_alta"):
                alertas.append(
                    f"⚠ CPU em {cpu:.0f}%. Algum processo está consumindo excessivamente."
                )

            # --- Disco ---
            disco = psutil.disk_usage("/")
            if disco.percent >= 90 and self._pode("disco_cheio"):
                livre_gb = disco.free / (1024 ** 3)
                alertas.append(
                    f"⚠ Disco em {disco.percent:.0f}% — {livre_gb:.1f} GB livres. "
                    "Limpeza recomendada."
                )

            # --- Novo: aviso de CPU alta sustentada (70%+ por muito tempo) ---
            if cpu >= 70 and self._pode("cpu_moderada") if "cpu_moderada" in self._COOLDOWN else False:
                alertas.append(f"ℹ CPU em {cpu:.0f}%. Sistema sob carga moderada.")

        except ImportError:
            pass
        except Exception as e:
            print(f"[PROATIVO]: Erro no monitor de sistema: {e}")
        return alertas


# ---------------------------------------------------------------------------
# Monitor de processos — detecta apps que estão pesando
# ---------------------------------------------------------------------------

class MonitorProcessos:
    """
    Detecta processos pesados rodando em background e alerta o usuário.
    Útil quando o PC fica lento sem motivo aparente.
    """
    def __init__(self):
        self._ultimo_alerta: float = 0
        self._COOLDOWN = 1800  # 30 minutos entre alertas

    def verificar(self) -> list[str]:
        agora = time.time()
        if agora - self._ultimo_alerta < self._COOLDOWN:
            return []
        try:
            import psutil
            # Só verifica se CPU estiver alta
            cpu_total = psutil.cpu_percent(interval=0.3)
            if cpu_total < 80:
                return []

            # Pega top 3 processos por CPU
            procs = []
            for p in psutil.process_iter(['name', 'cpu_percent', 'memory_percent']):
                try:
                    if p.info['cpu_percent'] and p.info['cpu_percent'] > 10:
                        procs.append(p.info)
                except Exception:
                    pass

            if not procs:
                return []

            procs.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)
            top = procs[:3]
            nomes = ', '.join(
                f"{p['name']} ({p['cpu_percent']:.0f}%)" for p in top
            )
            self._ultimo_alerta = agora
            return [f"ℹ Processos pesados detectados: {nomes}."]

        except ImportError:
            return []
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Monitor de clima proativo
# ---------------------------------------------------------------------------

class MonitorClima:
    def __init__(self):
        self._dia_saudacao       = ""
        self._ultimo_alerta_chuva = 0

    def verificar(self) -> list[str]:
        alertas = []
        agora   = datetime.now()
        hoje    = agora.date().isoformat()

        # Bom dia com clima (6h–9h, 1x por dia)
        if 6 <= agora.hour <= 9 and hoje != self._dia_saudacao:
            self._dia_saudacao = hoje
            msg = self._saudacao_clima()
            if msg:
                alertas.append(msg)

        # Alerta de chuva — só de manhã (7h–11h), máx 1x por dia
        # Fora desse horário não faz sentido avisar sobre chuva sem ser pedido
        if 7 <= agora.hour <= 11:
            if time.time() - self._ultimo_alerta_chuva > 86400:  # 1x por dia
                msg = self._checar_chuva()
                if msg:
                    self._ultimo_alerta_chuva = time.time()
                    alertas.append(msg)

        return alertas

    def _saudacao_clima(self) -> str | None:
        try:
            from sirius_tempo_real import obter_clima_atual, _detectar_cidade_por_ip
            cidade = _detectar_cidade_por_ip()
            clima  = obter_clima_atual(cidade)
            return f"{_saudacao()}, chefia! {clima}"
        except Exception:
            return None

    def _checar_chuva(self) -> str | None:
        try:
            from sirius_tempo_real import vai_chover, _detectar_cidade_por_ip
            resultado = vai_chover(_detectar_cidade_por_ip())
            if resultado and any(p in resultado.lower() for p in
                                 ["boa chance", "pode chover", "chuva forte"]):
                return resultado
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Monitor de hora (saudação automática)
# ---------------------------------------------------------------------------

class MonitorHora:
    _PERIODOS = {"manha": (6, 11), "tarde": (12, 17), "noite": (18, 22)}

    def __init__(self):
        self._cumprimentados: set = set()
        self._ultimo_dia = ""

    def verificar(self) -> list[str]:
        agora = datetime.now()
        hoje  = agora.date().isoformat()
        if hoje != self._ultimo_dia:
            self._ultimo_dia = hoje
            self._cumprimentados.clear()

        hora = agora.hour
        for periodo, (ini, fim) in self._PERIODOS.items():
            if ini <= hora <= fim and periodo not in self._cumprimentados:
                self._cumprimentados.add(periodo)
                saud    = {"manha": "Bom dia", "tarde": "Boa tarde", "noite": "Boa noite"}[periodo]
                dia_sem = DIAS_SEMANA[agora.weekday()]
                return [f"{saud}, chefia! Hoje é {dia_sem}, {agora.day} de {MESES[agora.month]}."]
        return []


# ---------------------------------------------------------------------------
# Monitor de concentração — avisa após uso contínuo longo sem pausa
# ---------------------------------------------------------------------------

class MonitorConcentracao:
    """
    Jarvis cuida do chefe.
    Após 90 minutos de uso contínuo, avisa para fazer uma pausa.
    Reseta ao detectar inatividade (scheduler em standby).
    """
    TEMPO_AVISO_MIN  = 90   # minutos de uso contínuo antes de avisar
    COOLDOWN_AVISO   = 3600 # 1h entre avisos

    def __init__(self):
        self._inicio_sessao  : float = time.time()
        self._ultimo_aviso   : float = 0
        self._em_standby     : bool  = False

    def registrar_atividade(self):
        """Chamado pelo cerebro a cada interação."""
        if self._em_standby:
            self._em_standby    = False
            self._inicio_sessao = time.time()  # reseta contador

    def registrar_standby(self):
        """Chamado pelo scheduler quando entra em standby."""
        self._em_standby = True

    def verificar(self) -> list[str]:
        agora = time.time()
        if self._em_standby:
            return []
        tempo_uso = (agora - self._inicio_sessao) / 60  # em minutos
        if tempo_uso >= self.TEMPO_AVISO_MIN:
            if agora - self._ultimo_aviso >= self.COOLDOWN_AVISO:
                self._ultimo_aviso = agora
                h = int(tempo_uso // 60)
                m = int(tempo_uso % 60)
                duracao = f"{h}h{m:02d}min" if h > 0 else f"{m} minutos"
                return [
                    f"ℹ Chefe, {duracao} de uso contínuo. "
                    "Considere fazer uma pausa."
                ]
        return []


# ---------------------------------------------------------------------------
# Briefing Matinal — fala sem ser perguntado todo dia de manhã
# ---------------------------------------------------------------------------

class BriefingMatinal:
    """
    Gera e fala um briefing completo todo dia, automaticamente.

    Conteúdo (na ordem):
      1. Saudação + data/dia da semana
      2. Clima do dia (temperatura, chuva)
      3. Lembretes do dia (se tiver)
      4. Status do sistema (bateria, se notebook)
      5. Frase motivacional aleatória

    Horário padrão: entre 7h e 9h, 1x por dia.
    Configurável via: briefing.configurar(hora_inicio=7, hora_fim=9)

    Também pode ser acionado sob demanda:
      "sirius, briefing"
      "sirius, me dá o resumo do dia"
      "sirius, o que tem pra hoje"
    """

    _FRASES_MOTIVACIONAIS = [
        "Sistemas operacionais. Pronto para a missão, chefe.",
        "Todos os sistemas nominais. É hora de trabalhar.",
        "Aguardando instruções, chefe.",
        "Pronto quando o senhor estiver.",
        "O dia é seu, chefe. Estou à disposição.",
        "Operacional e à postos.",
        "Missão do dia recebida. Pode contar comigo.",
        "Monitorando todos os sistemas. Tudo nominal por enquanto.",
    ]

    def __init__(self, lembretes_ref: list):
        """
        lembretes_ref: referência à lista de lembretes do SiriusProativo
        """
        self._lembretes   = lembretes_ref
        self._ultimo_dia  = ""          # controla 1x por dia
        self._hora_inicio = 7
        self._hora_fim    = 9

    def configurar(self, hora_inicio: int = 7, hora_fim: int = 9):
        """Muda a janela horária do briefing."""
        self._hora_inicio = hora_inicio
        self._hora_fim    = hora_fim

    def deve_disparar(self) -> bool:
        """Retorna True se está na janela horária e ainda não disparou hoje."""
        agora = datetime.now()
        hoje  = agora.date().isoformat()
        hora  = agora.hour
        return (
            hoje != self._ultimo_dia and
            self._hora_inicio <= hora <= self._hora_fim
        )

    def marcar_disparado(self):
        self._ultimo_dia = datetime.now().date().isoformat()

    def gerar(self) -> list[str]:
        """
        Gera o briefing como lista de partes para falar em sequência.
        Cada parte é uma frase curta — permite pausas naturais entre elas.
        Usa o perfil do usuário se disponível.
        """
        partes = []
        agora  = datetime.now()

        # Busca nome do perfil se disponível
        nome_usuario = "chefia"
        try:
            from sirius_perfil import get_perfil
            nome_usuario = get_perfil().nome_usuario()
        except Exception:
            pass

        # ── 1. Saudação + data ────────────────────────────────────────────
        dia_sem = DIAS_SEMANA[agora.weekday()]
        dia_num = agora.day
        mes     = MESES[agora.month]
        hora    = agora.strftime("%H:%M")
        partes.append(
            f"Bom dia, {nome_usuario}. São {hora} de {dia_sem}, "
            f"{dia_num} de {mes}. Sistemas operacionais."
        )

        # ── 2. Clima ──────────────────────────────────────────────────────
        try:
            from sirius_tempo_real import obter_clima_atual, _detectar_cidade_por_ip
            cidade = _detectar_cidade_por_ip()
            clima  = obter_clima_atual(cidade)
            # Encurta se muito longo
            if len(clima) > 150:
                clima = clima[:150].rsplit(".", 1)[0] + "."
            partes.append(f"Clima de hoje: {clima}")
        except Exception:
            pass

        # ── 3. Chuva ──────────────────────────────────────────────────────
        try:
            from sirius_tempo_real import vai_chover, _detectar_cidade_por_ip
            chuva = vai_chover(_detectar_cidade_por_ip())
            if chuva and any(p in chuva.lower() for p in
                             ["boa chance", "pode chover", "chuva forte", "sim"]):
                partes.append(chuva)
        except Exception:
            pass

        # ── 4. Lembretes do dia ───────────────────────────────────────────
        lembretes_hoje = [
            l for l in self._lembretes
            if not (l.disparado and not l.repetir)
        ]
        if lembretes_hoje:
            if len(lembretes_hoje) == 1:
                l = lembretes_hoje[0]
                partes.append(
                    f"Você tem um lembrete hoje: {l.hora:02d}:{l.minuto:02d}, {l.descricao}."
                )
            else:
                lista = ", ".join(
                    f"{l.hora:02d}:{l.minuto:02d} {l.descricao}"
                    for l in lembretes_hoje[:4]
                )
                partes.append(
                    f"Você tem {len(lembretes_hoje)} lembretes hoje: {lista}."
                )
        else:
            partes.append("Nenhum lembrete agendado para hoje.")

        # ── 5. Status da bateria (só se notebook e não carregando) ────────
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged and bat.percent < 80:
                partes.append(
                    f"Bateria em {bat.percent:.0f}%. "
                    "Lembra de carregar antes de sair."
                )
        except Exception:
            pass

        # ── 6. Frase do dia ───────────────────────────────────────────────
        import random
        partes.append(random.choice(self._FRASES_MOTIVACIONAIS))

        return partes

    def gerar_texto_completo(self) -> str:
        """Versão em texto único — para exibir no chat."""
        return " ".join(self.gerar())


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

class SiriusProativo:
    """
    Gerencia todos os alertas proativos do Sirius.
    Verifica a cada 30 segundos.
    """

    def __init__(self, callback_falar=None, callback_log=None):
        self._falar    = callback_falar or print
        self._log      = callback_log
        self._lembretes: list[Lembrete] = []
        self._lock     = threading.Lock()
        self._rodando  = False
        self._thread   = None

        self._monitor_sistema   = MonitorSistema()
        self._monitor_clima     = MonitorClima()
        self._monitor_hora      = MonitorHora()
        self._monitor_foco      = MonitorConcentracao()   # rastreia uso contínuo
        self._monitor_processos = MonitorProcessos()      # processos pesados
        self._briefing          = BriefingMatinal(self._lembretes)

        self._carregar()

    def registrar_atividade_usuario(self):
        """
        Chamado pelo cerebro.py a cada interação do usuário.
        Alimenta o MonitorConcentracao para rastrear tempo de uso.
        """
        self._monitor_foco.registrar_atividade()

    def registrar_standby(self):
        """Chamado pelo scheduler quando entra em standby."""
        self._monitor_foco.registrar_standby()

    def registrar_callback(self, callback_falar, callback_log=None):
        self._falar = callback_falar
        if callback_log:
            self._log = callback_log

    # -----------------------------------------------------------------------
    # Persistência
    # -----------------------------------------------------------------------

    def _salvar(self):
        try:
            os.makedirs(os.path.dirname(CAMINHO_LEMBRETES), exist_ok=True)
            with open(CAMINHO_LEMBRETES, "w", encoding="utf-8") as f:
                json.dump([l.to_dict() for l in self._lembretes], f,
                          ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PROATIVO]: Erro ao salvar: {e}")

    def _carregar(self):
        try:
            if os.path.exists(CAMINHO_LEMBRETES):
                with open(CAMINHO_LEMBRETES, encoding="utf-8") as f:
                    dados = json.load(f)
                with self._lock:
                    self._lembretes = [Lembrete.from_dict(d) for d in dados]
                    self._lembretes = [l for l in self._lembretes
                                       if not (l.disparado and not l.repetir)]
                if self._lembretes:
                    print(f"\033[92m[PROATIVO]: {len(self._lembretes)} lembrete(s) carregado(s).\033[0m")
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Loop
    # -----------------------------------------------------------------------

    def _loop(self):
        print("\033[94m[PROATIVO]: Monitor de alertas ativo.\033[0m")
        _ciclo = 0
        while self._rodando:
            time.sleep(30)
            _ciclo += 1

            alertas = []
            alertas += self._verificar_lembretes()
            alertas += self._monitor_sistema.verificar()
            alertas += self._monitor_hora.verificar()
            alertas += self._monitor_foco.verificar()

            if _ciclo % 4 == 0:   # processos pesados a cada ~2min
                alertas += self._monitor_processos.verificar()

            if _ciclo % 10 == 0:  # clima a cada ~5min
                alertas += self._monitor_clima.verificar()

            # Briefing matinal — verifica a cada ciclo, dispara 1x por dia
            if self._briefing.deve_disparar():
                self._briefing.marcar_disparado()
                threading.Thread(
                    target=self._disparar_briefing,
                    daemon=True
                ).start()
                continue  # pula outros alertas — briefing já é completo

            for msg in alertas:
                threading.Thread(target=self._disparar, args=(msg,), daemon=True).start()
                time.sleep(2)

    def _disparar_briefing(self):
        """
        Dispara o briefing matinal falando cada parte separadamente.
        Pausa natural entre cada frase — não vira um monólogo.
        """
        print("\n\033[93m[PROATIVO]: Iniciando briefing matinal...\033[0m")
        partes = self._briefing.gerar()

        for i, parte in enumerate(partes):
            print(f"\033[93m[BRIEFING {i+1}/{len(partes)}]: {parte}\033[0m")
            try:
                self._falar(parte)
            except Exception as e:
                print(f"[PROATIVO]: Erro ao falar parte do briefing: {e}")

            if self._log:
                try:
                    prefixo = "🌅" if i == 0 else "📋" if i < len(partes)-1 else "⚡"
                    self._log(f"{prefixo} {parte}")
                except Exception:
                    pass

            # Pausa entre partes para não soar robotizado
            if i < len(partes) - 1:
                time.sleep(1.5)

        print("\033[93m[PROATIVO]: Briefing matinal concluído.\033[0m")

    def _verificar_lembretes(self) -> list[str]:
        msgs = []
        with self._lock:
            for l in self._lembretes:
                if l.deve_disparar():
                    l.disparado = True
                    msgs.append(l.mensagem())
            if msgs:
                self._salvar()
        return msgs

    def _disparar(self, mensagem: str):
        print(f"\n\033[93m[PROATIVO]: {mensagem}\033[0m")
        try:
            self._falar(mensagem)
        except Exception as e:
            print(f"[PROATIVO]: Erro ao falar: {e}")
        if self._log:
            try:
                self._log(f"🔔 {mensagem}")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Comandos de voz
    # -----------------------------------------------------------------------

    _TRIGGERS_LEMBRETE = {
        "me lembra", "lembra de", "lembra que", "me avisa",
        "agenda lembrete", "cria lembrete", "bota lembrete",
        "coloca lembrete", "adiciona lembrete", "novo lembrete",
        "lembrete para", "lembrete às", "lembrete as",
        # Alarme — sinônimo de lembrete para o Sirius
        "programa um alarme", "programa alarme", "cria um alarme",
        "bota um alarme", "coloca um alarme", "agenda um alarme",
        "alarme para", "alarme às", "alarme as", "alarme em",
        "set alarm", "alarme daqui",
    }
    _TRIGGERS_LISTAR   = {
        "meus lembretes", "quais lembretes", "lista lembretes",
        "ver lembretes", "mostra lembretes", "tem lembrete",
    }
    _TRIGGERS_CANCELAR = {
        "cancela lembrete", "cancela todos", "remove lembrete",
        "apaga lembrete", "deleta lembrete", "limpa lembretes",
    }
    _TRIGGERS_STATUS = {
        "como ta o sistema", "como está o sistema", "status do sistema",
        "como ta o pc", "como está o pc", "estado do pc",
        "nivel da bateria", "nível da bateria",
        "quanto tem de bateria", "memoria disponivel",
    }
    _TRIGGERS_BRIEFING = {
        "briefing", "resumo do dia", "o que tem pra hoje",
        "o que tem hoje", "me dá o briefing", "me da o briefing",
        "resumo matinal", "relatorio do dia", "relatório do dia",
        "me atualiza", "novidades do dia", "como ta o dia",
        "o que acontece hoje", "agenda do dia",
    }

    def e_comando_proativo(self, texto: str) -> bool:
        t = texto.lower()
        return (
            any(tr in t for tr in self._TRIGGERS_LEMBRETE) or
            any(tr in t for tr in self._TRIGGERS_LISTAR) or
            any(tr in t for tr in self._TRIGGERS_CANCELAR) or
            any(tr in t for tr in self._TRIGGERS_STATUS) or
            any(tr in t for tr in self._TRIGGERS_BRIEFING)
        )

    def processar_comando(self, texto: str) -> str:
        t = texto.lower().strip()

        if any(tr in t for tr in self._TRIGGERS_BRIEFING):
            return self._briefing_sob_demanda()
        if any(tr in t for tr in self._TRIGGERS_LISTAR):
            return self._listar_lembretes()
        if any(tr in t for tr in self._TRIGGERS_CANCELAR):
            return self._cancelar_lembretes(t)
        if any(tr in t for tr in self._TRIGGERS_STATUS):
            return self._status_sistema()
        if any(tr in t for tr in self._TRIGGERS_LEMBRETE):
            return self._adicionar_lembrete(texto)

        return "Não entendi. Tente: 'me lembra às 15h de tomar remédio'."

    def _briefing_sob_demanda(self) -> str:
        """
        Dispara o briefing imediatamente quando solicitado.
        Fala em background e retorna confirmação.
        """
        threading.Thread(target=self._disparar_briefing, daemon=True).start()
        return "Preparando briefing. Um momento, chefe."

    def configurar_briefing(self, hora_inicio: int = 7, hora_fim: int = 9):
        """Configura o horário do briefing matinal."""
        self._briefing.configurar(hora_inicio, hora_fim)
        return f"Briefing configurado para entre {hora_inicio:02d}h e {hora_fim:02d}h."

    def _adicionar_lembrete(self, texto: str) -> str:
        resultado = _extrair_horario(texto)
        if not resultado:
            return (
                "Não consegui o horário. "
                "Tente: 'me lembra às 15h de tomar remédio' "
                "ou 'me lembra em 30 minutos de ligar'."
            )
        hora, minuto = resultado
        descricao    = _extrair_descricao(texto)
        repetir      = any(p in texto.lower() for p in
                           ["todo dia", "todos os dias", "diariamente"])
        with self._lock:
            self._lembretes.append(Lembrete(hora, minuto, descricao, repetir))
            self._salvar()
        rep = " (recorrente)" if repetir else ""
        return f"Registrado. {hora:02d}:{minuto:02d}{rep} — {descricao}."

    def _listar_lembretes(self) -> str:
        with self._lock:
            ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]
        if not ativos:
            return "Nenhum lembrete agendado, chefe."
        linhas = ["Lembretes agendados:"]
        for i, l in enumerate(ativos, 1):
            linhas.append(f"  {i}. {l}")
        return "\n".join(linhas)

    def _cancelar_lembretes(self, texto: str) -> str:
        with self._lock:
            if any(p in texto for p in ["todos", "tudo"]):
                n = len(self._lembretes)
                self._lembretes.clear()
                self._salvar()
                return f"{n} lembrete(s) cancelado(s)."

            m = re.search(r"(\d+)", texto)
            if m:
                idx    = int(m.group(1)) - 1
                ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]
                if 0 <= idx < len(ativos):
                    self._lembretes.remove(ativos[idx])
                    self._salvar()
                    return f"Lembrete '{ativos[idx].descricao}' cancelado."
                return "Número inválido."

            if self._lembretes:
                rem = self._lembretes.pop(0)
                self._salvar()
                return f"Lembrete '{rem.descricao}' cancelado."
        return "Sem lembretes para cancelar."

    def _status_sistema(self) -> str:
        """Formato Jarvis: 'Online. CPU 12% | RAM 45%. Tudo nominal.'"""
        try:
            import psutil
            from filtro_zoeiro import SiriusFiltro

            cpu       = psutil.cpu_percent(interval=0.5)
            mem       = psutil.virtual_memory()
            disco     = psutil.disk_usage("/")
            bat       = psutil.sensors_battery()
            bateria   = bat.percent if bat else None

            msg = SiriusFiltro.formatar_status(
                cpu          = cpu,
                ram          = mem.percent,
                ram_usada_mb = mem.used   // (1024 * 1024),
                ram_total_mb = mem.total  // (1024 * 1024),
                bateria      = bateria,
                disco        = disco.percent,
            )

            # Adiciona info de bateria se estiver descarregando
            if bat and not bat.power_plugged and bat.percent <= 30:
                mins = int(bat.secsleft / 60) if bat.secsleft and bat.secsleft > 0 else 0
                sufixo = f" ~{mins}min restantes." if mins > 0 else ""
                msg += f"{sufixo} Conecta o carregador."

            return msg

        except ImportError:
            return "Instala psutil: pip install psutil"
        except Exception as e:
            return f"Erro ao verificar sistema: {e}"

    # -----------------------------------------------------------------------
    # Controle
    # -----------------------------------------------------------------------

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="SiriusProativo")
        self._thread.start()
        print("\033[92m[PROATIVO]: Alertas proativos ativos.\033[0m")

    def parar(self):
        self._rodando = False