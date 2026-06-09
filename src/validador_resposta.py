"""
S.I.R.I.U.S. v5.2 — VALIDADOR DE RESPOSTAS
Garante que respostas são coerentes, relevantes e de qualidade
"""

from sirius_logging import get_logger, LogContext
from typing import Tuple, Optional, List
import re

logger = get_logger(__name__)

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

CONFIANCA_MINIMA = 0.65       # Confiança mínima para aceitar resposta
RELEVANCIA_MINIMA = 0.70      # Score mínimo de relevância
QUALIDADE_MINIMA = 0.60       # Score mínimo de qualidade
MAX_RETRIES = 3               # Máximo de tentativas antes de fallback

# ============================================================================
# VALIDADORES
# ============================================================================

class ValidadorResposta:
    """Valida qualidade de respostas do S.I.R.I.U.S."""
    
    @staticmethod
    def validar_confianca(confianca: float) -> Tuple[bool, str]:
        """Verifica se confiança está acima do mínimo."""
        if confianca < CONFIANCA_MINIMA:
            return False, f"Confiança baixa ({confianca:.1%} < {CONFIANCA_MINIMA:.1%})"
        return True, "✓ Confiança OK"
    
    @staticmethod
    def validar_comprimento(resposta: str) -> Tuple[bool, str]:
        """Verifica se resposta tem comprimento razoável."""
        palavras = len(resposta.split())
        
        if palavras < 3:
            return False, f"Resposta muito curta ({palavras} palavras)"
        
        if palavras > 1000:
            return False, f"Resposta muito longa ({palavras} palavras)"
        
        return True, f"✓ Comprimento OK ({palavras} palavras)"
    
    @staticmethod
    def validar_coerencia(resposta: str, pergunta: str) -> Tuple[bool, str]:
        """Verifica se resposta é coerente com pergunta."""

        # Saudações e interações curtas são coerentes por definição
        _SAUDACOES = frozenset({
            "bom dia", "boa tarde", "boa noite", "oi", "olá", "ola",
            "eae", "e ai", "e aí", "salve", "tudo bem", "tudo bom",
            "como vai", "como estás", "boa", "hey", "hi", "hello",
        })
        pergunta_norm = pergunta.lower().strip()
        if any(pergunta_norm.startswith(s) or pergunta_norm == s for s in _SAUDACOES):
            return True, "✓ Coerência OK (saudação)"

        palavras_pergunta = set(pergunta.lower().split())

        # Perguntas muito curtas (≤ 3 palavras) — overlap literal não é confiável
        if len(palavras_pergunta) <= 3:
            return True, "✓ Coerência OK (pergunta curta)"

        palavras_resposta = set(resposta.lower().split())

        # Stopwords não contribuem para coerência semântica
        _STOPWORDS = frozenset({
            "o", "a", "os", "as", "um", "uma", "de", "da", "do", "dos", "das",
            "em", "no", "na", "nos", "nas", "por", "para", "com", "que", "se",
            "e", "é", "eu", "tu", "você", "ele", "ela", "nós", "sim", "não",
        })
        kw_pergunta = palavras_pergunta - _STOPWORDS
        kw_resposta = palavras_resposta - _STOPWORDS

        if not kw_pergunta:
            return True, "✓ Coerência OK (sem keywords)"

        taxa_overlap = len(kw_pergunta & kw_resposta) / len(kw_pergunta)

        if taxa_overlap < 0.1:
            logger.warning(f"Overlap baixo: {taxa_overlap:.1%}")
            return False, f"Resposta desconectada da pergunta (overlap: {taxa_overlap:.1%})"

        return True, f"✓ Coerência OK (overlap: {taxa_overlap:.1%})"
    
    @staticmethod
    def validar_sem_autodialogo(resposta: str) -> Tuple[bool, str]:
        """Remove e valida presença de autodialógo."""
        
        # Padrões de autodialógo
        padroes_auto = [
            r'\[autodialogo\]',
            r'\[pensamento\]',
            r'\[interno\]',
            r'>> ',
            r'<<',
            # Reticências removidas — aparecem em respostas legítimas
        ]
        
        for padrao in padroes_auto:
            if re.search(padrao, resposta, re.IGNORECASE):
                return False, f"Contém autodialógo indesejado"
        
        return True, "✓ Sem autodialógo"
    
    @staticmethod
    def validar_sem_multiplos_tons(resposta: str) -> Tuple[bool, str]:
        """Valida que resposta mantém tom consistente."""
        
        # Detectar mudanças abruptas de tom
        linhas = resposta.split('.')
        
        # Verificar mistura excessiva de formalismos
        formal_markers = sum(1 for l in linhas if 
                            any(x in l.lower() for x in ['senhor', 'senhora', 'prezado']))
        informal_markers = sum(1 for l in linhas if 
                              any(x in l.lower() for x in ['mano', 'eae', 'opa', 'blz', 'tchau', 'parca', 'parceiro']))
        # Se misturar muito formal com muito informal é suspeito
        if formal_markers > 0 and informal_markers > 2:
            return False, f"Tom inconsistente (mistura {formal_markers} formal + {informal_markers} informal)"
        
        return True, "✓ Tom consistente"
    
    @staticmethod
    def validar_sem_repeticao(resposta: str) -> Tuple[bool, str]:
        """Valida ausência de repetições excessivas."""
        
        palavras = resposta.split()
        
        # Contar repetições
        from collections import Counter
        contador = Counter(palavras)
        
        # Se uma palavra aparece mais de 30% das vezes, é repetição
        max_freq = max(contador.values()) if contador else 0
        taxa_max = max_freq / len(palavras) if palavras else 0
        
        if taxa_max > 0.3:
            palavra_mais_frequente = contador.most_common(1)[0][0]
            return False, f"Repetição excessiva: '{palavra_mais_frequente}' ({taxa_max:.1%})"
        
        return True, "✓ Sem repetição excessiva"
    
    @staticmethod
    def validar_sem_multiplos_falantes(resposta: str) -> Tuple[bool, str]:
        """Detecta respostas que misturam falas de múltiplos interlocutores."""
        marcadores = [
            'mensagem enviada para',
            'mensagem enviada',
        ]
        for m in marcadores:
            if m in resposta.lower():
                return False, f"Resposta com múltiplos contextos: '{m}'"
        # Detecta sequências suspeitas: tchau + boa noite em contexto de bom dia
        if 'tchau' in resposta.lower() and 'bom dia' in resposta.lower():
            return False, "Resposta mistura contextos incompatíveis (tchau + bom dia)"
        if 'parca' in resposta.lower() or 'parça' in resposta.lower():
            return False, "Resposta com gíria de chat (parça)"
        return True, "✓ Falante único"

    @staticmethod
    def limpar_resposta(resposta: str) -> str:
        """Limpa resposta de artefatos e remove contaminação de contexto cruzado."""

        # ── Corta contaminação de múltiplos contextos ─────────────────────────
        # Se a resposta mistura contextos (histórico de WhatsApp/Discord vazando),
        # mantém apenas o trecho antes do primeiro marcador de contaminação.
        _CORTES = [
            'mensagem enviada para',
            'mensagem enviada',
        ]
        resposta_lower = resposta.lower()
        for marcador in _CORTES:
            idx = resposta_lower.find(marcador)
            if idx > 0:
                # Corta tudo a partir do marcador
                resposta = resposta[:idx].strip().rstrip('.,;:')
                resposta_lower = resposta.lower()
                break

        # Corta em 'parça' / 'parca' (gíria de chat que não deveria estar aqui)
        for giria in ('parça', 'parca'):
            idx = resposta_lower.find(giria)
            if idx > 0:
                resposta = resposta[:idx].strip().rstrip('.,;:')
                break

        # ── Remove padrões de autodialógo ─────────────────────────────────────
        resposta = re.sub(r'\[autodialogo\].*?\n', '', resposta, flags=re.IGNORECASE)
        resposta = re.sub(r'\[pensamento\].*?\n', '', resposta, flags=re.IGNORECASE)
        resposta = re.sub(r'\[interno\].*?\n', '', resposta, flags=re.IGNORECASE)

        # ── Normaliza espaços ──────────────────────────────────────────────────
        resposta = re.sub(r'\s+', ' ', resposta)
        resposta = resposta.strip()

        return resposta

