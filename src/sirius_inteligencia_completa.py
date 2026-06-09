"""
S.I.R.I.U.S. v5.2 — INTELIGÊNCIA COMPLETA
UM MÓDULO ÚNICO QUE FAZ TUDO:
  ✅ Carrega Gemini (incluso)
  ✅ Cache inteligente (economiza tokens)
  ✅ Monitor de tokens
  ✅ Análise de melhorias automática
  ✅ Aprendizado contínuo
  ✅ Fallback ao validador (100% offline)
  ✅ Sem dependências externas (exceto logging)
"""

import json
import hashlib
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict
from dataclasses import dataclass, asdict
from dotenv import load_dotenv

from sirius_logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CARREGAR .ENV
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent.parent / "config" / ".env"
if CONFIG_PATH.exists():
    load_dotenv(CONFIG_PATH)
    logger.info(f"✅ Carregado: {CONFIG_PATH}")
else:
    logger.warning(f"⚠️  Não encontrado: {CONFIG_PATH}")

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTAR GEMINI (INCLUSO AQUI)
# ═══════════════════════════════════════════════════════════════════════════════

_GEMINI_DISPONIVEL = False
try:
    import google.generativeai as genai
    _GEMINI_DISPONIVEL = True
except ImportError:
    logger.warning("google-generativeai não instalado")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    MAX_TOKENS_POR_DIA = 50000
    MAX_REQUISICOES_POR_DIA = 100
    SCORE_MINIMO_PARA_CORRIGIR = 0.50
    LIMPAR_CACHE_DIAS = 30
    
    BANCO_CACHE = "sirius_cache_gemini.db"
    BANCO_MONITOR = "sirius_monitor_tokens.db"
    BANCO_TREINO = "sirius_treino.db"

# ═══════════════════════════════════════════════════════════════════════════════
# MELHORIAS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MelhoriasDetectadas:
    removeu_contradicoes: bool = False
    corrigiu_gramatica: bool = False
    melhorou_coerencia: bool = False
    padronizou_tom: bool = False
    removeu_multiplas_frases: bool = False
    removeu_giria_excessiva: bool = False
    melhorou_clareza: bool = False
    
    def total(self) -> int:
        return sum([
            self.removeu_contradicoes,
            self.corrigiu_gramatica,
            self.melhorou_coerencia,
            self.padronizou_tom,
            self.removeu_multiplas_frases,
            self.removeu_giria_excessiva,
            self.melhorou_clareza,
        ])
    
    def descricao(self) -> str:
        melhorias = []
        if self.removeu_contradicoes: melhorias.append("❌ Contradições")
        if self.corrigiu_gramatica: melhorias.append("✏️ Gramática")
        if self.melhorou_coerencia: melhorias.append("🔗 Coerência")
        if self.padronizou_tom: melhorias.append("🎯 Tom")
        if self.removeu_multiplas_frases: melhorias.append("📝 Frases")
        if self.removeu_giria_excessiva: melhorias.append("🗣️ Gíria")
        if self.melhorou_clareza: melhorias.append("💡 Clareza")
        return " | ".join(melhorias) if melhorias else "Sem mudanças"

class AnalisadorMelhorias:
    CONTRADITORES = {("bom dia", "boa noite"), ("sim", "não")}
    GIRIAS = ["eae", "blz", "mano", "parca", "opa", "eita", "tchau"]
    
    @staticmethod
    def analisar(original: str, corrigida: str) -> MelhoriasDetectadas:
        melhorias = MelhoriasDetectadas()
        
        if original.strip() == corrigida.strip():
            return melhorias
        
        texto_orig = original.lower()
        texto_corr = corrigida.lower()
        
        for c1, c2 in AnalisadorMelhorias.CONTRADITORES:
            if c1 in texto_orig and c2 in texto_orig:
                if not (c1 in texto_corr and c2 in texto_corr):
                    melhorias.removeu_contradicoes = True
        
        if original.count("..") > corrigida.count(".."):
            melhorias.corrigiu_gramatica = True
        
        if original.count(". ") > corrigida.count(". ") + 1:
            melhorias.melhorou_coerencia = True
        
        girias_orig = sum(1 for g in AnalisadorMelhorias.GIRIAS if g in texto_orig)
        girias_corr = sum(1 for g in AnalisadorMelhorias.GIRIAS if g in texto_corr)
        
        if girias_orig > girias_corr:
            melhorias.padronizou_tom = True
        
        frases_orig = len([f for f in original.split(".") if f.strip()])
        frases_corr = len([f for f in corrigida.split(".") if f.strip()])
        
        if frases_orig > frases_corr + 1:
            melhorias.removeu_multiplas_frases = True
        
        if girias_orig >= 3 and girias_corr < girias_orig:
            melhorias.removeu_giria_excessiva = True
        
        if (len(corrigida) <= len(original) * 1.1 and
            len(corrigida) >= len(original) * 0.8):
            if corrigida != original:
                melhorias.melhorou_clareza = True
        
        return melhorias

