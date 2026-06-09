"""
sirius_professor.py — S.I.R.I.U.S. v5.2
=========================================
Fluxo:
  1. Sirius gera uma frase sobre um tema qualquer (do jeito que sabe hoje)
  2. Gemini recebe essa frase e mostra como deveria ser no estilo Jarvis
  3. O par (frase Sirius → versão Jarvis) é salvo no banco
  4. O neurônio treina em cima dos pares e aprende o estilo

Completamente automático — sem lista fixa, sem intervenção manual.

Quando o token do Gemini acabar:
  - Para sozinho sem erro
  - Sirius continua funcionando com o que já aprendeu
  - Retoma no próximo ciclo quando o token voltar

Uso:
  python src/sirius_professor.py --ciclos 20
  python src/sirius_professor.py --continuo --intervalo 3
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import threading
import json
import random
from typing import Optional

# ── Estrutura esperada do projeto ─────────────────────────────────────────────
#
#   Sistema_ChatBot/          ← _DIR_RAIZ
#     config/
#       .env                  ← GEMINI_API_KEY aqui
#     data/
#       sirius_treino.db
#       sirius_pessoal.db
#     src/                    ← _DIR_SRC (este arquivo está aqui)
#       sirius_professor.py

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))   # .../src
_DIR_RAIZ = os.path.dirname(_DIR_SRC)                    # .../Sistema_ChatBot
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

# ── Carrega .env de config/ ────────────────────────────────────────────────────
_ENV_PATH = os.path.join(_DIR_RAIZ, "config", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _linha in _f:
            _linha = _linha.strip()
            if _linha and not _linha.startswith("#") and "=" in _linha:
                _chave, _, _valor = _linha.partition("=")
                os.environ.setdefault(_chave.strip(), _valor.strip())
else:
    print(f"[PROFESSOR]: Aviso — {_ENV_PATH} não encontrado.")
    print( "             Crie o arquivo config/.env com GEMINI_API_KEY=sua_chave")

_CAMINHO_DATA = os.path.join(_DIR_RAIZ, "data")
_DB_TREINO    = os.path.join(_CAMINHO_DATA, "sirius_treino.db")
_DB_PESSOAL   = os.path.join(_CAMINHO_DATA, "sirius_pessoal.db")
os.makedirs(_CAMINHO_DATA, exist_ok=True)


# ── Gemini ────────────────────────────────────────────────────────────────────
_GEMINI_OK    = False
_gemini_model = None


def _inicializar_gemini() -> bool:
    global _GEMINI_OK, _gemini_model
    if _GEMINI_OK:
        return True
    try:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            print("\033[93m[PROFESSOR]: GEMINI_API_KEY não definida — professor desabilitado.\033[0m")
            print("             Adicione GEMINI_API_KEY=sua_chave no config/.env para ativar.")
            return False
        _gemini_model = genai.Client(api_key=api_key)
        # Testa a conexão com uma chamada mínima
        _gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents="ok"
        )
        _GEMINI_OK = True
        print("\033[92m[PROFESSOR]: Gemini conectado — professor Jarvis ativo.\033[0m")
        return True
    except ImportError:
        print("\033[93m[PROFESSOR]: SDK google-genai não instalado.\033[0m")
        print("             Execute: pip install google-genai")
        return False
    except Exception as e:
        print(f"\033[93m[PROFESSOR]: Gemini indisponível: {e}\033[0m")
        return False


# ── Prompt 1: Sirius gera uma frase sobre um tema ────────────────────────────
# Simula como o Sirius responderia hoje — antes de aprender o estilo Jarvis.
# Intencionalmente sem restrições de estilo para capturar o padrão atual.

_PROMPT_SIRIUS_GERA = """
Você é um assistente de IA chamado Sirius respondendo em Português do Brasil.
Gere UMA frase de resposta curta sobre o tema abaixo, do jeito natural que um assistente comum responderia.
Responda SOMENTE com a frase, sem explicação, sem JSON, sem aspas.

Tema: {tema}
""".strip()

# ── Prompt 2: Gemini corrige para o estilo Jarvis ────────────────────────────
# O Gemini recebe a frase do Sirius e mostra como deveria ser.

_PROMPT_GEMINI_CORRIGE = """
Você é um professor do estilo de fala do Jarvis (assistente do Iron Man).

