"""
sirius_leitor.py — Minerador de livros do Sirius
Arquitetura preparada para visão computacional futura.

AGORA:  playwright + BeautifulSoup (extração por HTML)
FUTURO: trocar extrair_de_screenshot() por SiriusVisao.ler_tela()
        sem mudar nada no resto do código.

Melhorias:
- Usa os 220+ temas do autodidata em vez de lista fixa
- Puxa perguntas reais do usuário com prioridade
- Evita repetir temas recentes
- Descobre novos temas a partir dos textos lidos
"""

import os
import sys
import time
import random
import threading
import re
import sqlite3

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
DB_PESSOAL   = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO    = os.path.join(CAMINHO_DATA, "sirius_treino.db")

# ---------------------------------------------------------------------------
# Temas — importa do autodidata para manter consistência
# Se o autodidata não estiver disponível, usa lista própria como fallback
# ---------------------------------------------------------------------------

def _carregar_temas() -> list[str]:
    try:
        from sirius_autodidata import TODOS_OS_TEMAS
        print(f"[LEITOR]: {len(TODOS_OS_TEMAS)} temas carregados do autodidata.")
        return TODOS_OS_TEMAS
    except Exception:
        # Fallback com temas próprios para livros/textos longos
        return [
            "artificial intelligence machine learning",
            "python programming algorithms",
            "neural networks deep learning",
            "computer science theory",
            "mathematics linear algebra calculus",
            "physics quantum mechanics relativity",
            "biology molecular genetics evolution",
            "philosophy logic epistemology",
            "history of science discoveries",
            "psychology behavior cognitive",
            "linguistics language structure",
            "literatura brasileira romance",
            "história do brasil república",
            "filosofia moderna contemporânea",
            "economia política sociedade",
            "chemistry organic inorganic",
            "astronomy astrophysics cosmology",
            "engineering software systems",
            "medicine anatomy physiology",
            "sociology anthropology culture",
        ]

TEMAS_LEITURA = _carregar_temas()

# Tamanhos de trecho
MIN_CHARS = 200
MAX_CHARS = 3000

# ---------------------------------------------------------------------------
# Fontes de livros gratuitos
# ---------------------------------------------------------------------------

FONTES_LIVROS = {
    "gutenberg": {
        "busca":    "https://www.gutenberg.org/ebooks/search/?query={tema}&languages=pt",
        "fallback": "https://www.gutenberg.org/ebooks/search/?query={tema}&languages=en",
        "idioma":   "pt/en",
        "tipo":     "classicos",
    },
    "archive": {
        "busca":    "https://archive.org/search?query={tema}&mediatype=texts",
        "idioma":   "multi",
        "tipo":     "tecnico",
    },
    "openlibrary": {
        "busca":    "https://openlibrary.org/search?q={tema}&language=por",
        "fallback": "https://openlibrary.org/search?q={tema}",
        "idioma":   "pt/en",
        "tipo":     "geral",
    },
}


# ---------------------------------------------------------------------------
# Fila de temas do leitor — mesma lógica do autodidata
# ---------------------------------------------------------------------------

