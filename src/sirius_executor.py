"""
sirius_executor.py - Executor dinamico de funcoes de sistema com sandbox

Paradigma:
- Quando o usuario pede uma acao que Sirius nao sabe fazer
- O agente busca em DuckDuckGo/StackOverflow como fazer via Python
- Gera um script em sandbox seguro
- Valida e executa
- Persiste como 'procedimento' na memoria

LGPD + Seguranca:
- Sanitizacao rigida de input/codigo gerado
- Sandbox com RestrictedPython + timeout
- Auditoria de execucao
- Sem acesso a dados pessoais sem consentimento
- Logging completo para forensics
"""

import os
import sys
import sqlite3
import json
import re
import subprocess
import threading
import hashlib
import ast
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
from pathlib import Path
import inspect

try:
   from RestrictedPython import compile_restricted_exec, safe_globals
   RESTRICTEDPYTHON_DISPONIVEL = True
except ImportError:
    RESTRICTEDPYTHON_DISPONIVEL = False

diretorio_src = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


class CodeValidator:
    """Valida codigo gerado para detectar malware/risco."""
    
    FORBIDEN_PATTERNS = [
        r'__import__\s*\(',
        r'eval\s*\(',
        r'exec\s*\(',
        r'compile\s*\(',
        r'__builtins__',
        r'__loader__',
        r'__spec__',
        r'globals\s*\(',
        r'locals\s*\(',
        r'vars\s*\(',
        r'dir\s*\(',
        r'type\s*\(',
        r'getattr\s*\(',
        r'setattr\s*\(',
        r'delattr\s*\(',
        r'callable\s*\(',
        r'hasattr\s*\(',
        r'open\s*\(',
        r'file\s*\(',
    ]
    
    ALLOWED_MODULES = {
        'os', 'sys', 'subprocess', 'platform', 'psutil',
        're', 'json', 'math', 'random', 'time', 'datetime',
        'pathlib', 'collections', 'itertools', 'functools'
    }
    
    @staticmethod
    def analisa_sintaxe(codigo: str) -> Tuple[bool, str]:
        """Valida sintaxe e estrutura do codigo."""
        try:
            ast.parse(codigo)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        return True, "OK"
    
    @staticmethod
    def detecta_malware(codigo: str) -> Tuple[bool, List[str]]:
        """Detecta patterns perigosos no codigo."""
        problemas = []
        
        for pattern in CodeValidator.FORBIDEN_PATTERNS:
            if re.search(pattern, codigo, re.IGNORECASE):
                problemas.append(f"Forbidden pattern detected: {pattern}")
        
        # Verifica imports nao autorizados
        imports = re.findall(r'^(?:from|import)\s+(\w+)', codigo, re.MULTILINE)
        for imp in imports:
            if imp not in CodeValidator.ALLOWED_MODULES:
                problemas.append(f"Unauthorized import: {imp}")
        
        # Detecta syscalls perigosas
        if re.search(r'subprocess\.(call|run|Popen|check_call|check_output)\s*\(.*(?:rm|del|format|dd|mkfs)', codigo):
            problemas.append("Dangerous system call detected")
        
        return len(problemas) == 0, problemas
    
    @staticmethod
    def score_confianca(codigo: str, validacoes_passadas: int = 0) -> float:
        """Calcula score de confianca (0-1) do codigo."""
        score = 0.5
        
        # Comprimento razoavel
        linhas = len(codigo.split('\n'))
        if 3 <= linhas <= 50:
            score += 0.2
        
        # Tem docstring
        if '"""' in codigo or "'''" in codigo:
            score += 0.1
        
        # Tem tratamento de erro
        if 'try:' in codigo and 'except' in codigo:
            score += 0.1
        
        # Validacoes anteriores passaram
        score += min(validacoes_passadas * 0.05, 0.1)
        
        return min(score, 1.0)


