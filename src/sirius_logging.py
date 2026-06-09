"""
S.I.R.I.U.S. v5.2 — SISTEMA DE LOGGING CENTRALIZADO
Módulo profissional com rotação, cores e estrutura
"""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime
from typing import Optional

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / f"sirius_{datetime.now().strftime('%Y%m%d')}.log"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 10  # Manter 10 logs antigos
LOG_LEVEL = logging.DEBUG  # Nível padrão

# ============================================================================
# CORES PARA CONSOLE
# ============================================================================

class Colors:
    """Códigos ANSI para cores no terminal."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Cores
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Cores de fundo
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'

# ============================================================================
# FORMATTER CUSTOMIZADO COM CORES
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """Formatter que adiciona cores baseado no nível de log."""
    
    # Cores por nível
    COLORS = {
        logging.DEBUG: Colors.CYAN,
        logging.INFO: Colors.GREEN,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.BG_RED + Colors.WHITE,
    }
    
    # Símbolos por nível
    SYMBOLS = {
        logging.DEBUG: "🔍",
        logging.INFO: "ℹ️ ",
        logging.WARNING: "⚠️ ",
        logging.ERROR: "❌",
        logging.CRITICAL: "🚨",
    }
    
    def format(self, record):
        """Formata log com cor e símbolo."""
        # Adicionar cor ao nível
        levelname = record.levelname
        color = self.COLORS.get(record.levelno, Colors.WHITE)
        symbol = self.SYMBOLS.get(record.levelno, "•")
        
        # Criar levelname colorido
        colored_levelname = f"{color}{symbol} {levelname}{Colors.RESET}"
        record.levelname = colored_levelname
        
        # Colorir módulo
        record.name = f"{Colors.BLUE}{record.name}{Colors.RESET}"
        
        # Chamar formatter original
        result = super().format(record)
        
        # Restaurar levelname original (para arquivo)
        record.levelname = levelname
        
        return result

# ============================================================================
# FORMATTER PARA ARQUIVO (SEM CORES)
# ============================================================================

class FileFormatter(logging.Formatter):
    """Formatter estruturado para arquivo de log."""
    
    def format(self, record):
        """Formata log estruturado para arquivo."""
        # Adicionar informações extras
        if not hasattr(record, 'elapsed'):
            record.elapsed = f"{record.msecs:.0f}ms"
        
        return super().format(record)

# ============================================================================
# LOGGER CENTRALIZADO
# ============================================================================

class SiriusLogger:
    """Logger centralizado para S.I.R.I.U.S."""
    
    _instance = None
    _loggers = {}
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Inicializa logger."""
        if self._initialized:
            return
        
        self._initialized = True
        self._setup_logging()
    
    @staticmethod
    def _setup_logging():
        """Configura o sistema de logging."""
        # Criar diretório de logs se não existir
        LOG_DIR.mkdir(exist_ok=True)
        
        # Configurar root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(LOG_LEVEL)
        
        # ────────────────────────────────────────────────────────────────
        # HANDLER: Console (colorido)
        # ────────────────────────────────────────────────────────────────
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_formatter = ColoredFormatter(
            fmt='%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
        
        # ────────────────────────────────────────────────────────────────
        # HANDLER: Arquivo com Rotação (estruturado)
        # ────────────────────────────────────────────────────────────────
        file_handler = logging.handlers.RotatingFileHandler(
            filename=LOG_FILE,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = FileFormatter(
            fmt='%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(funcName)-15s │ %(message)s │ [%(elapsed)s]',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        
        # ────────────────────────────────────────────────────────────────
        # HANDLER: Arquivo de Erros (só erros)
        # ────────────────────────────────────────────────────────────────
        error_handler = logging.handlers.RotatingFileHandler(
            filename=LOG_DIR / "sirius_errors.log",
            maxBytes=MAX_LOG_SIZE,
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_formatter = FileFormatter(
            fmt='%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(funcName)-15s │ %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        error_handler.setFormatter(error_formatter)
        root_logger.addHandler(error_handler)
        
        # Log inicial
        root_logger.info("="*70)
        root_logger.info("S.I.R.I.U.S. v5.2 — Sistema iniciado")
        root_logger.info(f"Arquivo de log: {LOG_FILE}")
        root_logger.info("="*70)
    
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Obtém logger para um módulo."""
        return logging.getLogger(name)

# ============================================================================
# FUNÇÕES GLOBAIS CONVENIENTES
# ============================================================================

def get_logger(name: str) -> logging.Logger:
    """Atalho para obter logger."""
    return SiriusLogger.get_logger(name)

def setup_logging():
    """Setup (chamado automaticamente, mas pode ser forçado)."""
    return SiriusLogger()

# ============================================================================
# CAPTURA DE EXCEÇÕES NÃO TRATADAS
# ============================================================================

def setup_exception_handler():
    """Configura handler para exceções não tratadas."""
    logger = get_logger("SIRIUS.UNCAUGHT")
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        """Handler para exceções não tratadas."""
        if issubclass(exc_type, KeyboardInterrupt):
            # Não logar Ctrl+C
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        logger.critical(
            "Exceção não tratada",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
    
    sys.excepthook = handle_exception

# ============================================================================
# CONTEXTO PARA LOGS ESTRUTURADOS
# ============================================================================

class LogContext:
    """Context manager para adicionar contexto aos logs."""
    
    def __init__(self, context: str, logger: Optional[logging.Logger] = None):
        self.context = context
        self.logger = logger or get_logger("SIRIUS")
        self.start_time = None
    
    def __enter__(self):
        import time
        self.start_time = time.time()
        self.logger.info(f"▶ {self.context}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        duration = (time.time() - self.start_time) * 1000
        
        if exc_type is None:
            self.logger.info(f"✅ {self.context} concluído ({duration:.1f}ms)")
        else:
            self.logger.error(
                f"❌ {self.context} falhou ({duration:.1f}ms): {exc_val}"
            )
        
        return False  # Não suprimir exceção

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

# Setup automático
setup_logging()
setup_exception_handler()

# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    logger = get_logger("SIRIUS.TEST")
    
    logger.debug("Mensagem de debug")
    logger.info("Informação importante")
    logger.warning("Aviso!")
    logger.error("Erro detectado")
    
    # Com contexto
    with LogContext("Operação de teste", logger):
        import time
        time.sleep(0.5)
        logger.info("Fazendo algo...")
    
    print("\n✅ Logs salvos em:", LOG_FILE)