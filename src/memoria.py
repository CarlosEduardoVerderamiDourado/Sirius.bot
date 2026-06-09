"""
memoria.py — S.I.R.I.U.S. v5.2
================================
Classe principal: SiriusMemoria  ← nome exato exigido pelo cerebro.py

Tabelas gerenciadas:
  • conversas          — histórico de diálogo (pessoal)
  • estado_sistema     — estado persistente do app
  • estudos_autonomos  — conhecimento gerado pelo autodidata
  • macros             — atalhos de ação registrados pelo Carlos
  • duvidas            — perguntas pendentes de resposta
  • sessoes            — tokens de autenticação

Otimizações mantidas da versão backup (SiriusMemoria):
  • WAL mode (escritas não bloqueiam leituras)
  • Conexão persistente por banco (threading.local)
  • PRAGMAs de performance (synchronous=NORMAL, cache_size=2000)
  • Cache LRU do histórico em RAM por user_id
  • Fila de escrita assíncrona (retorno imediato, I/O em background)
  • Migração automática de schema (ALTER TABLE IF NOT EXISTS)

Compatibilidade:
  • cerebro.py usa: from memoria import SiriusMemoria
  • sirius_autodidata.py usa: SiriusMemoria()
  • sirius_api.py usa: SiriusMemoria() para auth e status
"""

from __future__ import annotations

import os
import re
import queue
import sqlite3
import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Optional


# =============================================================================
# Schema SQL — todas as tabelas com criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
# =============================================================================

_SQL_SCHEMA_PESSOAL = """
-- ── Conversas ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    DEFAULT '',
    role       TEXT    NOT NULL,          -- 'user' ou 'assistant'
    content    TEXT    NOT NULL,
    sessao     TEXT    DEFAULT '',
    session_id TEXT    DEFAULT '',
    criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conversas_user_id  ON conversas(user_id);
CREATE INDEX IF NOT EXISTS idx_conversas_role     ON conversas(role);
CREATE INDEX IF NOT EXISTS idx_conversas_criado   ON conversas(criado_em);

-- ── Estado do sistema ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS estado_sistema (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    DEFAULT '',
    chave      TEXT    NOT NULL,
    valor      TEXT,
    criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, chave)
);
CREATE INDEX IF NOT EXISTS idx_estado_user_chave ON estado_sistema(user_id, chave);

-- ── Macros ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macros (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nome      TEXT    UNIQUE,
    comandos  TEXT,
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── Dúvidas pendentes ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS duvidas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pergunta  TEXT    UNIQUE,
    status    TEXT    DEFAULT 'pendente',
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_duvidas_status ON duvidas(status);

-- ── Sessões / autenticação ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessoes (
    token         TEXT PRIMARY KEY,
    user_id       TEXT,
    nome          TEXT,
    criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
    ultimo_acesso DATETIME DEFAULT CURRENT_TIMESTAMP,
    expira_em     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_sessoes_user_id ON sessoes(user_id);

-- ── Usuários ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usuarios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    UNIQUE NOT NULL,
    nome       TEXT    NOT NULL,
    senha_hash TEXT,
    criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_SQL_SCHEMA_TREINO = """
-- ── Estudos autônomos ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS estudos_autonomos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    DEFAULT '',
    tema         TEXT    NOT NULL,
    conteudo     TEXT    NOT NULL,
    tags         TEXT    DEFAULT 'geral',
    validado_por TEXT    DEFAULT 'Sirius',
    fonte        TEXT    DEFAULT 'autodidata',
    criado_em    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_estudos_tema     ON estudos_autonomos(tema);
CREATE INDEX IF NOT EXISTS idx_estudos_user_id  ON estudos_autonomos(user_id);
CREATE INDEX IF NOT EXISTS idx_estudos_criado   ON estudos_autonomos(criado_em);

