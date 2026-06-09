"""
sirius_gerador_hibrido.py — GEMINI COMO PRINCIPAL
====================================================
Sistema inteligente que usa:
  1. GEMINI (principal, qualidade máxima)
  2. BANCO PRÓPRIO (cache de respostas já geradas, 0 tokens)
  3. VALIDADOR (fallback offline se ambos falharem)

Estratégia:
  ✅ Gemini: tentativa 1 (resposta sempre fresca e de qualidade)
  ✅ Banco próprio: cache quando Gemini indisponível ou sem tokens (~0 tokens)
  ✅ Validador: fallback se ambos falharem
  ✅ Aprendizado: toda resposta do Gemini entra no banco como cache
"""

import os
import sqlite3
import json
import torch
import torch.nn as nn
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv

from sirius_logging import get_logger

logger = get_logger(__name__)

# Carregar .env
CONFIG_PATH = Path(__file__).parent.parent / "config" / ".env"
if CONFIG_PATH.exists():
    load_dotenv(CONFIG_PATH)

# Importar Gemini
try:
    import google.generativeai as genai
    _GEMINI_DISPONIVEL = True
except ImportError:
    _GEMINI_DISPONIVEL = False

# ═══════════════════════════════════════════════════════════════════════════════
# BANCO DE RESPOSTAS PRÓPRIAS
# ═══════════════════════════════════════════════════════════════════════════════

class BancoRespostasPropio:
    """Banco de respostas que o SIRIUS aprende"""
    
    def __init__(self, db_path: str = "sirius_respostas_proprias.db"):
        self.db_path = db_path
        self._criar_tabelas()
    
    def _criar_tabelas(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS respostas (
                    id INTEGER PRIMARY KEY,
                    pergunta TEXT UNIQUE,
                    resposta TEXT,
                    fonte TEXT,
                    timestamp TEXT,
                    qualidade REAL DEFAULT 0.75,
                    hits INTEGER DEFAULT 1
                )
            """)
            
            conn.commit()
            conn.close()
            logger.info("✅ Banco próprio inicializado")
        except Exception as e:
            logger.error(f"Erro ao criar banco: {e}")
    
    def obter_resposta(self, pergunta: str) -> Optional[str]:
        """Obtém resposta armazenada"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Busca exata ou similar
            cursor.execute("""
                SELECT resposta FROM respostas
                WHERE pergunta LIKE ?
                ORDER BY qualidade DESC, hits DESC
                LIMIT 1
            """, (f"%{pergunta[:20]}%",))
            
            resultado = cursor.fetchone()
            
            if resultado:
                # Incrementar hits
                cursor.execute("""
                    UPDATE respostas SET hits = hits + 1
                    WHERE pergunta LIKE ?
                """, (f"%{pergunta[:20]}%",))
                conn.commit()
            
            conn.close()
            return resultado[0] if resultado else None
        except Exception as e:
            logger.error(f"Erro ao obter resposta: {e}")
            return None
    
    def armazenar_resposta(
        self,
        pergunta: str,
        resposta: str,
        fonte: str = "gemini",
        qualidade: float = 0.75
    ) -> bool:
        """Armazena nova resposta aprendida"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO respostas
                (pergunta, resposta, fonte, timestamp, qualidade)
                VALUES (?, ?, ?, ?, ?)
            """, (pergunta, resposta, fonte, datetime.now().isoformat(), qualidade))
            
            conn.commit()
            conn.close()
            logger.info(f"🎓 Aprendizado: {pergunta[:30]}... ({fonte})")
            return True
        except Exception as e:
            logger.error(f"Erro ao armazenar: {e}")
            return False

# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR DE TOKENS (GEMINI)
# ═══════════════════════════════════════════════════════════════════════════════

class MonitorTokensGemini:
    """Monitora tokens do Gemini"""
    
    def __init__(self, db_path: str = "sirius_tokens.db"):
        self.db_path = db_path
        self._criar_tabela()
    
    def _criar_tabela(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY,
                    data TEXT UNIQUE,
                    tokens_usados INTEGER,
                    custo REAL
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception:
            pass
    
    def registrar(self, tokens: int = 200, custo: float = 0.001):
        """Registra uso de tokens"""
        try:
            data = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE tokens SET tokens_usados = tokens_usados + ?, custo = custo + ?
                WHERE data = ?
            """, (tokens, custo, data))
            
            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO tokens (data, tokens_usados, custo)
                    VALUES (?, ?, ?)
                """, (data, tokens, custo))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro ao registrar tokens: {e}")
    
    def pode_usar_gemini(self) -> Tuple[bool, str]:
        """Verifica limite de tokens"""
        MAX_TOKENS = 50000
        
        try:
            data = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT tokens_usados FROM tokens WHERE data = ?", (data,))
            resultado = cursor.fetchone()
            conn.close()
            
            tokens_usados = resultado[0] if resultado else 0
            tokens_restantes = MAX_TOKENS - tokens_usados
            
            if tokens_restantes <= 0:
                return False, "❌ Limite de tokens atingido"
            
            return True, f"✅ {tokens_restantes} tokens disponíveis"
        except Exception as e:
            logger.error(f"Erro ao verificar tokens: {e}")
            return True, "⚠️ Erro ao verificar (assumindo ok)"

# ═══════════════════════════════════════════════════════════════════════════════
# GERADOR COM GEMINI
# ═══════════════════════════════════════════════════════════════════════════════