ESTILO JARVIS — REGRAS OBRIGATÓRIAS:
- Tom seco, direto, sem emoção excessiva
- Nunca começa com "Claro!", "Com certeza!", "Ótima pergunta!", "Com prazer!"
- Nunca usa elogios, saudações desnecessárias ou rodeios
- Frases curtas — máximo 2 linhas
- Português do Brasil, sem emojis, sem markdown
- Se técnico: vai direto ao ponto, 1 exemplo no máximo
- Quando não sabe: diz sem drama ("Sem dados suficientes para isso.")

Abaixo está uma frase gerada pelo Sirius. Reescreva ela NO ESTILO JARVIS.
Mantenha a mesma informação — mude apenas o estilo e o tom.

Responda SOMENTE com JSON, sem texto adicional:
{{
  "sirius": "a frase original do Sirius",
  "jarvis": "a mesma frase reescrita no estilo Jarvis"
}}

Frase do Sirius:
{frase_sirius}
""".strip()


# ── Temas para o Sirius gerar frases ─────────────────────────────────────────
# Variedade de contextos reais que o Sirius enfrenta no dia a dia.

_TEMAS = [
    "CPU do computador está em 95%",
    "Como abrir o Bloco de Notas",
    "Temperatura atual em São Paulo",
    "O que é machine learning",
    "Como fechar um programa travado",
    "Resumir um texto longo",
    "Diferença entre RAM e HD",
    "Como fazer um loop em Python",
    "Tirar um screenshot da tela",
    "O que é uma API REST",
    "Aumentar o volume do computador",
    "Como deletar arquivos duplicados",
    "O que é DNS",
    "Como verificar o IP da máquina",
    "Diferença entre processo e thread",
    "O que é um token JWT",
    "Como limpar o cache do navegador",
    "O que é latência de rede",
    "Como instalar uma biblioteca Python",
    "O que significa erro 404",
    "Como verificar uso de memória RAM",
    "O que é um deadlock",
    "Diferença entre HTTP e HTTPS",
    "Como copiar texto para a área de transferência",
    "O que é um banco de dados SQLite",
    "Como renomear arquivos em lote",
    "O que é WebSocket",
    "Como matar um processo pelo terminal",
    "O que é compressão de dados",
    "Diferença entre síncrono e assíncrono",
]


# =============================================================================
# SiriusProfessor
# =============================================================================

class SiriusProfessor:
    """
    Ciclo automático de aprendizado de estilo:
      1. Sirius gera frase sobre tema aleatório
      2. Gemini reescreve no estilo Jarvis
      3. Par salvo no banco para o neurônio treinar

    Completamente automático — sem intervenção manual.
    """

    def __init__(self, memoria=None):
        self.memoria           = memoria
        self._rodando          = False
        self._pausado          = False
        self._total_gerados    = 0
        self._total_erros      = 0
        self._ultimo_erro      = ""
        self._rate_limit_espera = 0  # segundos a aguardar após rate limit
        self._disponivel       = _inicializar_gemini()

    # ── Disponibilidade ───────────────────────────────────────────────────────

    def _gemini_disponivel(self) -> bool:
        return self._disponivel and _GEMINI_OK and _gemini_model is not None

    # ── Detecta esgotamento de token ──────────────────────────────────────────

    def _tratar_erro_gemini(self, e: Exception):
        err = str(e).lower()
        self._ultimo_erro = str(e)
        # Erros que justificam parar imediatamente (sem tentar de novo)
        # Erros que encerram permanentemente até intervenção manual
        _ERROS_FATAIS = [
            "api_key_invalid", "api key expired",   # chave expirada
            "api_key_not_found", "invalid api key", # chave inválida
            "permission_denied",                    # sem permissão
            "is not found for api version",         # modelo descontinuado
            "not supported for generatecontent",    # modelo sem suporte
        ]
        # Rate limit (429) — aguarda o retry_delay indicado pelo Gemini e continua
        _ERROS_RATE_LIMIT = ["429", "resource_exhausted", "quota exceeded"]

        if any(k in err for k in _ERROS_FATAIS):
            if "expired" in err or "invalid" in err or "not_found" in err:
                print(
                    "\033[91m[PROFESSOR]: Chave Gemini inválida ou expirada — professor pausado.\033[0m"
                    "\n             Renove a chave em aistudio.google.com e atualize o config/.env"
                )
            elif "not found for api version" in err or "not supported" in err:
                print(
                    "\033[91m[PROFESSOR]: Modelo Gemini não disponível — professor pausado.\033[0m"
                    "\n             O modelo foi descontinuado. Atualize o sirius_professor.py."
                )
            else:
                print(f"\033[91m[PROFESSOR]: Erro fatal Gemini — professor pausado: {e}\033[0m")
            self._pausado    = True
            self._disponivel = False

        elif any(k in err for k in _ERROS_RATE_LIMIT):
            # Extrai retry_delay do erro se disponível (ex: "retry_delay { seconds: 7 }")
            import re as _re
            match = _re.search(r'seconds:\s*(\d+)', str(e))
            espera = int(match.group(1)) + 2 if match else 60
            print(
                f"\033[93m[PROFESSOR]: Rate limit atingido — aguardando {espera}s antes de continuar...\033[0m"
            )
            self._rate_limit_espera = espera  # lido pelo loop de ensinar()

        else:
            self._total_erros += 1
            print(f"\033[91m[PROFESSOR]: Erro Gemini: {e}\033[0m")

    # ── Passo 1: Sirius gera uma frase ────────────────────────────────────────

    def _sirius_gera_frase(self, tema: str) -> Optional[str]:
        """
        Usa o Gemini para simular como o Sirius responderia hoje
        (sem restrições de estilo — captura o padrão atual).
        """
        if not self._gemini_disponivel():
            return None

        # Aguarda rate limit se necessário
        if self._rate_limit_espera > 0:
            time.sleep(self._rate_limit_espera)
            self._rate_limit_espera = 0

        prompt = _PROMPT_SIRIUS_GERA.format(tema=tema)
        try:
            resp  = _gemini_model.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            frase = resp.text.strip().strip('"').strip("'")
            if len(frase) > 5:
                return frase
            print(f"\033[93m[PROFESSOR]: Frase vazia para '{tema[:40]}' — bruto: {resp.text!r}\033[0m")
            return None
        except Exception as e:
            self._tratar_erro_gemini(e)
            return None

    # ── Passo 2: Gemini reescreve no estilo Jarvis ────────────────────────────

    def _gemini_corrige_para_jarvis(self, frase_sirius: str) -> Optional[dict]:
        """
        Recebe a frase do Sirius e devolve a versão no estilo Jarvis.
        Retorna {"sirius": str, "jarvis": str} ou None.
        """
        if not self._gemini_disponivel():
            return None

        prompt = _PROMPT_GEMINI_CORRIGE.format(frase_sirius=frase_sirius)
        try:
            resp  = _gemini_model.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            texto = resp.text.strip()

            # Remove blocos markdown em qualquer variação
            texto = (
                texto
                .replace("```json", "")
                .replace("```JSON", "")
                .replace("```", "")
                .strip()
            )

            # Tenta extrair JSON mesmo que venha com texto ao redor
            inicio = texto.find("{")
            fim    = texto.rfind("}") + 1
            if inicio != -1 and fim > inicio:
                texto = texto[inicio:fim]

            par = json.loads(texto)

            if (
                isinstance(par, dict)
                and "sirius" in par
                and "jarvis" in par
                and len(par.get("jarvis", "")) > 2
                and par["jarvis"].lower().strip() != frase_sirius.lower().strip()
            ):
                return par

            print(
                f"\033[93m[PROFESSOR]: JSON válido mas estrutura inválida: "
                f"{list(par.keys()) if isinstance(par, dict) else type(par)}\033[0m"
            )
            return None

        except json.JSONDecodeError as e:
            # Loga o texto bruto para diagnóstico
            bruto = resp.text[:120] if 'resp' in dir() else '(sem resposta)'
            print(f"\033[93m[PROFESSOR]: JSON inválido — bruto: {bruto!r}\033[0m")
            print(f"\033[93m[PROFESSOR]: Erro JSON: {e}\033[0m")
            return None
        except Exception as e:
            self._tratar_erro_gemini(e)
            return None

    # ── Passo 3: Salva o par no banco ─────────────────────────────────────────

    def _salvar_par(self, frase_sirius: str, frase_jarvis: str) -> bool:
        """
        Salva o par de treino no banco.

        Formato:
          tema     = "estilo_jarvis"
          conteudo = "Sirius disse: <frase>\nJarvis diria: <corrigida>"

        IMPORTANTE: usa SQLite direto (síncrono) como caminho principal.
        A SiriusMemoria usa escrita assíncrona — o programa pode terminar
        antes da fila esvaziar, perdendo os dados. SQLite direto garante
        que o par está no disco antes de retornar True.
        """
        tema    = "estilo_jarvis"
        conteudo = (
            f"Sirius disse: {frase_sirius.strip()}\n"
            f"Jarvis diria: {frase_jarvis.strip()}"
        )

        # SQLite direto — síncrono, garante persistência imediata
        try:
            conn = sqlite3.connect(_DB_TREINO, timeout=10)
            conn.execute(
                "INSERT INTO estudos_autonomos "
                "(user_id, tema, conteudo, tags, validado_por, fonte) "
                "VALUES ('', ?, ?, 'estilo_jarvis', 'Gemini', 'professor_jarvis')",
                (tema, conteudo),
            )
            conn.execute(
                "INSERT INTO conhecimento_geral "
                "(user_id, tema, conteudo, validado_por, tags) "
                "VALUES ('', ?, ?, 'Gemini', 'estilo_jarvis')",
                (tema, conteudo),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"\033[91m[PROFESSOR]: Erro ao salvar par: {e}\033[0m")

        # Fallback — via SiriusMemoria (assíncrono, menos confiável ao encerrar)
        if self.memoria:
            try:
                self.memoria.salvar_estudo_autonomo(
                    tema    = tema,
                    conteudo = conteudo,
                    tags    = "estilo_jarvis",
                    fonte   = "professor_jarvis",
                )
                print("\033[93m[PROFESSOR]: Salvo via SiriusMemoria (assíncrono).\033[0m")
                return True
            except Exception as e:
                print(f"\033[91m[PROFESSOR]: Erro via memoria: {e}\033[0m")

        return False

    # ── Ciclo completo: gera → corrige → salva ────────────────────────────────

    def _executar_ciclo(self, tema: str) -> bool:
        """
        Executa um ciclo completo para um tema:
          Sirius gera frase → Gemini corrige → salva no banco.
        Retorna True se o par foi salvo com sucesso.
        """
        # Passo 1 — Sirius gera
        frase_sirius = self._sirius_gera_frase(tema)
        if not frase_sirius:
            print(f"\033[93m[PROFESSOR]: Passo 1 falhou (Sirius não gerou frase) — tema: '{tema[:40]}'\033[0m")
            return False

        # Passo 2 — Gemini corrige para Jarvis
        par = self._gemini_corrige_para_jarvis(frase_sirius)
        if not par:
            print(f"\033[93m[PROFESSOR]: Passo 2 falhou (Gemini não corrigiu) — frase: '{frase_sirius[:60]}'\033[0m")
            return False

        # Passo 3 — Salva
        ok = self._salvar_par(par["sirius"], par["jarvis"])
        if ok:
            self._total_gerados += 1
            print(
                f"\033[92m[PROFESSOR]: ✓ [{tema[:35]}]\033[0m\n"
                f"  Sirius : {par['sirius'][:80]}\n"
                f"  Jarvis : {par['jarvis'][:80]}"
            )
        else:
            print(f"\033[91m[PROFESSOR]: Passo 3 falhou (erro ao salvar) — tema: '{tema[:40]}'\033[0m")

        return ok

    # ── Ensinar N ciclos (bloqueante) ─────────────────────────────────────────

    def ensinar(self, n: int = 10, pausa_s: float = 2.0) -> int:
        """
        Executa N ciclos automáticos (Sirius gera → Gemini corrige → salva).
        Retorna: número de pares salvos.
        """
        if not self._gemini_disponivel():
            if self._pausado:
                print("\033[93m[PROFESSOR]: Token esgotado — aguardando renovação.\033[0m")
            else:
                print("\033[93m[PROFESSOR]: Gemini indisponível — nada a ensinar.\033[0m")
            return 0

        # Seleciona temas — mistura temas fixos com temas do banco de conhecimento
        temas_banco = self._buscar_temas_do_banco(limite=n)
        temas_fixos = random.sample(_TEMAS, min(n, len(_TEMAS)))
        temas = (temas_banco + temas_fixos)[:n]
        random.shuffle(temas)

        print(f"\033[94m[PROFESSOR]: Iniciando {len(temas)} ciclo(s) automáticos...\033[0m")
        print(f"  gemini_ok={_GEMINI_OK}, disponivel={self._disponivel}, model={'sim' if _gemini_model else 'None'}")

        salvos = 0
        for i, tema in enumerate(temas):
            if not self._gemini_disponivel():
                print(f"\033[93m[PROFESSOR]: Gemini ficou indisponível no ciclo {i+1}/{len(temas)} — abortando.\033[0m")
                print(f"  pausado={self._pausado}, ultimo_erro={self._ultimo_erro!r}")
                break

            if self._executar_ciclo(tema):
                salvos += 1

            if pausa_s > 0:
                time.sleep(pausa_s)

        print(f"\033[92m[PROFESSOR]: Sessão concluída — {salvos}/{len(temas)} pares salvos.\033[0m")
        return salvos

    # ── Temas extras do banco de conhecimento ────────────────────────────────

    def _buscar_temas_do_banco(self, limite: int = 10) -> list[str]:
        """Busca temas variados do banco para ampliar o vocabulário de treino."""
        temas = []
        try:
            conn = sqlite3.connect(_DB_TREINO)
            rows = conn.execute(
                "SELECT DISTINCT tema FROM conhecimento_geral "
                "WHERE tema IS NOT NULL AND length(tema) > 3 "
                "ORDER BY RANDOM() LIMIT ?",
                (limite,)
            ).fetchall()
            conn.close()
            temas = [r[0] for r in rows if r[0]]
        except sqlite3.Error as e:
            # Banco indisponível — continua com lista vazia
            print(f"\033[93m[PROFESSOR]: Aviso ao buscar temas: {e}\033[0m")
        except Exception as e:
            print(f"\033[91m[PROFESSOR]: Erro inesperado ao buscar temas: {e}\033[0m")
        return temas

    # ── Background ────────────────────────────────────────────────────────────

    def ensinar_bg(self, n: int = 10, callback=None):
        """Executa os ciclos em thread daemon — não bloqueia o treinador."""
        def _run():
            n_salvos = self.ensinar(n=n)
            if callback:
                try:
                    callback(n_salvos)
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True, name="SiriusProfessor").start()

    # ── Loop contínuo ─────────────────────────────────────────────────────────

    def iniciar_loop_autonomo(self, intervalo_horas: float = 3.0, n_por_ciclo: int = 10):
        """
        Roda em background continuamente.
        Para quando o token acaba. Tenta reconectar no próximo ciclo.
        """
        if self._rodando:
            return
        self._rodando = True

        def _loop():
            print(
                f"\033[94m[PROFESSOR]: Loop autônomo iniciado "
                f"({intervalo_horas}h / {n_por_ciclo} ciclos)\033[0m"
            )
            while self._rodando:
                if self._pausado:
                    print("\033[93m[PROFESSOR]: Tentando reconectar ao Gemini...\033[0m")
                    self._pausado    = False
                    self._disponivel = _inicializar_gemini()

                self.ensinar(n=n_por_ciclo)
                print(
                    f"\033[94m[PROFESSOR]: Próximo ciclo em {intervalo_horas}h. "
                    f"Total: {self._total_gerados} pares.\033[0m"
                )
                time.sleep(intervalo_horas * 3600)

        threading.Thread(
            target=_loop, daemon=True, name="SiriusProfessorLoop"
        ).start()

    def parar(self):
        self._rodando = False

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        n = 0
        try:
            conn = sqlite3.connect(_DB_TREINO)
            r = conn.execute(
                "SELECT COUNT(*) FROM estudos_autonomos WHERE fonte='professor_jarvis'"
            ).fetchone()
            n = r[0] if r else 0
            conn.close()
        except sqlite3.Error as e:
            # Banco indisponível — retorna n=0
            print(f"\033[93m[PROFESSOR]: Aviso ao contar estudos: {e}\033[0m")
        except Exception as e:
            print(f"\033[91m[PROFESSOR]: Erro inesperado ao contar estudos: {e}\033[0m")
        return {
            "gemini_disponivel": self._gemini_disponivel(),
            "pausado_sem_token": self._pausado,
            "rodando":           self._rodando,
            "total_gerados":     self._total_gerados,
            "pares_no_banco":    n,
            "ultimo_erro":       self._ultimo_erro,
        }

    def imprimir_status(self):
        s = self.status()
        g = "✓ ativo" if s["gemini_disponivel"] else ("⚠ sem token" if s["pausado_sem_token"] else "✗ indisponível")
        print("\n╔════════════════════════════════════════╗")
        print("║   Sirius Professor — Estilo Jarvis     ║")
        print("╠════════════════════════════════════════╣")
        print(f"║  Gemini:           {g:21s} ║")
        print(f"║  Loop autônomo:    {'Sim' if s['rodando'] else 'Não':21s} ║")
        print(f"║  Pares gerados:    {str(s['total_gerados']):21s} ║")
        print(f"║  Pares no banco:   {str(s['pares_no_banco']):21s} ║")
        print("╚════════════════════════════════════════╝\n")


# =============================================================================
# Integração com SiriusTreinador
# =============================================================================

def integrar_com_treinador(treinador, memoria=None, n_por_ciclo: int = 10):
    """
    Conecta o professor ao treinador existente.

    Antes de cada treinar_tudo(), o professor executa ciclos automáticos
    em background: Sirius gera → Gemini corrige → banco recebe os pares.

    Uso:
        from sirius_professor import integrar_com_treinador
        integrar_com_treinador(treinador, memoria=cerebro.memoria)
    """
    professor = SiriusProfessor(memoria=memoria)
    _original = treinador.treinar_tudo

    def treinar_tudo_com_professor(forcar: bool = False):
        if professor._gemini_disponivel():
            print("\033[94m[PROFESSOR]: Sirius gerando frases → Gemini corrigindo para Jarvis...\033[0m")
            professor.ensinar_bg(
                n=n_por_ciclo,
                callback=lambda n: print(
                    f"\033[92m[PROFESSOR]: {n} par(es) Sirius→Jarvis adicionados ao banco.\033[0m"
                ),
            )
            time.sleep(2)
        else:
            print("\033[93m[PROFESSOR]: Gemini indisponível — treino sem exemplos Jarvis.\033[0m")

        _original(forcar=forcar)

    treinador.treinar_tudo = treinar_tudo_com_professor
    treinador._professor   = professor
    print("\033[92m[PROFESSOR]: Professor Jarvis integrado ao treinador.\033[0m")
    return professor


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Professor Jarvis — Sirius gera, Gemini corrige o estilo"
    )
    parser.add_argument("--ciclos",    type=int,   default=10,  help="Executa N ciclos agora (padrão: 10)")
    parser.add_argument("--continuo",  action="store_true",     help="Loop contínuo em background")
    parser.add_argument("--intervalo", type=float, default=3.0, help="Horas entre ciclos (padrão: 3)")
    parser.add_argument("--status",    action="store_true",     help="Mostra status")
    args = parser.parse_args()

    memoria = None
    try:
        from memoria import SiriusMemoria
        memoria = SiriusMemoria()
        print("\033[92m[PROFESSOR]: SiriusMemoria conectada.\033[0m")
    except ImportError:
        print("\033[93m[PROFESSOR]: Usando SQLite direto.\033[0m")

    professor = SiriusProfessor(memoria=memoria)

    if args.status:
        professor.imprimir_status()
    elif args.continuo:
        professor.iniciar_loop_autonomo(
            intervalo_horas=args.intervalo,
            n_por_ciclo=args.ciclos,
        )
        print("  Professor rodando. Ctrl+C para parar.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            professor.parar()
            print("\n  Encerrado.")
    else:
        professor.ensinar(n=args.ciclos)
        professor.imprimir_status()