"""
sirius_moe.py — Hierarchical Mixture of Experts (MoE Hierárquico)

Arquitetura:
    Sirius (Router Principal)
    ├── CONVERSA     → Casual | Filosofia | Humor | Explicações
    ├── PROGRAMAÇÃO  → Python | JavaScript | Debug | Arquitetura
    ├── CONTROLE_PC  → Abrir | Automação | Sistema | Jogos
    ├── APRENDIZADO  → Memória | Treino | Otimização
    └── PERCEPÇÃO    → Voz | Visão | Texto | Tempo Real

Como funciona:
    1. Router L1 classifica o domínio (CONVERSA, PROGRAMAÇÃO, etc.)
    2. Router L2 classifica o sub-domínio (Python, Debug, etc.)
    3. Especialista certo é chamado com contexto focado

Vantagens sobre o classificador único:
    - Cada especialista tem seus próprios triggers e lógica
    - Não há colisão entre domínios (pergunta de programação não vira "controle")
    - Fácil de adicionar novos especialistas sem quebrar os existentes
    - Contexto passado ao especialista já é filtrado para o domínio

Integração com cerebro.py:
    from sirius_moe import SiriusMoE
    moe = SiriusMoE(memoria, agentes, control, visao, proativo)
    resultado = moe.processar(comando)
    if resultado:
        return resultado  # especialista respondeu
    # fallback para lógica existente
"""

import os
import sys
import re
import time

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)


# ---------------------------------------------------------------------------
# Utilitários de normalização
# ---------------------------------------------------------------------------

