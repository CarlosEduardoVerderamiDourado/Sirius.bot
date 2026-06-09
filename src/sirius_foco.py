"""
sirius_foco.py — Detector de Contexto de Janela do S.I.R.I.U.S.
================================================================

Detecta o que o Carlos está fazendo com base na janela ativa do Windows
e expõe esse contexto para o cerebro.py enriquecer a query enviada ao gerador.

Contextos possíveis:
  DESENVOLVIMENTO  → VS Code, terminal, PyCharm, arquivos .py/.js/.ts
  EDICAO_VIDEO     → Sony Vegas, DaVinci, Premiere, After Effects
  RPG              → fichas, roll20, FoundryVTT, PDFs de aventura
  NAVEGADOR        → Chrome, Firefox, Edge (sem contexto mais específico)
  GERAL            → qualquer coisa que não se encaixa acima

Uso pelo cerebro.py:
    from sirius_foco import SiriusFoco, ContextoSistema
    foco = SiriusFoco()
    ctx  = foco.obter_contexto()
    # ctx.tipo == "DESENVOLVIMENTO"
    # ctx.titulo_janela == "main.py - Visual Studio Code"
    # ctx.detalhes == {"arquivo": "main.py", "linguagem": "Python"}

Uso pela query enriquecida (cerebro.py.processar):
    payload = foco.montar_payload_contexto()
    # payload == "[CONTEXTO: DESENVOLVIMENTO | arquivo: main.py]"
"""

from __future__ import annotations

import os
import re
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Mapeamento de contextos
# ---------------------------------------------------------------------------

# Chave: contexto string  Valor: lista de substrings (match case-insensitive)
_REGRAS_CONTEXTO: dict[str, list[str]] = {
    "DESENVOLVIMENTO": [
        "visual studio code", "vscode", ".py - ", ".js - ", ".ts - ",
        ".jsx - ", ".tsx - ", ".cpp - ", ".c - ", ".java - ",
        "pycharm", "jupyter", "spyder", "idle", "neovim", "vim -",
        "cmd - python", "powershell - python", "terminal - python",
        "anaconda", "conda", "git bash",
        "debugger", "breakpoint",
    ],
    "EDICAO_VIDEO": [
        "vegas", "sony vegas", "vegas pro",
        "premiere", "after effects", "davinci", "resolve",
        "capcut", "kdenlive", "obs studio", "obs -",
        "audacity", "adobe audition",
    ],
    "RPG": [
        "roll20", "foundryvtt", "foundry vtt", "fantasy grounds",
        "ficha", "aventura", "campanha", "rpg", "d&d", "dungeon",
        "pathfinder", "storyteller",
    ],
    "NAVEGADOR": [
        "chrome", "firefox", "edge", "brave", "opera",
        "microsoft edge", "google chrome", "mozilla firefox",
    ],
}

# Mapeamento de extensão de arquivo → linguagem
_EXT_LINGUAGEM: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "React/JSX", ".tsx": "React/TSX", ".java": "Java",
    ".cpp": "C++", ".c": "C", ".cs": "C#", ".go": "Go",
    ".rs": "Rust", ".php": "PHP", ".rb": "Ruby", ".kt": "Kotlin",
    ".swift": "Swift", ".html": "HTML", ".css": "CSS",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".lua": "Lua", ".json": "JSON", ".yaml": "YAML", ".toml": "TOML",
}

# Cooldown mínimo entre detecções (segundos) — evita polling excessivo
_COOLDOWN_DETECCAO = 2.0


# ---------------------------------------------------------------------------
# ContextoSistema — resultado da detecção
# ---------------------------------------------------------------------------

@dataclass
class ContextoSistema:
    """
    Snapshot do contexto de janela em um momento específico.

    Atributos:
        tipo:           string de contexto (DESENVOLVIMENTO, EDICAO_VIDEO, etc.)
        titulo_janela:  título completo da janela ativa
        processo:       nome do executável (chrome.exe, Code.exe, etc.)
        detalhes:       informações extras derivadas do título
                        Ex: {"arquivo": "main.py", "linguagem": "Python"}
        timestamp:      momento da detecção (epoch)
        confianca:      0.0–1.0 (quão certeza temos do contexto)
    """
    tipo:          str            = "GERAL"
    titulo_janela: str            = ""
    processo:      str            = ""
    detalhes:      dict           = field(default_factory=dict)
    timestamp:     float          = field(default_factory=time.time)
    confianca:     float          = 1.0

    def montar_tag(self) -> str:
        """
        Monta a tag de contexto para injetar na query.
        Ex: "[CONTEXTO: DESENVOLVIMENTO | arquivo: main.py | linguagem: Python]"
        """
        partes = [f"CONTEXTO: {self.tipo}"]
        for k, v in self.detalhes.items():
            if v:
                partes.append(f"{k}: {v}")
        return "[" + " | ".join(partes) + "]"

    def esta_desatualizado(self, ttl: float = 5.0) -> bool:
        return (time.time() - self.timestamp) > ttl


