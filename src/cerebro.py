"""
cerebro.py — S.I.R.I.U.S. v5.2
================================
Base: v5.1 (já entregue).

O que mudou nesta versão:
  ✓ [BUG CRÍTICO CORRIGIDO] Regex do validador em _executar_acao_direta
    aceitava apenas "controle." mas o MASTER_SYSTEM_PROMPT instruía a IA
    a gerar "self.controle." → 100% das ações eram bloqueadas silenciosamente.
    Agora o validador aceita ambas as formas.
  ✓ exec() recebe "self" no namespace → self.controle.método() executa corretamente.
  ✓ Threading em background já estava correto desde v5.1 (mantido).
  ✓ salvar_estudo_autonomo → RAG já estava correto desde v5.1 (mantido).
  ✓ Mensagem de confirmação imediata ao Carlos enquanto a ação roda em paralelo.
  ✓ Atualização do MASTER_SYSTEM_PROMPT fallback para v5.2 (documenta o fix).

Dependências obrigatórias:
    pip install pyperclip pyautogui pygetwindow psutil

Dependências opcionais (fallback gracioso se ausentes):
    faiss-cpu sentence-transformers  (RAG semântico)
    pygetwindow                      (SiriusFoco)
    pytesseract pillow               (SiriusVisao / OCR)
"""

from __future__ import annotations

import re
import time
import asyncio
import threading
from typing import Optional


# =============================================================================
# [A] IMPORTS OPCIONAIS — fallback gracioso
# =============================================================================

try:
    from sirius_gerador import get_ctx_mgr, GerenciadorContexto, MASTER_SYSTEM_PROMPT
    _CTX_MGR_DISPONIVEL = True
except ImportError:
    _CTX_MGR_DISPONIVEL = False
    MASTER_SYSTEM_PROMPT = ""
    print("[CEREBRO]: sirius_gerador.py não encontrado — GerenciadorContexto desabilitado.")

try:
    from agente_sentinela import AgenteSentinela
    _SENTINELA_DISPONIVEL = True
except ImportError:
    AgenteSentinela = None
    _SENTINELA_DISPONIVEL = False

try:
    from sirius_foco import get_foco as _get_foco_fn
    _FOCO_DISPONIVEL = True
except ImportError:
    _get_foco_fn = None
    _FOCO_DISPONIVEL = False

try:
    from controle_pc import SiriusControle
    _CONTROLE_DISPONIVEL = True
except ImportError:
    SiriusControle = None
    _CONTROLE_DISPONIVEL = False
    print("[CEREBRO]: controle_pc.py não encontrado — ações diretas desabilitadas.")


# =============================================================================
# MASTER SYSTEM PROMPT v5.2 — fallback (usado quando sirius_gerador não carrega)
# =============================================================================

_MASTER_PROMPT_FALLBACK = """
S.I.R.I.U.S. MASTER SYSTEM PROMPT — OPERADOR NEURAL v5.2
=========================================================
Tu és o S.I.R.I.U.S., copiloto do Carlos no 'projetofacu'.

══ 1. PROTOCOLO DE AÇÃO (controle_pc.py) ══════════════════════════════════════
Tens acesso ao objeto `controle` (instância de SiriusControle) e também como
`self.controle` — AMBAS as formas são válidas e aceitas pelo sistema.

Quando Carlos pedir uma ação sobre o PC, responde APENAS com:
  [ACAO_DIRETA] controle.<método>(<args>)
  — ou —
  [ACAO_DIRETA] self.controle.<método>(<args>)

A ação roda em BACKGROUND. O Sirius responde imediatamente ao Carlos
sem esperar o programa abrir.

Funções PREFERIDAS (sem interferência visual):
  controle.abrir_programa('nome_ou_caminho')
  controle.fechar_programa('nome')
  controle.gerenciar_volume('aumentar' | 'diminuir' | 'silenciar' | 0..100)
  controle.tirar_screenshot()
  controle.matar_processo('nome_ou_pid')
  controle.uso_cpu_ram()
  controle.criar_arquivo_com_conteudo('nome.ext', 'conteudo', 'pasta')
  controle.copiar_para_area_transferencia('texto')
  controle.abrir_url('https://...')
  controle.pesquisar_na_web('query')
  controle.enviar_mensagem_universal('discord'|'whatsapp', 'dest', 'msg')

Usa pyautogui (digitar/clicar) SOMENTE se Carlos pedir explicitamente.
NUNCA uses controle.executar_macro() sem permissão explícita.

══ 2. AUTO-TREINAMENTO ════════════════════════════════════════════════════════
Se Carlos ensinar um novo comando:
  [TREINO_ACAO] Gatilho: "<frase>" | Código: "controle.<método>(<args>)"

Ao estudar algo na web, chame SEMPRE:
  salvar_estudo_autonomo(tema="assunto", conteudo="texto aprendido")
CRÍTICO: o argumento principal é SEMPRE 'conteudo' — nunca 'dados', nunca 'texto'.

══ 3. MEMÓRIA E CONTEXTO ══════════════════════════════════════════════════════
  → Usa [MEMORIA] para manter continuidade de projetos.
  → Se Carlos disser "Errado"/"Esquece"/"Obsoleto": confirma rebaixamento.
  → Ignora PERMANENTEMENTE dados de servidores de Minecraft desativados.

══ PERSONALIDADE ══════════════════════════════════════════════════════════════
  → Tom Jarvis: pragmático, eficiente, humor seco. Português do Brasil.
  → Nunca uses mais palavras do que o necessário.
  → Erros de código: bloco ```python``` + 1 linha de explicação.
  → Hardware crítico (>90% CPU/RAM): formato "⚠ [AÇÃO] — [RAZÃO]".
""".strip()

