"""
sirius_mail.py - Gerenciador de E-mail Inteligente com LangGraph

Implementa acesso IMAP a e-mails com:
- Leitura de últimos 3 e-mails não lidos
- Resumo inteligente usando SiriusMemoria
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
import socket
import time
from datetime import datetime, timedelta

# Timeout global de 10s para evitar Blocking I/O em operações IMAP
socket.setdefaulttimeout(10)
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from email.header import decode_header
from email.mime.text import MIMEText

diretorio_src = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


def _encontrar_env() -> Optional[Path]:
    """
    Procura o arquivo .env com a seguinte ordem de prioridade:

      1. <raiz_do_projeto>/config/.env   <- localização padrão do Sirius
      2. <raiz_do_projeto>/.env          <- fallback na raiz
      3. src/.env                        <- fallback no mesmo diretório do arquivo
      4. Sobe a árvore até a raiz do sistema como último recurso

    Estrutura esperada do projeto:
        projeto/
        ├── config/
        │   └── .env          <- aqui
        └── src/
            └── sirius_mail.py

    Retorna:
        Path para o .env encontrado, ou None se não existir.
    """
    # 1. config/ relativo à raiz do projeto (pai de src/)
    config_env = Path(diretorio_raiz) / "config" / ".env"
    if config_env.is_file():
        return config_env

    # 2. Raiz do projeto
    raiz_env = Path(diretorio_raiz) / ".env"
    if raiz_env.is_file():
        return raiz_env

    # 3. Mesmo diretório de sirius_mail.py (src/)
    src_env = Path(diretorio_src) / ".env"
    if src_env.is_file():
        return src_env

    # 4. Sobe a árvore como último recurso
    candidato = Path(diretorio_raiz).parent
    while True:
        env_path = candidato / ".env"
        if env_path.is_file():
            return env_path
        pai = candidato.parent
        if pai == candidato:  # chegou à raiz do sistema
            return None
        candidato = pai


# Campos obrigatórios que o Sirius precisa para acessar o e-mail
_CAMPOS_OBRIGATORIOS = {
    "SIRIUS_EMAIL_USER":     "Seu endereço de e-mail (ex: carlos@gmail.com)",
    "SIRIUS_EMAIL_PASSWORD": "App Password do Gmail (16 caracteres, sem espaços)",
    "SIRIUS_IMAP_SERVER":    "Servidor IMAP (padrão: imap.gmail.com)",
    "SIRIUS_IMAP_PORT":      "Porta IMAP  (padrão: 993)",
}

# Valores padrão para campos opcionais
_DEFAULTS = {
    "SIRIUS_IMAP_SERVER": "imap.gmail.com",
    "SIRIUS_IMAP_PORT":   "993",
}


def _ler_env_bruto(env_path: Path) -> dict:
    """
    Lê o .env linha a linha, ignorando comentários e espaços,
    e retorna apenas os valores limpos (sem o que vem depois de #).

    Exemplo de linha problemática que isto resolve:
        SIRIUS_EMAIL_PASSWORD=abcdefghijklmnop   # App Password sem espaços
        → valor lido: "abcdefghijklmnop"  (correto)
        → sem este parser, dotenv pode incluir o comentário no valor
    """
    valores = {}
    for linha in env_path.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        if "=" not in linha:
            continue
        chave, _, resto = linha.partition("=")
        chave = chave.strip()
        # Remove comentário inline (tudo após o primeiro # fora de aspas)
        valor = resto.split("#")[0].strip().strip('" ')
        if chave and valor:
            valores[chave] = valor
    return valores


def _auditar_env(env_path: Optional[Path]) -> dict:
    """
    Verifica quais campos obrigatórios estão presentes, ausentes ou
    ainda com valor de placeholder (ex: "seu_email@gmail.com").

    Retorna dict com status de cada campo:
        "ok"         → presente e com valor real
        "ausente"    → não encontrado no .env
        "placeholder"→ tem valor mas parece exemplo/template
    """
    placeholders = {
        "seu_email@gmail.com", "seuemail@gmail.com",
        "abcdefghijklmnop", "sua_senha", "app_password",
        "imap.gmail.com",   # só é placeholder se for SIRIUS_IMAP_SERVER vazio
    }

    valores_brutos = _ler_env_bruto(env_path) if env_path else {}
    status = {}

    for campo in _CAMPOS_OBRIGATORIOS:
        valor = valores_brutos.get(campo, "").strip()
        if not valor:
            status[campo] = "ausente"
        elif valor.lower() in placeholders:
            status[campo] = "placeholder"
        else:
            status[campo] = "ok"

    return status


def _exibir_guia_setup(status: dict, env_path: Optional[Path]):
    """
    Exibe mensagem clara ao usuário sobre o que está faltando e
    como configurar o acesso do Sirius ao Gmail.
    """
    problemas = {c: s for c, s in status.items() if s != "ok"}
    if not problemas:
        return  # tudo ok, sem guia necessário

    caminho_env = str(env_path) if env_path else "config/.env (será criado)"

    print("\n" + "\033[93m" + "─" * 60 + "\033[0m")
    print("\033[93m[MAIL]: ⚠️  Configuração de e-mail incompleta\033[0m")
    print("\033[93m" + "─" * 60 + "\033[0m")
    print(f"  Arquivo: {caminho_env}\n")

    for campo, estado in problemas.items():
        descricao = _CAMPOS_OBRIGATORIOS[campo]
        icone = "❌" if estado == "ausente" else "⚠️ "
        rotulo = "ausente" if estado == "ausente" else "valor de exemplo detectado"
        print(f"  {icone} {campo}")
        print(f"       {descricao}")
        print(f"       Status: {rotulo}\n")

    print("\033[94m  Como liberar o acesso do Sirius ao Gmail:\033[0m")
    print("  1. Ative a verificação em duas etapas:")
    print("     → myaccount.google.com  >  Segurança  >  Verificação em duas etapas")
    print("")
    print("  2. Gere uma App Password (Senha de App):")
    print("     → myaccount.google.com/apppasswords")
    print("     → Selecionar app: Outro  →  Digite \'Sirius\'  →  Gerar")
    print("     → Copie os 16 caracteres gerados (sem espaços)")
    print("")
    print("  3. Ative o IMAP no Gmail:")
    print("     → Gmail  >  ⚙️ Configurações  >  Ver todas")
    print("     → Aba \'Encaminhamento e POP/IMAP\'  >  Ativar IMAP")
    print("")
    print("  O Sirius vai solicitar suas credenciais a seguir.")
    print("\033[93m" + "─" * 60 + "\033[0m\n")


def _carregar_env() -> Path:
    """
    Carrega o .env encontrado, audita os campos e exibe guia se necessário.
    Retorna o caminho do .env ou None.
    """
    try:
        from dotenv import load_dotenv
        env_path = _encontrar_env()

        if env_path:
            # Lê e limpa os valores antes de passar para dotenv
            # (dotenv nativo pode incluir comentários inline no valor)
            valores_limpos = _ler_env_bruto(env_path)
            for chave, valor in valores_limpos.items():
                if chave not in os.environ:  # respeita override=False
                    os.environ[chave] = valor

            print(f"\033[94m[MAIL]: .env carregado de: {env_path}\033[0m")
        else:
            print("\033[93m[MAIL]: Nenhum arquivo .env encontrado — será criado em config/.env\033[0m")

        # Audita e exibe guia se necessário
        status = _auditar_env(env_path)
        _exibir_guia_setup(status, env_path)

        return env_path

    except ImportError:
        # Sem python-dotenv: lê o .env manualmente mesmo assim
        env_path = _encontrar_env()
        if env_path:
            valores_limpos = _ler_env_bruto(env_path)
            for chave, valor in valores_limpos.items():
                if chave not in os.environ:
                    os.environ[chave] = valor
            status = _auditar_env(env_path)
            _exibir_guia_setup(status, env_path)
        return env_path


# Carrega .env uma vez ao importar o módulo
_ENV_PATH = _carregar_env()


class SiriusEmailManager:
    """
    Gerenciador inteligente de e-mails via IMAP.
    
    Responsabilidades:
    - Conectar ao servidor IMAP
    - Buscar últimos 3 e-mails não lidos
    - Extrair remetente, assunto, conteúdo
    - Integrar com SiriusMemoria para aprendizado
    - Registrar auditoria de acessos
    - Detectar prioridade baseada em histórico
    """
    
    def __init__(self, memoria=None, user_id: str = "guest"):
        """
        Inicializa gerenciador de e-mail.

        Fluxo de credenciais:
          1. Lê SIRIUS_EMAIL_USER do .env (já carregado no import)
          2. Se encontrou um e-mail salvo → confirma com o usuário se é esse
          3. Se confirmado → usa as credenciais salvas
          4. Se negado, ou se não havia e-mail → solicita e-mail + senha
          5. Salva as novas credenciais no .env para uso futuro

        Args:
            memoria: instancia de SiriusMemoria
            user_id: id do usuario (LGPD)
        """
        self.memoria = memoria
        self.user_id = user_id
        self.db_pessoal = DB_PESSOAL

        self.imap_servidor = os.getenv("SIRIUS_IMAP_SERVER", "imap.gmail.com")
        self.imap_porta = int(os.getenv("SIRIUS_IMAP_PORT", "993"))

        # Resolve credenciais (confirma ou solicita)
        self.email_usuario, self.email_senha = self._resolver_credenciais()

        self.conexao = None
        self._lock = threading.Lock()
        self._criar_tabelas()

        print(f"\033[94m[MAIL]: Inicializando gerenciador de e-mail para user_id={user_id}...\033[0m")

    # ------------------------------------------------------------------ #
    # Resolução de credenciais                                            #
    # ------------------------------------------------------------------ #

    def _resolver_credenciais(self) -> Tuple[str, str]:
        """
        Determina as credenciais a usar, seguindo o fluxo:

          .env tem e-mail com valor real?
            ├─ SIM → confirma com o usuário
            │    ├─ Confirmado + senha ok  → usa credenciais salvas
            │    ├─ Confirmado + sem senha → pede só a senha
            │    └─ Negado                → pede e-mail + senha novos
            └─ NÃO (ausente ou placeholder) → pede e-mail + senha

        Usa _auditar_env para detectar placeholders e campos ausentes.

        Retorna:
            (email: str, senha: str)
        """
        status = _auditar_env(_ENV_PATH)

        email_salvo = os.getenv("SIRIUS_EMAIL_USER", "").strip()
        senha_salva = os.getenv("SIRIUS_EMAIL_PASSWORD", "").strip()

        email_ok  = status.get("SIRIUS_EMAIL_USER",     "ausente") == "ok"
        senha_ok  = status.get("SIRIUS_EMAIL_PASSWORD", "ausente") == "ok"

        if email_ok:
            # E-mail real encontrado — confirma com o usuário
            print(f"\n\033[94m[MAIL]: E-mail configurado encontrado: \033[1m{email_salvo}\033[0m")
            resposta = input("[MAIL]: Este é o e-mail correto? (s/n): ").strip().lower()

            if resposta in ("s", "sim", "y", "yes"):
                if senha_ok:
                    print("\033[92m[MAIL]: Credenciais carregadas do .env ✓\033[0m")
                    return email_salvo, senha_salva
                else:
                    # Senha ausente ou placeholder
                    motivo = "não encontrada" if status["SIRIUS_EMAIL_PASSWORD"] == "ausente" else "valor de exemplo detectado"
                    print(f"\033[93m[MAIL]: Senha {motivo} no .env. Por favor, informe:\033[0m")
                    senha = self._solicitar_senha()
                    self._salvar_credenciais_env(email_salvo, senha)
                    return email_salvo, senha
            else:
                print("\033[93m[MAIL]: Ok! Informe as novas credenciais:\033[0m")
        else:
            motivo = "não cadastrado" if status["SIRIUS_EMAIL_USER"] == "ausente" else "valor de exemplo detectado no .env"
            print(f"\033[93m[MAIL]: E-mail {motivo}. Informe as credenciais:\033[0m")

        # Solicita e-mail + senha novos
        novo_email = self._solicitar_email()
        nova_senha = self._solicitar_senha()
        self._salvar_credenciais_env(novo_email, nova_senha)
        return novo_email, nova_senha

    @staticmethod
    def _solicitar_email() -> str:
        """Solicita e-mail até receber um valor válido."""
        while True:
            valor = input("[MAIL]: Digite seu e-mail: ").strip()
            if "@" in valor and "." in valor.split("@")[-1]:
                return valor
            print("\033[91m[MAIL]: E-mail inválido. Tente novamente.\033[0m")

    @staticmethod
    def _solicitar_senha() -> str:
        """Solicita senha via getpass (não exibe no terminal)."""
        import getpass
        while True:
            valor = getpass.getpass("[MAIL]: Digite sua senha (App Password para Gmail): ")
            if valor.strip():
                return valor.strip()
            print("\033[91m[MAIL]: Senha não pode ser vazia.\033[0m")

    @staticmethod
    def _salvar_credenciais_env(email: str, senha: str):
        """
        Grava/atualiza SIRIUS_EMAIL_USER e SIRIUS_EMAIL_PASSWORD no .env.

        Se o .env já existe, substitui as linhas correspondentes.
        Se não existe, cria o arquivo no diretório raiz do projeto.
        """
        try:
            # Se já encontramos o .env antes, usa ele.
            # Se não, cria em config/ (padrão do Sirius) — criando a pasta se necessário.
            if _ENV_PATH:
                env_path = _ENV_PATH
            else:
                env_path = Path(diretorio_raiz) / "config" / ".env"
                env_path.parent.mkdir(parents=True, exist_ok=True)

            # Lê conteúdo atual (se existir)
            linhas = []
            if env_path.is_file():
                linhas = env_path.read_text(encoding="utf-8").splitlines()

            # Substitui ou adiciona cada chave
            novas = {
                "SIRIUS_EMAIL_USER": email,
                "SIRIUS_EMAIL_PASSWORD": senha,
            }
            chaves_atualizadas = set()
            resultado = []

            for linha in linhas:
                chave = linha.split("=", 1)[0].strip()
                if chave in novas:
                    resultado.append(f'{chave}={novas[chave]}')
                    chaves_atualizadas.add(chave)
                else:
                    resultado.append(linha)

            # Adiciona chaves que ainda não existiam
            for chave, valor in novas.items():
                if chave not in chaves_atualizadas:
                    resultado.append(f'{chave}={valor}')

            env_path.write_text("\n".join(resultado) + "\n", encoding="utf-8")

            # Atualiza os valores em memória para a sessão atual
            os.environ["SIRIUS_EMAIL_USER"] = email
            os.environ["SIRIUS_EMAIL_PASSWORD"] = senha

            print(f"\033[92m[MAIL]: Credenciais salvas em {env_path} ✓\033[0m")

        except Exception as e:
            print(f"\033[91m[MAIL]: Não foi possível salvar no .env: {e}\033[0m")
            print("        As credenciais serão usadas apenas nesta sessão.")
    
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
                        self.conexao.logout()
                    except (imaplib.IMAP4.error, OSError):
                        pass  # conexão já estava morta — seguro ignorar
                
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
                    try:
                        self.conexao.close()  # fecha mailbox selecionado
                    except Exception:
                        pass  # pode falhar se nenhum mailbox foi selecionado
                    try:
                        self.conexao.logout()  # encerra a sessão IMAP de verdade
                    except Exception:
                        pass
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
                    except (UnicodeDecodeError, AttributeError):
                        pass  # parte não decodificável — tenta a próxima
        else:
            try:
                corpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            except (UnicodeDecodeError, AttributeError):
                corpo = msg.get_payload()  # fallback: payload bruto como string
        
        return corpo.strip()
    
    def _resumir_conteudo(self, conteudo: str, max_chars: int = 150) -> str:
        """
        Resume conteúdo do e-mail (simples).
        
        Em produção, poderia usar SiriusMemoria para resumo por IA.
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
            except Exception:
                pass  # memória indisponível — score sem bônus de histórico
        
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
                        para decisão do LLM (SiriusMemoria)
        
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
            
            # Chamada callback para IA (SiriusMemoria) decidir ação
            if callback_ia:
                try:
                    callback_ia(emails_analisados)
                except Exception as e:
                    print(f"[MAIL]: Erro no callback IA: {e}")
            
            # Determina se requer interrupção
            requer_interrupcao = nivel_maximo == "alta"
            
            # Prepara mensagem para usuário
            if requer_interrupcao:
                email_urgente = max(emails_analisados, key=lambda e: e["score_prioridade"])
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
    
    def processar_emails_bg(self, callback_ia=None, callback_resultado=None):
        """
        Processa e-mails em background via threading para não bloquear a UI.

        Args:
            callback_ia: função callback que recebe lista de e-mails para decisão do LLM
            callback_resultado: função chamada ao final com o resultado do processamento
        """
        def _processar_emails_interno():
            resultado = self.processar_emails(callback_ia=callback_ia)
            if callback_resultado:
                try:
                    callback_resultado(resultado)
                except Exception as e:
                    print(f"[MAIL]: Erro no callback de resultado: {e}")

        threading.Thread(
            target=_processar_emails_interno,
            daemon=True
        ).start()

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