"""
sirius_visao.py — Visão computacional do Sirius

O Sirius consegue VER a tela e responder sobre o que está acontecendo.

Capacidades:
  - "o que tem na tela?" → descreve o conteúdo da tela
  - "lê o que está escrito na tela" → extrai texto via OCR
  - "o que é esse erro?" → analisa mensagens de erro na tela
  - "resume o que está aberto" → resume o conteúdo visível
  - "tira print e analisa" → captura + analisa
  - Leitura automática pelo SiriusLeitor (ponto de extensão)

Tecnologia:
  - pyautogui   → captura de tela
  - pytesseract → OCR (extração de texto)
  - Pillow       → processamento de imagem
  - Sem API externa — 100% local
"""

import os
import sys
import re
import time
import tempfile

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_SCREENSHOTS = os.path.join(diretorio_raiz, "data", "screenshots")
os.makedirs(CAMINHO_SCREENSHOTS, exist_ok=True)


# ---------------------------------------------------------------------------
# Instalação automática das dependências
# ---------------------------------------------------------------------------

def _verificar_dependencias() -> dict:
    """Verifica quais dependências estão disponíveis."""
    status = {
        "pyautogui":   False,
        "pillow":      False,
        "pytesseract": False,
        "tesseract":   False,  # binário do sistema
    }
    try:
        import pyautogui
        status["pyautogui"] = True
    except ImportError:
        pass

    try:
        from PIL import Image
        status["pillow"] = True
    except ImportError:
        pass

    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        status["pytesseract"] = True
        status["tesseract"]   = True
    except Exception:
        pass

    return status


# ---------------------------------------------------------------------------
# Extração de texto — OCR com pytesseract
# ---------------------------------------------------------------------------

class ExtratorOCR:
    """
    Extrai texto de imagens usando pytesseract (Tesseract OCR).
    100% local, sem API.

    Instalação:
        pip install pytesseract Pillow
        # Windows: instalar Tesseract OCR
        # https://github.com/UB-Mannheim/tesseract/wiki
        # Adicionar ao PATH ou definir TESSERACT_CMD abaixo
    """

    # Caminho do executável Tesseract no Windows
    TESSERACT_PATHS = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\{user}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    ]

    def __init__(self):
        self._disponivel = False
        self._configurar()

    def _configurar(self):
        try:
            import pytesseract

            # Tenta encontrar o executável automaticamente no Windows
            usuario = os.environ.get("USERNAME", "")
            for caminho in self.TESSERACT_PATHS:
                caminho_exp = caminho.replace("{user}", usuario)
                if os.path.exists(caminho_exp):
                    pytesseract.pytesseract.tesseract_cmd = caminho_exp
                    break

            pytesseract.get_tesseract_version()
            self._disponivel = True
            print("\033[92m[VISAO]: Tesseract OCR disponível.\033[0m")

        except Exception as e:
            print(f"\033[33m[VISAO]: Tesseract não disponível — OCR desabilitado.\033[0m")
            print(f"  Para instalar: https://github.com/UB-Mannheim/tesseract/wiki")
            print(f"  Depois: pip install pytesseract")

    def extrair_texto(self, imagem_path: str) -> str:
        """Extrai texto de uma imagem via OCR."""
        if not self._disponivel:
            return ""

        try:
            import pytesseract
            from PIL import Image, ImageFilter, ImageEnhance

            img = Image.open(imagem_path)

            # Redimensiona para melhorar OCR (2x se for pequena)
            w, h = img.size
            if w < 1200:
                img = img.resize((w * 2, h * 2), Image.LANCZOS)

            # Pré-processamento
            img = img.convert("L")                       # escala de cinza
            img = img.filter(ImageFilter.SHARPEN)        # nitidez
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.8)

            # OCR com configuração que respeita espaços e layout
            # --psm 6 = bloco uniforme de texto (melhor para telas)
            # --oem 3 = motor LSTM (mais preciso)
            texto = pytesseract.image_to_string(
                img,
                lang="por+eng",
                config="--psm 6 --oem 3"
            )

            return self._limpar_texto_ocr(texto)

        except Exception as e:
            print(f"[VISAO]: Erro no OCR: {e}")
            return ""

    def _limpar_texto_ocr(self, texto: str) -> str:
        """
        Corrige problemas comuns do OCR em telas:
        - Palavras coladas: 'OláMundo' → 'Olá Mundo'
        - Caracteres especiais do OCR: |, 1→l, 0→O
        - Linhas em branco excessivas
        """
        import re

        if not texto:
            return ""

        # Remove caracteres não-imprimíveis exceto newline e tab
        texto = re.sub(r"[^\x20-\x7E\x80-\xFF\n\t]", " ", texto)

        # Corrige palavras coladas: detecta transições minúscula→Maiúscula no meio
        # Ex: "DiálogoÉuma" → "Diálogo É uma"
        texto = re.sub(r"([a-záéíóúàãõêôç])([A-ZÁÉÍÓÚÀÃÕÊÔÇ])", r"\1 \2", texto)

        # Corrige múltiplos espaços
        texto = re.sub(r"[ \t]{2,}", " ", texto)

        # Remove linhas com só 1-2 caracteres (lixo do OCR)
        linhas = []
        for linha in texto.split("\n"):
            linha = linha.strip()
            if len(linha) > 2:
                linhas.append(linha)

        # Remove blocos de linhas em branco consecutivas
        resultado = []
        em_branco = 0
        for linha in linhas:
            if not linha:
                em_branco += 1
                if em_branco <= 1:
                    resultado.append("")
            else:
                em_branco = 0
                resultado.append(linha)

        return "\n".join(resultado).strip()

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# Analisador de conteúdo — interpreta o texto extraído
# ---------------------------------------------------------------------------