class GeradorGemini:
    """Gera respostas com Gemini"""
    
    def __init__(self):
        self.disponivel = False
        self.model = None
        
        if not _GEMINI_DISPONIVEL:
            logger.warning("google-generativeai não instalado")
            return
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY não configurada")
            return
        
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")
            self.disponivel = True
            logger.info("✅ Gemini pronto")
        except Exception as e:
            logger.error(f"Erro ao inicializar Gemini: {e}")
    
    def gerar(self, prompt_completo: str) -> Optional[str]:
        """Gera resposta com Gemini recebendo o prompt já estruturado pelo cérebro"""
        if not self.disponivel:
            return None
        
        try:
            # Como o cérebro já monta o sanduíche com as tags [SYSTEM] e [HISTORICO],
            # passamos o prompt direto para o Gemini respeitar as regras do projeto
            response = self.model.generate_content(
                prompt_completo,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=300,
                    temperature=0.7,
                )
            )
            
            if response and response.text:
                return response.text.strip()
        
        except Exception as e:
            logger.error(f"Erro Gemini: {e}")
        
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# GERADOR HÍBRIDO (DUPLA ENTREGA CORRIGIDO)
# ═══════════════════════════════════════════════════════════════════════════════

class GeradorHibrido:
    """Dupla entrega: Gerador próprio + Gemini com fallback por falta de tokens"""
    
    def __init__(self):
        self.banco = BancoRespostasPropio()
        self.monitor_tokens = MonitorTokensGemini()
        self.gemini = GeradorGemini()
        self.validador = None
        
        try:
            from validador_resposta import ValidadorCompleto
            self.validador = ValidadorCompleto()
        except:
            logger.warning("Validador não disponível")
    
    def gerar(self, prompt_enriquecido: str, pergunta_pura: Optional[str] = None) -> Tuple[str, str]:
        """
        Gera resposta com Gemini como modelo principal e salva no aprendiz (banco).
        """
        # Se não passar a pergunta pura separada, usa o começo do prompt (fallback)
        pergunta_chave = pergunta_pura if pergunta_pura else prompt_enriquecido

        # PASSO 1: Gemini — principal (qualidade máxima)
        pode_usar_gemini, msg_tokens = self.monitor_tokens.pode_usar_gemini()

        if pode_usar_gemini and self.gemini.disponivel:
            resposta_gemini = self.gemini.gerar(prompt_enriquecido)

            if resposta_gemini:
                logger.info(f"🤖 Resposta do Gemini (principal): {resposta_gemini[:40]}...")

                # Registrar uso de tokens no monitor
                self.monitor_tokens.registrar(200, 0.001)

                # 🎓 APRENDIZADO: Armazena usando a PERGUNTA PURA do Carlos como chave de busca!
                self.banco.armazenar_resposta(
                    pergunta_chave, resposta_gemini, fonte="gemini", qualidade=0.9
                )

                return resposta_gemini, "gemini"
        else:
            logger.warning(f"⚠️ Gemini indisponível ou sem tokens: {msg_tokens}")

        # PASSO 2: Aprendiz assume — Cache offline por falta de tokens (0 tokens)
        resposta_banco = self.banco.obter_resposta(pergunta_chave)
        if resposta_banco:
            logger.info(f"📚 Aprendiz assumiu (Offline): {resposta_banco[:40]}...")
            return resposta_banco, "banco"

        # PASSO 3: Validador como último de todos os fallbacks
        if self.validador:
            try:
                _, resposta_limpa, _, _ = self.validador.validar(pergunta_chave, pergunta_chave, 0.75)
                if resposta_limpa and resposta_limpa != pergunta_chave:
                    logger.info(f"✔️ Fallback validador: {resposta_limpa[:40]}...")
                    return resposta_limpa, "validador"
            except Exception as e:
                logger.error(f"Erro no validador: {e}")

        # PASSO 4: Sem resposta
        logger.warning(f"⚠️ Nenhuma resposta para: {pergunta_chave[:30]}")
        return "Desculpe, não consegui processar. Reformule e tente novamente.", "sem resposta"

# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_gerador_hibrido = None

def get_gerador_hibrido() -> GeradorHibrido:
    """Obtém gerador híbrido singleton"""
    global _gerador_hibrido
    if _gerador_hibrido is None:
        _gerador_hibrido = GeradorHibrido()
    return _gerador_hibrido

# ═══════════════════════════════════════════════════════════════════════════════
# TESTE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════════════╗
║   S.I.R.I.U.S. — GERADOR HÍBRIDO (DUPLA ENTREGA)                 ║
║   Gerador próprio + Gemini + Validador                           ║
╚════════════════════════════════════════════════════════════════════╝
    """)
    
    gen = get_gerador_hibrido()
    
    # Teste 1
    print("\n[TESTE 1] Primeira pergunta (Gemini — principal):")
    resposta1, fonte1 = gen.gerar("bom dia")
    print(f"Resposta ({fonte1}): {resposta1}")

    # Teste 2
    print("\n[TESTE 2] Segunda pergunta idêntica (Gemini novamente):")
    resposta2, fonte2 = gen.gerar("bom dia")
    print(f"Resposta ({fonte2}): {resposta2}")

    # Teste 3
    print("\n[TESTE 3] Nova pergunta (Gemini):")
    resposta3, fonte3 = gen.gerar("qual é sua função?")
    print(f"Resposta ({fonte3}): {resposta3}")
    
    print("\n✅ Gerador híbrido funcionando!")