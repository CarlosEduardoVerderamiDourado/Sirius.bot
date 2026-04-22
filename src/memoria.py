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

        # Cache do histórico em RAM — evita SQLite para contexto de sessão
        self._cache_historico: deque = deque(maxlen=50)
        self._cache_valido = False

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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversas (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    role      TEXT,
                    content   TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    sessao    TEXT
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
            """)
            conn.commit()

        with self._lock_t:
            conn = self._conn_treino()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conhecimento_geral (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    tema         TEXT,
                    conteudo     TEXT,
                    validado_por TEXT,
                    data_estudo  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    tags         TEXT
                );
                CREATE TABLE IF NOT EXISTS memoria_permanente (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    conteudo TEXT,
                    tema     TEXT
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

    def _make_writer(self, sql: str, params: tuple = (), banco: str = 'p',
                     tag: str = "") -> callable:
        """
        Factory de funções de escrita assíncrona.

        Elimina o padrão repetitivo de definir _write() dentro de cada método:
          ANTES (40 linhas de boilerplate para 4 métodos):
            def adicionar_duvida(self, pergunta):
                def _write():
                    try:
                        with self._lock_p:
                            conn = self._conn_pessoal()
                            conn.execute(SQL, (pergunta,))
                            conn.commit()
                    except Exception as e: print(...)
                self._async(_write)

          DEPOIS (1 linha por método):
            def adicionar_duvida(self, pergunta):
                self._async(self._make_writer(SQL, (pergunta,)))

        Parâmetros:
          sql:    SQL a executar
          params: tupla de parâmetros para o SQL
          banco:  'p' = pessoal (padrão)  |  't' = treino
          tag:    prefixo para mensagem de erro (opcional)

        Retorna:
          Callable () → None  que executa sql com params no banco correto.
        """
        if banco == 't':
            get_conn = self._conn_treino
            lock     = self._lock_t
        else:
            get_conn = self._conn_pessoal
            lock     = self._lock_p

        def _writer():
            try:
                with lock:
                    conn = get_conn()
                    conn.execute(sql, params)
                    conn.commit()
            except Exception as e:
                prefixo = f"[MEMORIA{f'/{tag}' if tag else ''}]"
                print(f"{prefixo}: Erro ao escrever: {e}")

        return _writer

    # -----------------------------------------------------------------------
    # Histórico — com cache em RAM
    # -----------------------------------------------------------------------

    def obter_historico_db(self, limit: int = 15):
        """
        Retorna as últimas conversas em ordem cronológica.
        Usa cache em RAM para as últimas 50 — só vai ao SQLite se necessário.
        """
        if self._cache_valido and len(self._cache_historico) >= min(limit, len(self._cache_historico)):
            items = list(self._cache_historico)
            return items[-limit:] if len(items) > limit else items

        try:
            with self._lock_p:
                conn   = self._conn_pessoal()
                cursor = conn.execute(
                    "SELECT role, content FROM conversas ORDER BY id DESC LIMIT ?",
                    (max(limit, 50),)
                )
                linhas = cursor.fetchall()

            historico = [(r, c) for r, c in reversed(linhas)]
            self._cache_historico = deque(historico, maxlen=50)
            self._cache_valido    = True
            return historico[-limit:] if len(historico) > limit else historico
        except Exception as e:
            print(f"[MEMORIA]: Falha ao obter histórico: {e}")
            return []

    def _salvar_historico_sync(self, pergunta: str, resposta: str):
        """Escrita real no banco — chamada pelo worker assíncrono."""
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                conn.execute(
                    "INSERT INTO conversas (role, content, sessao) VALUES (?, ?, ?)",
                    ("user", pergunta, "geral")
                )
                conn.execute(
                    "INSERT INTO conversas (role, content, sessao) VALUES (?, ?, ?)",
                    ("assistant", resposta, "geral")
                )
                conn.commit()
            # Invalida cache
            self._cache_valido = False
        except Exception as e:
            print(f"[MEMORIA]: Erro ao salvar histórico: {e}")

    def salvar_historico(self, pergunta: str, resposta: str):
        """
        Salva par pergunta/resposta.
        Atualiza o cache em RAM imediatamente e escreve no SQLite em background.
        """
        # Atualiza cache instantaneamente
        self._cache_historico.append(("user",      pergunta))
        self._cache_historico.append(("assistant", resposta))
        # Escreve no banco de forma assíncrona
        self._async(self._salvar_historico_sync, pergunta, resposta)

    # -----------------------------------------------------------------------
    # Dúvidas
    # -----------------------------------------------------------------------

    def adicionar_duvida(self, pergunta: str) -> bool:
        self._async(self._make_writer(
            "INSERT OR IGNORE INTO duvidas (pergunta) VALUES (?)",
            (pergunta.strip(),), tag="duvida"
        ))
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
        self._async(self._make_writer(
            "DELETE FROM duvidas WHERE pergunta=?",
            (pergunta,), tag="duvida_del"
        ))

    # -----------------------------------------------------------------------
    # Treino / conhecimento
    # -----------------------------------------------------------------------

    def salvar_estudo_autonomo(self, tema: str, conteudo: str, tags: str = "geral") -> bool:
        self._async(self._make_writer(
            "INSERT INTO conhecimento_geral (tema, conteudo, validado_por, tags) VALUES (?,?,?,?)",
            (tema.lower().strip(), conteudo, "Sirius", tags),
            banco='t', tag="estudo"
        ))
        return True

    def salvar_amostra_treino(self, tema: str, conteudo: str) -> bool:
        return self.salvar_estudo_autonomo(tema, conteudo, tags="reforco_manual")

    # -----------------------------------------------------------------------
    # Macros
    # -----------------------------------------------------------------------

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
        self._async(self._make_writer(
            "INSERT OR REPLACE INTO macros (nome, comandos) VALUES (?,?)",
            (nome.lower().strip(), comandos.strip()), tag="macro"
        ))
        return True