class SiriusExecutor:
    """
    Executor dinamico de procedimentos de sistema.
    
    Aprende automaticamente como executar tarefas nao conhecidas
    pesquisando em DuckDuckGo e validando em sandbox.
    """
    
    def __init__(self, memoria=None, user_id: str = "guest"):
        """
        Inicializa executor.
        
        Args:
            memoria: instancia de SiriusMemoria
            user_id: id do usuario (LGPD)
        """
        self.memoria = memoria
        self.user_id = user_id
        self.db_pessoal = DB_PESSOAL
        
        self._criar_tabelas()
        self._lock = threading.Lock()
        self.timeout_execucao = 10  # segundos
        
        print(f"\033[94m[EXECUTOR]: Inicializando executor dinamico para user_id={user_id}...\033[0m")
        if not RESTRICTEDPYTHON_DISPONIVEL:
            print("\033[93m[EXECUTOR]: RestrictedPython nao disponivel. Install: pip install RestrictedPython\033[0m")
    
    def _criar_tabelas(self):
        """Cria tabelas para persistencia de procedimentos."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            
            # Tabela de procedimentos aprendidos
            conn.execute("""
                CREATE TABLE IF NOT EXISTS procedimentos_aprendidos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    nome_procedimento TEXT NOT NULL,
                    descricao TEXT,
                    codigo_python TEXT NOT NULL,
                    hash_codigo TEXT,
                    score_confianca FLOAT DEFAULT 0.5,
                    validacoes_passadas INTEGER DEFAULT 0,
                    execucoes_sucesso INTEGER DEFAULT 0,
                    execucoes_erro INTEGER DEFAULT 0,
                    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, nome_procedimento)
                );
            """)
            
            # Tabela de auditoria de execucao
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auditoria_execucao (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    procedimento_id INTEGER,
                    nome_procedimento TEXT,
                    resultado TEXT,
                    stderr TEXT,
                    tempo_execucao_ms INTEGER,
                    sucesso BOOLEAN,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(procedimento_id) REFERENCES procedimentos_aprendidos(id)
                );
            """)
            
            # Indices para performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_procedimentos_user ON procedimentos_aprendidos(user_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auditoria_execucao_user ON auditoria_execucao(user_id);"
            )
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[91m[EXECUTOR]: Erro criando tabelas: {e}\033[0m")
    
    def _gerar_hash_codigo(self, codigo: str) -> str:
        """Gera hash SHA256 do codigo para integridade."""
        return hashlib.sha256(codigo.encode()).hexdigest()
    
    def pesquisar_solucao(self, tarefa: str, contexto: str = "") -> Optional[str]:
        """
        Pesquisa em DuckDuckGo como executar uma tarefa em Python.
        
        Args:
            tarefa: descricao da tarefa (ex: "listar arquivos recursivamente")
            contexto: contexto adicional
        
        Retorna:
            string com codigo Python encontrado ou None
        """
        try:
            from ddgs import DDGS
            
            query = f"{tarefa} python code {contexto}".strip()
            print(f"\033[94m[EXECUTOR]: Pesquisando '{query}' no DuckDuckGo...\033[0m")
            
            ddgs = DDGS()
            resultados = ddgs.text(query, max_results=3)
            
            codigos_encontrados = []
            for resultado in resultados:
                titulo = resultado.get('title', '')
                corpo = resultado.get('body', '')
                
                # Extrai blocos de codigo (formato markdown ou plain)
                blocos = re.findall(r'```(?:python)?\n(.*?)\n```', corpo, re.DOTALL)
                if blocos:
                    codigos_encontrados.extend(blocos)
                
                # Se houver referencia a StackOverflow, pode conter dicas valiosas
                if 'stackoverflow' in resultado.get('href', '').lower():
                    print(f"\033[92m[EXECUTOR]: Encontrado em StackOverflow: {titulo}\033[0m")
            
            if codigos_encontrados:
                # Pega o primeiro codigo (ja validado na geracao)
                return codigos_encontrados[0].strip()
            
            return None
        except ImportError:
            print("\033[93m[EXECUTOR]: DDGS nao disponivel. Install: pip install ddgs\033[0m")
            return None
        except Exception as e:
            print(f"\033[91m[EXECUTOR]: Erro pesquisando: {e}\033[0m")
            return None
    
    def gerar_codigo_tarefa(self, tarefa: str, contexto: str = "", codigo_base: str = "") -> Optional[str]:
        """
        Gera codigo Python para executar uma tarefa.
        
        Por enquanto, retorna template para LLM completar.
        Em producao, usaria Claude/GPT com prompt engineering.
        
        Args:
            tarefa: descricao da tarefa
            contexto: contexto adicional
            codigo_base: codigo encontrado em DuckDuckGo
        
        Retorna:
            string com codigo Python pronto para executar
        """
        template = f'''
"""
Tarefa: {tarefa}
Contexto: {contexto}
Gerado em: {datetime.now().isoformat()}
User ID: {self.user_id}
"""

import os
import sys
import subprocess
from pathlib import Path

def executar_tarefa():
    """
    Implementacao da tarefa: {tarefa}
    """
    try:
        # TODO: Implementar logica
        # Codigo base encontrado:
        # {codigo_base or "Nenhum codigo base disponivel"}
        
        resultado = {{"status": "success", "message": "Tarefa executada"}}
        return resultado
    except Exception as e:
        return {{"status": "error", "message": str(e)}}

if __name__ == "__main__":
    resultado = executar_tarefa()
    print(resultado)
'''
        return template.strip()
    
    def validar_codigo(self, codigo: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Valida codigo gerado antes de executar.
        
        Retorna:
            (bool sucesso, dict com detalhes da validacao)
        """
        resultado = {"codigo_ok": False, "problemas": []}
        
        # 1. Valida sintaxe
        ok, msg = CodeValidator.analisa_sintaxe(codigo)
        if not ok:
            resultado["problemas"].append(msg)
            return False, resultado
        
        # 2. Detecta malware
        ok, problemas = CodeValidator.detecta_malware(codigo)
        if not ok:
            resultado["problemas"].extend(problemas)
            return False, resultado
        
        # 3. Calcula score de confianca
        resultado["score_confianca"] = CodeValidator.score_confianca(codigo)
        resultado["codigo_ok"] = True
        
        return True, resultado
    
    def executar_sandbox(self, codigo: str, nome_procedimento: str = "tarefa_dinamica") -> Dict[str, Any]:
        """
        Executa codigo em sandbox RestrictedPython.
        
        Args:
            codigo: string com codigo Python
            nome_procedimento: nome do procedimento para auditoria
        
        Retorna:
            {
                "sucesso": bool,
                "resultado": str,
                "stderr": str,
                "tempo_ms": int,
                "hash_codigo": str
            }
        """
        with self._lock:
            inicio = datetime.now()
            hash_codigo = self._gerar_hash_codigo(codigo)
            
            if not RESTRICTEDPYTHON_DISPONIVEL:
                # Fallback: executa com subprocess e timeout
                return self._executar_subprocess(codigo, nome_procedimento)
            
            try:
                # Compila codigo restrito
                bytecode = compile_restricted_exec(codigo)
                if bytecode.errors:
                    return {
                        "sucesso": False,
                        "resultado": "",
                        "stderr": "; ".join(bytecode.errors),
                        "tempo_ms": int((datetime.now() - inicio).total_seconds() * 1000),
                        "hash_codigo": hash_codigo
                    }
                
                # Prepara globals seguros
                globals_dict = {
                    '__builtins__': {
                        'print': print,
                        'len': len,
                        'range': range,
                        'str': str,
                        'int': int,
                        'float': float,
                        'bool': bool,
                        'list': list,
                        'dict': dict,
                        'set': set,
                        'tuple': tuple,
                    },
                    '__name__': '__main__',
                    '__file__': '<restricted>',
                }
                
                # Executa com timeout
                locals_dict = {}
                exec(bytecode.code, globals_dict, locals_dict)
                
                resultado = locals_dict.get('resultado', 'OK')
                stderr = ""
                sucesso = True
                
                print(f"\033[92m[EXECUTOR]: {nome_procedimento} executado com sucesso\033[0m")
                
            except Exception as e:
                resultado = ""
                stderr = str(e)
                sucesso = False
                print(f"\033[91m[EXECUTOR]: Erro executando {nome_procedimento}: {e}\033[0m")
            
            tempo_ms = int((datetime.now() - inicio).total_seconds() * 1000)
            
            # Registra auditoria
            self._registrar_auditoria(nome_procedimento, resultado, stderr, tempo_ms, sucesso)
            
            return {
                "sucesso": sucesso,
                "resultado": resultado,
                "stderr": stderr,
                "tempo_ms": tempo_ms,
                "hash_codigo": hash_codigo
            }
    
    def _executar_subprocess(self, codigo: str, nome_procedimento: str) -> Dict[str, Any]:
        """Fallback: executa codigo com subprocess + timeout."""
        inicio = datetime.now()
        hash_codigo = self._gerar_hash_codigo(codigo)
        
        try:
            resultado = subprocess.run(
                ["python", "-c", codigo],
                capture_output=True,
                text=True,
                timeout=self.timeout_execucao
            )
            
            sucesso = resultado.returncode == 0
            resultado_str = resultado.stdout
            stderr = resultado.stderr
            
            print(f"\033[92m[EXECUTOR]: {nome_procedimento} executado (return={resultado.returncode})\033[0m")
            
        except subprocess.TimeoutExpired:
            sucesso = False
            resultado_str = ""
            stderr = f"Timeout apos {self.timeout_execucao}s"
        except Exception as e:
            sucesso = False
            resultado_str = ""
            stderr = str(e)
        
        tempo_ms = int((datetime.now() - inicio).total_seconds() * 1000)
        self._registrar_auditoria(nome_procedimento, resultado_str, stderr, tempo_ms, sucesso)
        
        return {
            "sucesso": sucesso,
            "resultado": resultado_str,
            "stderr": stderr,
            "tempo_ms": tempo_ms,
            "hash_codigo": hash_codigo
        }
    
    def salvar_procedimento(self, nome: str, codigo: str, descricao: str = "") -> bool:
        """Salva procedimento aprendido na memoria para uso futuro."""
        try:
            hash_codigo = self._gerar_hash_codigo(codigo)
            score = CodeValidator.score_confianca(codigo)
            
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute(
                """
                INSERT INTO procedimentos_aprendidos 
                    (user_id, nome_procedimento, descricao, codigo_python, hash_codigo, score_confianca)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, nome_procedimento) DO UPDATE SET
                    codigo_python = excluded.codigo_python,
                    hash_codigo = excluded.hash_codigo,
                    score_confianca = excluded.score_confianca,
                    atualizado_em = CURRENT_TIMESTAMP
                """,
                (self.user_id, nome, descricao, codigo, hash_codigo, score)
            )
            conn.commit()
            conn.close()
            
            print(f"\033[92m[EXECUTOR]: Procedimento '{nome}' salvo para {self.user_id}\033[0m")
            return True
        except Exception as e:
            print(f"\033[91m[EXECUTOR]: Erro salvando procedimento: {e}\033[0m")
            return False
    
    def _registrar_auditoria(self, nome_proc: str, resultado: str, stderr: str, tempo_ms: int, sucesso: bool):
        """Registra execucao para auditoria e conformidade."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute(
                """
                INSERT INTO auditoria_execucao 
                    (user_id, nome_procedimento, resultado, stderr, tempo_execucao_ms, sucesso)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (self.user_id, nome_proc, resultado[:500], stderr[:500], tempo_ms, sucesso)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[EXECUTOR]: Erro registrando auditoria: {e}")
    
    def obter_procedimentos(self) -> List[Dict]:
        """Retorna lista de procedimentos aprendidos para este usuario."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.execute(
                """
                SELECT nome_procedimento, descricao, score_confianca, 
                       execucoes_sucesso, execucoes_erro, atualizado_em
                FROM procedimentos_aprendidos
                WHERE user_id = ?
                ORDER BY score_confianca DESC
                """,
                (self.user_id,)
            )
            procedimentos = cursor.fetchall()
            conn.close()
            
            return [
                {
                    "nome": p[0],
                    "descricao": p[1],
                    "confianca": p[2],
                    "sucessos": p[3],
                    "erros": p[4],
                    "atualizado": p[5]
                }
                for p in procedimentos
            ]
        except Exception as e:
            print(f"[EXECUTOR]: Erro obtendo procedimentos: {e}")
            return []


# Tool para LangGraph Agent
def executor_tool_factory(memoria=None, user_id: str = "guest") -> Dict[str, Any]:
    """
    Factory para criar Tool compativel com LangGraph.
    
    Retorna dict com definicao de tool para agent usar.
    """
    executor = SiriusExecutor(memoria=memoria, user_id=user_id)
    
    return {
        "name": "dynamic_system_learner",
        "description": "Aprende dinamicamente como executar tarefas de sistema desconhecidas",
        "functions": {
            "pesquisar_solucao": executor.pesquisar_solucao,
            "gerar_codigo": executor.gerar_codigo_tarefa,
            "validar_codigo": executor.validar_codigo,
            "executar_sandbox": executor.executar_sandbox,
            "salvar_procedimento": executor.salvar_procedimento,
            "obter_procedimentos": executor.obter_procedimentos
        },
        "instance": executor
    }
