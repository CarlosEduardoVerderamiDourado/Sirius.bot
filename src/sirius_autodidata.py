"""
sirius_autodidata.py — S.I.R.I.U.S. v5.2 — Motor de Aprendizado Autônomo
=========================================================================
Processa os 325 temas da base de conhecimento:
  1. Lê a lista completa de TODOS_OS_TEMAS
  2. Verifica quais já existem no banco (evita duplicatas)
  3. Para cada tema novo: busca Wikipedia + web, gera resumo e salva via SiriusMemoria
  4. A cada lote de 10 temas: chama SiriusRAG.rebuild() para atualizar índice FAISS
  5. Logs coloridos no terminal com progresso em tempo real

Correções v5.2:
  - Wikipedia: headers UTF-8 corretos
  - DuckDuckGo: usa pacote 'duckduckgo-search' (import: duckduckgo_search)
  - Import corrigido: from memoria import SiriusMemoria (com 'ia' no final)
  - RAG rebuild em lote — não a cada item (performance)
  - Verificação de duplicata via SELECT COUNT antes de inserir

Dependências:
    pip install requests duckduckgo-search faiss-cpu sentence-transformers colorama
"""

from __future__ import annotations

import os
import re
import sys
import sqlite3
import threading
import time
import random
from typing import Optional

# ── Path ─────────────────────────────────────────────────────────────────────
_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

_CAMINHO_DATA = os.path.join(_DIR_RAIZ, "data")
_DB_TREINO    = os.path.join(_CAMINHO_DATA, "sirius_treino.db")
_DB_PESSOAL   = os.path.join(_CAMINHO_DATA, "sirius_pessoal.db")
os.makedirs(_CAMINHO_DATA, exist_ok=True)

# ── Imports opcionais ─────────────────────────────────────────────────────────
try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init(autoreset=True)
    _COLORAMA_OK = True
except ImportError:
    _COLORAMA_OK = False

# ── SiriusMemoria ─────────────────────────────────────────────────────────────
try:
    from memoria import SiriusMemoria
    _MEMORIA_OK = True
except ImportError:
    SiriusMemoria = None
    _MEMORIA_OK = False
    print("[AUTODIDATA]: AVISO — memoria.py não encontrado. Salvar estudos desabilitado.")


# =============================================================================
# Helpers de log colorido
# =============================================================================

def _log_info(msg: str):
    if _COLORAMA_OK:
        print(f"{Fore.CYAN}[AUTODIDATA]: {msg}{Style.RESET_ALL}")
    else:
        print(f"\033[96m[AUTODIDATA]: {msg}\033[0m")

def _log_ok(msg: str):
    if _COLORAMA_OK:
        print(f"{Fore.GREEN}[AUTODIDATA]: ✓ {msg}{Style.RESET_ALL}")
    else:
        print(f"\033[92m[AUTODIDATA]: ✓ {msg}\033[0m")

def _log_skip(msg: str):
    if _COLORAMA_OK:
        print(f"{Fore.YELLOW}[AUTODIDATA]: ↷ {msg}{Style.RESET_ALL}")
    else:
        print(f"\033[93m[AUTODIDATA]: ↷ {msg}\033[0m")

def _log_err(msg: str):
    if _COLORAMA_OK:
        print(f"{Fore.RED}[AUTODIDATA]: ✗ {msg}{Style.RESET_ALL}")
    else:
        print(f"\033[91m[AUTODIDATA]: ✗ {msg}\033[0m")

def _log_prog(atual: int, total: int, tema: str):
    pct = int((atual / total) * 100)
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    if _COLORAMA_OK:
        print(f"{Fore.BLUE}  [{bar}] {pct:3d}% ({atual}/{total}) — {tema[:50]}{Style.RESET_ALL}")
    else:
        print(f"\033[94m  [{bar}] {pct:3d}% ({atual}/{total}) — {tema[:50]}\033[0m")


# =============================================================================
# Banco de temas — 325 temas organizados por categoria
# =============================================================================

