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
        return f"Chefia, lembrete: {self.descricao}!"

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
        alertas = []
        try:
            import psutil

            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged:
                pct = bat.percent
                if pct <= 10 and self._pode("bateria_critica"):
                    alertas.append(
                        f"Chefia, bateria crítica em {pct:.0f}%! "
                        "Conecta o carregador ou vai desligar."
                    )
                elif pct <= 20 and self._pode("bateria_baixa"):
                    mins = int(bat.secsleft / 60) if bat.secsleft and bat.secsleft > 0 else 0
                    sufixo = f" Restam uns {mins} minutos." if mins > 5 else ""
                    alertas.append(f"Bateria em {pct:.0f}%, chefia.{sufixo} Bora plugar.")

            mem = psutil.virtual_memory()
            if mem.percent >= 90 and self._pode("ram_alta"):
                alertas.append(
                    f"Memória em {mem.percent:.0f}%, chefia. "
                    "Pode começar a travar. Fecha algum programa pesado."
                )

            cpu = psutil.cpu_percent(interval=0.5)
            if cpu >= 95 and self._pode("cpu_alta"):
                alertas.append(f"CPU em {cpu:.0f}% há um tempinho. Tem algo pesado rodando.")

            disco = psutil.disk_usage("/")
            if disco.percent >= 95 and self._pode("disco_cheio"):
                livre = disco.free / (1024 ** 3)
                alertas.append(
                    f"Disco quase cheio, chefia! Só {livre:.1f} GB livres. "
                    "Hora de limpar umas coisas."
                )
        except ImportError:
            pass
        except Exception as e:
            print(f"[PROATIVO]: Erro no monitor de sistema: {e}")
        return alertas


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

        # Alerta de chuva (máx 1x a cada 3h)
        if time.time() - self._ultimo_alerta_chuva > 10800:
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
        "Hoje é mais um dia pra mandar bem, chefia.",
        "Foco total. Vamos que vamos.",
        "Tô aqui do seu lado. Pode mandar.",
        "Missão do dia: arrasar. Pode deixar que eu cuido do resto.",
        "Mais um dia, mais uma chance de evoluir.",
        "Sistemas operacionais. Pronto pra missão.",
        "Hoje vai ser bom. Eu sinto nos meus circuitos.",
        "Carpe diem, chefia. Bora fazer acontecer.",
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
        """
        partes = []
        agora  = datetime.now()

        # ── 1. Saudação + data ────────────────────────────────────────────
        dia_sem = DIAS_SEMANA[agora.weekday()]
        dia_num = agora.day
        mes     = MESES[agora.month]
        hora    = agora.strftime("%H:%M")
        partes.append(
            f"Bom dia, chefia! São {hora} de {dia_sem}, "
            f"{dia_num} de {mes}."
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
            partes.append("Sem lembretes agendados pra hoje.")

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

        self._monitor_sistema = MonitorSistema()
        self._monitor_clima   = MonitorClima()
        self._monitor_hora    = MonitorHora()
        self._briefing        = BriefingMatinal(self._lembretes)

        self._carregar()

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

            if _ciclo % 10 == 0:   # clima a cada ~5min
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
        return "Preparando seu briefing, chefia!"

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
        rep = " (todo dia)" if repetir else ""
        return f"Anotado! Às {hora:02d}:{minuto:02d}{rep}: {descricao}."

    def _listar_lembretes(self) -> str:
        with self._lock:
            ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]
        if not ativos:
            return "Sem lembretes agendados, chefia."
        return "Lembretes:\n" + "\n".join(f"{i+1}. {l}" for i, l in enumerate(ativos))

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