-- ── Conhecimento geral (alias legado — usado pelo sirius_autodidata.py) ──────
CREATE TABLE IF NOT EXISTS conhecimento_geral (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    DEFAULT '',
    tema         TEXT,
    conteudo     TEXT,
    validado_por TEXT    DEFAULT 'Sirius',
    tags         TEXT    DEFAULT 'geral',
    data_estudo  DATETIME DEFAULT CURRENT_TIMESTAMP,
    criado_em    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ── Memória permanente ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memoria_permanente (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT    DEFAULT '',
    conteudo  TEXT,
    tema      TEXT,
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


# =============================================================================
# SiriusMemoria
# =============================================================================

class SiriusMemoria:
    """
    Memória persistente do S.I.R.I.U.S. v5.2.

    Uso básico:
        mem = SiriusMemoria()
        mem.salvar_historico("oi", "olá, Carlos!")
        hist = mem.obter_historico(limite=10)
        mem.salvar_estado("modo", "wallpaper")
        mem.salvar_estudo_autonomo(tema="Python", conteudo="linguagem de alto nível...")
    """

    def __init__(self, db_pessoal: str = None, db_path: str = None):
        # ── Caminhos dos bancos ───────────────────────────────────────────────
        dir_src  = os.path.dirname(os.path.abspath(__file__))
        dir_raiz = os.path.dirname(dir_src)
        dir_data = os.path.join(dir_raiz, "data")
        os.makedirs(dir_data, exist_ok=True)

        # Aceita ambos os parâmetros (compatibilidade com cerebro.py)
        self.db_pessoal = (
            db_pessoal
            or db_path
            or os.path.join(dir_data, "sirius_pessoal.db")
        )
        self.db_treino = os.path.join(dir_data, "sirius_treino.db")

        # ── Conexões persistentes por thread ──────────────────────────────────
        self._local_pessoal = threading.local()
        self._local_treino  = threading.local()
        self._lock_p = threading.Lock()
        self._lock_t = threading.Lock()

        # ── Cache do histórico em RAM (evita leitura constante do SQLite) ─────
        self._cache_historico: dict[str, deque] = {}
        self._lock_cache = threading.Lock()

        # ── Contexto de sessão ────────────────────────────────────────────────
        self.user_id:    str = ""
        self.session_id: str = ""

        # ── Fila de escrita assíncrona ────────────────────────────────────────
        self._fila_escrita: queue.Queue = queue.Queue()
        self._worker_escrita = threading.Thread(
            target=self._processar_fila_escrita,
            daemon=True,
            name="SiriusMemoria-Writer",
        )
        self._worker_escrita.start()

        # ── Inicializa schema ─────────────────────────────────────────────────
        self._inicializar_bancos()

    # =========================================================================
    # Conexões persistentes (WAL + PRAGMAs de performance)
    # =========================================================================

    def _conn_pessoal(self) -> sqlite3.Connection:
        if not getattr(self._local_pessoal, "conn", None):
            conn = sqlite3.connect(self.db_pessoal, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=2000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=67108864")
            self._local_pessoal.conn = conn
        return self._local_pessoal.conn

    def _conn_treino(self) -> sqlite3.Connection:
        if not getattr(self._local_treino, "conn", None):
            conn = sqlite3.connect(self.db_treino, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=2000")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local_treino.conn = conn
        return self._local_treino.conn

    # =========================================================================
    # Schema e migração
    # =========================================================================

    def _inicializar_bancos(self):
        """Cria todas as tabelas e aplica migrações automáticas."""
        with self._lock_p:
            conn = self._conn_pessoal()
            self._migrar_pessoal(conn)
            conn.executescript(_SQL_SCHEMA_PESSOAL)
            conn.commit()

        with self._lock_t:
            conn = self._conn_treino()
            self._migrar_treino(conn)       # ← CRÍTICO: migra ANTES do CREATE IF NOT EXISTS
            conn.executescript(_SQL_SCHEMA_TREINO)
            conn.commit()

    # ── Colunas que precisam existir em cada banco ────────────────────────────
    #
    # Regra: o CREATE TABLE IF NOT EXISTS só age em tabelas NOVAS.
    # Para tabelas já existentes sem a coluna, é obrigatório ALTER TABLE.
    # _migrar_treino resolve o ponto crítico: estudos_autonomos.criado_em.

    _MIGRACOES_PESSOAL = [
        ("conversas",      "user_id",    "TEXT DEFAULT ''"),
        ("conversas",      "session_id", "TEXT DEFAULT ''"),
        ("conversas",      "sessao",     "TEXT DEFAULT ''"),
        ("conversas",      "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("estado_sistema", "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("estado_sistema", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("estado_app",     "user_id",    "TEXT DEFAULT ''"),   # tabela legado
        ("macros",         "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("duvidas",        "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ]

    _MIGRACOES_TREINO = [
        # ← estas três linhas resolvem o erro de log reportado
        ("estudos_autonomos",  "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("estudos_autonomos",  "user_id",      "TEXT DEFAULT ''"),
        ("estudos_autonomos",  "tags",         "TEXT DEFAULT 'geral'"),
        ("estudos_autonomos",  "validado_por", "TEXT DEFAULT 'Sirius'"),
        ("estudos_autonomos",  "fonte",        "TEXT DEFAULT 'autodidata'"),
        ("conhecimento_geral", "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("conhecimento_geral", "user_id",      "TEXT DEFAULT ''"),
        ("conhecimento_geral", "tags",         "TEXT DEFAULT 'geral'"),
        ("memoria_permanente", "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("memoria_permanente", "user_id",      "TEXT DEFAULT ''"),
    ]

    def _aplicar_alter_table(
        self,
        conn: sqlite3.Connection,
        migracoes: list,
        banco_nome: str,
    ):
        """
        Aplica ALTER TABLE ADD COLUMN para cada entrada em migracoes.
        Seguro para rodar múltiplas vezes — ignora 'duplicate column name'.
        Não age em tabelas que ainda não existem (serão criadas pelo schema).
        """
        for tabela, coluna, definicao in migracoes:
            existe = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name=?",
                (tabela,),
            ).fetchone()[0]
            if not existe:
                continue   # será criada pelo executescript a seguir

            try:
                conn.execute(
                    f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}"
                )
                conn.commit()
                print(
                    f"\033[92m[MEMORIA]: migração — {banco_nome}.{tabela}"
                    f".{coluna} adicionada.\033[0m"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    pass   # coluna já existe — comportamento normal
                else:
                    print(
                        f"[MEMORIA]: AVISO em {banco_nome}.{tabela}"
                        f".{coluna}: {e}"
                    )

    def _migrar_pessoal(self, conn: sqlite3.Connection):
        """Migra o banco pessoal (sirius_pessoal.db)."""
        # Renomeia estado_app → estado_sistema se necessário
        tem_app = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='estado_app'"
        ).fetchone()[0]
        tem_sistema = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='estado_sistema'"
        ).fetchone()[0]

        if tem_app and not tem_sistema:
            try:
                conn.execute("ALTER TABLE estado_app RENAME TO estado_sistema")
                conn.commit()
                print("[MEMORIA]: migração — estado_app → estado_sistema")
            except sqlite3.OperationalError:
                pass

        self._aplicar_alter_table(conn, self._MIGRACOES_PESSOAL, "pessoal")

    def _migrar_treino(self, conn: sqlite3.Connection):
        """
        Migra o banco de treino (sirius_treino.db).
        Ponto crítico: garante criado_em em estudos_autonomos.
        """
        self._aplicar_alter_table(conn, self._MIGRACOES_TREINO, "treino")

    # =========================================================================
    # Fila de escrita assíncrona
    # =========================================================================

    def _processar_fila_escrita(self):
        """Worker daemon — drena a fila de escrita em background."""
        while True:
            try:
                fn, args = self._fila_escrita.get(timeout=1)
                fn(*args)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[MEMORIA-WRITER]: Erro: {e}")

    def _async(self, fn, *args):
        """Enfileira uma escrita para execução em background."""
        self._fila_escrita.put((fn, args))

    def _user_id_val(self) -> str:
        return (self.user_id or "").strip()

    # =========================================================================
    # salvar_historico / obter_historico
    # =========================================================================

    def salvar_historico(self, usuario: str, sirius: str):
        """
        Persiste um par pergunta/resposta no banco pessoal.
        Atualiza o cache em RAM imediatamente; escrita vai em background.

        Parâmetros aceitos pelo cerebro.py:
            memoria.salvar_historico(pergunta, resposta)
        """
        user_id = self._user_id_val()

        # Atualiza cache imediatamente
        with self._lock_cache:
            if user_id not in self._cache_historico:
                self._cache_historico[user_id] = deque(maxlen=50)
            self._cache_historico[user_id].append(("user",      usuario))
            self._cache_historico[user_id].append(("assistant", sirius))

        self._async(self._salvar_historico_sync, usuario, sirius, user_id)

    def _salvar_historico_sync(self, usuario: str, sirius: str, user_id: str):
        try:
            sid = self.session_id or ""
            with self._lock_p:
                conn = self._conn_pessoal()
                conn.executemany(
                    "INSERT INTO conversas (user_id, role, content, session_id) VALUES (?,?,?,?)",
                    [
                        (user_id, "user",      usuario, sid),
                        (user_id, "assistant", sirius,  sid),
                    ],
                )
                conn.commit()
        except Exception as e:
            print(f"[MEMORIA]: Erro ao salvar histórico: {e}")

    def obter_historico(self, limite: int = 10, user_id: str = None) -> list[dict]:
        """
        Retorna os últimos N pares de conversa como lista de dicts:
            [{"usuario": "...", "sirius": "..."}, ...]
        """
        uid = (user_id or self._user_id_val())

        # Tenta servir do cache
        with self._lock_cache:
            if uid in self._cache_historico:
                msgs = list(self._cache_historico[uid])
                pares = []
                i = 0
                while i + 1 < len(msgs):
                    role_a, txt_a = msgs[i]
                    role_b, txt_b = msgs[i + 1]
                    if role_a == "user" and role_b == "assistant":
                        pares.append({"usuario": txt_a, "sirius": txt_b})
                        i += 2
                    else:
                        i += 1
                return pares[-limite:]

        # Fallback: lê do banco
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                rows = conn.execute(
                    "SELECT role, content FROM conversas "
                    "WHERE user_id=? ORDER BY id DESC LIMIT ?",
                    (uid, limite * 2),
                ).fetchall()
            rows = list(reversed(rows))
            pares = []
            i = 0
            while i + 1 < len(rows):
                if rows[i][0] == "user" and rows[i + 1][0] == "assistant":
                    pares.append({"usuario": rows[i][1], "sirius": rows[i + 1][1]})
                    i += 2
                else:
                    i += 1
            return pares[-limite:]
        except Exception as e:
            print(f"[MEMORIA]: Erro ao obter histórico: {e}")
            return []

    # =========================================================================
    # salvar_estado / carregar_estado
    # =========================================================================

    def salvar_estado(self, chave: str, valor) -> bool:
        """
        Persiste qualquer estado do app (UPSERT) na tabela estado_sistema.
        Thread-safe; escrita assíncrona.

        Exemplos:
            mem.salvar_estado("ultimo_modo", "wallpaper")
            mem.salvar_estado("monitor_idx", "1")
        """
        chave_limpa = chave.strip()
        valor_str   = str(valor)
        user_id     = self._user_id_val()

        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT INTO estado_sistema (user_id, chave, valor, updated_at) "
                        "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(user_id, chave) DO UPDATE SET "
                        "    valor      = excluded.valor, "
                        "    updated_at = CURRENT_TIMESTAMP",
                        (user_id, chave_limpa, valor_str),
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao salvar estado '{chave}': {e}")

        self._async(_write)
        return True

    def carregar_estado(self, chave: str, padrao: str = None) -> Optional[str]:
        """
        Lê um valor de estado salvo para o user_id atual.
        Retorna padrao se a chave não existir.
        """
        try:
            user_id = self._user_id_val()
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT valor FROM estado_sistema "
                    "WHERE user_id=? AND chave=?",
                    (user_id, chave.strip()),
                ).fetchone()
            return r[0] if r else padrao
        except Exception as e:
            print(f"[MEMORIA]: Erro ao carregar estado '{chave}': {e}")
            return padrao

    def carregar_todos_estados(self) -> dict:
        """Retorna todos os estados do user_id atual como dicionário."""
        try:
            user_id = self._user_id_val()
            with self._lock_p:
                conn = self._conn_pessoal()
                rows = conn.execute(
                    "SELECT chave, valor FROM estado_sistema WHERE user_id=?",
                    (user_id,),
                ).fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception as e:
            print(f"[MEMORIA]: Erro ao carregar estados: {e}")
            return {}

    # =========================================================================
    # salvar_estudo_autonomo
    # =========================================================================

    def salvar_estudo_autonomo(
        self,
        tema: str,
        conteudo: str,
        tags: str = "geral",
        user_id: str = None,
        fonte: str = "autodidata",
    ) -> bool:
        """
        Persiste um estudo autônomo na tabela estudos_autonomos (banco treino).
        Também insere em conhecimento_geral para compatibilidade com o autodidata legado.

        CRÍTICO: parâmetros sempre 'tema' e 'conteudo' — nunca 'dados'/'texto'.
        Escrita assíncrona — retorna True imediatamente.
        """
        if not tema or not conteudo:
            return False

        uid = (user_id or self._user_id_val())

        # Valida user_id (previne SQL injection via formato)
        if uid and not re.match(r"^[a-zA-Z0-9_\-]*$", uid):
            print(f"[MEMORIA]: user_id inválido ignorado: {uid!r}")
            uid = ""

        tema_limpo     = tema.lower().strip()
        conteudo_limpo = conteudo.strip()
        tags_limpo     = (tags or "geral").strip()

        def _write():
            try:
                with self._lock_t:
                    conn = self._conn_treino()
                    # Tabela principal (v5.2)
                    conn.execute(
                        "INSERT INTO estudos_autonomos "
                        "(user_id, tema, conteudo, tags, validado_por, fonte) "
                        "VALUES (?,?,?,?,?,?)",
                        (uid, tema_limpo, conteudo_limpo, tags_limpo, "Sirius", fonte),
                    )
                    # Tabela legado (compatibilidade sirius_autodidata.py)
                    conn.execute(
                        "INSERT INTO conhecimento_geral "
                        "(user_id, tema, conteudo, validado_por, tags) "
                        "VALUES (?,?,?,?,?)",
                        (uid, tema_limpo, conteudo_limpo, "Sirius", tags_limpo),
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao salvar estudo '{tema_limpo[:40]}': {e}")

        self._async(_write)
        return True

    # =========================================================================
    # Macros
    # =========================================================================

    def salvar_macro(self, nome: str, comandos: str) -> bool:
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT OR REPLACE INTO macros (nome, comandos) VALUES (?,?)",
                        (nome.lower().strip(), comandos.strip()),
                    )
                    conn.commit()
            except Exception:
                pass
        self._async(_write)
        return True

    def buscar_macro(self, nome: str) -> Optional[str]:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT comandos FROM macros WHERE nome=?",
                    (nome.lower().strip(),),
                ).fetchone()
            return r[0] if r else None
        except Exception:
            return None

    # =========================================================================
    # Dúvidas pendentes
    # =========================================================================

    def adicionar_duvida(self, pergunta: str) -> bool:
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT OR IGNORE INTO duvidas (pergunta) VALUES (?)",
                        (pergunta.strip(),),
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao adicionar dúvida: {e}")
        self._async(_write)
        return True

    def buscar_duvida_pendente(self) -> Optional[str]:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT pergunta FROM duvidas "
                    "WHERE status='pendente' ORDER BY id ASC LIMIT 1"
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

    # =========================================================================
    # Sessões / autenticação (usado pelo sirius_api.py)
    # =========================================================================

    def criar_sessao(self, user_id: str, nome: str = "", horas: int = 24) -> str:
        token = str(uuid.uuid4())
        def _write():
            try:
                with self._lock_p:
                    conn = self._conn_pessoal()
                    conn.execute(
                        "INSERT INTO sessoes (token, user_id, nome, expira_em) "
                        "VALUES (?, ?, ?, datetime('now', ?))",
                        (token, user_id, nome, f"+{horas} hours"),
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MEMORIA]: Erro ao criar sessão: {e}")
        self._async(_write)
        return token

    def validar_sessao(self, token: str) -> Optional[dict]:
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT user_id, nome, expira_em FROM sessoes "
                    "WHERE token=? AND (expira_em IS NULL OR expira_em > datetime('now'))",
                    (token,),
                ).fetchone()
            if not r:
                return None
            # Atualiza último acesso em background
            def _touch():
                try:
                    with self._lock_p:
                        c = self._conn_pessoal()
                        c.execute(
                            "UPDATE sessoes SET ultimo_acesso=CURRENT_TIMESTAMP WHERE token=?",
                            (token,),
                        )
                        c.commit()
                except Exception:
                    pass
            self._async(_touch)
            return {"user_id": r[0], "nome": r[1], "expira_em": r[2]}
        except Exception:
            return None

    def validar_usuario(self, user_id: str, senha_hash: str) -> Optional[dict]:
        """Valida credenciais na tabela usuarios. Retorna dict ou None."""
        try:
            with self._lock_p:
                conn = self._conn_pessoal()
                r = conn.execute(
                    "SELECT user_id, nome FROM usuarios "
                    "WHERE user_id=? AND senha_hash=?",
                    (user_id.strip(), senha_hash),
                ).fetchone()
            return {"user_id": r[0], "nome": r[1]} if r else None
        except Exception:
            return None

    # =========================================================================
    # Alias legado — garante compatibilidade com código antigo
    # =========================================================================

    def salvar_amostra_treino(self, tema: str, conteudo: str, user_id: str = None) -> bool:
        return self.salvar_estudo_autonomo(tema, conteudo, tags="reforco_manual", user_id=user_id)


# =============================================================================
# Alias de compatibilidade — caso algum módulo ainda importe SiriusMemoria
# =============================================================================
SiriusMemory = SiriusMemoria


# =============================================================================
# Standalone — smoke test
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  S.I.R.I.U.S. v5.2 — memoria.py smoke test")
    print("=" * 60)

    mem = SiriusMemoria()

    mem.salvar_historico("oi Sirius", "Olá, Carlos. Pronto.")
    mem.salvar_estado("modo_atual", "janela")
    mem.salvar_estudo_autonomo(
        tema="Python async",
        conteudo="asyncio permite concorrência sem threads — ideal para I/O bound.",
        tags="programacao",
    )

    import time; time.sleep(0.3)  # deixa os workers assíncronos terminarem

    hist = mem.obter_historico(limite=5)
    modo = mem.carregar_estado("modo_atual")

    print(f"\n  Histórico ({len(hist)} par(es)): {hist}")
    print(f"  Estado modo_atual: {modo}")
    print("\n  ✓ Todos os métodos OK.")
    print("=" * 60)