"""
sirius_feedback.py — Sistema de feedback e qualidade do aprendizado

Como funciona:
  O Sirius aprende com cada interação — mas nem toda resposta é boa.
  Este módulo rastreia a qualidade de cada resposta e permite que
  o usuário corrija erros explicitamente.

  Com isso, o banco de treino vai melhorando com o tempo:
    - Respostas corretas recebem qualidade alta → usadas com prioridade no RAG
    - Respostas ruins são marcadas e descartadas no próximo retreino
    - Correções explícitas viram conhecimento de qualidade máxima

Banco de dados: sirius_pessoal.db
  Tabela: feedback
    id, pergunta, resposta_dada, resposta_correta, qualidade,
    fonte, timestamp

Triggers de correção reconhecidos (voz + texto):
  "isso ta errado, o certo é X"
  "errou, na verdade é X"
  "não é isso, é X"
  "corrige isso: X"
  "aprende que X"
  "memoriza que X"
  "isso tá certo" / "boa, isso tá correto"  → reforça positivo

Integração no cerebro.py:
    from sirius_feedback import SiriusFeedback
    self._feedback = SiriusFeedback(self.memoria)
    # No processar():
    resp = self._feedback.processar(comando, self._contexto_sessao, self._rag)
    if resp:
        return resp
"""

import os
import sys
import re
import sqlite3
import threading
import unicodedata
import time
from datetime import datetime
from typing import Optional

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Banco de feedback
# ---------------------------------------------------------------------------