TEMAS_POR_CATEGORIA: dict[str, list[str]] = {
    "ciencias_exatas": [
        "matematica pura algebra abstrata", "calculo diferencial integral",
        "geometria euclidiana nao-euclidiana", "topologia matematica",
        "algebra linear matrizes vetores", "teoria dos numeros primos",
        "estatistica probabilidade", "teoria dos jogos",
        "fisica classica newtoniana", "fisica quantica mecanica quantica",
        "relatividade especial geral Einstein", "termodinamica leis",
        "eletromagnetismo Maxwell", "optica luz lasers",
        "fisica de particulas boson Higgs", "astrofisica estrelas",
        "fisica do estado solido semicondutores", "mecanica dos fluidos",
        "quimica organica reacoes", "quimica inorganica tabela periodica",
        "fisico-quimica termodinamica", "bioquimica metabolismo",
        "quimica analitica espectroscopia", "polimeros materiais",
    ],
    "ciencias_biologicas": [
        "biologia celular mitose meiose", "genetica DNA RNA proteinas",
        "evolucao darwinismo selecao natural", "ecologia ecossistemas",
        "botanica plantas fotossintese", "zoologia classificacao animais",
        "microbiologia bacterias virus", "imunologia sistema imune",
        "neurociencia cerebro neuronios", "fisiologia humana sistemas",
        "anatomia humana orgaos", "embriologia desenvolvimento embrionario",
        "biotecnologia CRISPR engenharia genetica", "bioinformatica genoma",
        "parasitologia doencas tropicais", "virologia pandemia epidemia",
        "biomedicina diagnostico tratamento", "farmacologia medicamentos",
        "toxicologia venenos antidotos", "paleontologia fosseis dinossauros",
    ],
    "tecnologia_computacao": [
        "algoritmos estruturas de dados", "complexidade computacional",
        "programacao orientada a objetos", "programacao funcional",
        "sistemas operacionais Linux Windows", "redes de computadores TCP IP",
        "seguranca cibernetica criptografia", "banco de dados SQL NoSQL",
        "desenvolvimento web frontend backend", "APIs REST GraphQL",
        "computacao em nuvem AWS Azure", "containers Docker Kubernetes",
        "inteligencia artificial machine learning", "redes neurais deep learning",
        "processamento de linguagem natural NLP", "visao computacional",
        "robotica automacao", "internet das coisas IoT",
        "blockchain criptomoedas", "computacao quantica qubits",
        "compiladores interpretadores", "sistemas distribuidos",
        "programacao paralela GPU CUDA", "teoria da computacao Turing",
        "engenharia de software design patterns", "devops CI CD",
        "python avancado programacao", "javascript typescript web",
        "rust linguagem sistemas", "c++ programacao", "golang go",
    ],
    "historia_geral": [
        "pre-historia homo sapiens evolucao humana", "antigas civilizacoes Mesopotamia",
        "Egito antigo faraos piramides", "Grecia antiga democracia filosofia",
        "Imperio Romano ascensao queda", "Idade Media feudalismo cruzadas",
        "Renascimento humanismo arte ciencia", "Revolucao Francesa iluminismo",
        "Revolucao Industrial capitalismo", "Primeira Guerra Mundial causas",
        "Segunda Guerra Mundial Holocausto", "Guerra Fria URSS EUA",
        "descolonizacao Africa Asia", "historia do Brasil colonizacao",
        "historia da America Latina independencia", "historia da China imperial",
        "historia do Japao samurais modernizacao",
    ],
    "filosofia": [
        "filosofia pre-socratica Tales Heraclito", "Socrates Plato epistemologia",
        "Aristoteles logica etica", "estoicismo epicurismo",
        "filosofia medieval escolastica Tomas de Aquino", "Descartes dualismo",
        "empirismo Locke Hume", "Kant imperativo categorico",
        "Hegel dialética fenomenologia", "Marx materialismo historico",
        "Nietzsche vontade de poder", "existencialismo Sartre Camus",
        "filosofia analitica Wittgenstein", "etica deontologica consequencialismo",
        "filosofia da mente consciencia", "filosofia da ciencia Popper Kuhn",
        "hermeneutica fenomenologia Husserl", "filosofia politica Hobbes Rousseau",
    ],
    "economia_financas": [
        "microeconomia oferta demanda", "macroeconomia PIB inflacao",
        "mercado financeiro acoes bonds", "politica monetaria banco central",
        "sistema bancario credito moeda", "comercio internacional balanca",
        "economia comportamental vieses cognitivos", "keynesianismo monetarismo",
        "desenvolvimento economico crescimento", "desigualdade social Gini",
        "criptoeconomia DeFi tokenomics", "contabilidade financas corporativas",
        "investimentos renda fixa renda variavel", "startups venture capital",
        "economia circular sustentabilidade",
    ],
    "psicologia_comportamento": [
        "psicologia clinica psicoterapia", "psicanalise Freud Jung",
        "psicologia cognitiva comportamental TCC", "neuropsicologia cerebro emocoes",
        "desenvolvimento humano Piaget Vygotsky", "aprendizagem memoria cognicao",
        "inteligencia emocional Daniel Goleman", "psicologia positiva bem-estar",
        "motivacao hierarquia Maslow", "psicologia social grupos influencia",
        "transtornos mentais DSM diagnostico", "mindfulness meditacao",
        "psicologia organizacional trabalho", "vieses cognitivos heuristicas",
        "persuasao influencia Cialdini",
    ],
    "artes_cultura": [
        "historia da arte renascimento barroco", "arte moderna impressionismo",
        "arte contemporanea instalacao performance", "musica classica Bach Mozart",
        "jazz blues musica americana", "musica popular brasileira MPB samba",
        "cinema historia efeitos especiais", "literatura brasileira modernismo",
        "literatura mundial classicos", "teatro drama comédia tragedia",
        "arquitetura estilos historicos", "design grafico comunicacao visual",
        "fotografia historia tecnica", "danca ballet contemporanea",
        "mitologia grega romana nórdica",
    ],
    "saude_medicina": [
        "anatomia humana sistema cardiovascular", "sistema nervoso central periferico",
        "oncologia tipos de cancer tratamento", "cardiologia doencas cardiacas",
        "diabetes mellitus tipo 1 2", "saude mental depressao ansiedade",
        "nutricao macronutrientes micronutrientes", "epidemiologia saude publica",
        "medicina de emergencia primeiros socorros", "cirurgia tecnicas procedimentos",
        "genetica medica doencas hereditarias", "vacinacao imunizacao historia",
        "medicina tradicional chinesa ayurveda", "saude da mulher ginecologia",
        "pediatria desenvolvimento infantil",
    ],
    "direito_politica": [
        "direito constitucional democracia", "direito penal crimes penas",
        "direito civil contratos familia", "direito internacional tratados",
        "geopolitica relacoes internacionais", "sistemas de governo democracia",
        "direitos humanos liberdades fundamentais", "partidos politicos ideologias",
        "politicas publicas estado bem-estar", "direito trabalhista CLT",
        "direito digital privacidade LGPD", "corrupcao governança publica",
    ],
    "meio_ambiente": [
        "mudancas climaticas efeito estufa", "biodiversidade conservacao",
        "energia renovavel solar eolica", "poluicao tipos impactos",
        "desmatamento Amazonia florestas", "oceanos ecossistemas marinhos",
        "geologia tecnica placas tectônicas", "meteorologia clima previsão",
        "sustentabilidade agenda 2030 ODS", "gestao de residuos reciclagem",
    ],
    "matematica_aplicada": [
        "estatistica descritiva inferencial", "probabilidade combinatoria",
        "algebra linear aplicacoes ML", "calculo numerico metodos",
        "grafos teoria algoritmos", "criptografia matematica",
        "teoria da informacao Shannon", "matematica financeira juros",
        "pesquisa operacional otimizacao", "series temporais previsao",
    ],
    "linguistica_idiomas": [
        "linguistica estrutural Saussure", "psicolinguistica aquisicao linguagem",
        "sociolinguistica variacao linguistica", "fonologia morfologia sintaxe",
        "ingles avancado gramatica", "espanhol lingua hispanofona",
        "lingua portuguesa historia evolucao", "traducao interpretacao",
        "linguagem de sinais LIBRAS", "etimologia origem das palavras",
    ],
}

