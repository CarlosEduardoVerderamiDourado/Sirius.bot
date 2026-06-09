"""
S.I.R.I.U.S. v5.2 — ESTRATÉGIA DE RETRY COM VALIDAÇÃO
Tenta novamente se resposta for de baixa qualidade
"""

from sirius_logging import get_logger, LogContext
from validador_resposta import ValidadorCompleto, QUALIDADE_MINIMA
from typing import Tuple, Optional, Callable

logger = get_logger(__name__)

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

MAX_TENTATIVAS = 3
FALLBACK_MENSAGEM = (
    "Desculpe, não consegui processar bem essa pergunta. "
    "Pode tentar reformular?"
)

# ============================================================================
# GERENCIADOR DE RETRY
# ============================================================================

class RetryManager:
    """Gerencia tentativas de gerar resposta válida."""
    
    def __init__(self, max_tentativas: int = MAX_TENTATIVAS):
        self.max_tentativas = max_tentativas
        self.validador = ValidadorCompleto()
    
    def gerar_com_validacao(
        self,
        gerador_funcao: Callable,
        pergunta: str,
        debug: bool = False
    ) -> Tuple[str, float, bool]:
        """
        Tenta gerar resposta válida com retries.
        
        Args:
            gerador_funcao: Função que gera a resposta
            pergunta: Pergunta do usuário
            debug: Se True, loga tudo em detalhes
        
        Returns:
            (resposta, score, sucesso)
        """
        
        with LogContext(f"Geração com validação ({self.max_tentativas} tentativas)", logger):
            melhor_resposta = None
            melhor_score = 0
            melhor_confianca = 0
            
            for tentativa in range(1, self.max_tentativas + 1):
                logger.info(f"Tentativa {tentativa}/{self.max_tentativas}")
                
                try:
                    # Gerar resposta
                    resposta, confianca = gerador_funcao()
                    logger.debug(f"Gerada: {resposta[:100]}... (confiança: {confianca:.1%})")
                    
                    # Validar
                    eh_valida, resposta_limpa, score, observacoes = self.validador.validar(
                        resposta,
                        pergunta,
                        confianca,
                        debug=debug
                    )
                    
                    logger.info(f"Score: {score:.2f}, Válida: {eh_valida}")
                    
                    # Se melhor que temos, guardar
                    if score > melhor_score:
                        melhor_resposta = resposta_limpa
                        melhor_score = score
                        melhor_confianca = confianca
                    
                    # Se válida, retornar imediatamente
                    if eh_valida:
                        logger.info(f"✅ Resposta válida encontrada (tentativa {tentativa})")
                        return resposta_limpa, score, True
                    
                    # Se não válida, observações
                    if observacoes and debug:
                        logger.warning(f"Motivo da rejeição: {', '.join(observacoes)}")
                    
                except Exception as e:
                    logger.error(f"Erro ao gerar resposta", exc_info=True)
                    continue
            
            # Nenhuma válida encontrada
            logger.warning(f"Nenhuma resposta válida em {self.max_tentativas} tentativas")
            logger.warning(f"Melhor score: {melhor_score:.2f} (mínimo: {QUALIDADE_MINIMA:.2f})")
            
            if melhor_resposta and melhor_score > 0.4:
                # Se temos algo razoável, usar com cautela
                logger.warning("Retornando melhor resposta encontrada (abaixo do mínimo)")
                return melhor_resposta, melhor_score, False
            else:
                # Fallback
                logger.error("Retornando fallback genérico")
                return FALLBACK_MENSAGEM, 0.0, False

# ============================================================================
# INTEGRAÇÃO COM CEREBRO
# ============================================================================

class CerebroComValidacao:
    """
    Exemplo de como integrar validação ao Cérebro.
    Substitui a geração simples por geração com validação.
    """
    
    def __init__(self, cerebro_original):
        """
        Args:
            cerebro_original: Instância original do Sirius Cerebro
        """
        self.cerebro = cerebro_original
        self.retry_manager = RetryManager()
    
    def processar_com_validacao(
        self,
        texto: str,
        debug: bool = False
    ) -> str:
        """
        Processa texto com validação de resposta.
        
        Fluxo:
        1. Classificar intenção
        2. Gerar resposta (com retry se ruim)
        3. Falar (opcional)
        """
        
        logger.info(f"Processando: {texto[:100]}")
        
        with LogContext("Processamento completo com validação"):
            # Passo 1: Classificar
            tema, confianca = self.cerebro.neuronio.predizer(texto, debug=debug)
            logger.info(f"Tema: {tema} ({confianca:.1%})")
            
            # Passo 2: Gerar com validação
            def _gerar():
                """Função geradora que o RetryManager vai chamar."""
                resposta = self.cerebro._gerar_resposta(tema)
                return resposta, confianca
            
            resposta_final, score_resposta, sucesso = self.retry_manager.gerar_com_validacao(
                _gerar,
                texto,
                debug=debug
            )
            
            # Log da qualidade
            if sucesso:
                logger.info(f"✅ Resposta de qualidade (score: {score_resposta:.2f})")
            else:
                logger.warning(f"⚠️  Resposta degradada (score: {score_resposta:.2f})")
            
            return resposta_final

# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    from validador_resposta import ValidadorCompleto
    
    print("\n" + "="*70)
    print("S.I.R.I.U.S. — RETRY COM VALIDAÇÃO")
    print("="*70 + "\n")
    
    # Simular gerador
    tentativas = 0
    
    def gerador_ruim():
        """Gera resposta ruim na primeira, boa na segunda."""
        global tentativas
        tentativas += 1
        
        if tentativas == 1:
            return (
                "[autodialogo] pensando... blz blz blz. psiquiatria transtornos mentais eae mano opa!",
                0.3
            )
        else:
            return (
                "A capital da França é Paris, uma cidade histórica muito importante.",
                0.85
            )
    
    retry_mgr = RetryManager(max_tentativas=3)
    
    resposta, score, sucesso = retry_mgr.gerar_com_validacao(
        gerador_ruim,
        "Qual é a capital da França?",
        debug=True
    )
    
    print(f"\n✅ Resultado Final:")
    print(f"   Resposta: {resposta}")
    print(f"   Score: {score:.2f}")
    print(f"   Sucesso: {sucesso}")