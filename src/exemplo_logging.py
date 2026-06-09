"""
S.I.R.I.U.S. v5.2 — EXEMPLO: Módulo com Logging Integrado
Mostra como integrar o novo sistema de logging em um módulo real
"""

from sirius_logging import get_logger, LogContext
from typing import Optional
import json

# Obter logger para este módulo
logger = get_logger(__name__)

# ============================================================================
# EXEMPLO 1: CLASSE COM INICIALIZAÇÃO E LOGGING
# ============================================================================

class ExemploModulo:
    """Exemplo de classe com logging integrado."""
    
    def __init__(self, nome: str):
        """Inicializa com logging."""
        self.nome = nome
        logger.info(f"Inicializando {self.__class__.__name__}: {nome}")
        
        try:
            # Simular inicialização
            self.dados = {}
            logger.debug(f"Atributos inicializados")
            
            logger.info(f"{self.__class__.__name__} pronto: {nome}")
        except Exception as e:
            logger.error(f"Falha ao inicializar {self.__class__.__name__}", exc_info=True)
            raise
    
    # ────────────────────────────────────────────────────────────────────
    # EXEMPLO 2: MÉTODO COM CONTEXTO
    # ────────────────────────────────────────────────────────────────────
    
    def processar_dados(self, entrada: dict) -> dict:
        """Processa dados com logging estruturado."""
        with LogContext(f"Processamento de dados ({self.nome})", logger):
            logger.info(f"Entrada: {len(entrada)} campos")
            
            try:
                # Simular processamento
                resultado = {}
                for chave, valor in entrada.items():
                    resultado[chave] = self._processar_campo(chave, valor)
                
                logger.info(f"Saída: {len(resultado)} campos processados")
                return resultado
            
            except Exception as e:
                logger.error(f"Erro no processamento", exc_info=True)
                raise
    
    def _processar_campo(self, chave: str, valor: str) -> str:
        """Processa campo individual."""
        logger.debug(f"Processando campo: {chave} = {valor[:30]}")
        # Simular processamento
        resultado = valor.upper()
        logger.debug(f"Campo processado: {resultado[:30]}")
        return resultado
    
    # ────────────────────────────────────────────────────────────────────
    # EXEMPLO 3: MÉTODO COM VALIDAÇÃO E AVISOS
    # ────────────────────────────────────────────────────────────────────
    
    def validar(self, dados: dict) -> bool:
        """Valida dados com logging de avisos."""
        logger.info("Iniciando validação de dados")
        
        avisos = []
        
        if not dados:
            logger.warning("Dados vazios")
            avisos.append("Dados vazios")
        
        if 'requerido' not in dados:
            logger.warning("Campo obrigatório 'requerido' ausente")
            avisos.append("Campo 'requerido' ausente")
        
        if len(dados) > 100:
            logger.warning(f"Muitos campos: {len(dados)} (limite: 100)")
            avisos.append(f"Número de campos acima do limite")
        
        if avisos:
            logger.warning(f"Validação completada com {len(avisos)} aviso(s)")
            for aviso in avisos:
                logger.debug(f"  - {aviso}")
            return False
        
        logger.info("Validação bem-sucedida")
        return True
    
    # ────────────────────────────────────────────────────────────────────
    # EXEMPLO 4: OPERAÇÃO COM RETRY E LOGGING
    # ────────────────────────────────────────────────────────────────────
    
    def salvar(self, dados: dict, retries: int = 3) -> bool:
        """Salva dados com retry e logging detalhado."""
        logger.info(f"Salvando dados ({len(dados)} campos)")
        
        for tentativa in range(1, retries + 1):
            try:
                logger.debug(f"Tentativa {tentativa}/{retries}")
                
                # Simular salvamento
                self.dados = dados.copy()
                
                logger.info(f"Dados salvos com sucesso (tentativa {tentativa})")
                return True
            
            except Exception as e:
                if tentativa < retries:
                    logger.warning(f"Tentativa {tentativa} falhou: {e}. Retry...")
                else:
                    logger.error(f"Falha após {retries} tentativas", exc_info=True)
                    return False
        
        return False

