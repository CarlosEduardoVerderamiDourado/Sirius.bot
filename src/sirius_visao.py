"""
sirius_visao.py — S.I.R.I.U.S. v5.2 — Visão Computacional
===========================================================

Substitui o arquivo que existia apenas como patch.
Esta é a classe real, completa e auto-suficiente.

Capacidades:
  • OCR de imagens e screenshots (pytesseract com fallback easyocr)
  • Captura de tela completa via pyautogui
  • analisar_tela()              → lê e descreve o que está na tela
  • ler_texto(caminho)           → OCR de arquivo de imagem
  • ler_tela(caminho)            → alias de ler_texto (compatibilidade leitor)
  • identificar_botoes_em_imagem → detecta elementos clicáveis via OCR + contornos
  • extrair_erro_tela()          → OCR reativo só quando há erro Python na tela
                                   (chamado pelo GerenciadorContexto em DESENVOLVIMENTO)

Dependências opcionais (fallback gracioso se ausentes):
    pip install pyautogui Pillow pytesseract
    pip install easyocr          # fallback OCR sem Tesseract instalado
    pip install opencv-python    # para identificar_botoes_em_imagem

Interface pública usada pelo ecossistema:
    get_visao() → SiriusVisao   (singleton — importe assim)

    visao.analisar_tela(pergunta)           → str
    visao.ler_texto(caminho_imagem)         → str
    visao.ler_tela(caminho_imagem)          → str  (alias)
    visao.identificar_botoes_em_imagem(cam) → dict
    visao.extrair_erro_tela()               → str | None
    visao.capturar_tela(caminho=None)       → str | None
    visao.status()                          → dict
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import tempfile
import threading
import time
from typing import Optional

# =============================================================================
# Paths
# =============================================================================

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
_DIR_DATA = os.path.join(_DIR_RAIZ, "data")
_DIR_SCREENSHOTS = os.path.join(_DIR_DATA, "screenshots")
os.makedirs(_DIR_SCREENSHOTS, exist_ok=True)

if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

# =============================================================================
# Detecção de dependências
# =============================================================================

_PYAUTOGUI_OK   = False
_PYTESSERACT_OK = False
_EASYOCR_OK     = False
_OPENCV_OK      = False
_PIL_OK         = False

try:
    import pyautogui as _pyautogui
    _PYAUTOGUI_OK = True
except ImportError:
    pass

try:
    from PIL import Image as _PILImage, ImageChops as _ImageChops, ImageStat as _ImageStat
    _PIL_OK = True
except ImportError:
    pass

try:
    import pytesseract as _pytesseract
    _PYTESSERACT_OK = True
except ImportError:
    pass

try:
    import easyocr as _easyocr
    _EASYOCR_OK = True
except ImportError:
    pass

try:
    import cv2 as _cv2
    import numpy as _np
    _OPENCV_OK = True
except ImportError:
    pass


# =============================================================================
# Padrões de erro Python para extrair_erro_tela
# =============================================================================

_TRIGGERS_ERRO = frozenset({
    "exception", "traceback", "error", "runtimeerror", "nameerror",
    "typeerror", "valueerror", "importerror", "keyerror", "indexerror",
    "attributeerror", "filenotfounderror", "oserror", "permissionerror",
    "syntaxerror", "indentationerror", "modulenotfounderror",
    "zerodivisionerror", "recursionerror", "memoryerror",
    "failed", "assert", "fatal",
})

_COOLDOWN_CAPTURA = 3.0   # segundos mínimos entre capturas OCR reativas
_LIMIAR_MUDANCA   = 0.01  # diff de pixel normalizada para "tela mudou"


# =============================================================================
# OCR — camada de abstração com fallback automático
# =============================================================================

class _SiriusOCR:
    """
    Abstração de OCR com cascata automática:
      1. pytesseract (rápido, requer Tesseract instalado no sistema)
      2. easyocr     (mais pesado, funciona sem instalação extra)
      3. fallback    (retorna string vazia com aviso)
    """

    def __init__(self):
        self._easyocr_reader = None
        self._lock           = threading.Lock()

    def _get_easyocr(self):
        if self._easyocr_reader is None and _EASYOCR_OK:
            try:
                self._easyocr_reader = _easyocr.Reader(["pt", "en"], gpu=False, verbose=False)
            except Exception as e:
                print(f"[OCR]: EasyOCR falhou ao inicializar: {e}")
        return self._easyocr_reader

    def extrair_texto(self, caminho_imagem: str) -> str:
        """
        Extrai texto de um arquivo de imagem.
        Tenta pytesseract primeiro, cai em easyocr se falhar.
        """
        if not os.path.exists(caminho_imagem):
            return ""

        # 1. pytesseract
        if _PYTESSERACT_OK and _PIL_OK:
            try:
                img  = _PILImage.open(caminho_imagem)
                texto = _pytesseract.image_to_string(img, lang="por+eng")
                if texto.strip():
                    return texto.strip()
            except Exception as e:
                print(f"[OCR]: pytesseract falhou: {e}")

        # 2. easyocr
        if _EASYOCR_OK:
            with self._lock:
                reader = self._get_easyocr()
                if reader:
                    try:
                        resultados = reader.readtext(caminho_imagem, detail=0)
                        texto = " ".join(resultados).strip()
                        if texto:
                            return texto
                    except Exception as e:
                        print(f"[OCR]: EasyOCR falhou: {e}")

        if not _PYTESSERACT_OK and not _EASYOCR_OK:
            print(
                "[OCR]: Nenhum motor disponível. Instale:\n"
                "  pip install pytesseract Pillow   (+ Tesseract no sistema)\n"
                "  pip install easyocr              (alternativa sem instalação)"
            )
        return ""

    def extrair_de_pil(self, img) -> str:
        """Extrai texto direto de uma imagem PIL sem salvar em disco."""
        if not _PIL_OK:
            return ""

        # pytesseract aceita PIL direto
        if _PYTESSERACT_OK:
            try:
                return _pytesseract.image_to_string(img, lang="por+eng").strip()
            except Exception:
                pass

        # easyocr requer arquivo — salva temporário
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp.name)
                caminho = tmp.name
            texto = self.extrair_texto(caminho)
            try:
                os.remove(caminho)
            except Exception:
                pass
            return texto
        except Exception:
            return ""


# =============================================================================
# Comparação de telas
# =============================================================================

def _hash_imagem(img) -> str:
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return hashlib.sha1(buf.getvalue()).hexdigest()
    except Exception:
        return str(time.time())


def _telas_diferentes(img1, img2, limiar: float = _LIMIAR_MUDANCA) -> bool:
    """
    Retorna True se a diferença média de pixels entre as duas imagens
    superar o limiar (normalizado 0–1).
    """
    if not _PIL_OK:
        return _hash_imagem(img1) != _hash_imagem(img2)
    try:
        w, h   = img1.size
        p1     = img1.resize((w // 4, h // 4)).convert("L")
        p2     = img2.resize((w // 4, h // 4)).convert("L")
        diff   = _ImageChops.difference(p1, p2)
        stats  = _ImageStat.Stat(diff)
        return (stats.mean[0] / 255.0) > limiar
    except Exception:
        return _hash_imagem(img1) != _hash_imagem(img2)


# =============================================================================
# SiriusVisao — classe principal
# =============================================================================

class SiriusVisao:
    """
    Módulo de visão computacional do S.I.R.I.U.S. v5.2.

    Singleton via get_visao(). Todas as operações são thread-safe.
    Todas as dependências (pyautogui, pytesseract, cv2) são opcionais —
    o sistema nunca crasha por ausência delas.

    Métodos públicos:
        analisar_tela(pergunta)              → str
        ler_texto(caminho)                   → str  (OCR de arquivo)
        ler_tela(caminho)                    → str  (alias de ler_texto)
        identificar_botoes_em_imagem(caminho)→ dict
        extrair_erro_tela()                  → str | None
        capturar_tela(caminho=None)          → str | None
        status()                             → dict
    """

    def __init__(self):
        self._ocr  = _SiriusOCR()
        self._lock = threading.Lock()

        # Estado para extrair_erro_tela()
        self._ultimo_extrair_erro:    float = 0.0
        self._ultima_captura_reativa         = None   # PIL Image

        if not _PYAUTOGUI_OK:
            print(
                "[VISAO]: pyautogui não instalado — captura de tela desabilitada.\n"
                "  pip install pyautogui Pillow"
            )
        if not _PYTESSERACT_OK and not _EASYOCR_OK:
            print(
                "[VISAO]: Nenhum motor OCR disponível.\n"
                "  pip install pytesseract Pillow   (+ Tesseract binário)\n"
                "  pip install easyocr              (sem binário externo)"
            )

    # =========================================================================
    # capturar_tela — screenshot para arquivo
    # =========================================================================

    def capturar_tela(self, caminho: Optional[str] = None) -> Optional[str]:
        """
        Captura a tela inteira e salva em arquivo.

        Args:
            caminho: caminho do arquivo de saída (opcional).
                     Se None, gera nome automático em data/screenshots/.
        Returns:
            Caminho do arquivo salvo, ou None se falhou.
        """
        if not _PYAUTOGUI_OK:
            return None
        try:
            if caminho is None:
                nome    = f"screen_{int(time.time())}.png"
                caminho = os.path.join(_DIR_SCREENSHOTS, nome)

            screenshot = _pyautogui.screenshot()
            screenshot.save(caminho)
            return caminho
        except Exception as e:
            print(f"[VISAO]: Erro ao capturar tela: {e}")
            return None

    # =========================================================================
    # ler_texto / ler_tela — OCR de arquivo
    # =========================================================================

    def ler_texto(self, caminho_imagem: str) -> str:
        """
        Extrai texto de um arquivo de imagem via OCR.

        Usado por:
            sirius_leitor.py → extrator.extrair_de_screenshot()
            sirius_server.py → (via get_visao().ler_tela())
        """
        if not caminho_imagem or not os.path.exists(caminho_imagem):
            return ""
        return self._ocr.extrair_texto(caminho_imagem)

    def ler_tela(self, caminho_imagem: str) -> str:
        """Alias de ler_texto() para compatibilidade com sirius_leitor.py."""
        return self.ler_texto(caminho_imagem)

    # =========================================================================
    # analisar_tela — OCR da tela atual + resposta contextual
    # =========================================================================

    def analisar_tela(self, pergunta: str = "") -> str:
        """
        Captura a tela, extrai texto via OCR e retorna uma resposta
        contextualizada à pergunta do usuário.

        Usado por:
            sirius_moe.py → EspecialistaVisao.executar()
            cerebro.py    → quando usuário pede "o que está na tela"

        Args:
            pergunta: o que o usuário quer saber sobre a tela

        Returns:
            Texto descrevendo o conteúdo da tela, ou mensagem de erro.
        """
        if not _PYAUTOGUI_OK:
            return (
                "Não consigo ver a tela agora. "
                "Instale: pip install pyautogui Pillow"
            )

        try:
            screenshot = _pyautogui.screenshot()
            texto_ocr  = self._ocr.extrair_de_pil(screenshot)

            if not texto_ocr:
                return "Capturei a tela mas não encontrei texto legível."

            # Trunca para não sobrecarregar o contexto
            texto_truncado = texto_ocr[:2000]

            if pergunta:
                return (
                    f"Conteúdo da tela:\n{texto_truncado}\n\n"
                    f"[Pergunta: {pergunta}]"
                )
            return f"Conteúdo da tela:\n{texto_truncado}"

        except Exception as e:
            return f"Erro ao analisar tela: {e}"

    # =========================================================================
    # identificar_botoes_em_imagem — detecta elementos clicáveis
    # =========================================================================

    def identificar_botoes_em_imagem(self, caminho_imagem: str) -> dict:
        """
        Identifica botões e elementos clicáveis em uma imagem.
        Usado pelo sirius_server.py no endpoint /visao/demonstracao.

        Estratégia:
          1. OCR → extrai texto de cada região (pytesseract com dados de bbox)
          2. OpenCV → detecta contornos retangulares como regiões candidatas
          3. Combina: regiões com texto são classificadas como botões

        Args:
            caminho_imagem: caminho do arquivo de imagem

        Returns:
            dict com:
                elementos: list[dict] com keys texto, centro, largura, altura, tipo
                total:     int
                ocr_bruto: str (texto completo extraído)
        """
        if not os.path.exists(caminho_imagem):
            return {"elementos": [], "total": 0, "ocr_bruto": "", "erro": "Arquivo não encontrado"}

        elementos = []
        ocr_bruto = ""

        # ── 1. OCR com posição (pytesseract com output_type=dict) ─────────────
        if _PYTESSERACT_OK and _PIL_OK:
            try:
                img = _PILImage.open(caminho_imagem)
                dados = _pytesseract.image_to_data(
                    img, lang="por+eng",
                    output_type=_pytesseract.Output.DICT,
                )
                n = len(dados["text"])
                for i in range(n):
                    texto = str(dados["text"][i]).strip()
                    conf  = int(dados["conf"][i]) if dados["conf"][i] != "-1" else 0
                    if texto and conf > 40:
                        x, y  = dados["left"][i], dados["top"][i]
                        w, h   = dados["width"][i], dados["height"][i]
                        if w > 5 and h > 5:
                            elementos.append({
                                "texto":   texto,
                                "centro":  [x + w // 2, y + h // 2],
                                "largura": w,
                                "altura":  h,
                                "confianca": conf,
                                "tipo":    "texto_ocr",
                            })
                ocr_bruto = " ".join(
                    t for t in dados["text"] if t.strip()
                )
            except Exception as e:
                print(f"[VISAO]: pytesseract image_to_data falhou: {e}")
                # Fallback: OCR simples sem posição
                ocr_bruto = self._ocr.extrair_texto(caminho_imagem)

        # ── 2. OpenCV — detecta contornos retangulares (botões sem texto) ─────
        if _OPENCV_OK and not elementos:
            try:
                img_cv  = _cv2.imread(caminho_imagem)
                if img_cv is not None:
                    cinza   = _cv2.cvtColor(img_cv, _cv2.COLOR_BGR2GRAY)
                    _, bw   = _cv2.threshold(cinza, 127, 255, _cv2.THRESH_BINARY)
                    contours, _ = _cv2.findContours(
                        bw, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE
                    )
                    h_img, w_img = img_cv.shape[:2]
                    for cnt in contours:
                        x, y, w, h = _cv2.boundingRect(cnt)
                        # Filtra contornos muito pequenos ou muito grandes
                        area_ratio = (w * h) / (w_img * h_img)
                        if 0.001 < area_ratio < 0.3 and w > 20 and h > 10:
                            elementos.append({
                                "texto":   "",
                                "centro":  [x + w // 2, y + h // 2],
                                "largura": w,
                                "altura":  h,
                                "confianca": 0,
                                "tipo":    "contorno_cv2",
                            })
            except Exception as e:
                print(f"[VISAO]: OpenCV falhou na detecção de contornos: {e}")

        # ── 3. Fallback — OCR simples sem posição ─────────────────────────────
        if not elementos and not ocr_bruto:
            ocr_bruto = self._ocr.extrair_texto(caminho_imagem)
            if ocr_bruto:
                # Cria um elemento genérico com o texto todo
                elementos.append({
                    "texto":   ocr_bruto[:200],
                    "centro":  [0, 0],
                    "largura": 0,
                    "altura":  0,
                    "confianca": 0,
                    "tipo":    "ocr_simples",
                })

        return {
            "elementos": elementos,
            "total":     len(elementos),
            "ocr_bruto": ocr_bruto,
        }

    # =========================================================================
    # extrair_erro_tela — OCR reativo para contexto DESENVOLVIMENTO
    # =========================================================================

    def extrair_erro_tela(
        self,
        cooldown:        float = _COOLDOWN_CAPTURA,
        limiar_mudanca:  float = _LIMIAR_MUDANCA,
    ) -> Optional[str]:
        """
        Captura a tela e retorna o texto de erro SOMENTE se:
          1. Passou o cooldown desde a última captura
          2. A tela mudou significativamente
          3. O texto contém padrões de erro Python/terminal

        Chamado pelo GerenciadorContexto (sirius_gerador.py) quando
        o contexto ativo é DESENVOLVIMENTO.

        Returns:
            str com texto de erro extraído (truncado a 1000 chars), ou
            None se não houve mudança, sem erro, ou sem dependências.
        """
        agora = time.time()
        if agora - self._ultimo_extrair_erro < cooldown:
            return None
        self._ultimo_extrair_erro = agora

        if not _PYAUTOGUI_OK or not _PIL_OK:
            return None

        try:
            screenshot_atual = _pyautogui.screenshot()

            # Compara com a captura anterior
            if (self._ultima_captura_reativa is not None
                    and not _telas_diferentes(
                        self._ultima_captura_reativa,
                        screenshot_atual,
                        limiar_mudanca
                    )):
                return None   # tela não mudou → sem processamento

            self._ultima_captura_reativa = screenshot_atual

            # Extrai texto via OCR
            texto = self._ocr.extrair_de_pil(screenshot_atual)
            if not texto:
                return None

            # Verifica se há padrão de erro
            texto_lower = texto.lower()
            if not any(trigger in texto_lower for trigger in _TRIGGERS_ERRO):
                return None

            # Monta texto compacto: linhas de erro + contexto vizinho (máx 10)
            linhas    = [l.strip() for l in texto.split("\n") if l.strip()]
            resultado = []
            for linha in linhas:
                if any(t in linha.lower() for t in _TRIGGERS_ERRO):
                    resultado.append(linha)
                elif resultado:
                    resultado.append(linha)
                    if len([x for x in resultado if x]) >= 8:
                        break

            texto_final = "\n".join(resultado[:10])
            return texto_final[:1000] if texto_final else None

        except ImportError as e:
            print(f"[VISAO]: extrair_erro_tela indisponível: {e}")
            return None
        except Exception as e:
            print(f"[VISAO]: Erro em extrair_erro_tela: {e}")
            return None

    # =========================================================================
    # status
    # =========================================================================

    def status(self) -> dict:
        return {
            "pyautogui":       _PYAUTOGUI_OK,
            "pytesseract":     _PYTESSERACT_OK,
            "easyocr":         _EASYOCR_OK,
            "opencv":          _OPENCV_OK,
            "pil":             _PIL_OK,
            "ocr_disponivel":  _PYTESSERACT_OK or _EASYOCR_OK,
            "captura_disponivel": _PYAUTOGUI_OK and _PIL_OK,
        }


# =============================================================================
# Singleton global
# =============================================================================

_visao_instance: Optional[SiriusVisao] = None
_visao_lock = threading.Lock()


def get_visao() -> SiriusVisao:
    """
    Retorna a instância singleton de SiriusVisao.
    Thread-safe. Use sempre este método para obter a instância.

    Uso:
        from sirius_visao import get_visao
        visao = get_visao()
        texto = visao.analisar_tela("o que está aberto?")
    """
    global _visao_instance
    if _visao_instance is None:
        with _visao_lock:
            if _visao_instance is None:
                _visao_instance = SiriusVisao()
    return _visao_instance


# =============================================================================
# Standalone — smoke test
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SiriusVisao v5.2 — smoke test")
    parser.add_argument("--status",   action="store_true", help="Exibe status dos módulos")
    parser.add_argument("--tela",     action="store_true", help="Captura e analisa a tela")
    parser.add_argument("--ocr",      type=str,            help="Faz OCR de um arquivo de imagem")
    parser.add_argument("--botoes",   type=str,            help="Identifica botões em uma imagem")
    parser.add_argument("--erro",     action="store_true", help="Testa extrair_erro_tela()")
    args = parser.parse_args()

    print("\n\033[1m\033[96m" + "=" * 60)
    print("  S.I.R.I.U.S. Visao v5.2")
    print("=" * 60 + "\033[0m\n")

    visao = get_visao()

    if args.status or not any([args.tela, args.ocr, args.botoes, args.erro]):
        s = visao.status()
        print("\033[95m[STATUS]\033[0m")
        for k, v in s.items():
            icone = "✓" if v else "✗"
            cor   = "\033[92m" if v else "\033[91m"
            print(f"  {cor}{icone}\033[0m {k}")
        if not s["ocr_disponivel"]:
            print("\n  Para ativar OCR:")
            print("    pip install pytesseract Pillow  (+ Tesseract binário em https://github.com/UB-Mannheim/tesseract/wiki)")
            print("    pip install easyocr             (sem binário externo, mais pesado)")
        if not s["captura_disponivel"]:
            print("\n  Para captura de tela:")
            print("    pip install pyautogui Pillow")

    if args.tela:
        print("\n[TELA]: Analisando...")
        resultado = visao.analisar_tela("o que está visível na tela?")
        print(resultado[:500])

    if args.ocr:
        print(f"\n[OCR]: Extraindo texto de '{args.ocr}'...")
        texto = visao.ler_texto(args.ocr)
        print(texto[:500] or "(nenhum texto encontrado)")

    if args.botoes:
        print(f"\n[BOTOES]: Identificando elementos em '{args.botoes}'...")
        resultado = visao.identificar_botoes_em_imagem(args.botoes)
        print(f"  Total de elementos: {resultado['total']}")
        for el in resultado["elementos"][:5]:
            print(f"  • '{el['texto'][:40]}' em {el['centro']} ({el['tipo']})")

    if args.erro:
        print("\n[ERRO]: Testando extrair_erro_tela()...")
        resultado = visao.extrair_erro_tela()
        if resultado:
            print(f"  Erro detectado:\n{resultado}")
        else:
            print("  Nenhum erro detectado na tela (ou sem mudança desde a última captura).")

    print()