"""
sirius_autodidata.py — Motor de aprendizado autônomo do Sirius
Correções:
- Wikipedia: fix de encoding UTF-8 e headers corretos
- DuckDuckGo: migrado para pacote ddgs (renomeado de duckduckgo_search)
"""

import os
import sys
import time
import random
import threading
import sqlite3
import re
import requests

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
DB_TREINO    = os.path.join(CAMINHO_DATA, "sirius_treino.db")
DB_PESSOAL   = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
os.makedirs(CAMINHO_DATA, exist_ok=True)

# Headers padrao para todas as requisicoes — resolve problema do Wikipedia
HEADERS_HTTP = {
    "User-Agent":      "SiriusBot/1.0 (Assistente educacional)",
    "Accept":          "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# ---------------------------------------------------------------------------
# BANCO DE TEMAS
# ---------------------------------------------------------------------------

TEMAS_POR_CATEGORIA = {

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
        "descolonizacao Africa Asia", "historia da China Imperial",
        "Japao feudal Samurai Meiji", "Imperios Otomano Persa historia",
        "historia da Africa subsaariana", "civilizacoes pre-colombianas Maias",
        "historia medieval Europa", "era das navegacoes descobrimentos",
        "historia contemporanea seculo XX", "geopolitica relacoes internacionais",
    ],

    "historia_brasil": [
        "Brasil pre-colonial povos indigenas", "descobrimento colonizacao portuguesa",
        "ciclo do acucar escravidao Brasil", "Inconfidencia Mineira historia",
        "familia real portuguesa vinda Brasil", "independencia do Brasil 1822",
        "Imperio brasileiro Pedro I Pedro II", "abolicao escravidao Lei Aurea",
        "Republica Velha cafe com leite", "Era Vargas Estado Novo Brasil",
        "Juscelino Kubitschek Brasilia", "ditadura militar 1964 Brasil",
        "Diretas Ja redemocratizacao", "Constituicao Federal 1988 Brasil",
        "Plano Real estabilizacao economica", "cultura afro-brasileira quilombos",
        "imigracao italiana japonesa Brasil", "Amazonia desmatamento",
        "favelas urbanizacao Brasil", "futebol brasileiro historia Copa",
        "carnaval cultura popular brasileira", "literatura brasileira Machado Assis",
        "modernismo semana arte 1922", "tropicalismo musica brasileira",
    ],

    "filosofia_pensamento": [
        "pre-socraticos Tales Heraclito", "Socrates metodo maieutico",
        "Platao teoria das ideias", "Aristoteles logica metafisica",
        "estoicismo epicurismo filosofia", "filosofia medieval Tomas de Aquino",
        "Descartes racionalismo cogito", "Hume empirismo ceticismo",
        "Kant critica razao pura", "Hegel dialetica espirito",
        "Marx materialismo historico capital", "Nietzsche vontade de potencia",
        "existencialismo Sartre Camus", "fenomenologia Husserl Heidegger",
        "filosofia analitica Wittgenstein", "pragmatismo americano filosofia",
        "filosofia da ciencia Popper Kuhn", "etica deontologica consequencialista",
        "filosofia politica Hobbes Locke Rousseau", "feminismo filosofico",
        "filosofia budista zen", "logica formal paradoxos",
        "epistemologia teoria do conhecimento", "filosofia da mente consciencia",
    ],

    "arte_cultura": [
        "historia da arte renascimento barroco", "impressionismo pos-impressionismo",
        "arte moderna cubismo surrealismo", "arte contemporanea conceitual",
        "escultura grega medieval moderna", "arquitetura estilos historicos",
        "fotografia historia tecnica", "cinema historia linguagem",
        "teatro dramaturgia Shakespeare", "danca bale contemporaneo",
        "musica classica Bach Mozart Beethoven", "jazz blues historia origem",
        "rock historia Beatles Rolling Stones", "musica eletronica sintese",
        "MPB musica popular brasileira", "samba choro baiao historia",
        "funk rap hip-hop historia", "literatura mundial romances classicos",
        "poesia lirica epica", "mitologia grega nordica",
        "religioes mundiais comparadas", "budismo hinduismo islamismo",
        "cristandade historia biblica", "folclore brasileiro lendas",
    ],

    "ciencias_sociais": [
        "sociologia Durkheim Weber Marx", "antropologia cultural",
        "psicologia comportamental cognitiva", "psicanalise Freud Jung",
        "psicologia social grupos influencia", "neuropsicologia funcoes cognitivas",
        "economia microeconomia macroeconomia", "teoria economica keynesiana",
        "economia comportamental Kahneman", "mercado financeiro bolsa valores",
        "direito constitucional brasileiro", "direito penal civil",
        "ciencia politica sistemas governos", "teoria democratica",
        "relacoes internacionais diplomacia", "geopolitica potencias mundiais",
        "comunicacao jornalismo midia", "semiotica linguistica",
        "educacao pedagogia aprendizagem", "sociologia urbana cidades",
        "criminologia violencia seguranca", "direitos humanos",
    ],

    "saude_medicina": [
        "anatomia sistemas do corpo humano", "fisiologia cardiovascular",
        "neurologia sistema nervoso", "oncologia tipos de cancer",
        "cardiologia doencas cardiacas", "endocrinologia hormonios diabetes",
        "psiquiatria transtornos mentais", "nutricao dieta metabolismo",
        "medicina preventiva epidemiologia", "saude mental bem-estar",
        "farmacologia classes de medicamentos", "imunologia vacinas",
        "cirurgia historia tecnicas", "medicina de emergencia trauma",
        "pediatria saude infantil", "geriatria envelhecimento",
        "odontologia saude bucal", "oftalmologia visao",
        "fisioterapia reabilitacao", "medicina alternativa acupuntura",
        "genomica medicina personalizada", "bioetica experimentos clinicos",
    ],

    "meio_ambiente": [
        "mudancas climaticas aquecimento global", "efeito estufa gases",
        "energias renovaveis solar eolica", "sustentabilidade desenvolvimento",
        "biodiversidade extincao especies", "desmatamento Amazonia",
        "poluicao plastico oceanos", "gestao de residuos reciclagem",
        "ecossistemas biomas brasileiros", "agua saneamento basico",
        "agricultura organica agronegocio", "agroecologia permacultura",
        "geologia tectonica vulcoes terremotos", "oceanografia correntes",
        "climatologia meteorologia previsao", "recursos naturais mineracao",
        "politica ambiental acordos climaticos", "tecnologia limpa greentech",
    ],

    "engenharia_aplicada": [
        "engenharia civil estruturas pontes", "engenharia eletrica circuitos",
        "engenharia mecanica termodinamica", "engenharia quimica processos",
        "engenharia aeroespacial aviacao", "engenharia nuclear reator",
        "engenharia biomedica proteses", "nanotecnologia materiais",
        "automacao robotica industrial", "inteligencia artificial aplicada",
        "manufatura aditiva impressao 3D", "eletronica microcontroladores",
        "energia nuclear fusao fissao", "propulsao foguetes satelites",
        "engenharia de producao logistica", "metrologia controle qualidade",
    ],

    "espaco_astronomia": [
        "sistema solar planetas luas", "formacao estrelas nebulosas",
        "buracos negros singularidades", "galaxias Via Lactea universo",
        "Big Bang origem universo", "materia escura energia escura",
        "exploracao espacial NASA SpaceX", "Estacao Espacial Internacional",
        "exoplanetas vida extraterrestre", "telescopios James Webb Hubble",
        "missoes Marte Lua Artemis", "astrofisica altas energias",
        "ondas gravitacionais LIGO", "cosmologia inflacao cosmica",
        "meteoritos asteroides cometas", "astrobiologia origens vida",
    ],

    "entretenimento_games": [
        "historia dos videogames Atari Nintendo", "game design mecanicas",
        "RPG dungeons dragons", "e-sports competitivo League of Legends",
        "Minecraft construcao survival", "battle royale Fortnite PUBG",
        "Pokemon competitivo VGC estrategia", "jogos indie desenvolvedores",
        "realidade virtual aumentada games", "programacao de jogos Unity",
        "anime historia cultura otaku", "manga quadrinhos japoneses",
        "cultura pop geek nerd", "streaming Twitch YouTube gaming",
        "historia dos consoles geracoes", "retrogaming emuladores",
        "jogos de tabuleiro modernos", "xadrez abertura defesa estrategia",
    ],

    "economia_negocios": [
        "empreendedorismo startups inovacao", "venture capital investimento",
        "marketing digital redes sociais", "branding identidade marca",
        "gestao empresarial administracao", "contabilidade financas",
        "economia brasileira PIB inflacao", "bancos sistema financeiro",
        "criptomoedas Bitcoin Ethereum", "mercado imobiliario investimento",
        "renda fixa variavel bolsa", "economia circular sustentabilidade",
        "cadeia de suprimentos logistica", "economia comportamental vieses",
    ],

    "linguagem_comunicacao": [
        "linguistica estrutura linguagem", "gramatica portuguesa normas",
        "etimologia origem palavras", "dialetos variedades linguisticas",
        "linguas indigenas brasileiras", "latim linguas romanicas",
        "escrita historia alfabeto", "retorica argumentacao",
        "semiotica signos simbolos", "traducao interpretacao",
        "oratoria comunicacao publica", "redacao academica",
        "jornalismo investigativo", "publicidade propaganda",
        "idiomas mais falados mundo", "Libras lingua de sinais",
    ],

    "curiosidades_gerais": [
        "recordes mundiais feitos extraordinarios", "inventores descobertas acidentais",
        "misterios nao resolvidos ciencia", "fenomenos naturais raros",
        "animais mais inteligentes", "extremofilos vida extrema",
        "ilusoes de otica percepcao", "efeitos psicologicos vieses cognitivos",
        "lendas urbanas origem verdade", "tecnologias do futuro previsoes",
        "transhumanismo pos-humano", "inteligencia artificial etica",
        "singularidade tecnologica", "paradoxos filosoficos logica",
    ],
}