# ============================================================================
# EXEMPLO 5: FUNÇÕES COM LOGGING
# ============================================================================

def funcao_com_logging(parametro: str) -> bool:
    """Exemplo de função com logging."""
    logger.info(f"Chamado funcao_com_logging('{parametro}')")
    
    try:
        logger.debug(f"Parâmetro: {parametro}")
        
        # Simular processamento
        if not parametro:
            logger.warning("Parâmetro vazio")
            return False
        
        logger.info("Processamento concluído")
        return True
    
    except Exception as e:
        logger.error("Erro durante processamento", exc_info=True)
        return False

# ============================================================================
# EXEMPLO 6: DECORATOR PARA LOGGING AUTOMÁTICO
# ============================================================================

def log_function_calls(func):
    """Decorator que loga chamadas de função automaticamente."""
    def wrapper(*args, **kwargs):
        logger.info(f"Chamando {func.__name__}")
        logger.debug(f"  args: {args}")
        logger.debug(f"  kwargs: {kwargs}")
        
        try:
            resultado = func(*args, **kwargs)
            logger.info(f"{func.__name__} retornou sucesso")
            return resultado
        except Exception as e:
            logger.error(f"{func.__name__} levantou exceção", exc_info=True)
            raise
    
    return wrapper

@log_function_calls
def funcao_decorada(x: int, y: int) -> int:
    """Função decorada com logging automático."""
    return x + y

# ============================================================================
# EXEMPLO 7: OPERAÇÕES EM BATCH
# ============================================================================

def processar_batch(itens: list) -> dict:
    """Processa lote de itens com logging detalhado."""
    logger.info(f"Iniciando processamento em batch: {len(itens)} itens")
    
    sucessos = 0
    falhas = 0
    
    for idx, item in enumerate(itens, 1):
        try:
            logger.debug(f"Processando item {idx}/{len(itens)}: {item[:20]}")
            # Simular processamento
            sucesso = True
            
            if sucesso:
                sucessos += 1
                logger.debug(f"Item {idx} OK")
            else:
                falhas += 1
                logger.warning(f"Item {idx} falhou")
        
        except Exception as e:
            falhas += 1
            logger.error(f"Erro ao processar item {idx}: {e}")
    
    logger.info(f"Batch concluído: {sucessos} OK, {falhas} falhas")
    return {
        "total": len(itens),
        "sucessos": sucessos,
        "falhas": falhas
    }

# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*70)
    print("S.I.R.I.U.S. — EXEMPLO DE LOGGING INTEGRADO")
    print("="*70 + "\n")
    
    # Exemplo 1: Criar instância
    print(">>> Exemplo 1: Criar instância com logging")
    modulo = ExemploModulo("Teste")
    
    # Exemplo 2: Processar dados
    print("\n>>> Exemplo 2: Processar dados")
    dados = {
        "nome": "carlos",
        "projeto": "sirius",
        "versao": "5.2"
    }
    resultado = modulo.processar_dados(dados)
    
    # Exemplo 3: Validar
    print("\n>>> Exemplo 3: Validar dados")
    modulo.validar({"requerido": "valor"})
    modulo.validar({})  # Vai gerar avisos
    
    # Exemplo 4: Salvar
    print("\n>>> Exemplo 4: Salvar dados")
    modulo.salvar({"dado1": "valor1", "dado2": "valor2"})
    
    # Exemplo 5: Função com logging
    print("\n>>> Exemplo 5: Função com logging")
    funcao_com_logging("teste")
    
    # Exemplo 6: Decorator
    print("\n>>> Exemplo 6: Função decorada")
    resultado = funcao_decorada(5, 3)
    print(f"Resultado: {resultado}")
    
    # Exemplo 7: Batch
    print("\n>>> Exemplo 7: Processamento em batch")
    itens = ["item1", "item2", "item3", "item4", "item5"]
    stats = processar_batch(itens)
    print(f"Stats: {stats}")
    
    print("\n" + "="*70)
    print("✅ Exemplos concluídos!")
    print("📄 Verifique os logs em: logs/sirius_*.log")
    print("="*70 + "\n")