class AnalisadorTela:
    """
    Interpreta o texto extraído da tela e gera descrições úteis.
    100% local — sem IA externa.
    """

    # Padrões de elementos comuns na tela
    _PADROES = {
        "erro": [
            r"error\b", r"exception\b", r"traceback", r"erro\b",
            r"falhou", r"failed", r"crash", r"não encontrado",
            r"access denied", r"permission denied",
        ],
        "codigo": [
            r"def\s+\w+", r"import\s+\w+", r"class\s+\w+",
            r"function\s+\w+", r"var\s+\w+", r"const\s+\w+",
            r"#include", r"public\s+static",
        ],
        "url": [
            r"https?://\S+", r"www\.\S+",
        ],
        "email": [
            r"\b[\w.+-]+@[\w-]+\.\w+\b",
        ],
        "numero": [
            r"\b\d{4,}\b",
        ],
    }

    def _detectar_tipo(self, texto: str) -> str:
        """Detecta o tipo de conteúdo na tela."""
        texto_l = texto.lower()

        for tipo, padroes in self._PADROES.items():
            for p in padroes:
                if re.search(p, texto_l):
                    return tipo

        # Heurísticas simples
        if len(texto.split("\n")) > 20:
            return "documento"
        if len(texto.split()) > 50:
            return "texto_longo"
        return "geral"

    def _resumir(self, texto: str, max_chars: int = 500) -> str:
        """Gera um resumo do texto."""
        if not texto:
            return "Não consegui extrair texto da tela."

        linhas = [l.strip() for l in texto.split("\n") if len(l.strip()) > 3]
        if not linhas:
            return "A tela parece estar vazia ou com apenas elementos visuais."

        # Pega as linhas mais informativas
        resumo = " | ".join(linhas[:8])
        if len(resumo) > max_chars:
            resumo = resumo[:max_chars] + "..."

        return resumo

    def analisar(self, texto: str, modo: str = "geral") -> str:
        """
        Gera uma resposta natural sobre o conteúdo da tela.
        modo: 'geral' | 'erro' | 'ler' | 'resumir' | 'codigo'
        """
        if not texto or len(texto.strip()) < 5:
            return (
                "Não consegui ler o que está na tela. "
                "Pode ser que o conteúdo seja uma imagem ou esteja em baixa resolução."
            )

        # ── Detecta se o OCR produziu lixo ────────────────────────────────
        if self._ocr_e_lixo(texto):
            return (
                "Capturei a tela mas o conteúdo é visual ou tem texto estilizado "
                "que o OCR não consegue ler bem. "
                "Se quiser que eu leia um texto específico, "
                "tenta deixar só a janela com o texto em foco."
            )

        tipo = self._detectar_tipo(texto)
        linhas = [l.strip() for l in texto.split("\n") if len(l.strip()) > 2]
        n_palavras = len(texto.split())

        # Modo leitura — retorna o texto completo
        if modo == "ler":
            if len(texto) > 800:
                return (
                    f"O texto na tela é longo, uns {n_palavras} palavras. "
                    f"Aqui as primeiras linhas:\n{chr(10).join(linhas[:10])}"
                )
            return f"O que está escrito:\n{texto[:600]}"

        # Modo erro
        if modo == "erro" or tipo == "erro":
            erros = [l for l in linhas
                     if any(re.search(p, l.lower()) for p in self._PADROES["erro"])]
            if erros:
                return (
                    f"Detectei um erro: '{erros[0][:200]}'. "
                    f"Total de {len(erros)} linha(s) com problema. "
                    "Quer que eu pesquise sobre esse erro?"
                )
            return f"Não identifiquei erros claros. O que vi: {self._resumir(texto)}"

        # Modo código
        if tipo == "codigo" or modo == "codigo":
            linguagem = (
                "Python"     if any(k in texto for k in ["def ", "import ", "elif "]) else
                "JavaScript" if any(k in texto for k in ["function ", "const ", "let "]) else
                "código"
            )
            return (
                f"Tem {linguagem} na tela com {n_palavras} palavras. "
                f"Primeiras linhas:\n{chr(10).join(linhas[:5])}"
            )

        # Modo resumo / geral
        resumo = self._resumir(texto)
        if tipo == "documento":
            return f"Tem um documento com ~{n_palavras} palavras. Resumo: {resumo}"
        if tipo == "url":
            urls = re.findall(r"https?://\S+", texto)
            return f"Vejo URLs: {', '.join(urls[:3])}. Contexto: {resumo}"

        return f"Na tela: {resumo}"

    def _ocr_e_lixo(self, texto: str) -> bool:
        """
        Detecta se o resultado do OCR é inútil.
        Critérios:
        - Mais de 40% de caracteres especiais/lixo
        - Palavras médias muito curtas (menos de 2 chars)
        - Poucas palavras reais reconhecíveis
        """
        if not texto:
            return True

        import re as _re

        # Remove espaços e newlines para análise
        chars_validos = _re.sub(r"[a-záéíóúàãõêôç\w\s]", "", texto.lower())
        pct_lixo = len(chars_validos) / max(len(texto), 1)
        if pct_lixo > 0.35:
            return True

        # Analisa palavras
        palavras = [p for p in texto.split() if len(p) > 1]
        if not palavras:
            return True

        # Se a maioria das "palavras" são sequências sem vogal = lixo
        sem_vogal = [p for p in palavras if not _re.search(r"[aeiouáéíóú]", p.lower())]
        if len(sem_vogal) / len(palavras) > 0.6:
            return True

        # Comprimento médio de palavras muito baixo = lixo
        media_len = sum(len(p) for p in palavras) / len(palavras)
        if media_len < 2.5:
            return True

        return False