# ═══════════════════════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class GerenciadorCache:
    def __init__(self):
        self.banco = Config.BANCO_CACHE
        self._criar_tabela()
    
    def _criar_tabela(self):
        try:
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY,
                    hash TEXT UNIQUE,
                    original TEXT,
                    corrigida TEXT,
                    timestamp TEXT,
                    hits INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro criar cache: {e}")
    
    def obter(self, texto: str) -> Optional[str]:
        try:
            hash_texto = hashlib.md5(texto.encode()).hexdigest()
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("SELECT corrigida FROM cache WHERE hash = ?", (hash_texto,))
            resultado = cursor.fetchone()
            
            if resultado:
                cursor.execute("UPDATE cache SET hits = hits + 1 WHERE hash = ?", (hash_texto,))
                conn.commit()
            
            conn.close()
            return resultado[0] if resultado else None
        except Exception as e:
            logger.error(f"Erro obter cache: {e}")
            return None
    
    def armazenar(self, original: str, corrigida: str) -> bool:
        try:
            hash_texto = hashlib.md5(original.encode()).hexdigest()
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO cache (hash, original, corrigida, timestamp)
                VALUES (?, ?, ?, ?)
            """, (hash_texto, original, corrigida, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Erro armazenar cache: {e}")
            return False

# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR DE TOKENS
# ═══════════════════════════════════════════════════════════════════════════════

class MonitorTokens:
    def __init__(self):
        self.banco = Config.BANCO_MONITOR
        self._criar_tabela()
    
    def _criar_tabela(self):
        try:
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS uso (
                    id INTEGER PRIMARY KEY,
                    data TEXT UNIQUE,
                    tokens INTEGER,
                    requisicoes INTEGER,
                    custo REAL
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro criar monitor: {e}")
    
    def registrar(self, tokens: int = 200, custo: float = 0.001):
        try:
            data = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE uso SET tokens = tokens + ?, requisicoes = requisicoes + 1, custo = custo + ?
                WHERE data = ?
            """, (tokens, custo, data))
            
            if cursor.rowcount == 0:
                cursor.execute("""
                    INSERT INTO uso (data, tokens, requisicoes, custo)
                    VALUES (?, ?, 1, ?)
                """, (data, tokens, custo))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro registrar: {e}")
    
    def obter_uso_hoje(self) -> Dict:
        try:
            data = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("SELECT tokens, requisicoes, custo FROM uso WHERE data = ?", (data,))
            resultado = cursor.fetchone()
            conn.close()
            
            if resultado:
                return {
                    "tokens": resultado[0],
                    "requisicoes": resultado[1],
                    "custo": resultado[2],
                    "limite_tokens": Config.MAX_TOKENS_POR_DIA,
                    "limite_requisicoes": Config.MAX_REQUISICOES_POR_DIA,
                    "tokens_restantes": Config.MAX_TOKENS_POR_DIA - resultado[0],
                    "requisicoes_restantes": Config.MAX_REQUISICOES_POR_DIA - resultado[1],
                }
            
            return {
                "tokens": 0,
                "requisicoes": 0,
                "custo": 0.0,
                "limite_tokens": Config.MAX_TOKENS_POR_DIA,
                "limite_requisicoes": Config.MAX_REQUISICOES_POR_DIA,
                "tokens_restantes": Config.MAX_TOKENS_POR_DIA,
                "requisicoes_restantes": Config.MAX_REQUISICOES_POR_DIA,
            }
        except Exception as e:
            logger.error(f"Erro obter uso: {e}")
            return {}
    
    def pode_usar_gemini(self) -> Tuple[bool, str]:
        uso = self.obter_uso_hoje()
        if uso["tokens_restantes"] <= 0:
            return False, "❌ Limite tokens"
        if uso["requisicoes_restantes"] <= 0:
            return False, "❌ Limite requisições"
        return True, f"✅ {uso['tokens_restantes']} tokens"