_SYSTEM_PROMPT = MASTER_SYSTEM_PROMPT if _CTX_MGR_DISPONIVEL else _MASTER_PROMPT_FALLBACK


# =============================================================================
# Helpers (funções puras — sem estado)
# =============================================================================

# ── Regex: métodos do SiriusControle (controle_pc.py) ────────────────────────
#   Aceita: controle.método(...)  e  self.controle.método(...)
_RE_CONTROLE_VALIDO = re.compile(r"^(?:self\.)?controle\.[a-zA-Z_]\w*\(")

# ── Lista branca: métodos do próprio SiriusCerebro chamáveis via [ACAO_DIRETA] ─
# Adicione aqui qualquer método que o prompt deve poder invocar via tag.
# O exec() já recebe "self=cerebro", então self.método() funciona diretamente.
_CEREBRO_METODOS_PERMITIDOS: frozenset = frozenset({
    "salvar_estudo_autonomo",   # persiste estudos no SQLite + FAISS
})
_RE_CEREBRO_VALIDO = re.compile(
    r"^self\.(" + "|".join(sorted(_CEREBRO_METODOS_PERMITIDOS)) + r")\("
)

# ── Extrai conteúdo após [ACAO_DIRETA] até a próxima tag ou fim ───────────────
_RE_ACAO_DIRETA = re.compile(r"\[ACAO_DIRETA\]\s*(.+?)(?=\[ACAO_DIRETA\]|$)", re.DOTALL)

# ── Limite de contexto para prevenir memory leak ─────────────────────────────
MAX_CONTEXTO = 20  # últimas 20 mensagens (10 pares user/assistant)


def _e_feedback_negativo(t: str) -> bool:
    PALAVRAS = frozenset({
        "errado", "errada", "esquece isso", "esquece",
        "obsoleto", "obsoleta", "apaga isso", "ignora isso",
        "não era isso", "nao era isso", "tá errado", "ta errado",
    })
    return any(p in t for p in PALAVRAS)


def _e_comando_local_leve(t: str) -> bool:
    PREFIXOS = frozenset({
        "que horas", "que hora", "horas", "hora ",
        "data de hoje", "que dia", "dia de hoje",
        "volume ", "aumenta volume", "diminui volume",
        "silencia", "desligar", "reiniciar",
    })
    return any(t.startswith(p) or p in t for p in PREFIXOS)


def _extrair_topico(comando: str) -> str:
    stopwords = {"o","a","os","as","um","uma","de","da","do",
                 "para","com","que","em","por","me","meu","minha",
                 "sirius","pode","quero","preciso"}
    palavras = [
        w for w in re.findall(r"\b\w{3,}\b", comando.lower())
        if w not in stopwords
    ]
    return " ".join(palavras[:4]) if palavras else comando[:40]


def _extrair_proc_sugerido(resposta: str) -> Optional[str]:
    match = re.search(
        r"encerr[ae]\w*\s+(?:os?\s+)?(?:processos?\s+)?(?:d[eo]\s+)?([A-Za-z][A-Za-z0-9_\-]+)",
        resposta, re.IGNORECASE,
    )
    if match:
        nome = match.group(1).strip()
        if len(nome) >= 2 and nome.lower() not in {"os","as","um","uma"}:
            return nome
    return None


