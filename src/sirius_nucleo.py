"""
sirius_nucleo.py — Núcleo leve do S.I.R.I.U.S. para o servidor

Carrega APENAS os três sistemas essenciais:
  - SiriusMemory    → histórico de conversas, dúvidas, macros
  - SiriusNeuronio  → classificador neural + predição de intenção
  - Aprendizado     → SiriusAutodidata (Wikipedia + DDG) e SiriusTreinador

NÃO carrega:
  Câmera / visão computacional
  Áudio / TTS / wake word
  Controle do PC (janelas, shutdown, etc.)
  Apps externos (Spotify, WhatsApp, etc.)
  Proativo (lembretes, briefing)
  Interface gráfica
"""

import os
import sys
from typing import Optional

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
for _p in [_DIR_SRC, _DIR_RAIZ]:
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


class SiriusNucleo:
    """
    Núcleo mínimo do Sirius para uso no servidor.

    Expõe processar() e memoria — mesma interface que SiriusCerebro,
    sem nenhum módulo de hardware ou UI.
    """

    def __init__(self, db_pessoal: str = None):
        print("\033[94m[NUCLEO]: Inicializando núcleo leve...\033[0m")

        from memoria import SiriusMemory
        self.memoria  = SiriusMemory(db_pessoal=db_pessoal)
        print("\033[92m[NUCLEO]: Memória ativa.\033[0m")

        from neuronio import SiriusNeuronio
        self.neuronio = SiriusNeuronio()

        from filtro_zoeiro import SiriusFiltro
        self.filtro = SiriusFiltro()

        self._rag      = None
        self._moe      = None
        self._agentes  = None
        self._autodidata = None
        self._treinador  = None
        self._callback_falar = None
        self._callback_log   = None
        self._contexto: list[dict] = []

        self._carregar_opcionais()
        self._iniciar_aprendizado()
        print("\033[92m[NUCLEO]: Núcleo leve pronto.\033[0m")

    # ── Opcionais ─────────────────────────────────────────────────────────────

    def _carregar_opcionais(self):
        try:
            from sirius_rag import SiriusRAG
            self._rag = SiriusRAG(self.memoria)
            print("\033[92m[NUCLEO]: RAG local ativo.\033[0m")
        except Exception as e:
            print(f"[NUCLEO]: RAG indisponível: {e}")

        try:
            from sirius_moe import SiriusMoE
            self._moe = SiriusMoE(self.memoria)
            print("\033[92m[NUCLEO]: MoE ativo.\033[0m")
        except Exception as e:
            print(f"[NUCLEO]: MoE indisponível: {e}")

        try:
            from sirius_agentes import SiriusAgentes
            self._agentes = SiriusAgentes(self.memoria)
            print("\033[92m[NUCLEO]: Agentes ativos.\033[0m")
        except Exception as e:
            print(f"[NUCLEO]: Agentes indisponíveis: {e}")

    # ── Aprendizado ───────────────────────────────────────────────────────────

    def _iniciar_aprendizado(self):
        try:
            from sirius_autodidata import SiriusAutodidata
            self._autodidata = SiriusAutodidata(memoria=self.memoria, cerebro=self)
            self._autodidata.iniciar()
            print("\033[92m[NUCLEO]: Autodidata iniciado (Wikipedia + DDG).\033[0m")
        except Exception as e:
            print(f"[NUCLEO]: Autodidata indisponível: {e}")

        try:
            from sirius_treinador import SiriusTreinador
            self._treinador = SiriusTreinador()
            self._treinador.iniciar_ciclo_autonomo(intervalo_horas=2.0)
            print("\033[92m[NUCLEO]: Treinador iniciado (ciclo 2h).\033[0m")
        except Exception as e:
            print(f"[NUCLEO]: Treinador indisponível: {e}")

    # ── Interface pública ─────────────────────────────────────────────────────

    def registrar_callback(self, callback_falar=None, callback_log=None):
        """Compatível com SiriusCerebro.registrar_callback()."""
        if callback_falar:
            self._callback_falar = callback_falar
        if callback_log:
            self._callback_log = callback_log

    def processar(self, texto: str, forcar_processamento: bool = False) -> str:
        """
        Processa um texto e retorna a resposta.

        Fluxo:
          1. Resposta rápida (saudações)
          2. MoE → especialista correto
          3. Agentes (pesquisa Wikipedia/DDG)
          4. RAG local (histórico vetorial)
          5. Similaridade simples no histórico
          6. Fallback + registra como dúvida para o autodidata
        """
        if not texto or not texto.strip():
            return ""

        texto = texto.strip()

        # Remove wake word
        t_lower = texto.lower()
        for ww in ("sirius,", "sirius", "ei sirius", "oi sirius"):
            if t_lower.startswith(ww):
                texto   = texto[len(ww):].strip()
                t_lower = texto.lower()
                break

        if not texto:
            return "Oi! O que posso fazer por você?"

        # ── 1. Resposta rápida ────────────────────────────────────────────────
        r = self._resposta_rapida(t_lower)
        if r:
            self._salvar(texto, r)
            return r

        # ── 2. MoE ───────────────────────────────────────────────────────────
        if self._moe:
            try:
                r = self._moe.processar(texto)
                if r and len(r) > 15 and "não sei" not in r.lower():
                    self._salvar(texto, r)
                    return self.filtro.filtrar(r)
            except Exception as e:
                print(f"[NUCLEO] MoE: {e}")

        # ── 3. Agentes ────────────────────────────────────────────────────────
        if self._agentes:
            try:
                r = self._agentes.processar(texto)
                if r and len(r) > 20:
                    self._salvar(texto, r)
                    return self.filtro.filtrar(r)
            except Exception as e:
                print(f"[NUCLEO] Agentes: {e}")

        # ── 4. RAG ───────────────────────────────────────────────────────────
        if self._rag:
            try:
                r = self._rag.buscar(texto)
                if r and len(r) > 20:
                    self._salvar(texto, r)
                    return self.filtro.filtrar(r)
            except Exception as e:
                print(f"[NUCLEO] RAG: {e}")

        # ── 5. Similaridade simples ───────────────────────────────────────────
        try:
            hist = self.memoria.obter_historico_db(limit=30)
            palavras_q = set(t_lower.split())
            for p, r_hist in reversed(hist):
                if not p or not r_hist or len(r_hist) < 20:
                    continue
                comuns = palavras_q & set(p.lower().split())
                if len(comuns) >= 2 and len(comuns) / max(len(palavras_q), 1) > 0.4:
                    return self.filtro.filtrar(r_hist)
        except Exception:
            pass

        # ── 6. Fallback ───────────────────────────────────────────────────────
        try:
            self.memoria.adicionar_duvida(texto)
        except Exception:
            pass

        fallback = (
            f"Não tenho essa informação ainda. "
            f"Vou pesquisar '{texto}' e aprender sobre isso."
        )
        self._salvar(texto, fallback)
        return fallback

    # ── Helpers ───────────────────────────────────────────────────────────────

    _RAPIDAS = {
        "bom dia":   "Bom dia! To ligado.",
        "boa tarde": "Boa tarde! O que precisa?",
        "boa noite": "Boa noite! To aqui.",
        "oi":        "Oi! Manda bala.",
        "ola":       "Opa! To na escuta.",
        "tudo bem":  "Tudo certo! E você?",
        "valeu":     "Tmj!",
        "obrigado":  "De nada!",
        "tchau":     "Até mais!",
    }

    def _resposta_rapida(self, t: str) -> Optional[str]:
        for kw, resp in self._RAPIDAS.items():
            if kw in t:
                return resp
        return None

    def _salvar(self, pergunta: str, resposta: str):
        try:
            self.memoria.salvar_historico(pergunta, resposta)
        except Exception:
            pass

    def status(self) -> dict:
        return {
            "memoria":    self.memoria is not None,
            "neuronio":   self.neuronio is not None,
            "rag":        self._rag is not None,
            "moe":        self._moe is not None,
            "agentes":    self._agentes is not None,
            "autodidata": self._autodidata is not None,
            "treinador":  self._treinador is not None,
        }