TODOS_OS_TEMAS = [
    tema
    for categoria in TEMAS_POR_CATEGORIA.values()
    for tema in categoria
]

print(f"[AUTODIDATA]: {len(TODOS_OS_TEMAS)} temas de estudo carregados.")


# ---------------------------------------------------------------------------
# Extracao de novos temas
# ---------------------------------------------------------------------------

def _extrair_novos_temas(texto: str) -> list[str]:
    novos   = []
    padroes = [
        r"conhecid[ao] como ([A-Za-z\s]{5,40})",
        r"denominad[ao] ([A-Za-z\s]{5,40})",
        r"chamad[ao] de ([A-Za-z\s]{5,40})",
        r"teoria d[ae] ([A-Za-z\s]{5,40})",
        r"conceito de ([A-Za-z\s]{5,40})",
        r"efeito ([A-Za-z\s]{3,30})",
        r"([A-Za-z\s]{5,30}) foi descobert[ao]",
        r"([A-Za-z\s]{5,30}) foi desenvolvid[ao]",
    ]
    for padrao in padroes:
        for m in re.findall(padrao, texto, re.IGNORECASE):
            t = m.strip().lower()
            if 5 < len(t) < 50 and t not in TODOS_OS_TEMAS:
                novos.append(t)
    return list(set(novos))[:5]


