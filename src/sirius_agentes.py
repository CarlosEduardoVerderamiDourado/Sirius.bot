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


# ---------------------------------------------------------------------------
# Agente base
# ---------------------------------------------------------------------------

class AgenteBase:
    """Interface comum a todos os agentes."""

    nome     = "Base"
    descricao = "Agente genérico"

    def __init__(self, memoria):
        self.memoria = memoria

    def executar(self, tarefa: str, contexto: dict = None) -> str:
        raise NotImplementedError

    def _salvar_resultado(self, tarefa: str, resultado: str, tag: str = "agente"):
        """Salva o resultado no banco para que o Sirius aprenda."""
        try:
            self.memoria.salvar_estudo_autonomo(
                tema=tarefa[:100],
                conteudo=resultado,
                tags=f"agente_{tag}"
            )
        except Exception as e:
            print(f"[AGENTE {self.nome}]: Erro ao salvar resultado: {e}")


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

        resumidor      = AgenteResumidor(self.memoria)
        texto_completo = '\n\n'.join(resultados)
        resumo         = resumidor.executar(texto_completo)

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
        pesquisador = AgentePesquisador(self.memoria)
        resultado   = pesquisador.executar(tema)

        if resultado and "Não encontrei" not in resultado:
            self.memoria.marcar_duvida_como_resolvida(tema)
            print(f"\033[92m[AGENTE Dúvidas]: '{tema[:40]}' resolvida e salva.\033[0m")

        return resultado

    def processar_fila(self):
        """Processa todas as dúvidas pendentes."""
        duvida = self.memoria.buscar_duvida_pendente()
        while duvida:
            print(f"\033[94m[AGENTE Dúvidas]: Processando → '{duvida[:50]}'\033[0m")
            self.executar(duvida)
            time.sleep(5)  # pausa entre dúvidas
            duvida = self.memoria.buscar_duvida_pendente()


# ---------------------------------------------------------------------------
# Coordenador de agentes
# ---------------------------------------------------------------------------

class SiriusAgentes:
    """
    Coordena todos os agentes e decide qual usar baseado na tarefa.
    """

    def __init__(self, memoria):
        self.memoria    = memoria
        self.resumidor  = AgenteResumidor(memoria)
        self.pesquisador = AgentePesquisador(memoria)
        self.analisador = AgenteAnalisador(memoria)
        self.escritor   = AgenteEscritor(memoria)
        self.duvidas    = AgenteDuvidas(memoria)

        self._agentes_disponiveis = {
            "resumidor":   self.resumidor,
            "pesquisador": self.pesquisador,
            "analisador":  self.analisador,
            "escritor":    self.escritor,
            "duvidas":     self.duvidas,
        }

    def executar(self, comando: str, contexto: dict = None) -> str | None:
        """
        Detecta automaticamente qual agente usar e executa.
        Retorna None se nenhum agente for adequado.
        """
        cmd = comando.lower()

        # Arquivo detectado → analisador
        if os.path.exists(comando):
            return self.analisador.executar(comando, contexto)

        # Padrões de intenção → agente certo
        if any(p in cmd for p in ["resume", "resumo", "sintetiza", "síntese"]):
            texto = contexto.get("texto", comando) if contexto else comando
            return self.resumidor.executar(texto)

        # Padrões de pesquisa — qualquer pergunta de conhecimento
        _TRIGGERS_PESQUISA = {
            "pesquisa", "pesquise", "procure", "busque",
            "o que é", "o que e", "oque e", "oque é",
            "que e ", "que é ",        # "que e pokemon"
            "quem é", "quem e",
            "como funciona", "como é", "como e ",
            "me fala", "me fale", "me explica", "me conta",
            "explica ", "explique",
            "conta sobre", "fala sobre",
            "historia de", "história de",
            "o que foi", "o que são", "o que sao",
            "pra que serve", "para que serve",
        }
        if any(p in cmd for p in _TRIGGERS_PESQUISA):
            # Remove o trigger e usa o restante como tema
            tema = cmd
            for p in sorted(_TRIGGERS_PESQUISA, key=len, reverse=True):
                if p in tema:
                    tema = tema.replace(p, " ").strip()
                    break
            tema = tema.strip() or comando
            return self.pesquisador.executar(tema)

        if any(p in cmd for p in ["analisa", "analise", "analisa", "lê", "leia", "abra"]):
            caminho = contexto.get("arquivo") if contexto else None
            if caminho:
                return self.analisador.executar(caminho)

        if any(p in cmd for p in ["escreve", "escreva", "cria texto", "redija",
                                    "gera texto", "crie um"]):
            return self.escritor.executar(comando, contexto)

        return None

    def pesquisar_e_aprender(self, tema: str):
        """Pesquisa um tema e salva no banco (chamado pelo scheduler)."""
        resultado = self.pesquisador.executar(tema)
        print(f"\033[92m[AGENTES]: Aprendido sobre '{tema[:40]}'.\033[0m")
        return resultado

    def resolver_duvidas_em_background(self):
        """Processa fila de dúvidas em thread separada."""
        threading.Thread(
            target=self.duvidas.processar_fila,
            daemon=True,
            name="AgenteDuvidas"
        ).start()

    def listar_agentes(self) -> list[dict]:
        return [
            {"nome": a.nome, "descricao": a.descricao}
            for a in self._agentes_disponiveis.values()
        ]