# ---------------------------------------------------------------------------
# SiriusFoco — classe principal
# ---------------------------------------------------------------------------

class SiriusFoco:
    """
    Detecta o contexto de trabalho atual via título de janela ativa.

    Usa pygetwindow para obter o título — fallback graceful se não disponível.
    Faz cache com TTL de 5s para não sobrecarregar o polling.

    Thread-safety: leitura e escrita protegidas por RLock.
    """

    _TTL_CACHE = 5.0    # segundos antes de refazer a detecção

    def __init__(self):
        self._cache:   Optional[ContextoSistema] = None
        self._lock     = threading.RLock()
        self._gw_ok    = self._testar_pygetwindow()

        if not self._gw_ok:
            print(
                "\033[33m[FOCO]: pygetwindow não disponível. "
                "Instale: pip install pygetwindow\033[0m"
            )

    # ── pygetwindow ──────────────────────────────────────────────────────── #

    def _testar_pygetwindow(self) -> bool:
        try:
            import pygetwindow as gw
            gw.getActiveWindow()
            return True
        except ImportError:
            return False
        except Exception:
            return True   # disponível, só não tem janela ativa agora

    def _titulo_janela_ativa(self) -> tuple[str, str]:
        """
        Retorna (titulo, nome_processo) da janela ativa.
        Retorna ("", "") se não conseguir.
        """
        if not self._gw_ok:
            return ("", "")

        try:
            import pygetwindow as gw
            janela = gw.getActiveWindow()
            if not janela:
                return ("", "")
            titulo = janela.title or ""

            # Tenta obter o nome do processo via psutil
            processo = ""
            try:
                import psutil
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        if hasattr(janela, "_hWnd"):
                            import ctypes
                            pid = ctypes.c_ulong()
                            ctypes.windll.user32.GetWindowThreadProcessId(
                                janela._hWnd, ctypes.byref(pid)
                            )
                            if proc.pid == pid.value:
                                processo = proc.name()
                                break
                    except Exception:
                        continue
            except Exception:
                pass

            return (titulo, processo)

        except Exception:
            return ("", "")

    # ── Classificação ────────────────────────────────────────────────────── #

    def _classificar(self, titulo: str, processo: str) -> ContextoSistema:
        """
        Aplica as regras de contexto ao título e processo.
        Retorna um ContextoSistema com tipo, detalhes e confiança.
        """
        titulo_l    = titulo.lower()
        processo_l  = processo.lower()

        # Percorre regras em ordem de prioridade
        for tipo, palavras in _REGRAS_CONTEXTO.items():
            for palavra in palavras:
                if palavra in titulo_l or palavra in processo_l:
                    detalhes = self._extrair_detalhes(titulo, tipo)
                    confianca = 1.0 if palavra in titulo_l else 0.75
                    return ContextoSistema(
                        tipo=tipo,
                        titulo_janela=titulo,
                        processo=processo,
                        detalhes=detalhes,
                        confianca=confianca,
                    )

        # Nenhuma regra casou
        return ContextoSistema(
            tipo="GERAL",
            titulo_janela=titulo,
            processo=processo,
            confianca=0.5,
        )

    def _extrair_detalhes(self, titulo: str, tipo: str) -> dict:
        """
        Extrai detalhes extras do título conforme o tipo de contexto.

        DESENVOLVIMENTO → arquivo + linguagem
        EDICAO_VIDEO    → software
        NAVEGADOR       → domínio/URL
        """
        detalhes: dict[str, str] = {}
        titulo_l = titulo.lower()

        if tipo == "DESENVOLVIMENTO":
            # Tenta extrair nome do arquivo
            # Ex: "main.py — Visual Studio Code" → "main.py"
            match = re.search(
                r"([\w\-\.]+\.(py|js|ts|jsx|tsx|java|cpp|c|cs|go|rs|php|rb|lua|sh|ps1))",
                titulo,
                re.IGNORECASE,
            )
            if match:
                nome_arquivo = match.group(1)
                ext = os.path.splitext(nome_arquivo)[1].lower()
                detalhes["arquivo"]   = nome_arquivo
                detalhes["linguagem"] = _EXT_LINGUAGEM.get(ext, ext.lstrip(".").upper())

            # Detecta se está em terminal / depuração
            if any(p in titulo_l for p in ["debug", "terminal", "cmd", "powershell", "bash"]):
                detalhes["modo"] = "terminal"

        elif tipo == "EDICAO_VIDEO":
            # Ex: "Vegas Pro 18.0 — meu_video.veg"
            match = re.search(r"(vegas|premiere|davinci|resolve|obs|capcut)", titulo_l)
            if match:
                detalhes["software"] = match.group(1).title()
            # Tenta extrair nome do projeto
            match_proj = re.search(r"— (.+)\s*$", titulo)
            if match_proj:
                detalhes["projeto"] = match_proj.group(1).strip()[:40]

        elif tipo == "NAVEGADOR":
            # Extrai domínio da barra de título
            # Ex: "Documentação Python — Mozilla Firefox"
            partes = re.split(r"\s[-—|]\s", titulo)
            if partes:
                pagina = partes[0].strip()[:60]
                detalhes["pagina"] = pagina

        elif tipo == "RPG":
            # Tenta extrair nome da campanha/aventura
            partes = re.split(r"\s[-—|]\s", titulo)
            if len(partes) > 1:
                detalhes["campanha"] = partes[0].strip()[:40]

        return detalhes

    # ── Interface pública ────────────────────────────────────────────────── #

    def obter_contexto(self, forcar: bool = False) -> ContextoSistema:
        """
        Retorna o contexto atual, usando cache com TTL de 5s.

        Args:
            forcar: ignora cache e refaz a detecção imediatamente

        Returns:
            ContextoSistema com tipo, título, processo e detalhes
        """
        with self._lock:
            if (
                not forcar
                and self._cache is not None
                and not self._cache.esta_desatualizado(self._TTL_CACHE)
            ):
                return self._cache

        titulo, processo = self._titulo_janela_ativa()
        ctx = self._classificar(titulo, processo)

        with self._lock:
            self._cache = ctx

        return ctx

    def obter_contexto_atual(self) -> str:
        """
        Atalho que retorna apenas o tipo (string).
        Compatível com o exemplo de integração do prompt:
            contexto = foco.obter_contexto_atual()
            # → "DESENVOLVIMENTO" | "EDICAO_VIDEO" | "RPG" | "GERAL"
        """
        return self.obter_contexto().tipo

    def montar_payload_contexto(
        self,
        incluir_visao: bool = False,
        texto_ocr:     Optional[str] = None,
    ) -> str:
        """
        Monta a tag completa para injetar na query do cerebro.py.

        Args:
            incluir_visao: se True e texto_ocr presente, adiciona [VISAO_OCR: ...]
            texto_ocr:     texto extraído pela SiriusVisao (opcional)

        Returns:
            Ex: "[CONTEXTO: DESENVOLVIMENTO | arquivo: main.py | linguagem: Python]
                 [VISAO_OCR: Traceback (most recent call last)...]"
        """
        ctx  = self.obter_contexto()
        tags = [ctx.montar_tag()]

        if incluir_visao and texto_ocr and texto_ocr.strip():
            # Trunca para não sobrecarregar o gerador
            ocr_curto = texto_ocr.strip()[:800].replace("\n", " ")
            tags.append(f"[VISAO_OCR: {ocr_curto}]")

        return " ".join(tags)

    def contexto_mudou(self, desde: ContextoSistema) -> bool:
        """
        Retorna True se o contexto mudou desde o snapshot fornecido.
        Útil para o loop do sirius_proativo.py decidir quando reprocessar.
        """
        atual = self.obter_contexto()
        return (
            atual.tipo != desde.tipo
            or atual.titulo_janela != desde.titulo_janela
        )

    def status(self) -> dict:
        ctx = self.obter_contexto()
        return {
            "pygetwindow_disponivel": self._gw_ok,
            "contexto_atual":         ctx.tipo,
            "titulo_janela":          ctx.titulo_janela,
            "processo":               ctx.processo,
            "detalhes":               ctx.detalhes,
            "confianca":              ctx.confianca,
            "cache_ttl_s":            self._TTL_CACHE,
        }


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_foco_instance: Optional[SiriusFoco] = None

def get_foco() -> SiriusFoco:
    global _foco_instance
    if _foco_instance is None:
        _foco_instance = SiriusFoco()
    return _foco_instance


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SiriusFoco — detector de contexto")
    parser.add_argument("--watch", action="store_true",
                        help="Monitora contexto continuamente (Ctrl+C para parar)")
    parser.add_argument("--status", action="store_true",
                        help="Mostra contexto atual e sai")
    args = parser.parse_args()

    foco = SiriusFoco()

    if args.watch:
        print("Monitorando contexto (Ctrl+C para parar)...")
        ultimo = ""
        while True:
            ctx = foco.obter_contexto(forcar=True)
            if ctx.tipo != ultimo:
                print(f"\n[{time.strftime('%H:%M:%S')}] Contexto: {ctx.tipo}")
                print(f"  Janela:    {ctx.titulo_janela[:70]}")
                print(f"  Detalhes:  {ctx.detalhes}")
                print(f"  Tag:       {ctx.montar_tag()}")
                ultimo = ctx.tipo
            time.sleep(2)
    else:
        s = foco.status()
        print("\n[SIRIUS FOCO — Status]")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print(f"\n  Tag para query: {foco.montar_payload_contexto()}")