# ---------------------------------------------------------------------------
# Fila dinamica de temas
# ---------------------------------------------------------------------------

class FilaDeTemas:
    def __init__(self):
        self._fila_usuario      = []
        self._fila_descobertos  = []
        self._historico_recente = []

    def adicionar_descoberto(self, tema: str):
        if tema not in self._fila_descobertos and tema not in TODOS_OS_TEMAS:
            self._fila_descobertos.append(tema)

    def puxar_perguntas_usuario(self):
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
        except Exception as e:
            print(f"[FILA]: Erro ao ler perguntas: {e}")

    def proximo(self) -> str:
        self.puxar_perguntas_usuario()

        if self._fila_usuario and random.random() < 0.50:
            tema = self._fila_usuario.pop(0)
            self._registrar(tema)
            print(f"\033[95m[FILA]: Estudando pergunta do usuario -> '{tema}'\033[0m")
            return tema

        if self._fila_descobertos and random.random() < 0.30:
            tema = self._fila_descobertos.pop(0)
            self._registrar(tema)
            return tema

        candidatos = [t for t in TODOS_OS_TEMAS if t not in self._historico_recente]
        if not candidatos:
            candidatos = TODOS_OS_TEMAS
        tema = random.choice(candidatos)
        self._registrar(tema)
        return tema

    def _registrar(self, tema: str):
        self._historico_recente.append(tema)
        if len(self._historico_recente) > 20:
            self._historico_recente.pop(0)

    def total_usuario(self) -> int:
        return len(self._fila_usuario)

    def total_descobertos(self) -> int:
        return len(self._fila_descobertos)


# ---------------------------------------------------------------------------
# FIX Wikipedia — headers corretos + encoding explicito
# ---------------------------------------------------------------------------