class FilaLeitor:
    """
    Gerencia temas para leitura com 3 prioridades:
    1. Perguntas reais do usuário
    2. Temas descobertos nos textos lidos
    3. Sorteio aleatório dos temas base
    """

    def __init__(self):
        self._fila_usuario      = []
        self._fila_descobertos  = []
        self._historico_recente = []  # últimos 30 lidos (livros são mais lentos)

    def adicionar_descoberto(self, tema: str):
        if tema not in self._fila_descobertos and len(tema) > 5:
            self._fila_descobertos.append(tema)

    def puxar_perguntas_usuario(self):
        """Lê perguntas recentes do usuário do SQLite."""
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            rows = conn.execute(
                "SELECT content FROM conversas WHERE role = 'user' "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()

            for row in rows:
                pergunta = row[0].strip().lower() if row[0] else ""
                pergunta = re.sub(r"\bsirius\b[,\s]*", "", pergunta).strip()
                if (
                    len(pergunta) > 5
                    and pergunta not in self._fila_usuario
                    and pergunta not in self._historico_recente
                ):
                    self._fila_usuario.append(pergunta)

        except Exception:
            pass

    def proximo(self) -> str:
        self.puxar_perguntas_usuario()

        # PRIORIDADE 1: pergunta do usuário (40%)
        if self._fila_usuario and random.random() < 0.40:
            tema = self._fila_usuario.pop(0)
            self._registrar(tema)
            print(f"\033[95m[LEITOR]: Lendo sobre pergunta do usuário → '{tema}'\033[0m")
            return tema

        # PRIORIDADE 2: tema descoberto (30%)
        if self._fila_descobertos and random.random() < 0.30:
            tema = self._fila_descobertos.pop(0)
            self._registrar(tema)
            return tema

        # PRIORIDADE 3: aleatório dos temas base
        candidatos = [t for t in TEMAS_LEITURA if t not in self._historico_recente]
        if not candidatos:
            self._historico_recente.clear()
            candidatos = TEMAS_LEITURA

        tema = random.choice(candidatos)
        self._registrar(tema)
        return tema

    def _registrar(self, tema: str):
        self._historico_recente.append(tema)
        if len(self._historico_recente) > 30:
            self._historico_recente.pop(0)


# ---------------------------------------------------------------------------
# Extração de novos temas dos textos lidos
# ---------------------------------------------------------------------------

def _extrair_temas_do_texto(texto: str) -> list[str]:
    """Descobre novos temas para estudar a partir do texto lido."""
    novos = []
    padroes = [
        r"conhecid[ao] como ([A-Za-zÀ-ú\s]{5,40})",
        r"denominad[ao] ([A-Za-zÀ-ú\s]{5,40})",
        r"teori[ao] d[ae] ([A-Za-zÀ-ú\s]{5,40})",
        r"conceito de ([A-Za-zÀ-ú\s]{5,40})",
        r"princípio d[ao] ([A-Za-zÀ-ú\s]{5,30})",
        r"lei d[ae] ([A-Za-zÀ-ú\s]{5,30})",
        r"efeito ([A-Za-zÀ-ú\s]{3,30})",
        r"([A-Za-zÀ-ú\s]{5,30}) foi descobert[ao]",
        r"([A-Za-zÀ-ú\s]{5,30}) foi desenvolvid[ao]",
        r"baseado em ([A-Za-zÀ-ú\s]{5,40})",
        r"relacionado [aà] ([A-Za-zÀ-ú\s]{5,40})",
    ]
    for padrao in padroes:
        for m in re.findall(padrao, texto, re.IGNORECASE):
            t = m.strip().lower()
            if 5 < len(t) < 50:
                novos.append(t)
    return list(set(novos))[:5]


# ---------------------------------------------------------------------------
# Camada de extração — PONTO DE TROCA PARA VISÃO FUTURA
# ---------------------------------------------------------------------------

class ExtratorTexto:
    """
    Extrai texto de uma página.

    AGORA:  BeautifulSoup (parse do HTML recebido pelo playwright)
    FUTURO: substituir extrair_de_screenshot() por SiriusVisao
            sem mudar nada fora desta classe.
    """

    def extrair(self, html: str, url: str) -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup(["script", "style", "nav", "footer",
                              "header", "aside", "form", "button"]):
                tag.decompose()

            conteudo = ""
            for seletor in ["article", "main", ".content", "#content",
                             ".text", "#text", "section", "body"]:
                alvo = soup.select_one(seletor)
                if alvo:
                    conteudo = alvo.get_text(separator=" ", strip=True)
                    if len(conteudo) >= MIN_CHARS:
                        break

            if not conteudo:
                conteudo = soup.get_text(separator=" ", strip=True)

            return self._limpar(conteudo)

        except ImportError:
            print("[LEITOR]: pip install beautifulsoup4")
            return ""
        except Exception as e:
            print(f"[LEITOR]: Erro na extração HTML: {e}")
            return ""

    def extrair_de_screenshot(self, imagem_path: str) -> str:
        """
        PONTO DE EXTENSÃO — visão computacional futura.

        Quando SiriusVisao estiver pronto:
            from sirius_visao import SiriusVisao
            return SiriusVisao().ler_texto(imagem_path)
        """
        return ""  # TODO: implementar com SiriusVisao

    def _limpar(self, texto: str) -> str:
        texto  = re.sub(r"\s{2,}", " ", texto)
        texto  = re.sub(r"\n{3,}", "\n\n", texto)
        texto  = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", texto)
        linhas = [l.strip() for l in texto.split("\n") if len(l.strip()) > 40]
        return " ".join(linhas).strip()


# ---------------------------------------------------------------------------
# Navegador — controla o browser via Playwright
# ---------------------------------------------------------------------------

class NavegadorSirius:
    def __init__(self):
        self._browser    = None
        self._page       = None
        self._playwright = None

    def iniciar(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser    = self._playwright.chromium.launch(headless=True)
            self._page       = self._browser.new_page()
            self._page.set_extra_http_headers({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            })
            return True
        except ImportError:
            print("[LEITOR]: pip install playwright && playwright install chromium")
            return False
        except Exception as e:
            print(f"[LEITOR]: Erro ao iniciar browser: {e}")
            return False

    def navegar(self, url: str, timeout: int = 15000) -> str | None:
        try:
            self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            time.sleep(1.5)
            return self._page.content()
        except Exception as e:
            print(f"[LEITOR]: Falha ao navegar para {url}: {e}")
            return None

    def tirar_screenshot(self, caminho: str) -> bool:
        """Já funcional — SiriusVisao pode usar diretamente no futuro."""
        try:
            self._page.screenshot(path=caminho, full_page=False)
            return True
        except Exception as e:
            print(f"[LEITOR]: Erro no screenshot: {e}")
            return False

    def encerrar(self):
        try:
            if self._browser:    self._browser.close()
            if self._playwright: self._playwright.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Processador de trechos
# ---------------------------------------------------------------------------

def _processar_em_trechos(texto: str, tema: str) -> list[dict]:
    if not texto or len(texto) < MIN_CHARS:
        return []

    trechos    = []
    paragrafos = [
        p.strip() for p in re.split(r"\n\n|\. {2,}", texto)
        if len(p.strip()) >= MIN_CHARS
    ]

    buffer = ""
    for p in paragrafos:
        buffer += " " + p
        if len(buffer) >= MAX_CHARS:
            trechos.append({
                "tema":    tema,
                "conteudo": buffer.strip()[:MAX_CHARS],
                "fonte":   "livro_web",
            })
            buffer = ""

    if len(buffer) >= MIN_CHARS:
        trechos.append({
            "tema":    tema,
            "conteudo": buffer.strip(),
            "fonte":   "livro_web",
        })

    return trechos


# ---------------------------------------------------------------------------
# Motor principal de leitura
# ---------------------------------------------------------------------------

class SiriusLeitor:
    """
    Minerador de livros e textos longos da web.

    Fluxo:
    1. FilaLeitor escolhe o próximo tema (usuário > descoberto > aleatório)
    2. NavegadorSirius abre a página de busca na fonte
    3. Extrai link do livro/texto
    4. ExtratorTexto extrai o texto limpo
    5. Divide em trechos e salva no banco
    6. Descobre novos temas no texto lido
    """

    def __init__(self, memoria):
        self.memoria  = memoria
        self.extrator = ExtratorTexto()
        self._fila    = FilaLeitor()
        self._rodando = False
        self._thread  = None
        self._total   = 0

        # Callbacks injetados pelo coordenador
        self._on_tema_descoberto = None  # chamado quando descobre tema novo
        self._coordenador        = None

    def _buscar_link_conteudo(self, html_busca: str, fonte: str) -> str | None:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_busca, "html.parser")

            if fonte == "gutenberg":
                li = soup.select_one("li.booklink a")
                if li:
                    return "https://www.gutenberg.org" + li["href"] + ".txt.utf-8"

            elif fonte == "archive":
                a = soup.select_one("h3.item-heading a")
                if a:
                    return "https://archive.org" + a["href"]

            elif fonte == "openlibrary":
                a = soup.select_one("li.searchResultItem a.results")
                if a:
                    return "https://openlibrary.org" + a["href"]

        except Exception as e:
            print(f"[LEITOR]: Erro ao extrair link: {e}")
        return None

    def _tentar_ler_pagina(self, nav: NavegadorSirius, url: str, tema: str) -> list[dict]:
        """
        Extrai conteúdo da URL.
        AGORA:  HTML → BeautifulSoup
        FUTURO: screenshot → SiriusVisao (descomentar bloco abaixo)
        """
        html = nav.navegar(url)
        if not html:
            return []

        # --- FUTURO: ativar quando SiriusVisao estiver pronto ---
        # screenshot_path = f"/tmp/sirius_{int(time.time())}.png"
        # if nav.tirar_screenshot(screenshot_path):
        #     texto = self.extrator.extrair_de_screenshot(screenshot_path)
        #     os.remove(screenshot_path)
        #     if texto:
        #         return _processar_em_trechos(texto, tema)

        texto = self.extrator.extrair(html, url)
        return _processar_em_trechos(texto, tema)

    def _salvar_trechos(self, trechos: list[dict]) -> tuple[int, list[str]]:
        """Salva trechos evitando duplicatas. Retorna (salvos, novos_temas)."""
        salvos      = 0
        novos_temas = []

        for t in trechos:
            # Verifica duplicata
            try:
                from neuronio import SiriusNeuronio
                if SiriusNeuronio().verificar_se_ja_sabe(t["conteudo"], threshold=0.90):
                    continue
            except Exception:
                pass

            ok = self.memoria.salvar_estudo_autonomo(
                tema=t["tema"],
                conteudo=f"[Livro] {t['conteudo']}",
                tags="leitura_autonoma"
            )
            if ok:
                salvos += 1
                # Descobre novos temas no texto salvo
                novos = _extrair_temas_do_texto(t["conteudo"])
                novos_temas.extend(novos)

        # Notifica coordenador sobre temas descobertos
        for novo in novos_temas:
            if self._on_tema_descoberto:
                self._on_tema_descoberto(novo, "leitor")

        return salvos, novos_temas

    def ler_tema(self, tema: str) -> int:
        """Executa um ciclo completo de leitura para um tema."""
        nav = NavegadorSirius()
        if not nav.iniciar():
            return 0

        total_salvos = 0

        try:
            fontes = list(FONTES_LIVROS.items())
            random.shuffle(fontes)

            for nome_fonte, config in fontes:
                try:
                    url_busca = config["busca"].format(tema=tema.replace(" ", "+"))
                    print(f"[LEITOR]: Buscando '{tema}' em {nome_fonte}...")

                    html_busca = nav.navegar(url_busca)
                    if not html_busca:
                        continue

                    link = self._buscar_link_conteudo(html_busca, nome_fonte)
                    if not link:
                        # Tenta fallback se disponível
                        fallback = config.get("fallback")
                        if fallback:
                            url_fb     = fallback.format(tema=tema.replace(" ", "+"))
                            html_busca = nav.navegar(url_fb)
                            if html_busca:
                                link = self._buscar_link_conteudo(html_busca, nome_fonte)

                    if not link:
                        print(f"[LEITOR]: Sem resultado em {nome_fonte} para '{tema}'")
                        continue

                    print(f"[LEITOR]: Lendo → {link}")
                    trechos = self._tentar_ler_pagina(nav, link, tema)

                    if trechos:
                        salvos, novos_temas = self._salvar_trechos(trechos)
                        total_salvos += salvos
                        self._total  += salvos

                        # Adiciona temas descobertos na fila
                        for novo in novos_temas:
                            self._fila.adicionar_descoberto(novo)

                        print(
                            f"\033[92m[LEITOR]: +{salvos} trechos de '{tema}' "
                            f"via {nome_fonte} | total: {self._total}\033[0m"
                        )
                        break  # achou conteúdo, não tenta outra fonte

                    time.sleep(2)

                except Exception as e:
                    print(f"[LEITOR]: Erro em {nome_fonte}: {e}")
                    continue

        finally:
            nav.encerrar()

        return total_salvos

    def _ciclo_leitura(self):
        """Loop autônomo — lê um tema a cada 45 minutos."""
        print(f"\033[94m[LEITOR]: Motor de leitura iniciado com {len(TEMAS_LEITURA)} temas.\033[0m")

        while self._rodando:
            tema = self._fila.proximo()
            try:
                salvos = self.ler_tema(tema)
                if salvos == 0:
                    print(f"[LEITOR]: Nenhum trecho aproveitável para '{tema}'.")
            except Exception as e:
                print(f"[LEITOR]: Erro no ciclo: {e}")

            print("[LEITOR]: Próxima leitura em 45 minutos.")
            time.sleep(2700)

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._ciclo_leitura,
            daemon=True,
            name="SiriusLeitor"
        )
        self._thread.start()
        print("\033[92m[LEITOR]: Leitura autônoma ativada.\033[0m")

    def parar(self):
        self._rodando = False


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from memoria import SiriusMemory

    parser = argparse.ArgumentParser(description="Testa o SiriusLeitor")
    parser.add_argument("--tema", type=str, default="artificial intelligence",
                        help="Tema para buscar")
    parser.add_argument("--continuo", action="store_true",
                        help="Roda em loop contínuo")
    args = parser.parse_args()

    mem    = SiriusMemory()
    leitor = SiriusLeitor(memoria=mem)

    if args.continuo:
        leitor.iniciar()
        print(f"Leitor rodando com {len(TEMAS_LEITURA)} temas. Ctrl+C para parar.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            leitor.parar()
    else:
        print(f"Testando leitura de: '{args.tema}'")
        salvos = leitor.ler_tema(args.tema)
        print(f"\nTotal absorvido: {salvos} trechos")