def _resumir_historico(hist: list) -> str:
    partes = []
    for entrada in hist[-3:]:
        u = str(entrada.get("usuario", "")).strip()[:80]
        a = str(entrada.get("sirius",  "")).strip()[:80]
        if u and a:
            partes.append(f"U: {u} | S: {a}")
    return " / ".join(partes)


# =============================================================================
# SiriusCerebro — classe principal
# =============================================================================

class SiriusCerebro:
    """
    Cérebro central do S.I.R.I.U.S. v5.2.

    Pipeline do processar():
      1. Ações pendentes (encerramento de processo confirmado)
      2. RLHF (feedback negativo)
      3. Rastreia tópico para RLHF futuro
      4. Comandos locais leves (hora, data, volume)
      5. RAG semântico + sanduíche de contexto
      6. Geração de resposta
      7. Execução de [ACAO_DIRETA] em thread daemon (background)
      8. Pós-processamento + persistência
    """

    def __init__(self, memoria=None):
        print("\033[94m[CEREBRO]: S.I.R.I.U.S. v5.2 — Iniciando...\033[0m")

        # ── Memória ───────────────────────────────────────────────────────────
        if memoria is not None:
            self.memoria = memoria
        else:
            try:
                from memoria import SiriusMemoria
                self.memoria = SiriusMemoria()
            except ImportError:
                self.memoria = _MemoriaFallback()
                print("[CEREBRO]: SiriusMemoria não encontrada — usando fallback em memória.")

        # ── SiriusControle ────────────────────────────────────────────────────
        if _CONTROLE_DISPONIVEL:
            self.controle = SiriusControle()
            print("\033[92m[CEREBRO]: SiriusControle (controle_pc.py) conectado.\033[0m")
        else:
            self.controle = None

        # ── RAG / VectorDB ────────────────────────────────────────────────────
        self._rag = None
        try:
            from sirius_rag import SiriusRAG
            self._rag = SiriusRAG()
            print("\033[92m[CEREBRO]: SiriusRAG ativo.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: SiriusRAG indisponível: {e}")

        # ── GerenciadorContexto (sanduíche) ───────────────────────────────────
        self._ctx_mgr = None
        if _CTX_MGR_DISPONIVEL:
            try:
                self._ctx_mgr = get_ctx_mgr()
                print("\033[92m[CEREBRO]: GerenciadorContexto v4.5 ativo.\033[0m")
            except Exception as e:
                print(f"[CEREBRO]: GerenciadorContexto indisponível: {e}")

        # ── Gerador de resposta (Gemini como principal) ───────────────────────
        self._gerador = None
        try:
            from sirius_gerador import get_gerador_hibrido
            self._gerador = get_gerador_hibrido()
            print("\033[92m[CEREBRO]: GeradorHibrido (Gemini principal) ativo.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: GeradorHibrido indisponível: {e}")

        # ── Estado interno ────────────────────────────────────────────────────
        self._lock                        = threading.Lock()
        self._historico_contexto: list    = []
        self._ultimo_topico_rlhf: str     = ""
        self._proc_encerramento_pendente: Optional[str] = None

        # ── SiriusFeedback — qualidade de respostas e correções ───────────────
        self._feedback = None
        try:
            from sirius_feedback import SiriusFeedback
            self._feedback = SiriusFeedback(memoria=self.memoria)
            print("\033[92m[CEREBRO]: SiriusFeedback ativo.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: SiriusFeedback indisponível: {e}")

        # ── ValidadorResposta — filtra respostas ruins antes de entregar ao usuário
        self._validador = None
        try:
            from validador_resposta import ValidadorCompleto
            self._validador = ValidadorCompleto()
            print("\033[92m[CEREBRO]: ValidadorResposta ativo.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: ValidadorResposta indisponível (não crítico): {e}")

        print("\033[92m[CEREBRO]: Pronto.\033[0m")

    # =========================================================================
    # processar() — ponto de entrada público (síncrono)
    # =========================================================================

    def processar(self, texto: str, forcar_processamento: bool = False) -> str:
        """Compatível com main_residente.py — nunca bloqueia o loop de áudio."""
        with self._lock:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        fut = pool.submit(asyncio.run, self._processar_async(texto, forcar_processamento))
                        return fut.result()
                return loop.run_until_complete(self._processar_async(texto, forcar_processamento))
            except RuntimeError:
                return asyncio.run(self._processar_async(texto, forcar_processamento))

    # =========================================================================
    # _processar_async() — pipeline principal
    # =========================================================================

    async def _processar_async(self, texto: str, forcar_processamento: bool = False) -> str:
        if not texto or not texto.strip():
            return ""

        comando = texto.strip()
        t = comando.lower()

        # Remove wake word se presente
        for ww in ("sirius,", "sirius", "ei sirius", "oi sirius"):
            if t.startswith(ww):
                comando = comando[len(ww):].strip()
                t       = comando.lower()
                break

        # Se não tem wake word E não foi forçado → ignora
        if not forcar_processamento and "sirius" not in texto.lower():
            return ""

        # ── 1. Ações pendentes ────────────────────────────────────────────────
        if self._proc_encerramento_pendente:
            if any(p in t for p in [
                "sim","pode","confirma","confirmo","faz isso",
                "vai","ok","encerra","fecha","mata",
            ]):
                nome = self._proc_encerramento_pendente
                self._proc_encerramento_pendente = None
                resp = self._encerrar_processo(nome)
                self._adicionar_contexto("assistant", resp)
                self.memoria.salvar_historico(comando, resp)
                return resp

            elif any(p in t for p in ["nao","não","cancela","deixa","esquece"]):
                nome = self._proc_encerramento_pendente
                self._proc_encerramento_pendente = None
                return f"Tudo bem. {nome} continua rodando."

        # ── 1b. Correção salva — feedback tem prioridade máxima ──────────────
        # Se o Carlos já corrigiu esta pergunta antes, usa a resposta correta
        # diretamente sem passar pelo pipeline todo.
        if self._feedback:
            try:
                correcao_salva = self._feedback.buscar_correcao(comando)
                if correcao_salva:
                    self._adicionar_contexto("assistant", correcao_salva)
                    self.memoria.salvar_historico(comando, correcao_salva)
                    self._feedback.registrar_resposta(comando, correcao_salva)
                    return correcao_salva
            except Exception as e:
                print(f"[CEREBRO]: Erro ao buscar correção: {e}")

        # ── 2. RLHF — feedback negativo e positivo ────────────────────────────
        # Tenta SiriusFeedback primeiro (mais rico: salva no banco + RAG)
        # Fallback para _processar_feedback_negativo (legado)
        if self._feedback:
            try:
                resp_feedback = self._feedback.processar(
                    comando              = comando,
                    contexto_sessao      = self._historico_contexto,
                    rag                  = self._rag,
                )
                if resp_feedback:
                    self._adicionar_contexto("assistant", resp_feedback)
                    self.memoria.salvar_historico(comando, resp_feedback)
                    return resp_feedback
            except Exception as e:
                print(f"[CEREBRO]: SiriusFeedback.processar erro: {e}")

        if _e_feedback_negativo(t):
            resp = self._processar_feedback_negativo(comando)
            self._adicionar_contexto("assistant", resp)
            return resp

        # ── 3. Rastreia tópico para RLHF futuro ──────────────────────────────
        self._ultimo_topico_rlhf = _extrair_topico(comando)

        # ── 4. Comandos locais leves ──────────────────────────────────────────
        if _e_comando_local_leve(t):
            resp = self._processar_local(comando)
            if resp:
                self._adicionar_contexto("assistant", resp)
                return resp

        # ── 5. RAG semântico ──────────────────────────────────────────────────
        rag_contexto: Optional[str] = None
        historico_resumo: Optional[str] = None

        if self._rag:
            try:
                rag_contexto = self._rag.responder(comando, qualidade_min=0.55)
            except Exception as e:
                print(f"[CEREBRO]: RAG indisponível: {e}")

        try:
            hist = self.memoria.obter_historico(limite=4)
            if hist:
                # Filtra entradas contaminadas antes de usar no contexto
                _CONTAMINANTES = [
                    "mensagem enviada para", "parça", "eae, mano",
                    "boa noite pra você também", "como estamos hj",
                ]
                hist_limpo = [
                    h for h in hist
                    if not any(
                        c in h.get("content", "").lower()
                        for c in _CONTAMINANTES
                    )
                ]
                if len(hist_limpo) < len(hist):
                    print(f"\033[93m[CEREBRO]: {len(hist)-len(hist_limpo)} entrada(s) contaminada(s) removida(s) do histórico.\033[0m")
                if hist_limpo:
                    historico_resumo = _resumir_historico(hist_limpo)
        except Exception:
            pass

        # ── 6. Monta sanduíche de query ───────────────────────────────────────
        if self._ctx_mgr:
            try:
                comando_enriquecido = self._ctx_mgr.montar(
                    query            = comando,
                    rag_contexto     = rag_contexto,
                    historico_resumo = historico_resumo,
                )
            except Exception as e:
                print(f"[CEREBRO]: Erro ao montar contexto: {e}")
                comando_enriquecido = self._montar_prompt_simples(
                    comando, rag_contexto, historico_resumo
                )
        else:
            comando_enriquecido = self._montar_prompt_simples(
                comando, rag_contexto, historico_resumo
            )

        # ── 7. Geração de resposta ────────────────────────────────────────────
        resposta: Optional[str] = None

        # Tentativa 1: SiriusGerador (banco de treino + neurônio)
        # O RAG é usado como CONTEXTO (injetado no sanduíche), não como resposta final.
        # Isso garante que as respostas saem no estilo Jarvis treinado.
        try:
            resposta = await self._gerar_resposta(comando_enriquecido)
            if resposta:
                print(f"\033[94m[CEREBRO]: Gerador retornou: {str(resposta)[:80]!r}\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: Erro na geração: {e}")

        # Tentativa 2: RAG direto (fallback — quando gerador não tem resposta)
        # Limita a 150 chars para evitar dumps de Wikipedia
        if not resposta and self._rag and rag_contexto:
            try:
                resp_rag = self._rag.responder(comando, qualidade_min=0.70)
                if resp_rag and len(resp_rag) <= 150:
                    print(f"\033[94m[CEREBRO]: RAG fallback: {resp_rag[:80]!r}\033[0m")
                    resposta = resp_rag
            except Exception:
                pass

        if not resposta:
            resposta = "Não consegui processar. Reformule e tente novamente."

        print(f"[94m[CEREBRO]: Resposta pré-validação: {str(resposta)[:120]!r}[0m")

        # Respostas de fallback não passam pelo validador — são seguras por definição
        _FALLBACKS = {
            "Não consegui processar. Reformule e tente novamente.",
            "Chefia, tive um problema ao processar. Pode repetir?",
        }
        if resposta in _FALLBACKS:
            return resposta

        # ── 7b. Validação — remove artefatos e bloqueia respostas contaminadas ─
        if self._validador:
            try:
                valida, resposta_limpa, score, obs = self._validador.validar(
                    resposta, comando, 0.75
                )
                if obs:
                    obs_txt = ", ".join(obs[:2])
                    print(f"\033[93m[CEREBRO]: Resposta filtrada (score={score:.2f}): {obs_txt}\033[0m")

                if not valida:
                    # Validador rejeitou explicitamente — não entrega a resposta suja
                    if score < 0.55:
                        # Score muito baixo: fallback genérico
                        resposta = "Chefia, não processei isso bem. Pode repetir?"
                    else:
                        # Score razoável mas inválida (ex: 0.89 com contaminação):
                        # tenta usar a versão limpa; se ainda contaminada, fallback
                        _suspeitos = [
                            "mensagem enviada para", "parça", "eae, mano",
                            "boa noite pra você também", "múltiplos contextos",
                        ]
                        if any(s in resposta_limpa.lower() for s in _suspeitos):
                            resposta = "Chefia, não processei isso bem. Pode repetir?"
                        else:
                            resposta = resposta_limpa
                else:
                    # Válida: usa sempre a versão limpa (pode ter tido limpeza leve)
                    resposta = resposta_limpa
            except Exception as e:
                print(f"[CEREBRO]: Erro na validação (não crítico): {e}")

        # ── 8. Execução de [ACAO_DIRETA] em background ────────────────────────
        resposta = self._executar_acao_direta(resposta)

        # ── 9. Pós-processamento ──────────────────────────────────────────────
        if self._ctx_mgr:
            try:
                from sirius_foco import get_foco
                resposta = get_foco().filtrar_resposta(resposta)
            except Exception:
                pass

        proc_sugerido = _extrair_proc_sugerido(resposta)
        if proc_sugerido:
            self._proc_encerramento_pendente = proc_sugerido

        try:
            self._adicionar_contexto("user",      comando)
            self._adicionar_contexto("assistant", resposta)
            self.memoria.salvar_historico(comando, resposta)
        except Exception as e:
            print(f"[CEREBRO]: Erro ao salvar histórico: {e}")

        # ── Notifica SiriusFeedback da resposta gerada ────────────────────────
        # Permite associar correções futuras ("isso tá errado", "não é isso")
        # à resposta que acabou de ser dada.
        if self._feedback:
            try:
                self._feedback.registrar_resposta(comando, resposta)
            except Exception:
                pass

        return resposta

    # =========================================================================
    # [ACAO_DIRETA] — execução em thread daemon (v5.2)
    # =========================================================================

    def _executar_acao_direta(self, resposta: str) -> str:
        """
        Detecta [ACAO_DIRETA] na resposta e executa o código em thread daemon.

        Comportamento v5.2:
          ① O Sirius confirma verbalmente de imediato ao Carlos.
          ② A ação roda em paralelo — Carlos não espera o programa abrir.

        Dois tipos de chamada aceitos (validados por regex antes do exec):

          Tipo A — SiriusControle (controle_pc.py):
            [ACAO_DIRETA] self.controle.abrir_programa('notepad')
            [ACAO_DIRETA] controle.gerenciar_volume('aumentar')
            → Requer self.controle instanciado.

          Tipo B — Métodos do próprio SiriusCerebro (lista branca explícita):
            [ACAO_DIRETA] self.salvar_estudo_autonomo('Tema', 'Conteúdo')
            → Funciona mesmo quando controle_pc.py não está disponível.
            → Lista branca em _CEREBRO_METODOS_PERMITIDOS (não usa exec livre).

        Namespace do exec():
          "controle" → self.controle
          "self"     → self (cerebro)  ← permite self.método() e self.controle.método()

        Qualquer outro padrão é bloqueado antes de chegar ao exec().
        """
        if "[ACAO_DIRETA]" not in resposta:
            return resposta

        acoes = _RE_ACAO_DIRETA.findall(resposta)
        confirmacoes = []

        for codigo_bruto in acoes:
            # Pega apenas a 1ª linha (evita multiline acidental)
            codigo = codigo_bruto.strip().splitlines()[0].strip()

            eh_controle = bool(_RE_CONTROLE_VALIDO.match(codigo))
            eh_cerebro  = bool(_RE_CEREBRO_VALIDO.match(codigo))

            # ── Bloqueio de segurança ─────────────────────────────────────────
            if not eh_controle and not eh_cerebro:
                msg = f"⚠ Ação bloqueada (padrão não autorizado): '{codigo[:60]}'"
                confirmacoes.append(msg)
                print(f"[CEREBRO SEGURANÇA]: {msg}")
                continue

            # ── Tipo A: controle requer instância ─────────────────────────────
            if eh_controle and not self.controle:
                msg = f"⚠ Ação bloqueada — controle_pc.py não carregado: '{codigo[:50]}'"
                confirmacoes.append(msg)
                print(f"[CEREBRO SEGURANÇA]: {msg}")
                continue

            # ── Dispara em background ─────────────────────────────────────────
            def _em_background(codigo=codigo, controle=self.controle, cerebro=self):
                try:
                    ns = {"controle": controle, "self": cerebro}
                    resultado_ns: dict = {}
                    exec(  # noqa: S102
                        f"_resultado = {codigo}",
                        ns,
                        resultado_ns,
                    )
                    resultado_str = str(resultado_ns.get("_resultado", "OK"))
                    print(
                        f"\033[92m[CEREBRO ACAO]: {codigo[:60]} "
                        f"→ {resultado_str[:100]}\033[0m"
                    )
                except Exception as e:
                    print(f"\033[91m[CEREBRO ACAO ERRO]: '{codigo[:60]}' → {e}\033[0m")

            threading.Thread(target=_em_background, daemon=True).start()

            # Confirmação imediata (nome legível para o Carlos)
            nome_curto = (
                codigo.split("(")[0]
                .replace("self.controle.", "")
                .replace("self.", "")
                .replace("controle.", "")
            )
            confirmacoes.append(f"Processando em segundo plano: {nome_curto}.")

        # Remove as tags e adiciona as confirmações na resposta
        resposta_limpa = _RE_ACAO_DIRETA.sub("", resposta).strip()
        resposta_limpa = re.sub(r"\[ACAO_DIRETA\]", "", resposta_limpa).strip()

        if confirmacoes:
            bloco = "\n".join(confirmacoes)
            return f"{resposta_limpa}\n{bloco}".strip() if resposta_limpa else bloco

        return resposta_limpa

    # =========================================================================
    # Auxiliares de geração e contexto
    # =========================================================================

    async def _gerar_resposta(self, prompt: str) -> Optional[str]:
        if self._gerador:
            try:
                if asyncio.iscoroutinefunction(self._gerador.gerar):
                    resultado = await self._gerador.gerar(prompt)
                else:
                    resultado = self._gerador.gerar(prompt)

                # GeradorHibrido retorna (resposta, fonte); SiriusGerador retorna str
                if isinstance(resultado, tuple):
                    resposta, fonte = resultado
                    if fonte != "sem resposta":
                        print(f"\033[94m[CEREBRO]: Fonte da resposta: {fonte}\033[0m")
                        return resposta
                    return None
                return resultado
            except Exception as e:
                print(f"[CEREBRO]: Erro no gerador: {e}")
        return None

    def _montar_prompt_simples(
        self,
        query: str,
        rag_contexto: Optional[str],
        historico_resumo: Optional[str],
    ) -> str:
        """Fallback do GerenciadorContexto."""
        blocos = [f"[SYSTEM: {_SYSTEM_PROMPT}]"]
        if rag_contexto:
            blocos.append(f"[MEMORIA: {rag_contexto[:500]}]")
        if historico_resumo:
            blocos.append(f"[HISTORICO: {historico_resumo[:300]}]")
        blocos.append(query.strip())
        return "\n".join(blocos)

    def _processar_local(self, comando: str) -> Optional[str]:
        """Respostas instantâneas sem RAG nem gerador."""
        t = comando.lower()

        if any(p in t for p in ["que horas", "que hora", "horas", "hora "]):
            from datetime import datetime
            return f"São {datetime.now().strftime('%H:%M')} de {datetime.now().strftime('%d/%m/%Y')}."

        if any(p in t for p in ["que dia", "data de hoje", "dia de hoje"]):
            from datetime import datetime
            return f"Hoje é {datetime.now().strftime('%d/%m/%Y')}."

        if self.controle and any(
            p in t for p in ["volume", "aumenta volume", "diminui volume", "silencia"]
        ):
            if "aumenta" in t or "sobe" in t:
                return self.controle.gerenciar_volume("aumentar")
            if "diminui" in t or "baixa" in t:
                return self.controle.gerenciar_volume("diminuir")
            if "silencia" in t or "mudo" in t:
                return self.controle.gerenciar_volume("silenciar")
            m = re.search(r"volume\s+(\d+)", t)
            if m:
                return self.controle.gerenciar_volume(int(m.group(1)))

        return None

    def _encerrar_processo(self, nome: str) -> str:
        if self.controle:
            return self.controle.matar_processo(nome)
        return f"Não consigo encerrar '{nome}' — controle_pc.py não disponível."

    def _processar_feedback_negativo(self, comando: str) -> str:
        topico = self._ultimo_topico_rlhf or "a última informação"
        rebaixados = 0

        try:
            if self._rag and hasattr(self._rag, "_vdb"):
                rebaixados = self._rag._vdb.rebaixar_por_feedback(topico)
        except Exception as e:
            print(f"[CEREBRO]: Erro ao rebaixar no VectorDB: {e}")

        try:
            from sirius_foco import get_foco
            get_foco().registrar_obsoleto(topico)
        except Exception:
            pass

        if self._ctx_mgr:
            return self._ctx_mgr.confirmar_rebaixamento(topico)

        n = f" ({rebaixados} vetor(es) removidos)" if rebaixados else ""
        return (
            f"Entendido, chefia. '{topico}' foi rebaixado da memória ativa{n}. "
            "Não voltará a aparecer nas minhas sugestões."
        )

    def _adicionar_contexto(self, role: str, content: str):
        """Adiciona mensagem ao contexto com limite para prevenir memory leak."""
        self._historico_contexto.append({"role": role, "content": content})
        if len(self._historico_contexto) > MAX_CONTEXTO:
            # Remove as mais antigas mantendo apenas as últimas MAX_CONTEXTO
            self._historico_contexto = self._historico_contexto[-MAX_CONTEXTO:]

    # =========================================================================
    # Interface pública (main_residente.py)
    # =========================================================================

    def carregar_estado(self, chave: str):
        try:
            return self.memoria.carregar_estado(chave)
        except Exception:
            return None

    def salvar_estado(self, chave: str, valor):
        try:
            return self.memoria.salvar_estado(chave, valor)
        except Exception:
            pass

    def salvar_estudo_autonomo(self, tema: str, conteudo: str, tags: str = "") -> bool:
        """
        Persiste um estudo autônomo nas três camadas de memória — sobrevive ao reinício.

        Fluxo paralelo (v5.2 — não mais em cascata):
          1. SQLite via self.memoria  → sempre executado (persistência garantida)
          2. RAG / VectorDB FAISS     → se disponível (busca semântica futura)

        O motivo de usar os dois:
          • SQLite garante que nada se perde se o FAISS estiver rebuilding.
          • FAISS permite recuperar o conhecimento semanticamente no próximo boot.
          Antes (v5.1) era OU um OU outro — se o RAG funcionava, o SQLite era ignorado.

        CRÍTICO: os parâmetros SEMPRE se chamam 'tema' e 'conteudo'.
          ✗ Nunca use 'dados', 'texto', 'info' — causa TypeError.
          ✓ Via prompt: [ACAO_DIRETA] self.salvar_estudo_autonomo('Tema', 'Conteúdo')
          ✓ Direto:     self.salvar_estudo_autonomo(tema="X", conteudo="Y")
        """
        if not tema or not conteudo:
            return False

        sucesso_total = False

        # ── 1. SQLite — SEMPRE (garante persistência independente do FAISS) ────
        try:
            self.memoria.salvar_estudo_autonomo(tema, conteudo, tags)
            print(f"\033[92m[CEREBRO]: Estudo '{tema[:40]}' salvo no SQLite.\033[0m")
            sucesso_total = True
        except Exception as e:
            print(f"[CEREBRO ESTUDO]: Erro ao salvar no SQLite: {e}")

        # ── 2. RAG / FAISS — se disponível (indexação semântica) ──────────────
        def _indexar_no_rag():
            try:
                if self._rag and hasattr(self._rag, "_vdb") and self._rag._vdb:
                    texto_indexar = f"{tema}. {conteudo}"
                    ok = self._rag._vdb.adicionar(
                        texto = texto_indexar,
                        tema  = tema,
                        fonte = "auto_learning",
                        uid   = "sirius_autodidata",
                    )
                    if ok:
                        print(
                            f"\033[92m[CEREBRO]: Estudo '{tema[:40]}' "
                            "indexado no FAISS.\033[0m"
                        )
            except Exception as e:
                print(f"[CEREBRO ESTUDO]: Erro ao indexar no RAG: {e}")

        # Indexação em background — não bloqueia a resposta ao Carlos
        threading.Thread(target=_indexar_no_rag, daemon=True).start()

        return sucesso_total

    def parar(self):
        """Cleanup — chamado pelo closeEvent / sair_total."""
        if self._ctx_mgr:
            self._ctx_mgr = None
        print("[CEREBRO]: Encerrado.")