def _buscar_wikipedia(tema: str) -> list[dict]:
    # Limpa tema para URL — remove acentos e caracteres especiais
    tema_url = re.sub(r"[^\w\s]", "", tema).strip().replace(" ", "_")

    for lang in ["pt", "en"]:
        try:
            url  = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{tema_url}"
            resp = requests.get(url, headers=HEADERS_HTTP, timeout=10)
            resp.encoding = "utf-8"

            if resp.status_code != 200 or not resp.text.strip():
                # Fallback via OpenSearch
                params = {
                    "action": "opensearch",
                    "search": tema,
                    "limit":  1,
                    "format": "json",
                    "utf8":   1,
                }
                r = requests.get(
                    f"https://{lang}.wikipedia.org/w/api.php",
                    params=params,
                    headers=HEADERS_HTTP,
                    timeout=10
                )
                r.encoding = "utf-8"
                if not r.text.strip():
                    continue
                data = r.json()
                if not (data and len(data) > 1 and data[1]):
                    continue
                titulo_url = data[1][0].replace(" ", "_")
                resp = requests.get(
                    f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{titulo_url}",
                    headers=HEADERS_HTTP,
                    timeout=10
                )
                resp.encoding = "utf-8"

            if resp.status_code == 200 and resp.text.strip():
                dados   = resp.json()
                extrato = dados.get("extract", "").strip()
                if extrato and len(extrato) > 100:
                    print(f"[AUTODIDATA]: Wikipedia ({lang}) -> '{dados.get('title', tema)}'")
                    return [{
                        "tema":   tema,
                        "titulo": dados.get("title", tema),
                        "corpo":  extrato,
                        "fonte":  f"wikipedia_{lang}",
                    }]

        except requests.exceptions.Timeout:
            print(f"[AUTODIDATA]: Wikipedia timeout para '{tema}'")
        except requests.exceptions.ConnectionError:
            print(f"[AUTODIDATA]: Wikipedia sem conexao para '{tema}'")
        except Exception as e:
            print(f"[AUTODIDATA]: Wikipedia erro '{tema}': {type(e).__name__}: {e}")

    return []


# ---------------------------------------------------------------------------
# FIX DuckDuckGo — migrado para pacote ddgs
# ---------------------------------------------------------------------------

def _buscar_web(tema: str) -> list[dict]:
    resultados_raw = []

    # Tenta o pacote novo (ddgs)
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            resultados_raw = list(ddgs.text(tema, max_results=3))
    except ImportError:
        pass
    except Exception as e:
        print(f"[AUTODIDATA]: ddgs erro para '{tema}': {e}")

    # Fallback pacote antigo
    if not resultados_raw:
        try:
            from duckduckgo_search import DDGS as DDGSAntigo
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with DDGSAntigo() as ddgs:
                    resultados_raw = list(ddgs.text(tema, max_results=3))
        except ImportError:
            print("[AUTODIDATA]: Instale: pip install ddgs")
        except Exception as e:
            print(f"[AUTODIDATA]: duckduckgo_search erro para '{tema}': {e}")

    return [
        {
            "tema":   tema,
            "titulo": r.get("title", tema),
            "corpo":  r.get("body", ""),
            "fonte":  r.get("href", "web"),
        }
        for r in resultados_raw
        if isinstance(r, dict) and r.get("body") and len(r["body"]) > 80
    ]


# ---------------------------------------------------------------------------
# Auto-dialogo
# ---------------------------------------------------------------------------

PERGUNTAS_AUTODIALOGO = [
    "o que e {tema}?",
    "como funciona {tema}?",
    "quais sao os conceitos principais de {tema}?",
    "por que {tema} e importante?",
    "explica {tema} de forma simples",
    "quais sao as aplicacoes de {tema}?",
    "qual a historia de {tema}?",
]

def _gerar_autodialogo(tema: str) -> list[dict]:
    try:
        from sirius_gerador import SiriusGerador
        gerador = SiriusGerador()
        if not gerador.esta_treinado():
            return []
        pergunta = random.choice(PERGUNTAS_AUTODIALOGO).format(tema=tema)
        resposta = gerador.gerar(pergunta)
        if resposta and len(resposta) > 20:
            return [{"tema": tema, "titulo": pergunta, "corpo": resposta, "fonte": "autodialogo"}]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Salvar no banco
# ---------------------------------------------------------------------------

def _salvar_conhecimento(itens: list[dict], memoria) -> tuple[int, list[str]]:
    salvos      = 0
    novos_temas = []
    for item in itens:
        corpo = item.get("corpo", "").strip()
        tema  = item.get("tema", "geral").strip()
        fonte = item.get("fonte", "web")
        if not corpo or len(corpo) < 30:
            continue
        try:
            from neuronio import SiriusNeuronio
            if SiriusNeuronio().verificar_se_ja_sabe(corpo, threshold=0.92):
                continue
        except Exception:
            pass
        ok = memoria.salvar_estudo_autonomo(
            tema=tema,
            conteudo=f"[{fonte}] {corpo[:1500]}",
            tags="autodidata"
        )
        if ok:
            salvos += 1
            novos_temas.extend(_extrair_novos_temas(corpo))
    return salvos, novos_temas