# Lista plana de todos os temas
TODOS_OS_TEMAS: list[str] = [
    tema
    for temas in TEMAS_POR_CATEGORIA.values()
    for tema in temas
]


# =============================================================================
# Verificação de duplicata no banco
# =============================================================================

def _tema_ja_existe(tema: str) -> bool:
    """
    Verifica se o tema já foi estudado e salvo no banco treino.
    Retorna True se existe pelo menos 1 registro com aquele tema.
    """
    tema_lower = tema.lower().strip()
    for tabela in ("estudos_autonomos", "conhecimento_geral"):
        try:
            conn = sqlite3.connect(_DB_TREINO)
            r = conn.execute(
                f"SELECT COUNT(*) FROM {tabela} WHERE tema=?",
                (tema_lower,),
            ).fetchone()
            conn.close()
            if r and r[0] > 0:
                return True
        except Exception:
            pass
    return False


# =============================================================================
# Busca de conteúdo (Wikipedia + web)
# =============================================================================

_HEADERS_HTTP = {
    "User-Agent":      "SiriusBot/5.2 (Assistente educacional; +https://github.com/sirius)",
    "Accept":          "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


def _buscar_wikipedia(tema: str) -> list[dict]:
    """Busca resumo do tema na Wikipedia PT-BR."""
    if not _REQUESTS_OK:
        return []
    try:
        url    = "https://pt.wikipedia.org/api/rest_v1/page/summary/" + tema.replace(" ", "_")
        r      = requests.get(url, headers=_HEADERS_HTTP, timeout=8)
        r.encoding = "utf-8"
        if r.status_code != 200:
            return []
        data   = r.json()
        corpo  = data.get("extract", "").strip()
        titulo = data.get("title", tema)
        if len(corpo) < 80:
            return []
        return [{"tema": tema, "titulo": titulo, "corpo": corpo[:2000], "fonte": "wikipedia"}]
    except Exception:
        return []


def _buscar_web(tema: str) -> list[dict]:
    """
    Busca resultados via DuckDuckGo.
    Tenta os dois nomes de pacote para compatibilidade:
      - duckduckgo_search (PyPI: duckduckgo-search) — nome correto e atual
      - ddgs                                         — nome antigo (legado)
    """
    DDGS = None

    # Tenta o pacote atual primeiro
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        pass

    # Fallback para instalações antigas
    if DDGS is None:
        try:
            from ddgs import DDGS  # type: ignore[no-redef]
        except ImportError:
            return []   # nenhum pacote disponível

    try:
        resultados = []
        with DDGS() as ddg:
            for r in ddg.text(tema + " explicação conceito", max_results=3, region="br-pt"):
                corpo = r.get("body", "").strip()
                if corpo and len(corpo) > 80:
                    resultados.append({
                        "tema":   tema,
                        "titulo": r.get("title", tema),
                        "corpo":  corpo[:1500],
                        "fonte":  "web",
                    })
        return resultados
    except Exception:
        return []


# =============================================================================
# RAG rebuild
# =============================================================================

# Importação defensiva do SiriusRAG — não quebra se arquivo ausente
try:
    from sirius_rag import SiriusRAG as _SiriusRAG
    _RAG_DISPONIVEL = True
except ImportError:
    _SiriusRAG      = None   # type: ignore[assignment,misc]
    _RAG_DISPONIVEL = False

# Instância singleton — evita recriar modelo/índice a cada lote
_rag_instancia: Optional[object] = None
_rag_lock = threading.Lock()


def _get_rag() -> Optional[object]:
    """Retorna a instância singleton do RAG, criando se necessário."""
    global _rag_instancia
    if not _RAG_DISPONIVEL:
        return None
    with _rag_lock:
        if _rag_instancia is None:
            try:
                _rag_instancia = _SiriusRAG()
            except Exception as e:
                _log_err(f"Não foi possível inicializar SiriusRAG: {e}")
                return None
    return _rag_instancia


def _rebuild_rag():
    """
    Atualiza o índice FAISS/TF-IDF com os novos estudos salvos no banco.
    Chamado a cada lote de 10 temas processados.
    Falha silenciosamente se RAG não estiver disponível.
    """
    rag = _get_rag()
    if rag is None:
        return   # RAG não instalado — comportamento degradado esperado

    try:
        n = rag.rebuild()
        if n > 0:
            modo = getattr(rag, "modo", lambda: "?")()
            _log_ok(f"Índice RAG [{modo}] atualizado: {n} documentos.")
        else:
            _log_skip("RAG rebuild: banco ainda vazio, aguardando mais estudos.")
    except Exception as e:
        _log_err(f"Erro durante rebuild RAG: {e}")


# =============================================================================
# Fila de temas com shuffle e descoberta dinâmica
# =============================================================================

class FilaDeTemas:
    def __init__(self):
        import random
        self._base        = list(TODOS_OS_TEMAS)
        random.shuffle(self._base)
        self._fila_base   = list(self._base)
        self._descobertos: list[str] = []
        self._lock        = threading.Lock()
        self._idx         = 0

    def proximo(self) -> str:
        with self._lock:
            # Prioriza temas descobertos
            if self._descobertos:
                return self._descobertos.pop(0)
            if self._idx >= len(self._fila_base):
                # Reinicia ciclo com shuffle
                import random
                random.shuffle(self._fila_base)
                self._idx = 0
            tema = self._fila_base[self._idx]
            self._idx += 1
            return tema

    def adicionar_descoberto(self, tema: str):
        with self._lock:
            if tema not in self._fila_base and tema not in self._descobertos:
                self._descobertos.append(tema)

    def total_usuario(self) -> int:
        return 0

    def total_descobertos(self) -> int:
        with self._lock:
            return len(self._descobertos)


# =============================================================================
# SiriusAutodidata — Motor principal
# =============================================================================

class SiriusAutodidata:
    """
    Motor de aprendizado autônomo do S.I.R.I.U.S. v5.2.

    Uso:
        mem = SiriusMemoria()
        bot = SiriusAutodidata(memoria=mem)
        bot.iniciar()   # roda em background (thread daemon)
    """

    def __init__(self, memoria=None, cerebro=None):
        self.memoria       = memoria or (SiriusMemoria() if _MEMORIA_OK else None)
        self.cerebro       = cerebro
        self._rodando      = False
        self._thread       = None
        self._fila         = FilaDeTemas()
        self._ciclo        = 0
        self._total_salvos = 0
        self._lote_atual   = 0   # contador para RAG rebuild a cada 10 temas
        self._pausado      = False   # controlado pelo SiriusScheduler

    # =========================================================================
    # Loop principal
    # =========================================================================

    def _ciclo_aprendizado(self):
        total_temas = len(TODOS_OS_TEMAS)
        _log_info(f"Motor iniciado com {total_temas} temas base.")
        _log_info(f"RAG rebuild a cada 10 temas processados.")

        while self._rodando:
            # ── Pausa controlada pelo SiriusScheduler ──────────────────────
            if self._pausado:
                time.sleep(10)
                continue
            tema = self._fila.proximo()
            self._ciclo += 1

            # ── Exibe progresso ───────────────────────────────────────────────
            _log_prog(
                min(self._ciclo, total_temas),
                total_temas,
                tema,
            )

            # ── Verifica duplicata ────────────────────────────────────────────
            if _tema_ja_existe(tema):
                _log_skip(f"Já existe no banco: '{tema}'")
                time.sleep(2)
                continue

            # ── Busca conteúdo ────────────────────────────────────────────────
            itens: list[dict] = []

            wiki = _buscar_wikipedia(tema)
            itens.extend(wiki)

            if self._ciclo % 2 == 0:  # alterna para não sobrecarregar
                web = _buscar_web(tema)
                itens.extend(web)

            if self._ciclo % 3 == 0:
                auto = self._gerar_autodialogo(tema)
                itens.extend(auto)

            # ── Salva no banco ────────────────────────────────────────────────
            salvos, novos_temas = self._salvar_itens(itens, tema)
            self._total_salvos += salvos

            for novo in novos_temas:
                self._fila.adicionar_descoberto(novo)

            if salvos > 0:
                _log_ok(
                    f"+{salvos} conhecimento(s) sobre '{tema[:40]}' | "
                    f"total acumulado: {self._total_salvos}"
                )
            else:
                _log_skip(f"Nada novo sobre '{tema[:40]}'.")

            # ── RAG rebuild a cada lote de 10 temas ──────────────────────────
            self._lote_atual += 1
            if self._lote_atual >= 10:
                _log_info("Lote de 10 temas concluído → atualizando índice FAISS...")
                threading.Thread(target=_rebuild_rag, daemon=True).start()
                self._lote_atual = 0

            # Pausa entre temas (não sobrecarrega APIs externas)
            time.sleep(random.uniform(180, 360))  # 3 a 6 minutos

    # =========================================================================
    # Salvar itens no banco via SiriusMemoria
    # =========================================================================

    def _salvar_itens(self, itens: list[dict], tema_base: str) -> tuple[int, list[str]]:
        salvos      = 0
        novos_temas = []

        if not self.memoria:
            return salvos, novos_temas

        for item in itens:
            corpo = item.get("corpo", "").strip()
            tema  = item.get("tema", tema_base).strip()
            fonte = item.get("fonte", "web")

            if not corpo or len(corpo) < 30:
                continue

            ok = self.memoria.salvar_estudo_autonomo(
                tema    = tema,
                conteudo= f"[{fonte}] {corpo[:1500]}",
                tags    = "autodidata",
                fonte   = fonte,
            )
            if ok:
                salvos += 1
                # Extrai possíveis novos temas do conteúdo
                for novo in self._extrair_novos_temas(corpo):
                    novos_temas.append(novo)

        return salvos, novos_temas

    # =========================================================================
    # Extração de novos temas a partir do conteúdo
    # =========================================================================

    @staticmethod
    def _extrair_novos_temas(corpo: str) -> list[str]:
        stopwords = {
            "que","com","por","para","uma","dos","das","são","como",
            "mais","mas","não","foi","sobre","entre","quando","cada",
        }
        candidatos = re.findall(r"\b[A-Z][a-záéíóúãõâêôàç]{3,}\b", corpo)
        vistos     = set()
        resultado  = []
        for c in candidatos:
            cl = c.lower()
            if cl not in stopwords and cl not in vistos and len(cl) >= 4:
                vistos.add(cl)
                resultado.append(cl)
        return resultado[:3]

    # =========================================================================
    # Auto-diálogo com SiriusGerador
    # =========================================================================

    @staticmethod
    def _gerar_autodialogo(tema: str) -> list[dict]:
        try:
            from sirius_gerador import SiriusGerador
            gerador = SiriusGerador()
            if not gerador.esta_treinado():
                return []
            perguntas = [
                f"o que e {tema}?",
                f"como funciona {tema}?",
                f"explica {tema} de forma simples",
                f"quais sao as aplicacoes de {tema}?",
            ]
            pergunta = random.choice(perguntas)
            resposta = gerador.gerar(pergunta)
            if resposta and len(resposta) > 20:
                return [{"tema": tema, "titulo": pergunta, "corpo": resposta, "fonte": "autodialogo"}]
        except Exception:
            pass
        return []

    # =========================================================================
    # Interface pública
    # =========================================================================

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._ciclo_aprendizado,
            daemon=True,
            name="SiriusAutodidata",
        )
        self._thread.start()
        _log_ok("Aprendizado autônomo ativado.")

    def parar(self):
        self._rodando = False
        _log_info("Sinal de parada enviado.")

    def processar_batch_agora(self, n: int = 10) -> dict:
        """
        Processa N temas imediatamente (bloqueante) — útil para testes.
        Retorna estatísticas do batch.
        """
        total_temas = len(TODOS_OS_TEMAS)
        salvos_total = 0
        pulados      = 0

        _log_info(f"Batch síncrono: processando {n} temas...")

        for i in range(n):
            tema = self._fila.proximo()

            _log_prog(i + 1, n, tema)

            if _tema_ja_existe(tema):
                _log_skip(f"Já existe: '{tema}'")
                pulados += 1
                continue

            itens = _buscar_wikipedia(tema)
            if i % 2 == 0:
                itens += _buscar_web(tema)

            salvos, _ = self._salvar_itens(itens, tema)
            salvos_total += salvos

            if salvos > 0:
                _log_ok(f"+{salvos} sobre '{tema[:40]}'")
            else:
                _log_skip(f"Nada novo sobre '{tema[:40]}'")

            self._lote_atual += 1
            if self._lote_atual >= 10:
                _log_info("Lote completo → rebuild FAISS...")
                _rebuild_rag()
                self._lote_atual = 0

            time.sleep(1)  # pequena pausa para não sobrecarregar APIs

        resultado = {
            "temas_processados": n,
            "salvos":  salvos_total,
            "pulados": pulados,
        }
        _log_ok(f"Batch concluído: {resultado}")
        return resultado

    def status(self) -> dict:
        return {
            "rodando":            self._rodando,
            "ciclos_completados": self._ciclo,
            "total_salvos":       self._total_salvos,
            "temas_descobertos":  self._fila.total_descobertos(),
            "temas_base":         len(TODOS_OS_TEMAS),
            "lote_atual":         self._lote_atual,
        }

    def imprimir_status(self):
        s = self.status()
        print("\n\033[95m[AUTODIDATA STATUS]\033[0m")
        print(f"  Rodando:           {'Sim' if s['rodando'] else 'Não'}")
        print(f"  Ciclos:            {s['ciclos_completados']}")
        print(f"  Conhecimentos:     {s['total_salvos']}")
        print(f"  Temas base:        {s['temas_base']}")
        print(f"  Temas descobertos: {s['temas_descobertos']}")
        print(f"  Lote atual:        {s['lote_atual']}/10")
        print()


