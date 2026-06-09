"""
sirius_check.py — S.I.R.I.U.S. v5.2 — Verificador de Dependências
==================================================================
Ponto crítico 3: verifica e instala automaticamente as bibliotecas
necessárias para o ecossistema funcionar.

Execução:
    python sirius_check.py           # só verifica
    python sirius_check.py --fix     # instala o que falta
    python sirius_check.py --fix --quiet  # silencioso (para scripts)

Importável (chamado pelo sirius_boot.py):
    from sirius_check import verificar_dependencias
    ok = verificar_dependencias(auto_fix=True)
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Cores ANSI
# =============================================================================

_C = {
    "ok":   "\033[92m",
    "warn": "\033[93m",
    "err":  "\033[91m",
    "info": "\033[96m",
    "bold": "\033[1m",
    "dim":  "\033[2m",
    "rst":  "\033[0m",
}

def _ok(msg):   print(f"{_C['ok']}  ✓ {msg}{_C['rst']}")
def _warn(msg): print(f"{_C['warn']}  ⚠ {msg}{_C['rst']}")
def _err(msg):  print(f"{_C['err']}  ✗ {msg}{_C['rst']}")
def _info(msg): print(f"{_C['info']}  → {msg}{_C['rst']}")
def _dim(msg):  print(f"{_C['dim']}    {msg}{_C['rst']}")


# =============================================================================
# Catálogo de dependências
# =============================================================================

@dataclass
class Dep:
    import_name: str           # nome usado no import
    pip_name:    str           # nome usado no pip install
    obrigatoria: bool = True   # False = opcional com fallback gracioso
    descricao:   str = ""      # para o relatório
    versao_min:  Optional[str] = None   # ex: "0.4.0"
    extras:      list[str] = field(default_factory=list)  # ex: ["standard"]


_DEPENDENCIAS: list[Dep] = [
    # ── Obrigatórias ─────────────────────────────────────────────────────────
    Dep("fastapi",      "fastapi",          True,  "Servidor API REST"),
    Dep("uvicorn",      "uvicorn",          True,  "ASGI server",          extras=["standard"]),
    Dep("pydantic",     "pydantic",         True,  "Validação de schemas (incluso no FastAPI)"),
    Dep("requests",     "requests",         True,  "HTTP para Wikipedia + buscas web"),

    # ── Essenciais para o autodidata ─────────────────────────────────────────
    # NOTA: o pacote PyPI correto é 'duckduckgo-search'; o módulo importa como
    # 'duckduckgo_search'. O nome antigo 'ddgs' não existe no PyPI.
    Dep("duckduckgo_search", "duckduckgo-search", True, "DuckDuckGo Search API"),

    # ── RAG / FAISS — sem isso o índice semântico não funciona ───────────────
    Dep("faiss",        "faiss-cpu",        False, "Índice vetorial FAISS (busca semântica RAG)"),
    Dep("sentence_transformers", "sentence-transformers",
                                            False, "Embeddings para o RAG"),

    # ── Controle do PC ────────────────────────────────────────────────────────
    Dep("psutil",       "psutil",           True,  "Monitoramento CPU/RAM"),
    Dep("pyperclip",    "pyperclip",        False, "Clipboard (controle_pc.py)"),
    Dep("pyautogui",    "pyautogui",        False, "Automação de mouse/teclado"),

    # ── Áudio / Voz ───────────────────────────────────────────────────────────
    Dep("pyaudio",           "pyaudio",            False, "Microfone (STT via Faster-Whisper)"),
    Dep("pygame",            "pygame",             False, "Reprodução de áudio TTS"),
    Dep("faster_whisper",    "faster-whisper",     False, "STT — transcrição de voz offline"),
    Dep("pyttsx3",           "pyttsx3",            False, "TTS fallback (voz Windows SAPI5)"),
    Dep("speech_recognition","SpeechRecognition",  False, "Captura de áudio via microfone"),

    # ── Interface gráfica ─────────────────────────────────────────────────────
    Dep("PySide6",           "PySide6",            False, "Interface gráfica (main_residente.py)"),
    Dep("PIL",               "Pillow",             False, "Imagens (wallpaper, visão, OCR)"),

    # ── ML / Neural ───────────────────────────────────────────────────────────
    Dep("torch",             "torch",              False, "PyTorch (neuronio.py, sirius_gerador.py)"),
    Dep("sklearn",           "scikit-learn",       False, "TF-IDF fallback RAG"),
    Dep("numpy",             "numpy",              False, "Operações matriciais (embeddings, audio)"),

    # ── OCR / Visão ───────────────────────────────────────────────────────────
    Dep("pytesseract",       "pytesseract",        False, "OCR (sirius_visao.py — requer Tesseract)"),
    Dep("cv2",               "opencv-python",      False, "Visão computacional (sirius_visao.py)"),
    Dep("easyocr",           "easyocr",            False, "OCR sem Tesseract (fallback pytesseract)"),

    # ── Utilitários ───────────────────────────────────────────────────────────
    Dep("colorama",          "colorama",           False, "Logs coloridos no terminal"),
    Dep("httpx",             "httpx",              False, "HTTP assíncrono (uvicorn[standard] inclui)"),
    Dep("dotenv",            "python-dotenv",      False, "Variáveis de ambiente (.env)"),
]


# =============================================================================
# Verificação e instalação
# =============================================================================

def _tentar_import(dep: Dep) -> bool:
    """Tenta importar o módulo. Retorna True se conseguiu."""
    try:
        importlib.import_module(dep.import_name)
        return True
    except ImportError:
        return False


def _instalar(dep: Dep, quiet: bool = False) -> bool:
    """Instala via pip. Retorna True se sucesso."""
    pip_target = dep.pip_name
    if dep.extras:
        pip_target = f"{dep.pip_name}[{','.join(dep.extras)}]"

    cmd = [sys.executable, "-m", "pip", "install", pip_target]
    if quiet:
        cmd.append("--quiet")

    _info(f"Instalando {pip_target}...")
    try:
        result = subprocess.run(cmd, capture_output=quiet, text=True)
        if result.returncode == 0:
            _ok(f"{pip_target} instalado com sucesso.")
            return True
        else:
            _err(f"Falha ao instalar {pip_target}: {result.stderr[:200] if quiet else ''}")
            return False
    except Exception as e:
        _err(f"Erro ao chamar pip para {pip_target}: {e}")
        return False


def verificar_dependencias(
    auto_fix: bool = False,
    quiet:    bool = False,
) -> bool:
    """
    Verifica todas as dependências do ecossistema S.I.R.I.U.S.

    Parâmetros:
        auto_fix : se True, instala automaticamente o que faltar
        quiet    : suprime output do pip

    Retorna:
        True  — todas as dependências obrigatórias disponíveis
        False — pelo menos uma obrigatória está ausente e não foi instalada
    """
    print(f"\n{_C['bold']}{_C['info']}{'='*60}")
    print("  S.I.R.I.U.S. v5.2 — Verificador de Dependências")
    print(f"{'='*60}{_C['rst']}")
    print(f"{_C['dim']}  Python {sys.version.split()[0]} | {sys.executable}{_C['rst']}\n")

    faltando_obrig:   list[Dep] = []
    faltando_opcional: list[Dep] = []
    presentes:         list[Dep] = []

    # ── 1. Categoriza ─────────────────────────────────────────────────────────
    for dep in _DEPENDENCIAS:
        if _tentar_import(dep):
            presentes.append(dep)
        elif dep.obrigatoria:
            faltando_obrig.append(dep)
        else:
            faltando_opcional.append(dep)

    # ── 2. Relatório inicial ──────────────────────────────────────────────────
    print(f"{_C['bold']}[ Presentes ({len(presentes)}) ]{_C['rst']}")
    for d in presentes:
        _ok(f"{d.pip_name:<30} {_C['dim']}{d.descricao}{_C['rst']}")

    if faltando_obrig:
        print(f"\n{_C['bold']}[ Obrigatórias ausentes ({len(faltando_obrig)}) ]{_C['rst']}")
        for d in faltando_obrig:
            _err(f"{d.pip_name:<30} {d.descricao}")

    if faltando_opcional:
        print(f"\n{_C['bold']}[ Opcionais ausentes ({len(faltando_opcional)}) ]{_C['rst']}")
        for d in faltando_opcional:
            _warn(f"{d.pip_name:<30} {d.descricao}")

    # ── 3. Fix automático ─────────────────────────────────────────────────────
    instaladas_ok: list[str] = []
    instaladas_err: list[str] = []

    if auto_fix and (faltando_obrig or faltando_opcional):
        print(f"\n{_C['bold']}[ Instalando o que falta ]{_C['rst']}")
        for dep in faltando_obrig + faltando_opcional:
            ok = _instalar(dep, quiet=quiet)
            if ok:
                instaladas_ok.append(dep.pip_name)
                # Remoção da lista de faltando após instalar
                if dep in faltando_obrig:
                    faltando_obrig.remove(dep)
            else:
                instaladas_err.append(dep.pip_name)

    # ── 4. Diagnóstico de instalação manual ───────────────────────────────────
    if not auto_fix and (faltando_obrig or faltando_opcional):
        todos_faltando = faltando_obrig + faltando_opcional
        pip_cmd = "pip install " + " ".join(
            (f"{d.pip_name}[{','.join(d.extras)}]" if d.extras else d.pip_name)
            for d in todos_faltando
        )
        print(f"\n{_C['bold']}[ Comando para instalar tudo de uma vez ]{_C['rst']}")
        print(f"  {_C['info']}{pip_cmd}{_C['rst']}")

    # ── 5. Resultado final ────────────────────────────────────────────────────
    print()
    if instaladas_ok:
        _ok(f"Instaladas nesta sessão: {', '.join(instaladas_ok)}")
    if instaladas_err:
        _warn(f"Falha ao instalar: {', '.join(instaladas_err)}")

    sucesso = len(faltando_obrig) == 0

    cor    = _C["ok"] if sucesso else _C["err"]
    status = "TODAS AS OBRIGATÓRIAS OK" if sucesso else "DEPENDÊNCIAS OBRIGATÓRIAS FALTANDO"
    print(f"{cor}{_C['bold']}  {status}{_C['rst']}")

    if faltando_opcional and not auto_fix:
        _dim("As opcionais (faiss-cpu, sentence-transformers, pyautogui) ativam")
        _dim("funcionalidades extras — o sistema core funciona sem elas.")

    print()
    return sucesso


# =============================================================================
# Standalone
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="S.I.R.I.U.S. — Verificador de Dependências")
    parser.add_argument("--fix",   action="store_true", help="Instala automaticamente o que faltar")
    parser.add_argument("--quiet", action="store_true", help="Suprime output do pip")
    args = parser.parse_args()

    ok = verificar_dependencias(auto_fix=args.fix, quiet=args.quiet)
    sys.exit(0 if ok else 1)