# ---------------------------------------------------------------------------
# Classe principal — SiriusVisao
# ---------------------------------------------------------------------------

class SiriusVisao:
    """
    Interface principal de visão computacional do Sirius.

    Uso pelo cerebro.py:
        visao = SiriusVisao()
        resposta = visao.analisar_tela("o que tem na tela")

    Uso pelo sirius_leitor.py (ponto de extensão):
        texto = visao.ler_texto(caminho_screenshot)
    """

    def __init__(self):
        self._ocr       = ExtratorOCR()
        self._analisador = AnalisadorTela()
        self._ultimo_screenshot = None

    # -----------------------------------------------------------------------
    # Captura de tela
    # -----------------------------------------------------------------------

    def tirar_screenshot(self, nome: str = None) -> str | None:
        """
        Captura a tela atual e salva em arquivo.
        Retorna o caminho do arquivo ou None se falhar.
        """
        try:
            import pyautogui
            from PIL import Image

            screenshot = pyautogui.screenshot()

            if nome:
                nome_limpo = re.sub(r"[^\w]", "_", nome) + ".png"
            else:
                nome_limpo = f"screenshot_{int(time.time())}.png"

            caminho = os.path.join(CAMINHO_SCREENSHOTS, nome_limpo)
            screenshot.save(caminho)
            self._ultimo_screenshot = caminho
            return caminho

        except ImportError:
            print("[VISAO]: pip install pyautogui Pillow")
            return None
        except Exception as e:
            print(f"[VISAO]: Erro ao capturar tela: {e}")
            return None

    # -----------------------------------------------------------------------
    # Ponto de extensão para sirius_leitor.py
    # -----------------------------------------------------------------------

    def ler_texto(self, imagem_path: str) -> str:
        """
        Interface para o SiriusLeitor — extrai texto de qualquer imagem.
        Substitui o método extrair_de_screenshot vazio no sirius_leitor.py.
        """
        return self._ocr.extrair_texto(imagem_path)

    # -----------------------------------------------------------------------
    # Análise da tela — chamado pelo cerebro.py
    # -----------------------------------------------------------------------

    def analisar_tela(self, comando: str = "geral") -> str:
        """
        Captura a tela e analisa conforme o comando.
        Retorna uma resposta em linguagem natural.
        """
        # Detecta o modo de análise pelo comando
        modo = self._detectar_modo(comando)

        # Captura a tela
        caminho = self.tirar_screenshot()
        if not caminho:
            return (
                "Não consegui capturar a tela. "
                "Instale: pip install pyautogui Pillow"
            )

        # Extrai texto via OCR
        texto_ocr = self._ocr.extrair_texto(caminho)

        # Analisa e gera resposta
        resposta = self._analisador.analisar(texto_ocr, modo)

        print(f"\033[94m[VISAO]: Screenshot salvo em {caminho}\033[0m")
        return resposta

    def _detectar_modo(self, comando: str) -> str:
        """Detecta o que o usuário quer fazer com a tela."""
        c = comando.lower()
        if any(p in c for p in ["lê", "le ", "leia", "ler", "o que diz", "o que está escrito"]):
            return "ler"
        if any(p in c for p in ["erro", "error", "bug", "problema", "falha"]):
            return "erro"
        if any(p in c for p in ["codigo", "código", "script", "programa"]):
            return "codigo"
        if any(p in c for p in ["resume", "resumo", "sintetiza", "o que tem"]):
            return "resumir"
        return "geral"

    def analisar_regiao(self, x: int, y: int, largura: int, altura: int) -> str:
        """Analisa uma região específica da tela."""
        try:
            import pyautogui
            from PIL import Image

            screenshot = pyautogui.screenshot(region=(x, y, largura, altura))
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                caminho_tmp = f.name
            screenshot.save(caminho_tmp)

            texto = self._ocr.extrair_texto(caminho_tmp)
            try:
                os.remove(caminho_tmp)
            except Exception:
                pass

            return self._analisador.analisar(texto)

        except Exception as e:
            return f"Erro ao analisar região: {e}"

    def esta_disponivel(self) -> bool:
        """Retorna True se pelo menos screenshot funciona."""
        try:
            import pyautogui
            return True
        except ImportError:
            return False

    def status(self) -> dict:
        deps = _verificar_dependencias()
        return {
            "screenshot":        deps["pyautogui"],
            "ocr_disponivel":    deps["pytesseract"],
            "tesseract_ok":      deps["tesseract"],
            "ultimo_screenshot": self._ultimo_screenshot,
        }