# =============================================================================
# Funções de demonstração visual (mantidas da versão anterior)
# =============================================================================

def _criar_tabela_demonstracoes_visuais():
    conn = sqlite3.connect(_DB_PESSOAL)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demonstracoes_visuais (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           TEXT    DEFAULT '',
            nome              TEXT,
            descricao         TEXT,
            sequencia_json    TEXT,
            imagem_referencia TEXT,
            criado_em         DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, nome)
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_demo_visual_user ON demonstracoes_visuais(user_id, nome);"
    )
    conn.commit()
    conn.close()


def salvar_demonstracao_visual(
    user_id: str, nome: str, descricao: str,
    sequencia_json: str, imagem_referencia: str = "",
) -> bool:
    if not nome or not sequencia_json:
        return False
    user_id = (user_id or "guest").strip()
    nome    = nome.strip().lower()
    try:
        _criar_tabela_demonstracoes_visuais()
        conn = sqlite3.connect(_DB_PESSOAL)
        conn.execute(
            """
            INSERT INTO demonstracoes_visuais
                (user_id, nome, descricao, sequencia_json, imagem_referencia, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, nome) DO UPDATE SET
                descricao         = excluded.descricao,
                sequencia_json    = excluded.sequencia_json,
                imagem_referencia = excluded.imagem_referencia,
                updated_at        = CURRENT_TIMESTAMP
            """,
            (user_id, nome, descricao or "", sequencia_json, imagem_referencia or ""),
        )
        conn.commit()
        return True
    except Exception as e:
        _log_err(f"Erro ao salvar demonstração visual: {e}")
        return False
    finally:
        try: conn.close()
        except Exception: pass


