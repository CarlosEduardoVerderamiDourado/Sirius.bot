"""
sirius_agentes.py — Agentes especializados do Sirius

Cada agente é especializado em uma tarefa:
- AgenteResumidor   → resume textos longos
- AgentePesquisador → pesquisa e sintetiza info da web
- AgenteAnalisador  → analisa arquivos e dados
- AgenteEscritor    → gera textos e documentos
- AgenteDuvidas     → resolve dúvidas pendentes em background

O SiriusAgentes coordena todos eles e decide qual usar.
"""

import os
import sys
import re
import time
import threading

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

# Session HTTP reutilizada entre chamadas — evita handshake TCP a cada request
_http_session = None
_session_lock = threading.Lock()

def _get_session():
    global _http_session
    if _http_session is None:
        with _session_lock:
            if _http_session is None:
                try:
                    import requests
                    _http_session = requests.Session()
                    _http_session.headers.update({"User-Agent": "Sirius/1.0"})
                except ImportError:
                    pass
    return _http_session


# ---------------------------------------------------------------------------
# Agente base
# ---------------------------------------------------------------------------

class AgenteBase:
    nome      = "Base"
    descricao = "Agente genérico"

    def __init__(self, memoria):
        self.memoria         = memoria
        self._resumidor_obj  = None   # lazy — criado na primeira vez que precisar
        self._pesquisador_obj = None   # lazy — criado na primeira vez que precisar

    @property
    def _resumidor(self) -> "AgenteResumidor":
        """
        Lazy property — instancia AgenteResumidor na primeira chamada.
        Evita criar uma nova instância a cada chamada de executar().
        Antes: resumidor = AgenteResumidor(self.memoria) dentro do método.
        Depois: self._resumidor.executar(texto)  — reutiliza a mesma instância.
        """
        if self._resumidor_obj is None:
            self._resumidor_obj = AgenteResumidor(self.memoria)
        return self._resumidor_obj

    @property
    def _pesquisador(self) -> "AgentePesquisador":
        """Lazy property — instancia AgentePesquisador na primeira chamada."""
        if self._pesquisador_obj is None:
            self._pesquisador_obj = AgentePesquisador(self.memoria)
        return self._pesquisador_obj

    def executar(self, tarefa: str, contexto: dict = None) -> str:
        raise NotImplementedError

    def _salvar_resultado(self, tarefa: str, resultado: str, tag: str = "agente"):
        try:
            self.memoria.salvar_estudo_autonomo(
                tema=tarefa[:100], conteudo=resultado, tags=f"agente_{tag}"
            )
        except Exception as e:
            print(f"[AGENTE {self.nome}]: Erro ao salvar: {e}")


# ---------------------------------------------------------------------------
# Agente Resumidor
# ---------------------------------------------------------------------------