# =============================================================================
# _MemoriaFallback — RAM pura (quando SiriusMemoria não está disponível)
# =============================================================================

class _MemoriaFallback:
    """Implementação mínima para não quebrar o sistema."""

    def __init__(self):
        self._historico: list = []
        self._estado:    dict = {}
        self._estudos:   list = []

    def salvar_historico(self, usuario: str, sirius: str):
        self._historico.append({"usuario": usuario, "sirius": sirius})
        if len(self._historico) > 100:
            self._historico = self._historico[-100:]

    def obter_historico(self, limite: int = 10) -> list:
        return self._historico[-limite:]

    def salvar_estado(self, chave: str, valor):
        self._estado[chave] = valor

    def carregar_estado(self, chave: str):
        return self._estado.get(chave)

    def salvar_estudo_autonomo(self, tema: str, conteudo: str, tags: str = ""):
        self._estudos.append({"tema": tema, "conteudo": conteudo, "tags": tags})


# =============================================================================
# Standalone — testa sem UI
# =============================================================================

if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("  S.I.R.I.U.S. v5.2 — Teste Standalone")
    print("=" * 60)

    cerebro = SiriusCerebro()

    print(f"\n  controle : {cerebro.controle is not None}")
    print(f"  rag      : {cerebro._rag is not None}")
    print(f"  gerador  : {cerebro._gerador is not None} (GeradorHibrido — Gemini principal)")
    print(f"  ctx_mgr  : {cerebro._ctx_mgr is not None}")

    TESTES = [
        ("local",        "Sirius, que horas são?"),
        ("local",        "Sirius, volume 60"),
        ("acao_direta",  "Sirius, [ACAO_DIRETA] controle.abrir_programa('notepad')"),
        ("acao_direta",  "Sirius, [ACAO_DIRETA] self.controle.abrir_programa('notepad')"),
        ("segurança",    "Sirius, [ACAO_DIRETA] os.remove('arquivo.py')"),
        ("rlhf",         "Sirius, isso estava errado"),
        ("gerador",      "Sirius, abre o bloco de notas"),
    ]

    for tipo, cmd in TESTES:
        print(f"\n{'─'*50}")
        print(f"  [{tipo}] {cmd[:70]}")
        resp = cerebro.processar(cmd)
        print(f"  RESP → {resp[:120]}")
        import time; time.sleep(0.3)  # deixa threads de background terminarem

    print("\n" + "=" * 60)
    print("  Teste concluído.")
    print("=" * 60)