# ═══════════════════════════════════════════════════════════════════════════════
# ARMAZENAMENTO DE TREINO
# ═══════════════════════════════════════════════════════════════════════════════

class ArmazenadorTreino:
    def __init__(self):
        self.banco = Config.BANCO_TREINO
        self._criar_tabela()
    
    def _criar_tabela(self):
        try:
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aprendizados (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    pergunta TEXT,
                    resposta_original TEXT,
                    resposta_corrigida TEXT,
                    melhorias JSON,
                    total_melhorias INTEGER,
                    hash TEXT UNIQUE,
                    tema TEXT,
                    confianca REAL,
                    score REAL
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro criar treino: {e}")
    
    def armazenar(self, pergunta: str, original: str, corrigida: str, 
                  melhorias: MelhoriasDetectadas, tema: str = None, 
                  confianca: float = 0.0, score: float = 0.0) -> bool:
        
        if melhorias.total() == 0:
            return False
        
        try:
            hash_resp = hashlib.md5(f"{pergunta}{corrigida}".encode()).hexdigest()
            conn = sqlite3.connect(self.banco)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO aprendizados 
                (timestamp, pergunta, resposta_original, resposta_corrigida, 
                 melhorias, total_melhorias, hash, tema, confianca, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                pergunta,
                original,
                corrigida,
                json.dumps(asdict(melhorias)),
                melhorias.total(),
                hash_resp,
                tema,
                confianca,
                score
            ))
            
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            logger.error(f"Erro armazenar: {e}")
            return False

# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI (INCLUSO - SEM DEPENDÊNCIA EXTERNA)
# ═══════════════════════════════════════════════════════════════════════════════

class CorretorGemini:
    """Corretor Gemini incluso"""
    
    PROMPT = """Você é um corretor de respostas para um assistente IA.
Corrija APENAS:
1. Erros gramaticais
2. Contradições (remova uma das partes)
3. Múltiplas frases desconexas (consolidar)
4. Gíria excessiva (padronizar tom)

NUNCA adicione informação nova.
Responda APENAS com a resposta corrigida, sem explicações.

Pergunta: {pergunta}
Resposta: {resposta}

Resposta corrigida:"""
    
    def __init__(self):
        self.disponivel = False
        self.model = None
        
        if not _GEMINI_DISPONIVEL:
            logger.warning("Gemini não disponível (google-generativeai não instalado)")
            return
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY não configurada")
            return
        
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")
            self.disponivel = True
            logger.info("✅ Gemini Corretor pronto")
        except Exception as e:
            logger.error(f"Erro inicializar Gemini: {e}")
    
    def corrigir(self, resposta: str, pergunta: str) -> Tuple[str, bool]:
        """Corrige resposta com Gemini"""
        
        if not self.disponivel or not self.model:
            return resposta, False
        
        try:
            prompt = self.PROMPT.format(pergunta=pergunta[:100], resposta=resposta[:500])
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=1000,
                    temperature=0.3,
                )
            )
            
            if not response or not response.text:
                return resposta, False
            
            resposta_corrigida = response.text.strip()
            
            if len(resposta_corrigida) < 3:
                return resposta, False
            
            if resposta_corrigida == resposta:
                return resposta, False
            
            return resposta_corrigida, True
        
        except Exception as e:
            logger.error(f"Erro Gemini: {e}")
            return resposta, False

# ═══════════════════════════════════════════════════════════════════════════════
# SISTEMA INTELIGÊNCIA COMPLETA
# ═══════════════════════════════════════════════════════════════════════════════