# =============================================================================
# Standalone
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="S.I.R.I.U.S. Autodidata v5.2")
    parser.add_argument(
        "--batch", type=int, default=0,
        help="Processa N temas imediatamente (síncrono) e sai. Ex: --batch 20"
    )
    parser.add_argument(
        "--continuo", action="store_true",
        help="Roda em loop contínuo em background (daemon)"
    )
    args = parser.parse_args()

    if not _MEMORIA_OK:
        print("[AUTODIDATA]: ERRO — memoria.py não encontrado. Instale ou coloque no path.")
        sys.exit(1)

    mem = SiriusMemoria()
    bot = SiriusAutodidata(memoria=mem)

    print("=" * 60)
    print(f"  S.I.R.I.U.S. Autodidata v5.2")
    print(f"  Base de conhecimento: {len(TODOS_OS_TEMAS)} temas")
    print("=" * 60)

    if args.batch > 0:
        resultado = bot.processar_batch_agora(n=args.batch)
        print(f"\n  Resultado: {resultado}")
        sys.exit(0)

    # Modo contínuo (padrão)
    bot.iniciar()
    print(f"  Autodidata rodando. Ctrl+C para parar.")
    try:
        while True:
            time.sleep(60)
            bot.imprimir_status()
    except KeyboardInterrupt:
        bot.parar()
        print("\n  Encerrado.")