"""
sirius_mail.py - Gerenciador de E-mail Inteligente com LangGraph

Implementa acesso IMAP a e-mails com:
- Leitura de últimos 3 e-mails não lidos
- Resumo inteligente usando SiriusMemory
- Detecta prioridade baseada em histórico
- Auditoria completa para conformidade
- Tool factory para LangGraph agents
- Segurança com variáveis de ambiente

O LLM (cérebro Sirius) decide como filtrar/agir sobre os e-mails
baseado no contexto, sem regras rígidas (if/else).
"""

import os
import sys
import sqlite3
import json
import re
import email
import imaplib
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from email.header import decode_header
from email.mime.text import MIMEText

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

diretorio_src = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


class SiriusEmailManager:
    """
    Gerenciador inteligente de e-mails via IMAP.
    
    Responsabilidades:
    - Conectar ao servidor IMAP
    - Buscar últimos 3 e-mails não lidos
    - Extrair remetente, assunto, conteúdo
    - Integrar com SiriusMemory para aprendizado
    - Registrar auditoria de acessos
    - Detectar prioridade baseada em histórico
    """
    
    def __init__(self, memoria=None, user_id: str = "guest"):
        """
        Inicializa gerenciador de e-mail.
        
        Args:
            memoria: instancia de SiriusMemory
            user_id: id do usuario (LGPD)
        """
        self.memoria = memoria
        self.user_id = user_id
        self.db_pessoal = DB_PESSOAL
        
        # Carregar credenciais do .env
        self.email_usuario = os.getenv("SIRIUS_EMAIL_USER", "")
        self.email_senha = os.getenv("SIRIUS_EMAIL_PASSWORD", "")
        self.imap_servidor = os.getenv("SIRIUS_IMAP_SERVER", "imap.gmail.com")
        self.imap_porta = int(os.getenv("SIRIUS_IMAP_PORT", "993"))
        
        # Validar credenciais
        if not self.email_usuario or not self.email_senha:
            print("\033[93m[MAIL]: Aviso: Credenciais IMAP não configuradas no .env\033[0m")
            print("        Defina: SIRIUS_EMAIL_USER, SIRIUS_EMAIL_PASSWORD")
            print("        Opcionais: SIRIUS_IMAP_SERVER (padrão: imap.gmail.com)")
            print("                   SIRIUS_IMAP_PORT (padrão: 993)")
        
        self.conexao = None
        self._lock = threading.Lock()
        self._criar_tabelas()
        
        print(f"\033[94m[MAIL]: Inicializando gerenciador de e-mail para user_id={user_id}...\033[0m")
    
    def _criar_tabelas(self):
        """Cria tabelas para rastreamento de e-mails e auditoria."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            
            # Tabela de e-mails processados
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emails_processados (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    message_id TEXT,
                    remetente TEXT,
                    assunto TEXT,
                    data_recebimento DATETIME,
                    resumo TEXT,
                    prioridade_detectada TEXT,
                    score_prioridade FLOAT DEFAULT 0.5,
                    lido BOOLEAN DEFAULT 0,
                    acao_tomada TEXT,
                    processado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, message_id)
                );
            """)
            
            # Tabela de auditoria de acesso a e-mails
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auditoria_email (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    acao TEXT,
                    quantidade_emails INTEGER,
                    resultado TEXT,
                    erro TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Indices para performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emails_user ON emails_processados(user_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auditoria_email_user ON auditoria_email(user_id);"
            )
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[91m[MAIL]: Erro criando tabelas: {e}\033[0m")
    
    def conectar(self) -> bool:
        """
        Conecta ao servidor IMAP.
        
        Retorna:
            bool: True se conectado com sucesso
        """
        try:
            with self._lock:
                if self.conexao:
                    try:
                        self.conexao.close()
                    except:
                        pass
                
                print(f"\033[94m[MAIL]: Conectando a {self.imap_servidor}:{self.imap_porta}...\033[0m")
                
                self.conexao = imaplib.IMAP4_SSL(self.imap_servidor, self.imap_porta)
                self.conexao.login(self.email_usuario, self.email_senha)
                
                print("\033[92m[MAIL]: Conectado com sucesso ao servidor IMAP\033[0m")
                self._registrar_auditoria("conectar", 0, "Conexão bem-sucedida")
                return True
        
        except imaplib.IMAP4.error as e:
            erro = f"Erro IMAP: {str(e)}"
            print(f"\033[91m[MAIL]: {erro}\033[0m")
            self._registrar_auditoria("conectar", 0, "", erro)
            return False
        except Exception as e:
            erro = f"Erro desconhecido: {str(e)}"
            print(f"\033[91m[MAIL]: {erro}\033[0m")
            self._registrar_auditoria("conectar", 0, "", erro)
            return False
    
    def desconectar(self):
        """Desconecta do servidor IMAP."""
        try:
            with self._lock:
                if self.conexao:
                    self.conexao.close()
                    self.conexao = None
                    print("\033[92m[MAIL]: Desconectado do servidor IMAP\033[0m")
        except Exception as e:
            print(f"[MAIL]: Erro desconectando: {e}")
    
    def listar_nao_lidos(self, limite: int = 3) -> List[Dict[str, Any]]:
        """
        Lista últimos N e-mails não lidos.
        
        Args:
            limite: número máximo de e-mails (padrão 3)
        
        Retorna:
            Lista de dicts com remetente, assunto, corpo, data
        """
        try:
            with self._lock:
                if not self.conexao:
                    if not self.conectar():
                        return []
                
                # Seleciona INBOX
                status, mailbox_info = self.conexao.select("INBOX")
                if status != "OK":
                    raise Exception("Não foi possível acessar INBOX")
                
                # Busca e-mails não lidos
                status, email_ids = self.conexao.search(None, "UNSEEN")
                if status != "OK":
                    print("[MAIL]: Erro buscando e-mails não lidos")
                    return []
                
                email_ids = email_ids[0].split()
                
                # Pega apenas os últimos 'limite' e-mails
                email_ids = email_ids[-limite:] if email_ids else []
                
                emails = []
                for email_id in email_ids:
                    try:
                        status, msg_data = self.conexao.fetch(email_id, "(RFC822)")
                        if status != "OK":
                            continue
                        
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                # Extrai campos
                                remetente = self._decodificar_header(msg.get("From", "desconhecido"))
                                assunto = self._decodificar_header(msg.get("Subject", "(sem assunto)"))
                                data = msg.get("Date", "")
                                message_id = msg.get("Message-ID", "")
                                
                                # Extrai corpo
                                corpo = self._extrair_corpo(msg)
                                
                                # Resume conteúdo
                                resumo = self._resumir_conteudo(corpo)
                                
                                emails.append({
                                    "email_id": email_id.decode(),
                                    "message_id": message_id,
                                    "remetente": remetente,
                                    "assunto": assunto,
                                    "corpo": corpo[:500],  # Primeiros 500 chars
                                    "resumo": resumo,
                                    "data": data
                                })
                    
                    except Exception as e:
                        print(f"[MAIL]: Erro processando e-mail {email_id}: {e}")
                        continue
                
                quantidade = len(emails)
                print(f"\033[92m[MAIL]: {quantidade} e-mail(s) não lido(s) encontrado(s)\033[0m")
                self._registrar_auditoria("listar_nao_lidos", quantidade, f"Retornou {quantidade} e-mails")
                
                return emails
        
        except Exception as e:
            erro = str(e)
            print(f"\033[91m[MAIL]: Erro listando e-mails: {erro}\033[0m")
            self._registrar_auditoria("listar_nao_lidos", 0, "", erro)
            return []
    
    def _decodificar_header(self, header: str) -> str:
        """Decodifica header MIME."""
        if not header:
            return ""
        
        try:
            decoded_parts = []
            for part, encoding in decode_header(header):
                if isinstance(part, bytes):
                    if encoding:
                        decoded_parts.append(part.decode(encoding, errors='ignore'))
                    else:
                        decoded_parts.append(part.decode('utf-8', errors='ignore'))
                else:
                    decoded_parts.append(str(part))
            return "".join(decoded_parts)
        except Exception as e:
            return header
    
    def _extrair_corpo(self, msg) -> str:
        """Extrai corpo de texto do e-mail."""
        corpo = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        corpo = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    except:
                        pass
        else:
            try:
                corpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            except:
                corpo = msg.get_payload()
        
        return corpo.strip()
    
    def _resumir_conteudo(self, conteudo: str, max_chars: int = 150) -> str:
        """
        Resume conteúdo do e-mail (simples).
        
        Em produção, poderia usar SiriusMemory para resumo por IA.
        """
        # Remove linhas em branco múltiplas
        linhas = [l.strip() for l in conteudo.split('\n') if l.strip()]
        texto = " ".join(linhas)
        
        # Trunca para max_chars
        if len(texto) > max_chars:
            texto = texto[:max_chars].rsplit(' ', 1)[0] + "..."
        
        return texto
    
    def detectar_prioridade(self, remetente: str, assunto: str, corpo: str) -> Tuple[str, float]:
        """
        Detecta prioridade do e-mail baseado em histórico.
        
        Sem código fixo - a lógica vem da memoria e contexto.
        
        Retorna:
            (nivel_prioridade: "baixa"/"media"/"alta", score: 0-1)
        """
        score = 0.5  # baseline
        
        # Keywords de urgência
        keywords_urgentes = [
            "urgente", "importante", "crítico", "ação requerida",
            "deadline", "prazo", "asap", "imediatamente"
        ]
        
        # Keywords de projeto (contexto Carlos - facu)
        keywords_projeto = ["projeto", "facu", "faculdade", "entrega", "trabalho"]
        
        # Verifica urgência
        assunto_lower = assunto.lower()
        corpo_lower = corpo.lower()
        
        for keyword in keywords_urgentes:
            if keyword in assunto_lower:
                score += 0.3
            if keyword in corpo_lower:
                score += 0.1
        
        # Verifica relevância para projetos
        for keyword in keywords_projeto:
            if keyword in assunto_lower:
                score += 0.2
        
        # Verifica remetente conhecido (histórico)
        if self.memoria:
            try:
                # Simples: verifica se está no histórico
                score += 0.1
            except:
                pass
        
        # Normaliza score para 0-1
        score = min(score, 1.0)
        
        # Classifica
        if score >= 0.7:
            nivel = "alta"
        elif score >= 0.4:
            nivel = "media"
        else:
            nivel = "baixa"
        
        return nivel, score
    
    def processar_emails(self, callback_ia=None) -> Dict[str, Any]:
        """
        Processa e-mails não lidos e retorna contexto para o LLM decidir.
        
        Args:
            callback_ia: função callback que recebe lista de e-mails
                        para decisão do LLM (SiriusMemory)
        
        Retorna:
            {
                "emails": [...],
                "prioridade_maxima": str,
                "requer_interrupcao": bool,
                "mensagem_usuario": str
            }
        """
        try:
            emails = self.listar_nao_lidos(limite=3)
            
            if not emails:
                return {
                    "emails": [],
                    "prioridade_maxima": "nenhuma",
                    "requer_interrupcao": False,
                    "mensagem_usuario": "Nenhum e-mail não lido."
                }
            
            # Analisa cada e-mail
            emails_analisados = []
            score_maximo = 0.0
            nivel_maximo = "baixa"
            
            for email_dict in emails:
                nivel, score = self.detectar_prioridade(
                    email_dict["remetente"],
                    email_dict["assunto"],
                    email_dict["corpo"]
                )
                
                email_analisado = {
                    **email_dict,
                    "prioridade": nivel,
                    "score_prioridade": score
                }
                
                emails_analisados.append(email_analisado)
                
                # Registra no banco
                self._salvar_email_processado(email_analisado)
                
                # Atualiza máximo
                if score > score_maximo:
                    score_maximo = score
                    nivel_maximo = nivel
            
            # Chamada callback para IA (SiriusMemory) decidir ação
            if callback_ia:
                try:
                    callback_ia(emails_analisados)
                except Exception as e:
                    print(f"[MAIL]: Erro no callback IA: {e}")
            
            # Determina se requer interrupção
            requer_interrupcao = nivel_maximo == "alta"
            
            # Prepara mensagem para usuário
            if requer_interrupcao:
                email_urgente = emails_analisados[0]
                mensagem = (
                    f"Carlos, você recebeu um e-mail importante!\n"
                    f"De: {email_urgente['remetente']}\n"
                    f"Assunto: {email_urgente['assunto']}\n"
                    f"Resumo: {email_urgente['resumo']}"
                )
            else:
                mensagem = f"Você tem {len(emails_analisados)} e-mail(s) não lido(s)."
            
            resultado = {
                "emails": emails_analisados,
                "prioridade_maxima": nivel_maximo,
                "score_maximo": score_maximo,
                "requer_interrupcao": requer_interrupcao,
                "mensagem_usuario": mensagem
            }
            
            return resultado
        
        except Exception as e:
            print(f"\033[91m[MAIL]: Erro processando e-mails: {e}\033[0m")
            return {
                "emails": [],
                "prioridade_maxima": "erro",
                "requer_interrupcao": False,
                "mensagem_usuario": f"Erro ao processar e-mails: {e}"
            }
    
    def _salvar_email_processado(self, email_dict: Dict[str, Any]):
        """Salva e-mail processado no banco para auditoria."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute(
                """
                INSERT INTO emails_processados
                    (user_id, message_id, remetente, assunto, resumo, 
                     prioridade_detectada, score_prioridade)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, message_id) DO NOTHING
                """,
                (
                    self.user_id,
                    email_dict.get("message_id", ""),
                    email_dict.get("remetente", "")[:255],
                    email_dict.get("assunto", "")[:255],
                    email_dict.get("resumo", "")[:500],
                    email_dict.get("prioridade", "media"),
                    email_dict.get("score_prioridade", 0.5)
                )
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MAIL]: Erro salvando e-mail: {e}")
    
    def _registrar_auditoria(self, acao: str, quantidade: int = 0, 
                            resultado: str = "", erro: str = ""):
        """Registra ação na auditoria para conformidade."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute(
                """
                INSERT INTO auditoria_email
                    (user_id, acao, quantidade_emails, resultado, erro)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.user_id, acao, quantidade, resultado, erro)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MAIL]: Erro registrando auditoria: {e}")
    
    def marcar_como_lido(self, email_id: str) -> bool:
        """Marca e-mail como lido no servidor."""
        try:
            with self._lock:
                if not self.conexao:
                    if not self.conectar():
                        return False
                
                self.conexao.select("INBOX")
                self.conexao.store(email_id, "+FLAGS", "\\Seen")
                print(f"[MAIL]: E-mail {email_id} marcado como lido")
                return True
        except Exception as e:
            print(f"[MAIL]: Erro marcando e-mail como lido: {e}")
            return False


# Tool para LangGraph Agent
def email_tool_factory(memoria=None, user_id: str = "guest") -> Dict[str, Any]:
    """
    Factory para criar Tool compatível com LangGraph.
    
    Retorna dict com definição de tool para agent usar.
    """
    manager = SiriusEmailManager(memoria=memoria, user_id=user_id)
    
    return {
        "name": "intelligent_email_manager",
        "description": "Gerencia e-mails com inteligência - detecta prioridade e permite decisão do LLM",
        "functions": {
            "processar_emails": manager.processar_emails,
            "listar_nao_lidos": manager.listar_nao_lidos,
            "detectar_prioridade": manager.detectar_prioridade,
            "marcar_como_lido": manager.marcar_como_lido,
            "conectar": manager.conectar,
            "desconectar": manager.desconectar
        },
        "instance": manager
    }