class SiriusInteligenciaCompleta:
    """Sistema único que faz TUDO"""
    
    def __init__(self):
        self.cache = GerenciadorCache()
        self.monitor = MonitorTokens()
        self.treinador = ArmazenadorTreino()
        self.analisador = AnalisadorMelhorias()
        self.gemini = CorretorGemini()
        
        self.validador = None
        
        # Carregar validador (fallback)
        try:
            from validador_resposta import ValidadorCompleto
            self.validador = ValidadorCompleto()
            logger.info("✅ Validador fallback pronto")
        except:
            logger.warning("⚠️  Validador não disponível")
        
        logger.info("✅ Inteligência Completa inicializada")
    
    def processar(
        self,
        resposta: str,
        pergunta: str,
        score_validacao: float = 0.5,
        tema: str = None,
        confianca: float = 0.0
    ) -> Tuple[str, str, str]:
        """
        Processa resposta COMPLETO:
        1. Cache (0 tokens)
        2. Score OK (0 tokens)
        3. Gemini (~200 tokens)
        4. Validador fallback (0 tokens, offline)
        5. Detecta melhorias
        6. Armazena para treino
        
        Returns:
            (resposta_final, modo_usado, feedback)
        """
        
        # PASSO 1: CACHE
        cached = self.cache.obter(resposta)
        if cached:
            return cached, "🔄 Cache", "Recuperado (0 tokens)"
        
        # PASSO 2: SCORE
        if score_validacao >= Config.SCORE_MINIMO_PARA_CORRIGIR:
            return resposta, "✅ OK", "Score bom (0 tokens)"
        
        # PASSO 3: GEMINI
        pode_usar, msg = self.monitor.pode_usar_gemini()
        
        if pode_usar and self.gemini.disponivel:
            resposta_corrigida, corrigiu = self.gemini.corrigir(resposta, pergunta)
            
            if corrigiu:
                melhorias = self.analisador.analisar(resposta, resposta_corrigida)
                self.cache.armazenar(resposta, resposta_corrigida)
                self.monitor.registrar(200, 0.001)
                self.treinador.armazenar(
                    pergunta, resposta, resposta_corrigida, melhorias, tema, confianca, score_validacao
                )
                
                feedback = f"🤖 Gemini: {melhorias.descricao()}"
                return resposta_corrigida, "🤖 Gemini", feedback
        
        # PASSO 4: VALIDADOR (FALLBACK OFFLINE)
        if self.validador:
            try:
                _, limpa, _, _ = self.validador.validar(resposta, pergunta, 0.75)
                
                if limpa != resposta:
                    melhorias = self.analisador.analisar(resposta, limpa)
                    self.treinador.armazenar(
                        pergunta, resposta, limpa, melhorias, tema, confianca, score_validacao
                    )
                    feedback = f"✔️ Validador: {melhorias.descricao()}"
                    return limpa, "✔️ Validador", feedback
            except Exception as e:
                logger.error(f"Erro validador: {e}")
        
        return resposta, "⚠️ Sem correção", "Nenhuma mudança"

# ═══════════════════════════════════════════════════════════════════════════════
# TESTE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════════════╗
║   S.I.R.I.U.S. — INTELIGÊNCIA COMPLETA (TUDO JUNTO)              ║
╚════════════════════════════════════════════════════════════════════╝
    """)
    
    sistema = SiriusInteligenciaCompleta()
    
    print("\n" + "="*80)
    print("TESTE 1: Score baixo (corrigir)")
    print("="*80)
    
    resposta, modo, feedback = sistema.processar(
        resposta="bom dia como estamos. boa noite. eae mano!",
        pergunta="bom dia",
        score_validacao=0.35,
        tema="saudacao",
        confianca=0.75
    )
    
    print(f"\nModo: {modo}")
    print(f"Output: {resposta}")
    print(f"Feedback: {feedback}")
    
    print("\n" + "="*80)
    print("TESTE 2: Mesma entrada (cache)")
    print("="*80)
    
    resposta2, modo2, feedback2 = sistema.processar(
        resposta="bom dia como estamos. boa noite. eae mano!",
        pergunta="bom dia",
        score_validacao=0.35
    )
    
    print(f"\nModo: {modo2}")
    print(f"Output: {resposta2}")
    print(f"Tokens economizados: ~200 ✅")
    
    print("\n" + "="*80)
    print("USO DE TOKENS")
    print("="*80)
    
    uso = sistema.monitor.obter_uso_hoje()
    print(f"\nTokens: {uso['tokens']} / {uso['limite_tokens']}")
    print(f"Requisições: {uso['requisicoes']} / {uso['limite_requisicoes']}")
    print(f"Custo: R$ {uso['custo']:.2f}")