def _norm(texto: str) -> str:
    """Normaliza texto removendo acentos e lowercaseando."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Especialistas de nível 2 — cada um sabe fazer UMA coisa bem
# ---------------------------------------------------------------------------

class EspecialistaBase:
    nome        = "Base"
    descricao   = ""
    _TRIGGERS: set = set()

    def __init__(self, ctx: dict):
        self.ctx = ctx  # contexto compartilhado: memoria, agentes, control, etc.

    def aceita(self, texto: str) -> bool:
        t = _norm(texto)
        return any(trigger in t for trigger in self._TRIGGERS)

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        raise NotImplementedError


# ── DOMÍNIO: CONVERSA ───────────────────────────────────────────────────────

class EspecialistaCasual(EspecialistaBase):
    nome      = "Casual"
    descricao = "Bate-papo, humor e conversa informal"
    _TRIGGERS = {
        "piada", "me conta uma piada", "faz uma piada",
        "me diverte", "conta uma historia", "historia engraçada",
        "o que voce acha", "sua opiniao", "voce prefere",
        "me conta algo", "curiosidade", "sabia que",
        "fato curioso", "me surpreende",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        t = _norm(texto)
        if any(p in t for p in ["piada", "faz uma piada", "me conta uma piada"]):
            piadas = [
                "Por que o programador foi ao médico? Porque estava com muitos bugs!",
                "O que o zero disse para o oito? Bonito cinto, mano.",
                "Por que a IA não vai ao cinema? Porque já sabe o final de todos os filmes.",
                "Qual é o animal mais antigo? A zebra, porque está em preto e branco.",
            ]
            import random
            return random.choice(piadas)

        if any(p in t for p in ["fato curioso", "curiosidade", "sabia que", "me surpreende"]):
            fatos = [
                "O mel nunca estraga. Arqueólogos acharam mel de 3000 anos nas pirâmides e ainda era comestível.",
                "Os polvos têm três corações e sangue azul.",
                "O WiFi usa ondas de rádio na mesma frequência que o micro-ondas.",
                "Formigas nunca dormem — elas tiram sonecas de 1 minuto ao longo do dia.",
                "O cérebro humano usa aproximadamente 20% de toda a energia do corpo.",
            ]
            import random
            return f"Sabia que... {random.choice(fatos)}"

        return None


class EspecialistaFilosofia(EspecialistaBase):
    nome      = "Filosofia"
    descricao = "Questões existenciais, filosofia e reflexão"
    _TRIGGERS = {
        "sentido da vida", "o que e consciencia", "livre arbitrio",
        "existe deus", "realidade e uma simulacao", "o que e o tempo",
        "significado de tudo", "proposito da vida", "o que somos",
        "mente e cerebro", "o que e felicidade", "moralidade",
        "etica", "o que e belo", "arte e",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        # Delega ao pesquisador com contexto filosófico
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                resultado = agentes.pesquisador.executar(texto)
                if resultado and len(resultado) > 30:
                    return f"Boa pergunta, chefia. {resultado}"
        except Exception:
            pass
        return None


# ── DOMÍNIO: PROGRAMAÇÃO ────────────────────────────────────────────────────

class EspecialistaPython(EspecialistaBase):
    nome      = "Python"
    descricao = "Ajuda com código Python"
    _TRIGGERS = {
        "codigo python", "script python", "em python", "usando python",
        "django", "flask", "fastapi", "pandas", "numpy", "pytorch",
        "como fazer em python", "erro python", "exception python",
        "lista por compreensao", "decorator", "generator", "async python",
        "pip install", "venv", "virtualenv",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                query    = f"Python programação: {texto}"
                resultado = agentes.pesquisador.executar(query)
                if resultado and len(resultado) > 30:
                    return resultado
        except Exception:
            pass
        return None


class EspecialistaDebug(EspecialistaBase):
    nome      = "Debug"
    descricao = "Analisa erros e exceções de código"
    _TRIGGERS = {
        "erro no codigo", "exception", "traceback", "nao funciona",
        "bug", "como resolver esse erro", "o que significa esse erro",
        "typeerror", "valueerror", "indexerror", "keyerror",
        "nameerror", "attributeerror", "importerror", "syntaxerror",
        "modulenotfounderror", "runtimeerror", "segmentation fault",
    }

    # Banco de erros comuns → explicação direta
    _ERROS_COMUNS = {
        "typeerror":        "Você está usando um tipo errado. Ex: somando string com número sem converter.",
        "valueerror":       "Valor inválido para a operação. Ex: int('abc') vai dar ValueError.",
        "indexerror":       "Índice fora do range da lista. Ex: lista[10] em uma lista de 5 itens.",
        "keyerror":         "Chave não existe no dicionário. Use .get(chave) para evitar o erro.",
        "nameerror":        "Variável não foi definida antes de usar.",
        "attributeerror":   "Objeto não tem esse atributo/método. Verifique o tipo do objeto.",
        "importerror":      "Módulo não encontrado. Tente: pip install <nome_do_modulo>",
        "modulenotfounderror": "Módulo não instalado. Rode: pip install <nome>",
        "syntaxerror":      "Erro de sintaxe. Verifique parênteses, dois pontos e indentação.",
        "runtimeerror":     "Erro em tempo de execução. Geralmente recursão infinita ou thread.",
        "zerodivisionerror": "Divisão por zero. Verifique se o denominador pode ser 0.",
        "filenotfounderror": "Arquivo não encontrado. Verifique o caminho.",
        "permissionerror":  "Sem permissão para acessar o arquivo. Rode como administrador.",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        t = _norm(texto)

        # Identifica o tipo de erro no texto
        for nome_erro, explicacao in self._ERROS_COMUNS.items():
            if nome_erro in t:
                return (
                    f"Esse é um {nome_erro.title()}. {explicacao} "
                    f"Se quiser, me manda o traceback completo que eu analiso."
                )

        # Se tem "erro no codigo" mas não identificou o tipo
        if any(p in t for p in ["erro no codigo", "nao funciona", "bug", "o que significa esse erro"]):
            if contexto_sessao:
                return (
                    "Me manda o erro completo (traceback) que eu analiso. "
                    "Copia e cola a mensagem de erro aqui."
                )
        return None


class EspecialistaArquitetura(EspecialistaBase):
    nome      = "Arquitetura"
    descricao = "Padrões de projeto e arquitetura de software"
    _TRIGGERS = {
        "design pattern", "padrao de projeto", "solid", "clean code",
        "microservicos", "api rest", "graphql", "banco de dados",
        "como estruturar", "arquitetura de software", "mvc", "mvvm",
        "clean architecture", "ddd", "tdd", "ci cd", "docker", "kubernetes",
        "como organizar o projeto", "estrutura de pastas",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                resultado = agentes.pesquisador.executar(texto)
                if resultado and len(resultado) > 30:
                    return resultado
        except Exception:
            pass
        return None


# ── DOMÍNIO: CONTROLE PC ────────────────────────────────────────────────────

class EspecialistaJogos(EspecialistaBase):
    nome      = "Jogos"
    descricao = "Controle de jogos e configurações de gaming"
    _TRIGGERS = {
        "abre o jogo", "abre o steam", "fecha o jogo", "abre o minecraft",
        "abre o valorant", "abre o lol", "abre o fortnite",
        "modo jogo", "performance de jogo", "fps", "ping alto",
        "abrir epic games", "abrir battlenet", "abrir origin",
        "modo full screen", "modo janela", "resolucao do jogo",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        # Delega ao controle_pc com contexto de jogo
        control = self.ctx.get("control")
        if not control:
            return None
        try:
            from controle_pc import _parsear_controle_pc
            return _parsear_controle_pc(texto, control)
        except Exception:
            return None


class EspecialistaAutomacao(EspecialistaBase):
    nome      = "Automação"
    descricao = "Macros, atalhos e automação de tarefas"
    _TRIGGERS = {
        "cria uma macro", "grava macro", "executa macro",
        "automatiza", "faz isso automaticamente", "repete isso",
        "agenda tarefa", "quando abrir", "toda vez que",
        "atalho para", "cria atalho", "script de automacao",
        "abre todos", "fecha todos", "reinicia todos",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        memoria = self.ctx.get("memoria")
        if not memoria:
            return None

        t = _norm(texto)

        # Lista macros salvas
        if any(p in t for p in ["lista macros", "minhas macros", "quais macros"]):
            # SiriusMemory não tem list_macros, então retornamos instrução
            return "Para ver suas macros, me diz o nome e eu executo. Para criar: 'cria macro [nome] que faz [ação]'."

        # Executa macro pelo nome
        if any(p in t for p in ["executa macro", "roda macro"]):
            m = re.search(r"(?:executa|roda)\s+(?:a\s+)?macro\s+(.+)", t)
            if m:
                nome = m.group(1).strip()
                comandos = memoria.buscar_macro(nome)
                if comandos:
                    return f"Executando macro '{nome}': {comandos}"
                return f"Não encontrei a macro '{nome}'. Cria com: 'cria macro {nome} que faz [ação]'."

        return None


# ── DOMÍNIO: APRENDIZADO ────────────────────────────────────────────────────

class EspecialistaMemoria(EspecialistaBase):
    nome      = "Memória"
    descricao = "Acesso ao histórico e memória do Sirius"
    _TRIGGERS = {
        "o que eu disse", "o que falei antes", "lembra quando",
        "o que discutimos", "nossa ultima conversa", "me lembra o que",
        "o que aprendi", "o que voce aprendeu", "meu historico",
        "quantas conversas", "quanto tempo conversamos",
        "o que voce sabe sobre mim", "o que eu gosto",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        memoria = self.ctx.get("memoria")
        if not memoria:
            return None

        t = _norm(texto)

        if any(p in t for p in ["quantas conversas", "quanto conversamos"]):
            try:
                import sqlite3, os
                db = memoria.db_pessoal
                conn = sqlite3.connect(db)
                n = conn.execute("SELECT COUNT(*) FROM conversas WHERE role='user'").fetchone()[0]
                conn.close()
                return f"Você já me mandou {n} mensagens, chefia. Tô evoluindo a cada uma!"
            except Exception:
                pass

        if any(p in t for p in ["o que voce sabe sobre mim", "o que eu gosto"]):
            hist = memoria.obter_historico_db(limit=20)
            temas = set()
            for role, cont in hist:
                if role == "user" and len(cont) > 10:
                    palavras = cont.lower().split()
                    for p in palavras:
                        if len(p) > 5 and p not in {"sirius", "voce", "você", "como", "qual"}:
                            temas.add(p)
            if temas:
                amostra = list(temas)[:8]
                return f"Pelo histórico, você tem falado sobre: {', '.join(amostra)}."

        # Contexto de sessão — responde sobre o que foi falado agora
        if contexto_sessao and any(p in t for p in ["o que eu disse", "falei antes", "nossa conversa"]):
            return f"No nosso papo de hoje:\n{contexto_sessao[-400:]}"

        return None


class EspecialistaTreino(EspecialistaBase):
    nome      = "Treino"
    descricao = "Gerencia o treino das redes neurais"
    _TRIGGERS = {
        "treina", "treinamento", "retreina", "evolui o cerebro",
        "quanto dados tenho", "quantos dados", "status do treino",
        "quando treinar", "precisa treinar", "modelos treinados",
        "diagnostico", "saude das redes",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        t = _norm(texto)

        if any(p in t for p in ["quanto dados", "quantos dados", "status do treino", "saude das redes", "diagnostico"]):
            try:
                import sqlite3, os
                db_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
                db_t   = os.path.join(db_dir, "sirius_treino.db")
                db_p   = os.path.join(db_dir, "sirius_pessoal.db")

                n_conv = n_conh = 0
                try:
                    c = sqlite3.connect(db_p)
                    n_conv = c.execute("SELECT COUNT(*) FROM conversas").fetchone()[0]
                    c.close()
                except Exception: pass
                try:
                    c = sqlite3.connect(db_t)
                    n_conh = c.execute("SELECT COUNT(*) FROM conhecimento_geral").fetchone()[0]
                    c.close()
                except Exception: pass

                modelos = []
                for arq, nome in [("sirius_model.pth", "Classificador"),
                                   ("sirius_gerador.pth", "Gerador"),
                                   ("sirius_embeddings.pkl", "Embeddings")]:
                    caminho = os.path.join(db_dir, arq)
                    if os.path.exists(caminho):
                        tam = os.path.getsize(caminho) / 1024
                        modelos.append(f"{nome} ({tam:.0f}KB)")
                    else:
                        modelos.append(f"{nome} (não treinado)")

                return (
                    f"Status: {n_conv} conversas + {n_conh} conhecimentos. "
                    f"Modelos: {', '.join(modelos)}."
                )
            except Exception as e:
                return f"Erro ao verificar status: {e}"

        return None


# ── DOMÍNIO: PERCEPÇÃO ──────────────────────────────────────────────────────

class EspecialistaTempoReal(EspecialistaBase):
    nome      = "Tempo Real"
    descricao = "Hora, clima e informações em tempo real"
    _TRIGGERS = {
        "que horas", "que hora", "horas sao", "hora atual",
        "que dia", "qual dia", "que data", "data de hoje",
        "clima", "temperatura", "vai chover", "previsao", "chuva",
        "faz frio", "faz calor", "ta frio", "ta quente",
        "hora agora", "data agora",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        try:
            from sirius_tempo_real import processar_tempo_real
            return processar_tempo_real(texto)
        except Exception:
            return None


class EspecialistaVisao(EspecialistaBase):
    nome      = "Visão"
    descricao = "Analisa e lê o conteúdo da tela"
    _TRIGGERS = {
        "o que tem na tela", "o que esta na tela", "o que ta na tela",
        "leia a tela", "le a tela", "ler a tela",
        "analisa a tela", "analise a tela", "descreve a tela",
        "o que diz na tela", "o que esta escrito na tela",
        "o que voce ve", "ve a tela", "olha a tela",
        "qual e o erro na tela", "o que aparece na tela",
        "o que esta escrito", "leia o que ta escrito",
        "resume o que esta aberto",
    }

    def executar(self, texto: str, contexto_sessao: str = "") -> str | None:
        try:
            from sirius_visao import get_visao
            return get_visao().analisar_tela(texto)
        except Exception as e:
            return f"Visão indisponível: {e}. Instale: pip install pyautogui Pillow pytesseract"


# ---------------------------------------------------------------------------
# Domínios de nível 1 — agrupam especialistas L2
# ---------------------------------------------------------------------------

class DominioBase:
    nome        = "Base"
    _TRIGGERS_L1: set = set()
    _ESPECIALISTAS: list = []

    def __init__(self, ctx: dict):
        self.ctx          = ctx
        self.especialistas = [E(ctx) for E in self._ESPECIALISTAS]

    def aceita(self, texto: str) -> bool:
        t = _norm(texto)
        return any(tr in t for tr in self._TRIGGERS_L1)

    def processar(self, texto: str, contexto_sessao: str = "") -> str | None:
        """Tenta cada especialista L2 na ordem."""
        for esp in self.especialistas:
            if esp.aceita(texto):
                resultado = esp.executar(texto, contexto_sessao)
                if resultado:
                    print(f"\033[94m[MoE]: {self.nome}/{esp.nome} respondeu.\033[0m")
                    return resultado

        # Nenhum especialista específico aceitou — usa fallback do domínio
        return self._fallback(texto, contexto_sessao)

    def _fallback(self, texto: str, contexto_sessao: str = "") -> str | None:
        """Fallback do domínio — pode ser sobrescrito."""
        return None


class DominioConhecimento(DominioBase):
    nome         = "Conhecimento"
    _TRIGGERS_L1 = {
        # Perguntas de conhecimento geral que eram perdidas
        "o que e", "oque e", "o que sao", "quem foi", "quem e",
        "me fala sobre", "me fale sobre", "conta sobre", "fala sobre",
        "explica", "explique", "como funciona", "como surgiu",
        "historia de", "historia do", "historia da",
        "o que foi", "quando foi", "onde fica", "por que",
        "qual e a diferenca", "me ensina", "me ensine",
        "o que significa", "definicao de", "conceito de",
        # Perguntas com "5 perguntas", "me faz perguntas" etc
        "me faz", "me faca", "gera", "cria", "liste",
        "quais sao", "quais foram", "me da exemplos",
    }
    _ESPECIALISTAS = []

    def _fallback(self, texto, contexto_sessao=""):
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                resultado = agentes.pesquisador.executar(texto)
                if resultado and len(resultado) > 30:
                    return resultado
        except Exception:
            pass
        return None


class DominioConversa(DominioBase):
    nome         = "Conversa"
    _TRIGGERS_L1 = {
        "piada", "historia", "curiosidade", "fato curioso",
        "o que voce acha", "sua opiniao", "voce prefere",
        "sentido da vida", "consciencia", "livre arbitrio", "existe deus",
        "o que e felicidade", "moralidade", "etica", "filosofia",
        "o que voce e", "voce e humano", "voce tem sentimentos",
        "me conta algo", "me diverte", "me surpreende",
    }
    _ESPECIALISTAS = [EspecialistaCasual, EspecialistaFilosofia]

    def _fallback(self, texto, contexto_sessao=""):
        # Tenta pesquisador genérico para conversa
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                resultado = agentes.pesquisador.executar(texto)
                if resultado and len(resultado) > 30:
                    return resultado
        except Exception:
            pass
        return None


class DominioProgramacao(DominioBase):
    nome         = "Programação"
    _TRIGGERS_L1 = {
        "python", "javascript", "codigo", "script", "funcao", "classe",
        "bug", "erro no codigo", "traceback", "exception", "debug",
        "programar", "programacao", "desenvolvimento", "dev",
        "typeerror", "valueerror", "indexerror", "keyerror",
        "nameerror", "attributeerror", "importerror", "syntaxerror",
        "modulenotfounderror", "pip", "npm", "yarn",
        "design pattern", "solid", "clean code", "arquitetura",
        "api rest", "banco de dados", "sql", "nosql", "docker",
        "git", "github", "commit", "branch", "merge",
        # Perguntas sobre programação (eram classificadas como acao antes)
        "perguntas sobre programacao", "perguntas de programacao",
        "me faz perguntas", "me faca perguntas", "quero perguntas",
        "exercicios de programacao", "exercicios sobre",
        "quiz de", "teste de", "praticar programacao",
        "como programar", "aprender programacao", "ensina programacao",
        "diferenca entre", "o que e uma funcao", "o que e uma classe",
        "o que e um objeto", "o que e heranca", "o que e polimorfismo",
        "o que e recursao", "como funciona o", "explica o conceito",
    }
    _ESPECIALISTAS = [EspecialistaDebug, EspecialistaPython, EspecialistaArquitetura]

    def _fallback(self, texto, contexto_sessao=""):
        try:
            agentes = self.ctx.get("agentes")
            if agentes:
                resultado = agentes.pesquisador.executar(f"programação {texto}")
                if resultado and len(resultado) > 30:
                    return resultado
        except Exception:
            pass
        return None


class DominioControlePc(DominioBase):
    nome         = "Controle PC"
    _TRIGGERS_L1 = {
        "abre", "abrir", "fecha", "fechar", "abre o jogo", "steam",
        "macro", "automatiza", "atalho", "script de automacao",
        "copia", "cola", "digita", "pressiona", "clica",
        "screenshot", "print", "tira print",
    }
    _ESPECIALISTAS = [EspecialistaJogos, EspecialistaAutomacao]

    def _fallback(self, texto, contexto_sessao=""):
        # Delega ao controle_pc padrão
        try:
            from controle_pc import _parsear_controle_pc
            control = self.ctx.get("control")
            if control:
                return _parsear_controle_pc(texto, control)
        except Exception:
            pass
        return None


class DominioAprendizado(DominioBase):
    nome         = "Aprendizado"
    _TRIGGERS_L1 = {
        "o que eu disse", "lembra quando", "nossa conversa", "historico",
        "quantas conversas", "o que voce sabe sobre mim",
        "treina", "retreina", "status do treino", "diagnostico",
        "quantos dados", "modelos treinados", "saude das redes",
        "o que aprendi", "o que voce aprendeu",
    }
    _ESPECIALISTAS = [EspecialistaMemoria, EspecialistaTreino]


class DominioPercepcao(DominioBase):
    nome         = "Percepção"
    _TRIGGERS_L1 = {
        "que horas", "que hora", "que dia", "que data",
        "clima", "temperatura", "vai chover", "previsao",
        "o que tem na tela", "o que esta na tela", "leia a tela",
        "analisa a tela", "o que voce ve", "ve a tela",
        "o que esta escrito", "qual erro na tela",
    }
    _ESPECIALISTAS = [EspecialistaTempoReal, EspecialistaVisao]


# ---------------------------------------------------------------------------
# Router principal — Hierarchical Mixture of Experts
# ---------------------------------------------------------------------------

class SiriusMoE:
    """
    Router hierárquico do Sirius.

    Uso no cerebro.py:
        moe = SiriusMoE(
            memoria=self.memoria,
            agentes=self._agentes,
            control=self.control,
        )
        resposta = moe.processar(comando, contexto_sessao)
        if resposta:
            return resposta
        # fallback para lógica existente do cerebro
    """

    _DOMINIOS_CLASSES = [
        DominioPercepcao,    # 1. Tempo real e visão — mais específico
        DominioProgramacao,  # 2. Programação — antes de conversa
        DominioAprendizado,  # 3. Memória e treino
        DominioControlePc,   # 4. Controle do PC
        DominioConhecimento, # 5. Conhecimento geral (o que é, me faz perguntas...)
        DominioConversa,     # 6. Conversa casual — mais genérico
    ]

    def __init__(self, memoria=None, agentes=None, control=None,
                 visao=None, proativo=None):
        self._ctx = {
            "memoria":  memoria,
            "agentes":  agentes,
            "control":  control,
            "visao":    visao,
            "proativo": proativo,
        }
        self._dominios = [D(self._ctx) for D in self._DOMINIOS_CLASSES]
        self._stats: dict[str, int] = {}  # contagem de uso por domínio
        print("\033[92m[MoE]: Sistema de especialistas hierárquico ativo.\033[0m")

    def processar(self, texto: str, contexto_sessao: str = "") -> str | None:
        """
        Rota o texto para o domínio e especialista certo.
        Retorna None se nenhum especialista souber responder.
        """
        t_norm = _norm(texto)

        # L1: encontra o domínio certo
        for dominio in self._dominios:
            if dominio.aceita(t_norm):
                resultado = dominio.processar(texto, contexto_sessao)
                if resultado:
                    # Atualiza estatísticas
                    self._stats[dominio.nome] = self._stats.get(dominio.nome, 0) + 1
                    return resultado

        return None  # nenhum especialista aceitou → cerebro.py usa fallback

    def status(self) -> dict:
        return {
            "dominios":    [d.nome for d in self._dominios],
            "especialistas": sum(len(d.especialistas) for d in self._dominios),
            "uso":         self._stats,
        }

    def imprimir_status(self):
        s = self.status()
        print("\n╔════════════════════════════════════════╗")
        print("║   Sirius MoE — Especialistas Ativos    ║")
        print("╠════════════════════════════════════════╣")
        for dominio in self._dominios:
            esps = ", ".join(e.nome for e in dominio.especialistas)
            uso  = self._stats.get(dominio.nome, 0)
            print(f"║  {dominio.nome:15s} [{esps[:28]}] ({uso}x)")
        print("╚════════════════════════════════════════╝\n")

    def adicionar_especialista(self, dominio_nome: str, especialista_class):
        """Adiciona um novo especialista a um domínio existente em runtime."""
        for dominio in self._dominios:
            if dominio.nome.lower() == dominio_nome.lower():
                dominio.especialistas.append(especialista_class(self._ctx))
                print(f"[MoE]: {especialista_class.nome} adicionado ao domínio {dominio_nome}.")
                return True
        print(f"[MoE]: Domínio '{dominio_nome}' não encontrado.")
        return False