def _contar_novos_dados() -> int:
    try:
        conn = sqlite3.connect(DB_TREINO)
        n    = conn.execute(
            "SELECT COUNT(*) FROM conhecimento_geral WHERE tags = 'autodidata'"
        ).fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

class SiriusAutodidata:
    def __init__(self, memoria, cerebro=None):
        self.memoria       = memoria
        self.cerebro       = cerebro
        self._rodando      = False
        self._thread       = None
        self._fila         = FilaDeTemas()
        self._ciclo        = 0
        self._total_salvos = 0

        self._leitor = None
        try:
            from sirius_leitor import SiriusLeitor
            self._leitor = SiriusLeitor(memoria=self.memoria)
            self._leitor.iniciar()
        except Exception as e:
            print(f"[AUTODIDATA]: Leitor nao disponivel: {e}")

    def _ciclo_aprendizado(self):
        print(f"\033[94m[AUTODIDATA]: Motor iniciado com {len(TODOS_OS_TEMAS)} temas.\033[0m")
        while self._rodando:
            tema        = self._fila.proximo()
            total_ciclo = 0
            novos_temas = []

            print(f"\n\033[90m[AUTODIDATA]: Estudando -> '{tema}'\033[0m")

            itens = _buscar_wikipedia(tema)
            s, d  = _salvar_conhecimento(itens, self.memoria)
            total_ciclo += s
            novos_temas.extend(d)

            if self._ciclo % 2 == 0:
                itens = _buscar_web(tema)
                s, d  = _salvar_conhecimento(itens, self.memoria)
                total_ciclo += s
                novos_temas.extend(d)

            if self._ciclo % 3 == 0:
                itens = _gerar_autodialogo(tema)
                s, d  = _salvar_conhecimento(itens, self.memoria)
                total_ciclo += s
                novos_temas.extend(d)

            for novo in novos_temas:
                self._fila.adicionar_descoberto(novo)

            self._total_salvos += total_ciclo
            self._ciclo        += 1

            if total_ciclo > 0:
                print(
                    f"\033[92m[AUTODIDATA]: +{total_ciclo} sobre '{tema}' | "
                    f"total: {self._total_salvos} | "
                    f"novos temas: {self._fila.total_descobertos()}\033[0m"
                )
            else:
                print(f"\033[90m[AUTODIDATA]: Nada novo sobre '{tema}'.\033[0m")

            if self._ciclo % 24 == 0:
                novos = _contar_novos_dados()
                if novos >= 15:
                    print(f"\n[AUTODIDATA]: {novos} dados -> evoluindo redes...")
                    threading.Thread(target=self._retreinar, daemon=True).start()

            time.sleep(300)

    def _retreinar(self):
        try:
            from sirius_treinador import SiriusTreinador
            SiriusTreinador().treinar_tudo()
        except Exception as e:
            print(f"[AUTODIDATA]: Erro no retreino: {e}")

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._ciclo_aprendizado,
            daemon=True,
            name="SiriusAutodidata"
        )
        self._thread.start()
        print("\033[92m[AUTODIDATA]: Aprendizado autonomo ativado.\033[0m")

    def parar(self):
        self._rodando = False

    def status(self) -> dict:
        return {
            "rodando":            self._rodando,
            "ciclos_completados": self._ciclo,
            "total_salvos":       self._total_salvos,
            "fila_usuario":       self._fila.total_usuario(),
            "temas_descobertos":  self._fila.total_descobertos(),
            "temas_base":         len(TODOS_OS_TEMAS),
            "dados_no_banco":     _contar_novos_dados(),
        }

    def imprimir_status(self):
        s = self.status()
        print("\n[AUTODIDATA STATUS]")
        print(f"  Rodando:           {'Sim' if s['rodando'] else 'Nao'}")
        print(f"  Ciclos:            {s['ciclos_completados']}")
        print(f"  Conhecimentos:     {s['total_salvos']}")
        print(f"  Temas base:        {s['temas_base']}")
        print(f"  Fila usuario:      {s['fila_usuario']}")
        print(f"  Temas descobertos: {s['temas_descobertos']}")
        print(f"  No banco:          {s['dados_no_banco']}\n")


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from memoria import SiriusMemory
    mem = SiriusMemory()
    bot = SiriusAutodidata(memoria=mem)
    bot.iniciar()
    print(f"Autodidata rodando com {len(TODOS_OS_TEMAS)} temas. Ctrl+C para parar.")
    try:
        while True:
            time.sleep(60)
            bot.imprimir_status()
    except KeyboardInterrupt:
        bot.parar()
        print("Encerrado.")