# ============================================================================
# SCORE DE QUALIDADE
# ============================================================================

class ScorerQualidade:
    """Calcula score de qualidade da resposta."""
    
    @staticmethod
    def calcular_score(
        resposta: str,
        pergunta: str,
        confianca: float
    ) -> Tuple[float, List[str]]:
        """Calcula score geral de qualidade (0-1)."""
        
        scores = {}
        observacoes = []
        
        # Score de confiança (peso: 0.4)
        scores['confianca'] = min(1.0, confianca / 0.90) if confianca > 0 else 0
        
        # Score de comprimento (peso: 0.1)
        palavras = len(resposta.split())
        if 5 <= palavras <= 300:
            scores['comprimento'] = 1.0
        elif 3 <= palavras <= 500:
            scores['comprimento'] = 0.8
        else:
            scores['comprimento'] = 0.3
        
        # Score de coerência com pergunta (peso: 0.3)
        # Saudações e perguntas curtas recebem score cheio — overlap literal não funciona
        _SAUDACOES_SCORER = frozenset({
            "bom dia", "boa tarde", "boa noite", "oi", "olá", "ola",
            "eae", "e ai", "e aí", "salve", "tudo bem", "tudo bom",
            "como vai", "boa", "hey", "hi", "hello",
        })
        _STOPWORDS_SCORER = frozenset({
            "o", "a", "os", "as", "um", "uma", "de", "da", "do", "dos", "das",
            "em", "no", "na", "nos", "nas", "por", "para", "com", "que", "se",
            "e", "é", "eu", "tu", "você", "ele", "ela", "nós", "sim", "não",
        })
        pergunta_norm = pergunta.lower().strip()
        palavras_pergunta = set(pergunta_norm.split())
        eh_saudacao = any(pergunta_norm.startswith(s) or pergunta_norm == s for s in _SAUDACOES_SCORER)
        if eh_saudacao or len(palavras_pergunta) <= 3:
            scores['coerencia'] = 1.0
        else:
            kw_pergunta = palavras_pergunta - _STOPWORDS_SCORER
            kw_resposta = set(resposta.lower().split()) - _STOPWORDS_SCORER
            overlap = len(kw_pergunta & kw_resposta) / len(kw_pergunta) if kw_pergunta else 1.0
            scores['coerencia'] = min(1.0, overlap * 2)
        
        # Score de consistência de tom (peso: 0.2)
        formal = sum(1 for word in resposta.lower().split() if word in ['senhor', 'senhora', 'prezado'])
        informal = sum(1 for word in resposta.lower().split() if word in ['mano', 'eae', 'opa'])
        
        if formal > 0 and informal > 3:
            scores['consistencia'] = 0.4
        elif formal == 0 and informal == 0:
            scores['consistencia'] = 0.8  # Neutro é ok
        else:
            scores['consistencia'] = 0.9
        
        # Calcular score ponderado
        pesos = {
            'confianca': 0.40,
            'comprimento': 0.10,
            'coerencia': 0.30,
            'consistencia': 0.20,
        }
        
        score_final = sum(scores.get(k, 0) * v for k, v in pesos.items())
        
        # Gerar observações
        if scores['confianca'] < 0.7:
            observacoes.append("Confiança da predição baixa")
        if scores['comprimento'] < 0.7:
            observacoes.append("Comprimento inadequado")
        if scores['coerencia'] < 0.6:
            observacoes.append("Baixa relevância com pergunta")
        if scores['consistencia'] < 0.7:
            observacoes.append("Ton inconsistente")
        
        return score_final, observacoes

