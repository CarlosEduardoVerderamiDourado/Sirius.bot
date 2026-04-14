"""
sirius_proativo.py — Proatividade estilo Jarvis

O Sirius fala sem ser perguntado quando necessário:
  - Lembretes agendados pelo usuário ("me lembra às 15h de tomar remédio")
  - Alertas de horário ("são 9h, chefia!")
  - Verificação periódica de clima antes de sair
  - Alertas de CPU/RAM alta

Uso no cerebro.py:
    from sirius_proativo import SiriusProativo
    self._proativo = SiriusProativo(callback_falar=audio.falar)
    self._proativo.iniciar()

Comandos reconhecidos:
    "me lembra às 15h de tomar remédio"
    "lembra de ligar pro médico às 9 da manhã"
    "agenda lembrete para 14:30 reunião"
    "cancela lembrete"
    "quais são meus lembretes"
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

# Onde salvar lembretes entre sessões
CAMINHO_LEMBRETES = os.path.join(diretorio_raiz, "data", "lembretes.json")


# ---------------------------------------------------------------------------
# Parser de horário — extrai hora e minuto do texto natural
# ---------------------------------------------------------------------------

def _extrair_horario(texto: str) -> tuple[int, int] | None:
    """
    Extrai hora e minuto de texto natural em português.

    Suporta:
      "às 15h"          → (15, 0)
      "às 15:30"        → (15, 30)
      "às 3 da tarde"   → (15, 0)
      "às 9 da manhã"   → (9, 0)
      "às 8 da noite"   → (20, 0)
      "às 9h30"         → (9, 30)
      "daqui a 10 min"  → agora + 10min
      "em 1 hora"       → agora + 1h
    """
    t = texto.lower()

    # "daqui a X minutos / em X minutos"
    m = re.search(r"(?:daqui|em)\s+(?:a\s+)?(\d+)\s*min(?:utos?)?", t)
    if m:
        agora = datetime.now()
        alvo  = agora + timedelta(minutes=int(m.group(1)))
        return (alvo.hour, alvo.minute)

    # "em X hora(s)"
    m = re.search(r"em\s+(\d+)\s*hora(?:s)?", t)
    if m:
        agora = datetime.now()
        alvo  = agora + timedelta(hours=int(m.group(1)))
        return (alvo.hour, alvo.minute)

    # HH:MM explícito
    m = re.search(r"(\d{1,2})[h:h](\d{2})", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return _ajustar_periodo(h, mn, t)

    # "X h" ou "Xh"
    m = re.search(r"(\d{1,2})\s*h(?:oras?)?(?!\d)", t)
    if m:
        h = int(m.group(1))
        return _ajustar_periodo(h, 0, t)

    # Número solto (assume hora)
    m = re.search(r"(?:as?|às?)\s+(\d{1,2})(?:\s|$)", t)
    if m:
        h = int(m.group(1))
        return _ajustar_periodo(h, 0, t)

    return None


def _ajustar_periodo(hora: int, minuto: int, texto: str) -> tuple[int, int]:
    """Ajusta hora para AM/PM com base em período do dia mencionado."""
    if any(p in texto for p in ["tarde", "noite"]):
        if hora < 12:
            hora += 12
    elif any(p in texto for p in ["manhã", "manha"]):
        if hora == 12:
            hora = 0
    elif hora < 7:
        # Sem período explícito: horas < 7 assumem tarde/noite
        hora += 12
    return (hora % 24, minuto)


def _extrair_descricao(texto: str) -> str:
    """
    Remove o horário e os verbos de agenda do texto,
    deixando apenas a descrição do lembrete.
    """
    t = texto.lower()

    # Remove wake word e verbos de comando
    removiveis = [
        r"sirius,?\s*", r"me lembra\s*", r"lembra de\s*", r"lembra\s*",
        r"agenda(?:r)?\s*(?:um\s*)?lembrete\s*(?:para\s*)?",
        r"cria\s*(?:um\s*)?lembrete\s*(?:para\s*)?",
        r"bota\s*(?:um\s*)?lembrete\s*(?:para\s*)?",
    ]
    for r_ in removiveis:
        t = re.sub(r_, "", t).strip()

    # Remove expressões de horário
    padroes_hora = [
        r"(?:às?|as?|para\s+as?|para\s+às?)\s+\d{1,2}[h:]\d{2}",
        r"(?:às?|as?|para\s+as?|para\s+às?)\s+\d{1,2}\s*h(?:oras?)?",
        r"(?:às?|as?|para\s+as?|para\s+às?)\s+\d{1,2}(?:\s|$)",
        r"daqui\s+(?:a\s+)?\d+\s*min(?:utos?)?",
        r"em\s+\d+\s*(?:minutos?|horas?)",
        r"\d{1,2}[h:]\d{2}",
        r"da\s+(?:manhã|manha|tarde|noite)",
    ]
    for p in padroes_hora:
        t = re.sub(p, "", t).strip()

    # Remove conectivos soltos
    t = re.sub(r"^(de|para|que|em|a|o|um|uma)\s+", "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()

    return t if len(t) > 2 else "Lembrete"


# ---------------------------------------------------------------------------
# Lembrete
# ---------------------------------------------------------------------------

class Lembrete:
    def __init__(self, hora: int, minuto: int, descricao: str, repetir: bool = False):
        self.hora       = hora
        self.minuto     = minuto
        self.descricao  = descricao
        self.repetir    = repetir        # se True, repete todo dia no mesmo horário
        self.disparado  = False
        self.criado_em  = datetime.now().isoformat()

    def deve_disparar(self) -> bool:
        """Verifica se o lembrete deve disparar agora (janela de ±30s)."""
        if self.disparado and not self.repetir:
            return False
        agora  = datetime.now()
        target = agora.replace(hour=self.hora, minute=self.minuto, second=0, microsecond=0)
        diff   = abs((agora - target).total_seconds())
        return diff <= 30

    def mensagem(self) -> str:
        return f"Chefia, lembrete: {self.descricao}!"

    def to_dict(self) -> dict:
        return {
            "hora": self.hora, "minuto": self.minuto,
            "descricao": self.descricao, "repetir": self.repetir,
            "disparado": self.disparado, "criado_em": self.criado_em,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lembrete":
        l = cls(d["hora"], d["minuto"], d["descricao"], d.get("repetir", False))
        l.disparado = d.get("disparado", False)
        l.criado_em = d.get("criado_em", "")
        return l

    def __str__(self):
        return f"{self.hora:02d}:{self.minuto:02d} — {self.descricao}"


# ---------------------------------------------------------------------------
# Motor de proatividade principal
# ---------------------------------------------------------------------------

class SiriusProativo:
    """
    Gerencia lembretes e alertas proativos.

    Uso:
        proativo = SiriusProativo(callback_falar=audio.falar)
        proativo.iniciar()

        # Adiciona via comando de voz:
        resposta = proativo.processar_comando("me lembra às 15h de tomar remédio")
    """

    def __init__(self, callback_falar=None, callback_log=None):
        """
        callback_falar: função que fala algo (ex: audio.falar)
        callback_log:   função que exibe no chat (ex: interface.log_sirius)
        """
        self._falar     = callback_falar or print
        self._log       = callback_log
        self._lembretes: list[Lembrete] = []
        self._lock      = threading.Lock()
        self._rodando   = False
        self._thread    = None

        self._carregar()

    # -----------------------------------------------------------------------
    # Persistência
    # -----------------------------------------------------------------------

    def _salvar(self):
        try:
            os.makedirs(os.path.dirname(CAMINHO_LEMBRETES), exist_ok=True)
            with open(CAMINHO_LEMBRETES, "w", encoding="utf-8") as f:
                dados = [l.to_dict() for l in self._lembretes]
                json.dump(dados, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PROATIVO]: Erro ao salvar lembretes: {e}")

    def _carregar(self):
        try:
            if os.path.exists(CAMINHO_LEMBRETES):
                with open(CAMINHO_LEMBRETES, encoding="utf-8") as f:
                    dados = json.load(f)
                with self._lock:
                    self._lembretes = [Lembrete.from_dict(d) for d in dados]
                    # Remove lembretes não-repetitivos já disparados
                    self._lembretes = [
                        l for l in self._lembretes
                        if not (l.disparado and not l.repetir)
                    ]
                print(f"\033[92m[PROATIVO]: {len(self._lembretes)} lembrete(s) carregado(s).\033[0m")
        except Exception as e:
            print(f"[PROATIVO]: Erro ao carregar lembretes: {e}")

    # -----------------------------------------------------------------------
    # Loop de verificação
    # -----------------------------------------------------------------------

    def _loop(self):
        print("\033[94m[PROATIVO]: Monitor de lembretes ativo.\033[0m")
        while self._rodando:
            time.sleep(15)  # verifica a cada 15 segundos
            self._verificar_lembretes()

    def _verificar_lembretes(self):
        with self._lock:
            for lembrete in self._lembretes:
                if lembrete.deve_disparar():
                    lembrete.disparado = True
                    msg = lembrete.mensagem()

                    # Fala o lembrete
                    threading.Thread(
                        target=self._disparar_alerta,
                        args=(msg,),
                        daemon=True
                    ).start()

            self._salvar()

    def _disparar_alerta(self, mensagem: str):
        """Dispara o alerta de forma não-bloqueante."""
        print(f"\n\033[93m[PROATIVO]: {mensagem}\033[0m")
        try:
            self._falar(mensagem)
        except Exception as e:
            print(f"[PROATIVO]: Erro ao falar alerta: {e}")

        if self._log:
            try:
                self._log(mensagem)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Parser de comandos de voz
    # -----------------------------------------------------------------------

    # Triggers que identificam comando de lembrete
    _TRIGGERS_LEMBRETE = {
        "me lembra", "lembra de", "lembra que", "me avisa",
        "agenda lembrete", "cria lembrete", "bota lembrete",
        "coloca lembrete", "adiciona lembrete", "novo lembrete",
        "lembrete para", "lembrete às", "lembrete as",
    }

    # Triggers de listagem
    _TRIGGERS_LISTAR = {
        "meus lembretes", "quais lembretes", "lista lembretes",
        "ver lembretes", "mostra lembretes", "tem lembrete",
    }

    # Triggers de cancelamento
    _TRIGGERS_CANCELAR = {
        "cancela lembrete", "cancela todos lembretes", "remove lembrete",
        "apaga lembrete", "deleta lembrete", "limpa lembretes",
    }

    def e_comando_proativo(self, texto: str) -> bool:
        """Retorna True se o texto contém um comando de lembrete."""
        t = texto.lower()
        return (
            any(trigger in t for trigger in self._TRIGGERS_LEMBRETE) or
            any(trigger in t for trigger in self._TRIGGERS_LISTAR) or
            any(trigger in t for trigger in self._TRIGGERS_CANCELAR)
        )

    def processar_comando(self, texto: str) -> str:
        """
        Processa um comando de lembrete e retorna a resposta.
        Chamado pelo cerebro.py quando detecta intenção proativa.
        """
        t = texto.lower().strip()

        # --- LISTAR ---
        if any(trigger in t for trigger in self._TRIGGERS_LISTAR):
            return self._listar_lembretes()

        # --- CANCELAR ---
        if any(trigger in t for trigger in self._TRIGGERS_CANCELAR):
            return self._cancelar_lembretes(t)

        # --- ADICIONAR ---
        if any(trigger in t for trigger in self._TRIGGERS_LEMBRETE):
            return self._adicionar_lembrete(texto)

        return "Não entendi o comando de lembrete. Tente: 'me lembra às 15h de tomar remédio'."

    def _adicionar_lembrete(self, texto: str) -> str:
        """Extrai horário e descrição, cria o lembrete."""
        resultado = _extrair_horario(texto)
        if not resultado:
            return (
                "Não consegui identificar o horário. "
                "Tente assim: 'me lembra às 15h de tomar remédio' "
                "ou 'me lembra em 30 minutos de ligar pro médico'."
            )

        hora, minuto   = resultado
        descricao      = _extrair_descricao(texto)
        repetir        = any(p in texto.lower() for p in ["todo dia", "todos os dias", "diariamente"])
        lembrete       = Lembrete(hora, minuto, descricao, repetir)

        with self._lock:
            self._lembretes.append(lembrete)
            self._salvar()

        rep_str = " (todo dia)" if repetir else ""
        return (
            f"Anotado! Vou te lembrar às {hora:02d}:{minuto:02d}{rep_str}: {descricao}."
        )

    def _listar_lembretes(self) -> str:
        with self._lock:
            ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]

        if not ativos:
            return "Não tem nenhum lembrete agendado, chefia."

        linhas = [f"Seus lembretes:"]
        for i, l in enumerate(ativos, 1):
            rep = " (todo dia)" if l.repetir else ""
            linhas.append(f"{i}. {l.hora:02d}:{l.minuto:02d} — {l.descricao}{rep}")

        return "\n".join(linhas)

    def _cancelar_lembretes(self, texto: str) -> str:
        """Cancela lembretes por número ou todos."""
        with self._lock:
            # "cancela todos"
            if any(p in texto for p in ["todos", "tudo"]):
                n = len(self._lembretes)
                self._lembretes.clear()
                self._salvar()
                return f"{n} lembrete(s) cancelado(s)."

            # "cancela lembrete 2"
            m = re.search(r"(\d+)", texto)
            if m:
                idx = int(m.group(1)) - 1
                ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]
                if 0 <= idx < len(ativos):
                    removido = ativos[idx]
                    self._lembretes.remove(removido)
                    self._salvar()
                    return f"Lembrete '{removido.descricao}' cancelado."
                return "Número de lembrete inválido."

            # Sem especificação — cancela o próximo
            if self._lembretes:
                removido = self._lembretes.pop(0)
                self._salvar()
                return f"Lembrete '{removido.descricao}' cancelado."

        return "Não tem lembretes para cancelar."

    # -----------------------------------------------------------------------
    # Controle
    # -----------------------------------------------------------------------

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name="SiriusProativo"
        )
        self._thread.start()
        print("\033[92m[PROATIVO]: Sistema de lembretes ativo.\033[0m")

    def parar(self):
        self._rodando = False

    def status(self) -> dict:
        with self._lock:
            ativos = [l for l in self._lembretes if not (l.disparado and not l.repetir)]
        return {
            "rodando":         self._rodando,
            "lembretes_ativos": len(ativos),
            "lembretes":       [str(l) for l in ativos],
        }