class AgenteResumidor(AgenteBase):
    nome      = "Resumidor"
    descricao = "Resume textos longos em pontos principais"

    def executar(self, texto: str, contexto: dict = None) -> str:
        if len(texto) < 200:
            return texto
        sentencas = [s.strip() for s in re.split(r"[.!?]\s+", texto) if len(s.strip()) > 30]
        if len(sentencas) <= 5:
            return texto
        n       = len(sentencas)
        indices = sorted(set(min(i, n-1) for i in [0, n//4, n//2, 3*n//4, n-1]))
        resumo  = ". ".join(sentencas[i] for i in indices) + "."
        if len(resumo) > len(texto) * 0.4:
            resumo = " ".join(texto.split()[:80]) + "..."
        return resumo.strip()


# ---------------------------------------------------------------------------
# Agente Pesquisador
# ---------------------------------------------------------------------------

class AgentePesquisador(AgenteBase):
    nome      = "Pesquisador"
    descricao = "Pesquisa na web e sintetiza informações"

    def executar(self, tema: str, contexto: dict = None) -> str:
        resultados_pt  = []
        resultados_gen = []
        session        = _get_session()

        # 1. Wikipedia PT — timeout reduzido de 6→3s
        try:
            url  = "https://pt.wikipedia.org/api/rest_v1/page/summary/" + tema.replace(" ", "_")
            resp = session.get(url, timeout=3) if session else None
            if resp and resp.status_code == 200:
                extrato = resp.json().get("extract", "")
                if extrato and len(extrato) > 80:
                    resultados_pt.append(extrato[:500])
        except Exception as e:
            print(f"[AGENTE Pesquisador]: Wikipedia PT: {e}")

        # 2. Wikipedia EN — só se PT não achou, timeout 3s
        if not resultados_pt:
            try:
                url  = "https://en.wikipedia.org/api/rest_v1/page/summary/" + tema.replace(" ", "_")
                resp = session.get(url, timeout=3) if session else None
                if resp and resp.status_code == 200:
                    extrato = resp.json().get("extract", "")
                    if extrato and len(extrato) > 80:
                        resultados_gen.append(extrato.split(". ")[0] + ".")
            except Exception:
                pass

        # 3. DuckDuckGo PT — só se Wikipedia falhou
        if not resultados_pt:
            try:
                from ddgs import DDGS
                query = tema + " site:pt.wikipedia.org OR site:brasilescola.uol.com.br"
                with DDGS() as ddgs:
                    busca = list(ddgs.text(query, max_results=2))
                for r in busca:
                    if isinstance(r, dict) and r.get("body") and len(r["body"]) > 50:
                        resultados_pt.append(r["body"][:400])
            except Exception as e:
                print(f"[AGENTE Pesquisador]: DDG: {e}")

        resultados = resultados_pt if resultados_pt else resultados_gen
        if not resultados:
            return f"Não encontrei informações sobre '{tema}' agora."

        # self._resumidor: lazy property de AgenteBase — reutiliza sem recriar
        texto_completo = "\n\n".join(resultados)
        resumo         = self._resumidor.executar(texto_completo)
        self._salvar_resultado(tema, texto_completo, "pesquisador")
        return resumo


class AgenteAnalisador(AgenteBase):
    nome      = "Analisador"
    descricao = "Analisa arquivos e dados, extrai insights"

    def executar(self, caminho_ou_texto: str, contexto: dict = None) -> str:
        if os.path.exists(caminho_ou_texto):
            return self._analisar_arquivo(caminho_ou_texto)
        return self._analisar_texto(caminho_ou_texto)

    def _analisar_arquivo(self, caminho: str) -> str:
        from sirius_arquivos import SiriusArquivos
        resultado = SiriusArquivos().ler_e_salvar_no_banco(caminho, self.memoria)
        if not resultado.sucesso:
            return f"Não consegui ler o arquivo: {resultado.erro}"
        return f"{resultado.resumo}\n\nAnálise:\n{self._analisar_texto(resultado.texto)}"

    def _analisar_texto(self, texto: str) -> str:
        if not texto or len(texto) < 10:
            return "Texto vazio ou muito curto para analisar."
        palavras   = texto.lower().split()
        stopwords  = frozenset({"de","a","o","e","que","do","da","em","um","uma",
                                "para","com","se","por","as","os","ao","na","no",
                                "the","an","is","of","to","in","and","or"})
        freq = {}
        for p in palavras:
            p = re.sub(r"[^\w]", "", p)
            if len(p) > 3 and p not in stopwords:
                freq[p] = freq.get(p, 0) + 1
        top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]
        temas = ", ".join(p for p, _ in top)
        return (
            f"Estatísticas: {len(palavras)} palavras, "
            f"{len(re.findall(r'[.!?]', texto))} sentenças.\n"
            f"Temas principais: {temas}.\n"
            f"Resumo: {texto[:200].strip()}..."
        )


# ---------------------------------------------------------------------------
# Agente Escritor
# ---------------------------------------------------------------------------

class AgenteEscritor(AgenteBase):
    nome      = "Escritor"
    descricao = "Gera textos, emails, documentos e conteúdo"

    _TEMPLATE_EMAIL = (
        "Assunto: {assunto}\n\n"
        "Olá {destinatario},\n\n{corpo}\n\n"
        "Atenciosamente,\n{remetente}"
    )

    def executar(self, descricao: str, contexto: dict = None) -> str:
        ctx  = contexto or {}
        if ctx.get("tipo") == "email":
            return self._TEMPLATE_EMAIL.format(
                assunto=ctx.get("assunto", descricao),
                destinatario=ctx.get("destinatario", ""),
                corpo=ctx.get("corpo", descricao),
                remetente=ctx.get("remetente", "Sirius"),
            )
        try:
            from sirius_gerador import SiriusGerador
            g = SiriusGerador()
            if g.esta_treinado():
                r = g.gerar(descricao)
                if r and len(r) > 20:
                    self._salvar_resultado(descricao, r, "escritor")
                    return r
        except Exception:
            pass
        try:
            from sirius_embeddings import SiriusEmbeddings
            emb = SiriusEmbeddings()
            if emb.esta_treinado():
                historico = self.memoria.obter_historico_db(limit=30)
                respostas = [c for role, c in historico if role == "assistant" and len(c) > 20]
                if respostas:
                    similar = emb.buscar_mais_similar(descricao, respostas)
                    if similar:
                        return similar
        except Exception:
            pass
        return f"Gerador ainda aprendendo. Tema: {descricao}."


# ---------------------------------------------------------------------------
# Agente de Dúvidas
# ---------------------------------------------------------------------------

class AgenteDuvidas(AgenteBase):
    nome      = "Duvidas"
    descricao = "Resolve dúvidas pendentes pesquisando na web"

    def executar(self, tema: str, contexto: dict = None) -> str:
        resultado = AgentePesquisador(self.memoria).executar(tema)
        if resultado and "Não encontrei" not in resultado:
            self.memoria.marcar_duvida_como_resolvida(tema)
            print(f"\033[92m[AGENTE Dúvidas]: '{tema[:40]}' resolvida.\033[0m")
        return resultado

    def processar_fila(self):
        duvida = self.memoria.buscar_duvida_pendente()
        while duvida:
            print(f"\033[94m[AGENTE Dúvidas]: → '{duvida[:50]}'\033[0m")
            self.executar(duvida)
            time.sleep(2)   # era 5s — reduzido para 2s
            duvida = self.memoria.buscar_duvida_pendente()


# ---------------------------------------------------------------------------
# Coordenador de agentes
# ---------------------------------------------------------------------------

# Triggers como frozenset — verificação O(1) em vez de lista
_TRIGGERS_PESQUISA = frozenset({
    "pesquisa", "pesquise", "procure", "busque",
    "o que é", "o que e", "oque e", "oque é",
    "que e ", "que é ", "quem é", "quem e",
    "como funciona", "como é", "como e ",
    "me fala", "me fale", "me explica", "me conta",
    "explica ", "explique", "conta sobre", "fala sobre",
    "historia de", "história de", "o que foi",
    "o que são", "o que sao", "pra que serve", "para que serve",
})

_TRIGGERS_RESUMO = frozenset({"resume", "resumo", "sintetiza", "síntese"})
_TRIGGERS_ESCRITA = frozenset({"escreve", "escreva", "cria texto", "redija",
                                "gera texto", "crie um"})


class SiriusAgentes:
    def __init__(self, memoria):
        self.memoria     = memoria
        self.resumidor   = AgenteResumidor(memoria)
        self.pesquisador = AgentePesquisador(memoria)
        self.analisador  = AgenteAnalisador(memoria)
        self.escritor    = AgenteEscritor(memoria)
        self.duvidas     = AgenteDuvidas(memoria)
        self._agentes_disponiveis = {
            "resumidor": self.resumidor, "pesquisador": self.pesquisador,
            "analisador": self.analisador, "escritor": self.escritor,
            "duvidas": self.duvidas,
        }

    def executar(self, comando: str, contexto: dict = None) -> str | None:
        cmd = comando.lower()

        if os.path.exists(comando):
            return self.analisador.executar(comando, contexto)

        if any(p in cmd for p in _TRIGGERS_RESUMO):
            texto = contexto.get("texto", comando) if contexto else comando
            return self.resumidor.executar(texto)

        if any(p in cmd for p in _TRIGGERS_PESQUISA):
            tema = cmd
            for p in sorted(_TRIGGERS_PESQUISA, key=len, reverse=True):
                if p in tema:
                    tema = tema.replace(p, " ").strip()
                    break
            return self.pesquisador.executar(tema.strip() or comando)

        if any(p in cmd for p in {"analisa", "analise", "lê", "leia"}):
            caminho = contexto.get("arquivo") if contexto else None
            if caminho:
                return self.analisador.executar(caminho)

        if any(p in cmd for p in _TRIGGERS_ESCRITA):
            return self.escritor.executar(comando, contexto)

        return None

    def pesquisar_e_aprender(self, tema: str):
        resultado = self.pesquisador.executar(tema)
        print(f"\033[92m[AGENTES]: Aprendido sobre '{tema[:40]}'.\033[0m")
        return resultado

    def resolver_duvidas_em_background(self):
        threading.Thread(
            target=self.duvidas.processar_fila,
            daemon=True, name="AgenteDuvidas"
        ).start()

    def listar_agentes(self) -> list[dict]:
        return [{"nome": a.nome, "descricao": a.descricao}
                for a in self._agentes_disponiveis.values()]


# ---------------------------------------------------------------------------
# Agente Resumidor
# ---------------------------------------------------------------------------

class AgenteResumidor(AgenteBase):
    nome      = "Resumidor"
    descricao = "Resume textos longos em pontos principais"

    def executar(self, texto: str, contexto: dict = None) -> str:
        if len(texto) < 200:
            return texto

        # Divide em sentenças e pega as mais informativas
        sentencas = re.split(r"[.!?]\s+", texto)
        sentencas = [s.strip() for s in sentencas if len(s.strip()) > 30]

        if len(sentencas) <= 5:
            return texto

        # Heurística: primeira, última e distribuídas no meio
        n       = len(sentencas)
        indices = [0, n//4, n//2, 3*n//4, n-1]
        indices = sorted(set(min(i, n-1) for i in indices))

        resumo = ". ".join(sentencas[i] for i in indices) + "."

        # Garante que o resumo não seja maior que 40% do original
        if len(resumo) > len(texto) * 0.4:
            resumo = " ".join(texto.split()[:80]) + "..."

        return resumo.strip()


# ---------------------------------------------------------------------------
# Agente Pesquisador
# ---------------------------------------------------------------------------

class AgentePesquisador(AgenteBase):
    nome      = "Pesquisador"
    descricao = "Pesquisa na web e sintetiza informações"

    def executar(self, tema: str, contexto: dict = None) -> str:
        resultados_pt  = []   # resultados em portugues (prioridade)
        resultados_gen = []   # fallback

        # 1. Wikipedia PT — sempre em portugues
        try:
            import requests
            url  = 'https://pt.wikipedia.org/api/rest_v1/page/summary/' + tema.replace(' ', '_')
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200 and 'json' in resp.headers.get('Content-Type', ''):
                dados   = resp.json()
                extrato = dados.get('extract', '')
                if extrato and len(extrato) > 80:
                    resultados_pt.append(extrato[:500])
        except Exception as e:
            print('[AGENTE Pesquisador]: Wikipedia PT: {}'.format(e))

        # 2. Wikipedia EN so se PT nao achou — pega apenas o primeiro paragrafo
        if not resultados_pt:
            try:
                import requests
                url  = 'https://en.wikipedia.org/api/rest_v1/page/summary/' + tema.replace(' ', '_')
                resp = requests.get(url, timeout=6)
                if resp.status_code == 200 and 'json' in resp.headers.get('Content-Type', ''):
                    extrato = resp.json().get('extract', '')
                    if extrato and len(extrato) > 80:
                        primeiro = extrato.split('. ')[0] + '.'
                        resultados_gen.append(primeiro[:300])
            except Exception:
                pass

        # 3. DuckDuckGo PT (busca em sites br)
        if not resultados_pt:
            try:
                from ddgs import DDGS
                query = tema + ' site:pt.wikipedia.org OR site:brasilescola.uol.com.br OR site:mundoeducacao.uol.com.br'
                with DDGS() as ddgs:
                    busca = list(ddgs.text(query, max_results=2))
                for r in busca:
                    if isinstance(r, dict) and r.get('body') and len(r['body']) > 50:
                        resultados_pt.append(r['body'][:400])
            except Exception as e:
                print('[AGENTE Pesquisador]: DDG PT: {}'.format(e))

        resultados = resultados_pt if resultados_pt else resultados_gen

        if not resultados:
            return "Nao encontrei informacoes sobre '{}' agora.".format(tema)

        texto_completo = '\n\n'.join(resultados)
        resumo         = self._resumidor.executar(texto_completo)

        self._salvar_resultado(tema, texto_completo, 'pesquisador')
        return resumo
class AgenteAnalisador(AgenteBase):
    nome      = "Analisador"
    descricao = "Analisa arquivos e dados, extrai insights"

    def executar(self, caminho_ou_texto: str, contexto: dict = None) -> str:
        # Se for um caminho de arquivo
        if os.path.exists(caminho_ou_texto):
            return self._analisar_arquivo(caminho_ou_texto)

        # Se for texto puro
        return self._analisar_texto(caminho_ou_texto)

    def _analisar_arquivo(self, caminho: str) -> str:
        from sirius_arquivos import SiriusArquivos
        arq      = SiriusArquivos()
        resultado = arq.ler_e_salvar_no_banco(caminho, self.memoria)

        if not resultado.sucesso:
            return f"Não consegui ler o arquivo: {resultado.erro}"

        analise = self._analisar_texto(resultado.texto)
        return f"{resultado.resumo}\n\nAnálise:\n{analise}"

    def _analisar_texto(self, texto: str) -> str:
        if not texto or len(texto) < 10:
            return "Texto vazio ou muito curto para analisar."

        palavras    = texto.lower().split()
        n_palavras  = len(palavras)
        n_sentencas = len(re.findall(r"[.!?]", texto))
        n_chars     = len(texto)

        # Palavras mais frequentes (sem stopwords simples)
        stopwords = {"de", "a", "o", "e", "que", "do", "da", "em", "um", "uma",
                     "para", "com", "se", "por", "as", "os", "ao", "na", "no",
                     "the", "a", "an", "is", "of", "to", "in", "and", "or"}
        freq = {}
        for p in palavras:
            p = re.sub(r"[^\w]", "", p)
            if len(p) > 3 and p not in stopwords:
                freq[p] = freq.get(p, 0) + 1

        top_palavras = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]
        temas_pratos = ", ".join(p for p, _ in top_palavras)

        return (
            f"Estatísticas: {n_palavras} palavras, {n_sentencas} sentenças, {n_chars} caracteres.\n"
            f"Temas principais: {temas_pratos}.\n"
            f"Resumo: {texto[:200].strip()}..."
        )


# ---------------------------------------------------------------------------
# Agente Escritor
# ---------------------------------------------------------------------------

class AgenteEscritor(AgenteBase):
    nome      = "Escritor"
    descricao = "Gera textos, emails, documentos e conteúdo"

    TEMPLATES = {
        "email": (
            "Assunto: {assunto}\n\n"
            "Olá {destinatario},\n\n"
            "{corpo}\n\n"
            "Atenciosamente,\n{remetente}"
        ),
        "lista": "• {item}",
        "codigo": "```{linguagem}\n{codigo}\n```",
    }

    def executar(self, descricao: str, contexto: dict = None) -> str:
        ctx  = contexto or {}
        tipo = ctx.get("tipo", "texto")

        if tipo == "email":
            return self.TEMPLATES["email"].format(
                assunto      = ctx.get("assunto", descricao),
                destinatario = ctx.get("destinatario", ""),
                corpo        = ctx.get("corpo", descricao),
                remetente    = ctx.get("remetente", "Sirius"),
            )

        # Gerador seq2seq próprio
        try:
            from sirius_gerador import SiriusGerador
            gerador = SiriusGerador()
            if gerador.esta_treinado():
                resultado = gerador.gerar(descricao)
                if resultado and len(resultado) > 20:
                    self._salvar_resultado(descricao, resultado, "escritor")
                    return resultado
        except Exception:
            pass

        # Fallback: busca semântica no histórico
        try:
            from sirius_embeddings import SiriusEmbeddings
            emb = SiriusEmbeddings()
            if emb.esta_treinado():
                historico = self.memoria.obter_historico_db(limit=30)
                respostas = [c for role, c in historico if role == "assistant" and len(c) > 20]
                if respostas:
                    similar = emb.buscar_mais_similar(descricao, respostas)
                    if similar:
                        return similar
        except Exception:
            pass

        return f"Gerador ainda aprendendo. Tema: {descricao}."


# ---------------------------------------------------------------------------
# Agente de Dúvidas (roda em background)
# ---------------------------------------------------------------------------

class AgenteDuvidas(AgenteBase):
    nome      = "Duvidas"
    descricao = "Resolve dúvidas pendentes pesquisando na web"

    def executar(self, tema: str, contexto: dict = None) -> str:
        # self._pesquisador: lazy property de AgenteBase — não recria a cada chamada
        resultado = self._pesquisador.executar(tema)

        if resultado and "Não encontrei" not in resultado:
            self.memoria.marcar_duvida_como_resolvida(tema)
            print(f"\033[92m[AGENTE Dúvidas]: '{tema[:40]}' resolvida e salva.\033[0m")