# ============================================================================
# VALIDADOR COMPLETO
# ============================================================================

class ValidadorCompleto:
    """Valida resposta completa."""
    
    def validar(
        self,
        resposta: str,
        pergunta: str,
        confianca: float,
        debug: bool = False
    ) -> Tuple[bool, str, float, List[str]]:
        """
        Valida resposta completamente.
        
        Returns:
            (é_valida, resposta_limpa, score, observacoes)
        """
        
        observacoes = []
        
        with LogContext("Validação de resposta", logger):
            # Limpar resposta
            resposta_limpa = ValidadorResposta.limpar_resposta(resposta)
            logger.debug(f"Resposta original: {resposta[:100]}...")
            logger.debug(f"Resposta limpa: {resposta_limpa[:100]}...")
            
            # Validações individuais
            validacoes = [
                ValidadorResposta.validar_confianca(confianca),
                ValidadorResposta.validar_comprimento(resposta_limpa),
                ValidadorResposta.validar_coerencia(resposta_limpa, pergunta),
                ValidadorResposta.validar_sem_autodialogo(resposta_limpa),
                ValidadorResposta.validar_sem_multiplos_tons(resposta_limpa),
                ValidadorResposta.validar_sem_repeticao(resposta_limpa),
                ValidadorResposta.validar_sem_multiplos_falantes(resposta_limpa),
            ]
            
            # Registrar resultados
            for ok, msg in validacoes:
                logger.debug(msg)
                if not ok:
                    observacoes.append(msg)
            
            # Calcular score
            score, obs_score = ScorerQualidade.calcular_score(
                resposta_limpa,
                pergunta,
                confianca
            )
            observacoes.extend(obs_score)
            
            # Determinar se é válida
            eh_valida = (
                score >= QUALIDADE_MINIMA and
                all(ok for ok, _ in validacoes)
            )
            
            logger.info(f"Score: {score:.2f}, Válida: {eh_valida}")
            
            return eh_valida, resposta_limpa, score, observacoes

# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    validador = ValidadorCompleto()
    
    # Teste 1: Resposta boa
    print("\n" + "="*70)
    print("TESTE 1: Resposta Boa")
    print("="*70)
    
    pergunta = "Qual é a capital da França?"
    resposta_boa = "A capital da França é Paris. Paris é uma cidade histórica e importante na Europa."
    
    valida, limpa, score, obs = validador.validar(resposta_boa, pergunta, 0.85)
    print(f"Válida: {valida}, Score: {score:.2f}")
    if obs:
        print(f"Observações: {obs}")
    
    # Teste 2: Resposta ruim
    print("\n" + "="*70)
    print("TESTE 2: Resposta Ruim (incoerente)")
    print("="*70)
    
    resposta_ruim = "A psiquiatria é a psiquiatria. [autodialogo] bom dia mano eae opa opa opa!"
    
    valida, limpa, score, obs = validador.validar(resposta_ruim, pergunta, 0.3)
    print(f"Válida: {valida}, Score: {score:.2f}")
    if obs:
        print(f"Observações: {obs}")