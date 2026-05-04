"""
memoria.py — Memória persistente do Sirius

Otimizações aplicadas:
  - WAL mode (Write-Ahead Logging): escritas não bloqueiam leituras
  - Conexão persistente por banco: abre uma vez, reutiliza sempre
  - PRAGMAs de performance: synchronous=NORMAL, cache_size=2000
  - Cache LRU do histórico em RAM: evita SQLite para leituras frequentes
  - Fila de escrita assíncrona: salvar_historico() retorna imediatamente
  - Batch insert: agrupa múltiplas inserções quando possível
"""

import sqlite3
import os
import threading
import queue
import uuid
import re
from datetime import datetime, timedelta
from collections import deque
from functools import lru_cache


class SiriusMemory:
    def __init__(self, db_pessoal: str = None, db_path: str = None):
        """
        db_pessoal / db_path: caminho do banco pessoal (aliases aceitos).
        Se None, usa o banco padrão (sirius_pessoal.db).
        """
        diretorio_src  = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
        caminho_data   = os.path.join(diretorio_raiz, "data")
        os.makedirs(caminho_data, exist_ok=True)

        # Aceita ambos os nomes de parâmetro
        self.db_pessoal = db_pessoal or db_path or os.path.join(caminho_data, "sirius_pessoal.db")
        self.db_treino  = os.path.join(caminho_data, "sirius_treino.db")

        # Conexões persistentes por banco — uma por thread
        self._local_pessoal = threading.local()
        self._local_treino  = threading.local()
        self._lock_p = threading.Lock()
        self._lock_t = threading.Lock()

        # Cache do histórico em RAM segregado por user_id — evita SQLite para leituras frequentes
        self._cache_historico: dict = {}  # user_id -> deque(maxlen=50)
        self._cache_valido: dict = {}      # user_id -> bool
        self._lock_cache = threading.Lock()
        self.user_id: str = ""
        self.session_id: str = ""

        # Fila de escrita assíncrona — retorna imediatamente, escreve em bg
        self._fila_escrita: queue.Queue = queue.Queue()
        self._worker_escrita = threading.Thread(
            target=self._processar_fila_escrita,
            daemon=True,
            name="SiriusMemoria-Writer"
        )
        self._worker_escrita.start()

        self.inicializar_bancos()

    # -----------------------------------------------------------------------
    # Conexões persistentes com WAL e PRAGMAs de performance
    # -----------------------------------------------------------------------

    def _conn_pessoal(self) -> sqlite3.Connection:
        """Retorna conexão persistente para o banco pessoal da thread atual."""
        if not hasattr(self._local_pessoal, "conn") or self._local_pessoal.conn is None:
            conn = sqlite3.connect(self.db_pessoal, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")         # escritas não bloqueiam leituras
            conn.execute("PRAGMA synchronous=NORMAL")       # mais rápido, ainda seguro
            conn.execute("PRAGMA cache_size=2000")          # 2000 páginas em RAM (~8MB)
            conn.execute("PRAGMA temp_store=MEMORY")        # tabelas temp em RAM
            conn.execute("PRAGMA mmap_size=67108864")       # 64MB mmap
            self._local_pessoal.conn = conn
        return self._local_pessoal.conn

    def _conn_treino(self) -> sqlite3.Connection:
        """Retorna conexão persistente para o banco de treino da thread atual."""
        if not hasattr(self._local_treino, "conn") or self._local_treino.conn is None:
            conn = sqlite3.connect(self.db_treino, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=2000")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local_treino.conn = conn
        return self._local_treino.conn

    # -----------------------------------------------------------------------
    # Inicialização dos bancos
    # -----------------------------------------------------------------------

    def inicializar_bancos(self):
        with self._lock_p:
            conn = self._conn_pessoal()
            # Migração automática para bancos existentes
            try:
                # Adiciona user_id se não existir
                conn.execute("ALTER TABLE conversas ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Coluna já existe
            try:
                conn.execute("ALTER TABLE estado_app ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE conhecimento_geral ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE memoria_permanente ADD COLUMN user_id TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversas (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   TEXT DEFAULT '',
                    role      TEXT,
                    content   TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    sessao    TEXT,
                    session_id TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS macros (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome       TEXT UNIQUE,
                    comandos   TEXT,
                    criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS duvidas (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    pergunta     TEXT UNIQUE,
                    status       TEXT DEFAULT 'pendente',
                    data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_conversas_role ON conversas(role);
                CREATE INDEX IF NOT EXISTS idx_duvidas_status  ON duvidas(status);
                CREATE TABLE IF NOT EXISTS estado_app (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT DEFAULT '',
                    chave      TEXT,
                    valor      TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, chave)
                );
                CREATE TABLE IF NOT EXISTS sessoes (
                    token         TEXT PRIMARY KEY,
                    user_id       TEXT,
                    nome          TEXT,
                    criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ultimo_acesso DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expira_em     DATETIME
                );
                CREATE INDEX IF NOT EXISTS idx_conversas_role ON conversas(role);
                CREATE INDEX IF NOT EXISTS idx_conversas_user_id ON conversas(user_id);
                CREATE INDEX IF NOT EXISTS idx_duvidas_status  ON duvidas(status);
                CREATE INDEX IF NOT EXISTS idx_estado_user_chave ON estado_app(user_id, chave);
                CREATE INDEX IF NOT EXISTS idx_sessoes_user_id ON sessoes(user_id);
            """)
            conn.commit()
            self._migrar_schema(conn)

        with self._lock_t:
            conn = self._conn_treino()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conhecimento_geral (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    tema         TEXT,
                    conteudo     TEXT,
                    validado_por TEXT,
                    data_estudo  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    tags         TEXT,
                    user_id      TEXT
                );
                CREATE TABLE IF NOT EXISTS memoria_permanente (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    conteudo TEXT,
                    tema     TEXT,
                    user_id  TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_conhecimento_tema ON conhecimento_geral(tema);
            """)
            conn.commit()

    # -----------------------------------------------------------------------
    # Fila de escrita assíncrona
    # -----------------------------------------------------------------------

    def _processar_fila_escrita(self):
        """Worker que processa escritas no banco em background."""
        while True:
            try:
                item = self._fila_escrita.get(timeout=1)
                if item is None:
                    break
                fn, args = item
                try:
                    fn(*args)
                except Exception as e:
                    print(f"[MEMORIA]: Erro na fila de escrita: {e}")
                finally:
                    self._fila_escrita.task_done()
            except queue.Empty:
                continue

    def _async(self, fn, *args):
        """Envia operação de escrita para a fila assíncrona."""
        self._fila_escrita.put((fn, args))

    def _exec_async_pessoal(self, sql: str, params: tuple = ()):
        """Factory de escrita assíncrona no banco pessoal."""
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(sql, params)
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro na escrita: {e}")
        self._async(_write)

    def _exec_async_treino(self, sql: str, params: tuple = ()):
        """Factory de escrita assíncrona no banco de treino."""
        def _write():
            try:
                with self._lock_t:
                    conn = self._conn_treino()
                    conn.execute(sql, params)
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro na escrita (treino): {e}")
        self._async(_write)

    def _user_id_val(self) -> str:
        return self.user_id or ""

    def definir_usuario(self, user_id: str, session_id: str | None = None):
        """Define qual usuário e sessão atual esta instância deve usar."""
        user_id = (user_id or "").strip()
        session_id = (session_id or "").strip()
        # Limpa cache anterior quando muda de usuário
        if self.user_id != user_id:
            with self._lock_cache:
                if self.user_id in self._cache_valido:
                    self._cache_valido[self.user_id] = False
        self.user_id = user_id
        self.session_id = session_id

    def _now_iso(self) -> str:
        return datetime.now().isoformat(sep=" ", timespec="seconds")

    def _migrar_schema(self, conn: sqlite3.Connection):
        """Migra esquemas legados para o novo modelo de usuário e sessões."""
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conversas)").fetchall()}
            if "user_id" not in cols:
                conn.execute("ALTER TABLE conversas ADD COLUMN user_id TEXT DEFAULT ''")
            if "session_id" not in cols:
                conn.execute("ALTER TABLE conversas ADD COLUMN session_id TEXT DEFAULT ''")

            cols = {r[1] for r in conn.execute("PRAGMA table_info(estado_app)").fetchall()}
            if "user_id" not in cols:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS estado_app_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT DEFAULT '',
                        chave TEXT,
                        valor TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, chave)
                    );
                """)
                conn.execute(
                    "INSERT INTO estado_app_new (user_id, chave, valor, updated_at) "
                    "SELECT '', chave, valor, updated_at FROM estado_app"
                )
                conn.execute("DROP TABLE estado_app")
                conn.execute("ALTER TABLE estado_app_new RENAME TO estado_app")

            # Migrar tabela conhecimento_geral
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conhecimento_geral)").fetchall()}
            if "user_id" not in cols:
                conn.execute("ALTER TABLE conhecimento_geral ADD COLUMN user_id TEXT DEFAULT ''")

            # Migrar tabela memoria_permanente
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memoria_permanente)").fetchall()}
            if "user_id" not in cols:
                conn.execute("ALTER TABLE memoria_permanente ADD COLUMN user_id TEXT DEFAULT ''")

            conn.commit()
        except Exception as e:
            print(f"[MEMORIA]: Erro ao migrar esquema do banco: {e}")

    def criar_sessao(self, user_id: str, nome: str, duracao_segundos: int = 86400) -> dict:
        token = str(uuid.uuid4())
        agora = datetime.now()
        expira = agora + timedelta(seconds=duracao_segundos)
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                conn.execute(
                    "INSERT OR REPLACE INTO sessoes (token, user_id, nome, criado_em, ultimo_acesso, expira_em) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (token, user_id, nome, agora.isoformat(), agora.isoformat(), expira.isoformat())
                )
                conn.commit()
            return {
                "token": token,
                "user_id": user_id,
                "nome": nome,
                "expira_em": expira.isoformat(),
            }
        except Exception as e:
            print(f"[MEMORIA]: Erro ao criar sessão: {e}")
            return {}

    def validar_sessao(self, token: str) -> dict | None:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                row = conn.execute(
                    "SELECT user_id, nome, criado_em, ultimo_acesso, expira_em "
                    "FROM sessoes WHERE token = ?", (token,)
                ).fetchone()
                if not row:
                    return None
                user_id, nome, criado_em, ultimo_acesso, expira_em = row
                if expira_em and datetime.fromisoformat(expira_em) < datetime.now():
                    conn.execute("DELETE FROM sessoes WHERE token = ?", (token,))
                    conn.commit()
                    return None
                ultimo = datetime.now().isoformat()
                conn.execute(
                    "UPDATE sessoes SET ultimo_acesso = ? WHERE token = ?",
                    (ultimo, token)
                )
                conn.commit()
            return {
                "token": token,
                "user_id": user_id,
                "nome": nome,
                "criado_em": criado_em,
                "ultimo_acesso": ultimo,
                "expira_em": expira_em,
            }
        except Exception as e:
            print(f"[MEMORIA]: Erro ao validar sessão: {e}")
            return None

    def revogar_sessao(self, token: str) -> bool:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                conn.execute("DELETE FROM sessoes WHERE token = ?", (token,))
                conn.commit()
            return True
        except Exception as e:
            print(f"[MEMORIA]: Erro ao revogar sessão: {e}")
            return False

    def obter_historico_db(self, limit: int = 15):
        """
        Retorna as últimas conversas em ordem cronológica do usuário atual.
        Usa cache em RAM segregado por user_id para as últimas 50 — só vai ao SQLite se necessário.
        Thread-safe: protege acesso ao cache com lock.
        """
        user_id = self._user_id_val()
        
        with self._lock_cache:
            # Verifica cache válido para este usuário
            if self._cache_valido.get(user_id, False) and user_id in self._cache_historico:
                cache = self._cache_historico[user_id]
                if len(cache) >= min(limit, len(cache)):
                    items = list(cache)
                    return items[-limit:] if len(items) > limit else items

        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                cursor = conn.execute(
                    "SELECT role, content FROM conversas WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, max(limit, 50))
                )
                linhas = cursor.fetchall()

            historico = [(r, c) for r, c in reversed(linhas)]
            
            # Atualiza cache segregado por user_id com thread-safety
            with self._lock_cache:
                self._cache_historico[user_id] = deque(historico, maxlen=50)
                self._cache_valido[user_id] = True
            
            return historico[-limit:] if len(historico) > limit else historico
        except Exception as e:
            print(f"[MEMORIA]: Falha ao obter histórico para user_id={user_id}: {e}")
            return []

    def _salvar_historico_sync(self, pergunta: str, resposta: str):
        """Escrita real no banco — chamada pelo worker assíncrono.
        Usa prepared statements com ? para segurança contra SQL injection.
        """
        user_id = self._user_id_val()
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                agora = self._now_iso()
                
                # Insert pergunta do usuário
                conn.execute(
                    "INSERT INTO conversas (user_id, role, content, sessao, session_id, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "user", pergunta, self.session_id or "default", self.session_id or "", agora)
                )
                # Insert resposta do assistente
                conn.execute(
                    "INSERT INTO conversas (user_id, role, content, sessao, session_id, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "assistant", resposta, self.session_id or "default", self.session_id or "", agora)
                )
                conn.commit()
            
            # Invalida cache para este usuário
            with self._lock_cache:
                self._cache_valido[user_id] = False
        except Exception as e:
            print(f"[MEMORIA]: Erro ao salvar histórico para user_id={user_id}: {e}")

    def salvar_historico(self, pergunta: str, resposta: str):
        """
        Salva par pergunta/resposta de forma assíncrona e thread-safe.
        Atualiza o cache em RAM imediatamente segregado por user_id.
        Escreve no SQLite em background pela fila assíncrona.
        """
        user_id = self._user_id_val()
        
        # Atualiza cache instantaneamente de forma thread-safe
        with self._lock_cache:
            if user_id not in self._cache_historico:
                self._cache_historico[user_id] = deque(maxlen=50)
            self._cache_historico[user_id].append(("user", pergunta))
            self._cache_historico[user_id].append(("assistant", resposta))
        
        # Escreve no banco de forma assíncrona
        self._async(self._salvar_historico_sync, pergunta, resposta)

    # -----------------------------------------------------------------------
    # Dúvidas
    # -----------------------------------------------------------------------

    def adicionar_duvida(self, pergunta: str) -> bool:
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT OR IGNORE INTO duvidas (pergunta) VALUES (?)",
                        (pergunta.strip(),)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao adicionar dúvida: {e}")
        self._async(_write)
        return True

    def buscar_duvida_pendente(self) -> str | None:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r    = conn.execute(
                    "SELECT pergunta FROM duvidas WHERE status='pendente' ORDER BY id ASC LIMIT 1"
                ).fetchone()
            return r[0] if r else None
        except Exception:
            return None

    def marcar_duvida_como_resolvida(self, pergunta: str):
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute("DELETE FROM duvidas WHERE pergunta=?", (pergunta,))
                    conn.commit()
            except Exception:
                pass
        self._async(_write)

    # -----------------------------------------------------------------------
    # Treino / conhecimento
    # -----------------------------------------------------------------------

    def salvar_estudo_autonomo(self, tema: str, conteudo: str, tags: str = "geral", user_id: str = None) -> bool:
        user_id = user_id or self.user_id
        # Validar user_id
        if user_id and not re.match(r'^[a-zA-Z0-9_-]+$', user_id):
            print(f"[MEMORIA]: user_id inválido: {user_id}")
            return False
        def _write():
            try:
                with self._lock_t:
                    conn = self._conn_treino()
                    conn.execute(
                        "INSERT INTO conhecimento_geral (tema, conteudo, validado_por, tags, user_id) VALUES (?,?,?,?,?)",
                        (tema.lower().strip(), conteudo, "Sirius", tags, user_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao salvar estudo: {e}")
        self._async(_write)
        return True

    def salvar_amostra_treino(self, tema: str, conteudo: str, user_id: str = None) -> bool:
        return self.salvar_estudo_autonomo(tema, conteudo, tags="reforco_manual", user_id=user_id)

    # -----------------------------------------------------------------------
    # Macros
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Estado do app — persiste preferências entre sessões
    # -----------------------------------------------------------------------

    def salvar_estado(self, chave: str, valor: str) -> bool:
        """
        Persiste qualquer estado do app no banco pessoal, segregado por user_id.
        Usa UPSERT — cria ou atualiza a chave.
        Thread-safe com prepared statements.

        Exemplos:
            memoria.salvar_estado("ultimo_modo", "wallpaper")
            memoria.salvar_estado("monitor_idx", "1")
            memoria.salvar_estado("janela_geometria", "100,200,640,820")
        """
        chave_limpa = chave.strip()
        valor_str = str(valor)
        user_id = self._user_id_val()
        
        self._exec_async_pessoal(
            "INSERT INTO estado_app (user_id, chave, valor, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, chave) DO UPDATE SET "
            "    valor      = excluded.valor, "
            "    updated_at = CURRENT_TIMESTAMP",
            (user_id, chave_limpa, valor_str)
        )
        return True

    def carregar_estado(self, chave: str, padrao: str = None) -> str | None:
        """
        Lê um valor de estado salvo, filtrado por user_id atual.
        Retorna padrao se a chave não existir.
        Thread-safe com prepared statements.

        Exemplos:
            modo = memoria.carregar_estado("ultimo_modo", "janela")
            idx  = int(memoria.carregar_estado("monitor_idx", "0"))
        """
        try:
            user_id = self._user_id_val()
            chave_limpa = chave.strip()
            
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT valor FROM estado_app WHERE user_id = ? AND chave = ?",
                    (user_id, chave_limpa)
                ).fetchone()
            return r[0] if r else padrao
        except Exception as e:
            print(f"[MEMORIA]: Erro ao carregar estado '{chave}' para user_id={self._user_id_val()}: {e}")
            return padrao

    def carregar_todos_estados(self) -> dict:
        """Retorna todos os estados salvos como dicionário, filtrado por user_id atual.
        Thread-safe com prepared statements.
        """
        try:
            user_id = self._user_id_val()
            
            with self._lock_p:
                conn = self._conn_pessoal()
                rows = conn.execute(
                    "SELECT chave, valor FROM estado_app WHERE user_id = ?",
                    (user_id,)
                ).fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception as e:
            print(f"[MEMORIA]: Erro ao carregar estados para user_id={self._user_id_val()}: {e}")
            return {}

    def buscar_macro(self, nome: str) -> str | None:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r    = conn.execute(
                    "SELECT comandos FROM macros WHERE nome=?",
                    (nome.lower().strip(),)
                ).fetchone()
            return r[0] if r else None
        except Exception:
            return None

    def salvar_macro(self, nome: str, comandos: str) -> bool:
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT OR REPLACE INTO macros (nome, comandos) VALUES (?,?)",
                        (nome.lower().strip(), comandos.strip())
                    )
                    conn.commit()
            except Exception:
                pass
        self._async(_write)
        return True