class BancoFeedback:
    """
    Gerencia a tabela de feedback no banco pessoal.
    Persiste todas as correções e avaliações do usuário.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._inicializar()

    def _inicializar(self):
        """Cria a tabela feedback se não existir."""
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    pergunta         TEXT,
                    resposta_dada    TEXT,
                    resposta_correta TEXT,
                    qualidade        REAL    DEFAULT 0.5,
                    tipo             TEXT    DEFAULT 'correcao',
                    fonte            TEXT    DEFAULT 'usuario',
                    timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Índice para busca por pergunta
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_pergunta
                ON feedback(pergunta)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[FEEDBACK]: Erro ao inicializar banco: {e}")

    def salvar(self, pergunta: str, resposta_dada: str,
               resposta_correta: str, qualidade: float,
               tipo: str = "correcao") -> bool:
        """Salva um registro de feedback."""
        with self._lock:
            try:
                conn = sqlite3.connect(DB_PESSOAL)
                conn.execute("""
                    INSERT INTO feedback
                        (pergunta, resposta_dada, resposta_correta, qualidade, tipo)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    pergunta[:300],
                    resposta_dada[:500] if resposta_dada else "",
                    resposta_correta[:500],
                    qualidade,
                    tipo
                ))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[FEEDBACK]: Erro ao salvar: {e}")
                return False

    def buscar_correcao(self, pergunta: str) -> Optional[str]:
        """
        Busca se já existe uma correção para esta pergunta.
        Útil para evitar dar a mesma resposta errada de novo.
        """
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            row = conn.execute("""
                SELECT resposta_correta FROM feedback
                WHERE tipo IN ('correcao', 'positivo')
                  AND qualidade >= 0.8
                  AND lower(pergunta) LIKE ?
                ORDER BY qualidade DESC, id DESC
                LIMIT 1
            """, (f"%{_norm(pergunta)[:50]}%",)).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def listar_recentes(self, limit: int = 10) -> list[dict]:
        """Lista os feedbacks mais recentes para diagnóstico."""
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            rows = conn.execute("""
                SELECT pergunta, resposta_correta, qualidade, tipo, timestamp
                FROM feedback
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            return [
                {"pergunta": r[0], "correta": r[1],
                 "qualidade": r[2], "tipo": r[3], "quando": r[4]}
                for r in rows
            ]
        except Exception:
            return []

    def estatisticas(self) -> dict:
        """Retorna estatísticas do banco de feedback."""
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            correcoes = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE tipo='correcao'"
            ).fetchone()[0]
            positivos = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE tipo='positivo'"
            ).fetchone()[0]
            q_media = conn.execute(
                "SELECT AVG(qualidade) FROM feedback"
            ).fetchone()[0] or 0.0
            conn.close()
            return {
                "total":      total,
                "correcoes":  correcoes,
                "positivos":  positivos,
                "qualidade_media": round(q_media, 2),
            }
        except Exception:
            return {"total": 0, "correcoes": 0, "positivos": 0, "qualidade_media": 0.0}


# ---------------------------------------------------------------------------
# Detector de feedback — analisa o texto do usuário
# ---------------------------------------------------------------------------

class DetectorFeedback:
    """
    Detecta intenção de feedback no texto do usuário.
    Retorna um dict com tipo, conteúdo extraído e confiança.
    """

    # Triggers de CORREÇÃO negativa — o usuário diz que errou
    _TRIGGERS_CORRECAO = [
        # Diretos
        "ta errado",     "está errado",   "errou",
        "nao e isso",    "não é isso",    "nao e assim",   "não é assim",
        "nao foi isso",  "não foi isso",  "tá errado",
        # Com a resposta certa
        "na verdade e",  "na verdade é",  "na verdade:",
        "o correto e",   "o correto é",   "o certo e",     "o certo é",
        "a resposta certa e",   "a resposta certa é",
        "a resposta correta e", "a resposta correta é",
        # Ensinar
        "corrige isso",  "corrige ai",
        "aprende isso",  "aprende que",   "aprende:",
        "memoriza que",  "memoriza isso", "memoriza:",
        "salva isso",    "grava isso",    "anota isso",
        "sabe que",      "saiba que",     "sabia que",
        "quero que aprenda", "preciso que aprenda",
        # Informais
        "nao mano",      "isso nao",      "errou feio",
        "que nada",      "nada disso",    "pelo contrario",
    ]

    # Triggers de CONFIRMAÇÃO positiva — o usuário aprova a resposta
    _TRIGGERS_POSITIVO = [
        "isso mesmo",    "exato",         "correto",       "certinho",
        "isso ai",       "acertou",       "ta certo",      "está certo",
        "boa",           "perfeito",      "isso ta certo", "isso está certo",
        "ta correto",    "está correto",  "confirmado",    "sim isso",
        "exatamente",    "preciso assim", "era isso",      "foi isso",
        "muito bom",     "arrasou",       "mandou bem",
    ]

    # Separadores entre o trigger e o conteúdo correto
    _SEPARADORES = [":", " e ", " é ", " e:", " é:", " que ", ","]

    def detectar(self, texto: str) -> Optional[dict]:
        """
        Analisa o texto e retorna:
          {"tipo": "correcao"|"positivo"|None,
           "conteudo": str,        # a resposta correta extraída
           "confianca": float}     # 0.0 a 1.0
        Ou None se não for feedback.
        """
        t = _norm(texto)

        # Testa correção
        resultado = self._detectar_correcao(t, texto)
        if resultado:
            return resultado

        # Testa confirmação positiva
        resultado = self._detectar_positivo(t)
        if resultado:
            return resultado

        return None

    def _detectar_correcao(self, t_norm: str, texto_original: str) -> Optional[dict]:
        """Detecta correção e extrai o conteúdo correto."""
        for trigger in self._TRIGGERS_CORRECAO:
            if trigger not in t_norm:
                continue

            idx = t_norm.find(trigger)
            resto = t_norm[idx + len(trigger):].strip()

            # Remove separadores do início
            for sep in self._SEPARADORES:
                sep_norm = _norm(sep)
                if resto.startswith(sep_norm):
                    resto = resto[len(sep_norm):].strip()
                    break

            # Limpa pontuação inicial
            resto = re.sub(r"^[,\.\s]+", "", resto).strip()

            if len(resto) >= 8:
                # Preserva capitalização do original se possível
                idx_orig = texto_original.lower().find(trigger)
                if idx_orig >= 0:
                    conteudo = texto_original[idx_orig + len(trigger):].strip()
                    for sep in self._SEPARADORES:
                        if conteudo.lower().startswith(sep.strip()):
                            conteudo = conteudo[len(sep):].strip()
                            break
                    conteudo = re.sub(r"^[,\.\s]+", "", conteudo).strip()
                else:
                    conteudo = resto

                if len(conteudo) >= 8:
                    return {
                        "tipo":      "correcao",
                        "conteudo":  conteudo,
                        "trigger":   trigger,
                        "confianca": 0.95 if ":" in trigger else 0.80,
                    }

        return None

    def _detectar_positivo(self, t_norm: str) -> Optional[dict]:
        """Detecta aprovação da resposta anterior."""
        # Exige que seja uma frase curta — frases longas raramente são só aprovação
        if len(t_norm.split()) > 8:
            return None

        for trigger in self._TRIGGERS_POSITIVO:
            if trigger in t_norm:
                return {
                    "tipo":      "positivo",
                    "conteudo":  "",
                    "trigger":   trigger,
                    "confianca": 0.85,
                }
        return None


# ---------------------------------------------------------------------------
# SiriusFeedback — interface principal
# ---------------------------------------------------------------------------

class SiriusFeedback:
    """
    Sistema completo de feedback do Sirius.

    Responsabilidades:
      1. Detectar intenção de feedback no texto do usuário
      2. Extrair pergunta original e resposta correta do contexto
      3. Persistir no banco com qualidade adequada
      4. Injetar no RAG para uso imediato
      5. Salvar no banco de treino para retreino futuro
      6. Responder confirmando o aprendizado

    Uso:
        feedback = SiriusFeedback(memoria)
        resp = feedback.processar(comando, contexto_sessao, rag)
        if resp:
            return resp  # era um feedback, já tratado
    """

    def __init__(self, memoria=None):
        self.memoria   = memoria
        self._banco    = BancoFeedback()
        self._detector = DetectorFeedback()
        self._lock     = threading.Lock()

        # Rastreia última resposta dada (para associar à correção)
        self._ultima_pergunta  = ""
        self._ultima_resposta  = ""

    # -----------------------------------------------------------------------
    # Interface principal
    # -----------------------------------------------------------------------

    def processar(self, comando: str,
                  contexto_sessao: list[dict],
                  rag=None) -> Optional[str]:
        """
        Verifica se o comando é um feedback e processa.
        Retorna a resposta de confirmação ou None se não for feedback.
        """
        deteccao = self._detector.detectar(comando)
        if not deteccao:
            return None

        tipo      = deteccao["tipo"]
        conteudo  = deteccao["conteudo"]
        confianca = deteccao["confianca"]

        # Recupera contexto: qual pergunta estava sendo respondida
        pergunta_original, resposta_anterior = self._extrair_contexto(
            comando, contexto_sessao
        )

        if tipo == "correcao":
            return self._processar_correcao(
                pergunta_original, resposta_anterior,
                conteudo, confianca, rag
            )
        elif tipo == "positivo":
            return self._processar_positivo(
                pergunta_original, resposta_anterior, rag
            )

        return None

    def registrar_resposta(self, pergunta: str, resposta: str):
        """
        Chamado pelo cerebro.py após cada resposta para rastrear o contexto.
        Permite associar correções futuras à resposta que acabou de ser dada.
        """
        self._ultima_pergunta = pergunta
        self._ultima_resposta = resposta

    # -----------------------------------------------------------------------
    # Processamento de tipos
    # -----------------------------------------------------------------------

    def _processar_correcao(self, pergunta: str, resposta_errada: str,
                             resposta_correta: str, confianca: float,
                             rag=None) -> str:
        """
        Salva a correção com qualidade máxima em todas as fontes:
          - banco feedback (rastreamento)
          - banco conhecimento_geral (treino futuro)
          - índice RAG (uso imediato)
        """
        qualidade = min(1.0, confianca)

        # 1. Banco de feedback (histórico de correções)
        self._banco.salvar(
            pergunta        = pergunta,
            resposta_dada   = resposta_errada,
            resposta_correta= resposta_correta,
            qualidade       = qualidade,
            tipo            = "correcao"
        )

        # 2. Banco de treino (aprende para o futuro)
        if self.memoria:
            try:
                self.memoria.salvar_estudo_autonomo(
                    tema     = pergunta[:100] or "correcao_usuario",
                    conteudo = resposta_correta,
                    tags     = "feedback_usuario_qualidade_1"
                )
            except Exception as e:
                print(f"[FEEDBACK]: Erro ao salvar no banco treino: {e}")

        # 3. RAG — injeta com qualidade 1.0 para uso imediato
        if rag is not None:
            try:
                rag.adicionar_feedback(
                    pergunta         = pergunta,
                    resposta_correta = resposta_correta
                )
            except Exception as e:
                print(f"[FEEDBACK]: Erro ao injetar no RAG: {e}")

        # Log
        print(f"\033[92m[FEEDBACK]: Correção salva — "
              f"pergunta='{pergunta[:40]}' | correta='{resposta_correta[:50]}'\033[0m")

        # Resposta de confirmação
        if pergunta and len(pergunta) > 5:
            return (f"Anotado, chefia. Sobre '{pergunta[:60]}': "
                    f"{resposta_correta[:200]}. "
                    f"Vou lembrar disso.")
        return f"Anotado! Aprendi que: {resposta_correta[:200]}."

    def _processar_positivo(self, pergunta: str, resposta: str,
                             rag=None) -> str:
        """
        Reforço positivo — aumenta a qualidade da resposta anterior no banco.
        """
        if not resposta or len(resposta) < 15:
            return "Valeu, chefia! Fico feliz que ajudou."

        # Salva como confirmação (qualidade 0.9)
        self._banco.salvar(
            pergunta         = pergunta,
            resposta_dada    = resposta,
            resposta_correta = resposta,
            qualidade        = 0.9,
            tipo             = "positivo"
        )

        # Salva no banco de treino com tag de qualidade
        if self.memoria:
            try:
                self.memoria.salvar_estudo_autonomo(
                    tema     = pergunta[:100] or "confirmacao_usuario",
                    conteudo = resposta,
                    tags     = "feedback_positivo_qualidade_09"
                )
            except Exception:
                pass

        # Reforça no RAG também
        if rag is not None and pergunta:
            try:
                rag.adicionar_feedback(
                    pergunta         = pergunta,
                    resposta_correta = resposta
                )
            except Exception:
                pass

        print(f"\033[92m[FEEDBACK]: Confirmação positiva salva — "
              f"'{pergunta[:40]}'\033[0m")

        respostas_confirmacao = [
            "Boa! Anotei que essa resposta tá certa.",
            "Tmj, chefia! Vou usar essa como referência.",
            "Registrado. Próxima vez já sei o caminho.",
            "Valeu pelo feedback! Isso me ajuda a acertar mais.",
        ]
        import random
        return random.choice(respostas_confirmacao)

    # -----------------------------------------------------------------------
    # Extração de contexto
    # -----------------------------------------------------------------------

    def _extrair_contexto(self, comando_atual: str,
                          contexto_sessao: list[dict]) -> tuple[str, str]:
        """
        Recupera a última pergunta do usuário e a última resposta do Sirius
        do contexto de sessão, excluindo o comando atual (que é o feedback).

        Retorna (pergunta_original, resposta_sirius).
        """
        pergunta_original = self._ultima_pergunta
        resposta_anterior = self._ultima_resposta

        # Percorre o contexto de sessão de trás para frente
        # buscando o par user/assistant mais recente antes do feedback
        if contexto_sessao:
            ultimo_user      = ""
            ultimo_assistant = ""
            for msg in reversed(contexto_sessao):
                content = msg.get("content", "")
                role    = msg.get("role", "")

                # Pula o próprio comando de feedback
                if content.strip().lower() == comando_atual.strip().lower():
                    continue

                if role == "assistant" and not ultimo_assistant:
                    ultimo_assistant = content
                elif role == "user" and ultimo_assistant and not ultimo_user:
                    ultimo_user = content
                    break  # encontrou o par completo

            if ultimo_user:
                pergunta_original = ultimo_user
            if ultimo_assistant:
                resposta_anterior = ultimo_assistant

        return pergunta_original, resposta_anterior

    # -----------------------------------------------------------------------
    # Consulta e diagnóstico
    # -----------------------------------------------------------------------

    def buscar_correcao(self, pergunta: str) -> Optional[str]:
        """
        Verifica se o usuário já corrigiu o Sirius sobre este tema.
        Usado pelo cerebro.py para evitar repetir erros conhecidos.
        """
        return self._banco.buscar_correcao(pergunta)

    def status(self) -> str:
        """Retorna status legível do sistema de feedback."""
        stats = self._banco.estatisticas()
        return (
            f"Feedback: {stats['total']} registros total. "
            f"{stats['correcoes']} correções, {stats['positivos']} confirmações. "
            f"Qualidade média: {stats['qualidade_media']:.0%}."
        )

    def listar_recentes(self, limit: int = 5) -> str:
        """Lista as correções mais recentes em formato legível."""
        recentes = self._banco.listar_recentes(limit)
        if not recentes:
            return "Nenhum feedback registrado ainda."

        linhas = [f"Últimas {len(recentes)} correções:"]
        for r in recentes:
            tipo_icon = "✓" if r["tipo"] == "positivo" else "✗"
            linhas.append(
                f"  {tipo_icon} [{r['tipo']}] '{r['pergunta'][:40]}' → "
                f"'{r['correta'][:60]}'"
            )
        return "\n".join(linhas)

    def exportar_para_treino(self) -> list[dict]:
        """
        Exporta todos os feedbacks de alta qualidade para uso no retreino.
        Retorna lista de dicts com pergunta/resposta/qualidade.
        """
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            rows = conn.execute("""
                SELECT pergunta, resposta_correta, qualidade
                FROM feedback
                WHERE qualidade >= 0.8
                ORDER BY qualidade DESC, id DESC
            """).fetchall()
            conn.close()
            return [
                {"pergunta": r[0], "resposta": r[1], "qualidade": r[2]}
                for r in rows
            ]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_feedback_instance: Optional[SiriusFeedback] = None

def get_feedback(memoria=None) -> SiriusFeedback:
    global _feedback_instance
    if _feedback_instance is None:
        _feedback_instance = SiriusFeedback(memoria)
    return _feedback_instance


# ---------------------------------------------------------------------------
# Standalone — testa o sistema de feedback
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sistema de feedback do Sirius")
    parser.add_argument("--status",   action="store_true", help="Status do banco")
    parser.add_argument("--listar",   action="store_true", help="Lista feedbacks recentes")
    parser.add_argument("--testar",   type=str, metavar="TEXTO",
                        help="Testa detecção de feedback em um texto")
    parser.add_argument("--exportar", action="store_true",
                        help="Exporta feedbacks de alta qualidade")
    args = parser.parse_args()

    fb = SiriusFeedback()

    if args.status or not any([args.listar, args.testar, args.exportar]):
        print("\n" + fb.status())

    if args.listar:
        print("\n" + fb.listar_recentes(10))

    if args.testar:
        detector = DetectorFeedback()
        resultado = detector.detectar(args.testar)
        print(f"\nTexto: '{args.testar}'")
        if resultado:
            print(f"Tipo:      {resultado['tipo']}")
            print(f"Conteúdo:  {resultado['conteudo']}")
            print(f"Trigger:   {resultado['trigger']}")
            print(f"Confiança: {resultado['confianca']:.0%}")
        else:
            print("Não detectado como feedback.")

    if args.exportar:
        dados = fb.exportar_para_treino()
        print(f"\n{len(dados)} feedbacks de alta qualidade:")
        for d in dados[:10]:
            print(f"  q={d['qualidade']:.1f} | '{d['pergunta'][:50]}' → '{d['resposta'][:60]}'")