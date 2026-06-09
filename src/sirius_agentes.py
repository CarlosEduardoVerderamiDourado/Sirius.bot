"""
sirius_agentes.py — Orquestrador com roteamento por IA e Fábrica de Agentes

Arquitetura:
  SiriusAgentes.executar(comando)
      │
      ├─ neuronio.predizer(comando)  →  tema conhecido  →  agente especializado
      │                              →  "Novo_Tema"     →  AgenteEspecialista (dinâmico)
      │
      └─ Ciclo de Aprendizado Ativo: a cada 10 temas novos → treinar() em thread
"""

import os
import sys
import re
import time
import threading

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

# Session HTTP reutilizada — evita handshake TCP a cada request
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
# Agente Base
# ---------------------------------------------------------------------------

class AgenteBase:
    nome      = "Base"
    descricao = "Agente genérico"

    def __init__(self, memoria):
        self.memoria          = memoria
        self._resumidor_obj   = None  # lazy
        self._pesquisador_obj = None  # lazy

    @property
    def _resumidor(self) -> "AgenteResumidor":
        """Lazy property — instancia AgenteResumidor na primeira chamada."""
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
        sentencas = [s.strip() for s in re.split(r"[.!?]\s+", texto)
                     if len(s.strip()) > 30]
        if len(sentencas) <= 5:
            return texto
        n = len(sentencas)
        indices = sorted(set(min(i, n - 1) for i in [0, n//4, n//2, 3*n//4, n-1]))
        resumo = ". ".join(sentencas[i] for i in indices) + "."
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
        session = _get_session()

        # 1. Wikipedia PT
        try:
            url  = "https://pt.wikipedia.org/api/rest_v1/page/summary/" + tema.replace(" ", "_")
            resp = session.get(url, timeout=3) if session else None
            if resp and resp.status_code == 200:
                extrato = resp.json().get("extract", "")
                if extrato and len(extrato) > 80:
                    resultados_pt.append(extrato[:500])
        except Exception as e:
            print(f"[AGENTE Pesquisador]: Wikipedia PT: {e}")

        # 2. Wikipedia EN — fallback
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

        # 3. DuckDuckGo PT — último recurso
        if not resultados_pt:
            try:
                from ddgs import DDGS
                query = (tema +
                         " site:pt.wikipedia.org OR site:brasilescola.uol.com.br")
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

        texto_completo = "\n\n".join(resultados)
        resumo = self._resumidor.executar(texto_completo)
        self._salvar_resultado(tema, texto_completo, "pesquisador")
        return resumo


# ---------------------------------------------------------------------------
# Agente Analisador
# ---------------------------------------------------------------------------

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
        palavras  = texto.lower().split()
        stopwords = frozenset({
            "de","a","o","e","que","do","da","em","um","uma",
            "para","com","se","por","as","os","ao","na","no",
            "the","an","is","of","to","in","and","or"
        })
        freq = {}
        for p in palavras:
            p = re.sub(r"[^\w]", "", p)
            if len(p) > 3 and p not in stopwords:
                freq[p] = freq.get(p, 0) + 1
        top   = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]
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
        ctx = contexto or {}
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
                respostas = [c for role, c in historico
                             if role == "assistant" and len(c) > 20]
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
        resultado = self._pesquisador.executar(tema)
        if resultado and "Não encontrei" not in resultado:
            self.memoria.marcar_duvida_como_resolvida(tema)
            print(f"\033[92m[AGENTE Dúvidas]: '{tema[:40]}' resolvida.\033[0m")
        return resultado

    def processar_fila(self):
        duvida = self.memoria.buscar_duvida_pendente()
        while duvida:
            print(f"\033[94m[AGENTE Dúvidas]: → '{duvida[:50]}'\033[0m")
            self.executar(duvida)
            time.sleep(2)
            duvida = self.memoria.buscar_duvida_pendente()


# ---------------------------------------------------------------------------
# Agente Especialista — instanciado dinamicamente para "Novo_Tema"
# ---------------------------------------------------------------------------

class AgenteEspecialista(AgenteBase):
    """
    Agente criado em tempo de execução para lidar com temas desconhecidos.

    Fluxo:
      1. Pesquisa o termo via AgentePesquisador (Wikipedia / DuckDuckGo).
      2. Registra o conhecimento em memoria.salvar_estudo_autonomo com a tag do tema.
      3. O SiriusAgentes incrementa o contador de temas novos; a cada 10,
         dispara SiriusNeuronio.treinar() em thread separada.
    """
    nome      = "Especialista"
    descricao = "Agente dinâmico para temas desconhecidos"

    def __init__(self, memoria, tema_detectado: str):
        super().__init__(memoria)
        self.tema_detectado = tema_detectado

    def executar(self, termo: str, contexto: dict = None) -> str:
        print(f"\033[94m[ESPECIALISTA]: Pesquisando tema novo: '{termo}'\033[0m")

        # Pesquisa via AgentePesquisador (lazy property herdado)
        resultado = self._pesquisador.executar(termo)

        # Registra na memória com tag do tema detectado para alimentar próximo treino
        try:
            self.memoria.salvar_estudo_autonomo(
                tema=termo[:100],
                conteudo=resultado,
                tags=f"novo_tema_{self.tema_detectado}"
            )
            print(f"\033[92m[ESPECIALISTA]: '{termo[:40]}' salvo com "
                  f"tag='{self.tema_detectado}'.\033[0m")
        except Exception as e:
            print(f"[ESPECIALISTA]: Erro ao salvar conhecimento: {e}")

        return resultado


# ---------------------------------------------------------------------------
# Coordenador de Agentes — roteamento por IA
# ---------------------------------------------------------------------------

class SiriusAgentes:
    """
    Orquestrador do sistema multi-agente S.I.R.I.U.S.

    - Usa neuronio.predizer() para roteamento inteligente.
    - Instancia AgenteEspecialista dinamicamente para "Novo_Tema".
    - Dispara retreino automático a cada 10 temas novos (thread daemon).
    - Mantém Lazy Properties para os agentes fixos.
    """

    # Número de temas novos que dispara retreino automático
    _THRESHOLD_RETREINO = 10

    def __init__(self, memoria):
        self.memoria = memoria

        # Agentes fixos — instanciados uma única vez
        self._resumidor_obj   = None
        self._pesquisador_obj = None
        self._analisador_obj  = None
        self._escritor_obj    = None
        self._duvidas_obj     = None

        # Contador de temas novos processados nesta sessão
        self._contador_novos_temas = 0
        self._retreino_lock = threading.Lock()

        # Neurônio — importado lazy para não atrasar startup
        self._neuronio = None

    # ---- Lazy Properties — agentes fixos ----------------------------------

    @property
    def resumidor(self) -> AgenteResumidor:
        if self._resumidor_obj is None:
            self._resumidor_obj = AgenteResumidor(self.memoria)
        return self._resumidor_obj

    @property
    def pesquisador(self) -> AgentePesquisador:
        if self._pesquisador_obj is None:
            self._pesquisador_obj = AgentePesquisador(self.memoria)
        return self._pesquisador_obj

    @property
    def analisador(self) -> AgenteAnalisador:
        if self._analisador_obj is None:
            self._analisador_obj = AgenteAnalisador(self.memoria)
        return self._analisador_obj

    @property
    def escritor(self) -> AgenteEscritor:
        if self._escritor_obj is None:
            self._escritor_obj = AgenteEscritor(self.memoria)
        return self._escritor_obj

    @property
    def duvidas(self) -> AgenteDuvidas:
        if self._duvidas_obj is None:
            self._duvidas_obj = AgenteDuvidas(self.memoria)
        return self._duvidas_obj

    # ---- Mapa de temas → agentes fixos ------------------------------------

    @property
    def _mapa_agentes(self) -> dict:
        """Dicionário preguiçoso — instancia agentes só ao acessar."""
        return {
            "pesquisa":   self.pesquisador,
            "resumo":     self.resumidor,
            "analise":    self.analisador,
            "escrita":    self.escritor,
            "duvidas":    self.duvidas,
        }

    # ---- Neurônio lazy ----------------------------------------------------

    def _get_neuronio(self):
        if self._neuronio is None:
            try:
                from neuronio import SiriusNeuronio
                self._neuronio = SiriusNeuronio()
            except Exception as e:
                print(f"[AGENTES]: Neurônio indisponível: {e}")
        return self._neuronio

    # ---- Ciclo de Aprendizado Ativo ---------------------------------------

    def _incrementar_contador_novos_temas(self):
        """
        Incrementa o contador de temas novos. A cada _THRESHOLD_RETREINO,
        dispara neuronio.treinar() em thread daemon para não travar a UI.
        """
        with self._retreino_lock:
            self._contador_novos_temas += 1
            print(f"[AGENTES]: Temas novos acumulados: {self._contador_novos_temas}")

            if self._contador_novos_temas >= self._THRESHOLD_RETREINO:
                self._contador_novos_temas = 0
                neuronio = self._get_neuronio()
                if neuronio:
                    print("\033[93m[AGENTES]: Disparando retreino automático "
                          "(10 temas novos)...\033[0m")
                    threading.Thread(
                        target=neuronio.treinar,
                        daemon=True,
                        name="SiriusNeuronio-Retreino"
                    ).start()

    # ---- Execução principal — roteamento por IA ---------------------------

    def executar(self, comando: str, contexto: dict = None) -> str | None:
        """
        Roteia o comando usando neuronio.predizer().

        Fluxo:
          1. Se for caminho de arquivo → AgenteAnalisador.
          2. neuronio.predizer(comando) → tema.
          3. tema em _mapa_agentes → agente correspondente.
          4. tema == "Novo_Tema" ou ausente → AgenteEspecialista (dinâmico).
          5. Cada novo tema incrementa o contador de aprendizado ativo.
        """
        # Arquivos têm prioridade máxima
        if os.path.exists(comando):
            return self.analisador.executar(comando, contexto)

        # Roteamento por IA
        neuronio = self._get_neuronio()
        tema_predito = neuronio.predizer(comando) if neuronio else "Novo_Tema"

        mapa = self._mapa_agentes

        # Tema conhecido e mapeado
        if tema_predito in mapa:
            agente = mapa[tema_predito]
            print(f"[AGENTES]: Roteando '{comando[:40]}' → {agente.nome}")
            return agente.executar(comando, contexto)

        # Tema desconhecido → Fábrica de Agentes Especialistas
        print(f"[AGENTES]: Tema desconhecido '{tema_predito}' — "
              f"instanciando AgenteEspecialista...")

        especialista = AgenteEspecialista(self.memoria, tema_predito)
        resultado    = especialista.executar(comando, contexto)

        # Incrementa contador → pode disparar retreino automático
        self._incrementar_contador_novos_temas()

        return resultado

    # ---- Métodos auxiliares -----------------------------------------------

    def pesquisar_e_aprender(self, tema: str) -> str:
        resultado = self.pesquisador.executar(tema)
        print(f"\033[92m[AGENTES]: Aprendido sobre '{tema[:40]}'.\033[0m")
        return resultado

    def resolver_duvidas_em_background(self):
        threading.Thread(
            target=self.duvidas.processar_fila,
            daemon=True, name="AgenteDuvidas"
        ).start()

    def listar_agentes(self) -> list[dict]:
        agentes_fixos = [
            self.resumidor, self.pesquisador,
            self.analisador, self.escritor, self.duvidas
        ]
        return [{"nome": a.nome, "descricao": a.descricao}
                for a in agentes_fixos]