# ---------------------------------------------------------------------------
# Singleton global — evita instanciar múltiplas vezes
# ---------------------------------------------------------------------------

_visao_instance = None

def get_visao() -> SiriusVisao:
    global _visao_instance
    if _visao_instance is None:
        _visao_instance = SiriusVisao()
    return _visao_instance


# ---------------------------------------------------------------------------
# Standalone — testa a visão
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Testa o SiriusVisao")
    parser.add_argument("--screenshot", action="store_true", help="Tira screenshot e mostra o texto")
    parser.add_argument("--status",     action="store_true", help="Mostra status das dependências")
    args = parser.parse_args()

    visao = SiriusVisao()

    if args.status or not args.screenshot:
        s = visao.status()
        print("\n[VISAO STATUS]")
        print(f"  Screenshot (pyautogui): {'✓' if s['screenshot'] else '✗ pip install pyautogui'}")
        print(f"  OCR (pytesseract):      {'✓' if s['ocr_disponivel'] else '✗ pip install pytesseract'}")
        print(f"  Tesseract binário:      {'✓' if s['tesseract_ok'] else '✗ instalar Tesseract OCR'}")
        print(f"\n  Instalação completa:")
        print(f"    pip install pyautogui Pillow pytesseract")
        print(f"    Tesseract: https://github.com/UB-Mannheim/tesseract/wiki\n")

    if args.screenshot:
        print("Tirando screenshot em 3 segundos...")
        time.sleep(3)
        resposta = visao.analisar_tela("o que tem na tela")
        print(f"\nResposta: {resposta}")