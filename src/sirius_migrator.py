"""
sirius_migrator.py — S.I.R.I.U.S. v5.2 — Migração de Schema
=============================================================
Resolve o ponto crítico 1: garante que o banco de dados ATIVO
(já existente em data/) possui todas as colunas exigidas pelo
sirius_autodidata.py e sirius_api.py, em especial `criado_em`
na tabela `estudos_autonomos`.

Execução autônoma (roda antes de qualquer outro módulo):
    python sirius_migrator.py

Também importável (chamado pelo sirius_boot.py):
    from sirius_migrator import migrar_tudo
    migrar_tudo()

Estratégia:
  • ALTER TABLE ADD COLUMN IF NOT EXISTS — nunca destrói dados
  • CREATE TABLE IF NOT EXISTS           — cria tabelas ausentes
  • Sem DROP, sem TRUNCATE, sem apagamento de nada
  • Idempotente — pode rodar N vezes sem efeito colateral
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import NamedTuple


# =============================================================================
# Caminhos dos bancos
# =============================================================================

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)

# Suporta rodar de src/ ou da raiz do projeto
_CANDIDATOS_DATA = [
    os.path.join(_DIR_RAIZ, "data"),
    os.path.join(_DIR_SRC,  "data"),
    os.path.join(_DIR_SRC,  "..", "data"),
]

def _achar_data_dir() -> str:
    for c in _CANDIDATOS_DATA:
        norm = os.path.normpath(c)
        if os.path.isdir(norm):
            return norm
    # Cria no local padrão se não existir
    padrao = os.path.normpath(_CANDIDATOS_DATA[0])
    os.makedirs(padrao, exist_ok=True)
    return padrao

_DIR_DATA    = _achar_data_dir()
_DB_PESSOAL  = os.path.join(_DIR_DATA, "sirius_pessoal.db")
_DB_TREINO   = os.path.join(_DIR_DATA, "sirius_treino.db")


# =============================================================================
# Cores ANSI (sem dependência externa)
# =============================================================================

_C = {
    "ok":   "\033[92m",   # verde
    "warn": "\033[93m",   # amarelo
    "err":  "\033[91m",   # vermelho
    "info": "\033[96m",   # ciano
    "bold": "\033[1m",
    "rst":  "\033[0m",
}

def _ok(msg):   print(f"{_C['ok']}  ✓ {msg}{_C['rst']}")
def _warn(msg): print(f"{_C['warn']}  ⚠ {msg}{_C['rst']}")
def _err(msg):  print(f"{_C['err']}  ✗ {msg}{_C['rst']}")
def _info(msg): print(f"{_C['info']}  → {msg}{_C['rst']}")


# =============================================================================
# Definição declarativa das migrações necessárias
# =============================================================================

class ColunaMigracao(NamedTuple):
    tabela: str
    coluna: str
    definicao: str   # tipo + default completo


# ── Banco pessoal (sirius_pessoal.db) ────────────────────────────────────────
_MIGRACOES_PESSOAL: list[ColunaMigracao] = [
    ColunaMigracao("conversas",      "user_id",    "TEXT DEFAULT ''"),
    ColunaMigracao("conversas",      "session_id", "TEXT DEFAULT ''"),
    ColunaMigracao("conversas",      "sessao",     "TEXT DEFAULT ''"),
    ColunaMigracao("conversas",      "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("estado_app",     "user_id",    "TEXT DEFAULT ''"),       # tabela legado
    ColunaMigracao("estado_app",     "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("estado_sistema", "user_id",    "TEXT DEFAULT ''"),
    ColunaMigracao("estado_sistema", "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("estado_sistema", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("macros",         "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("duvidas",        "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("sessoes",        "criado_em",  "DATETIME DEFAULT CURRENT_TIMESTAMP"),
]

# ── Banco de treino (sirius_treino.db) ────────────────────────────────────────
_MIGRACOES_TREINO: list[ColunaMigracao] = [
    # Tabela nova v5.2 — criada se não existir (sem perda de dados)
    # As ALTER TABLE abaixo cobrem bancos que já tinham a tabela sem criado_em
    ColunaMigracao("estudos_autonomos",  "user_id",      "TEXT DEFAULT ''"),
    ColunaMigracao("estudos_autonomos",  "tags",         "TEXT DEFAULT 'geral'"),
    ColunaMigracao("estudos_autonomos",  "validado_por", "TEXT DEFAULT 'Sirius'"),
    ColunaMigracao("estudos_autonomos",  "fonte",        "TEXT DEFAULT 'autodidata'"),
    ColunaMigracao("estudos_autonomos",  "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),  # ← crítico
    ColunaMigracao("conhecimento_geral", "user_id",      "TEXT DEFAULT ''"),
    ColunaMigracao("conhecimento_geral", "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ColunaMigracao("conhecimento_geral", "tags",         "TEXT DEFAULT 'geral'"),
    ColunaMigracao("memoria_permanente", "user_id",      "TEXT DEFAULT ''"),
    ColunaMigracao("memoria_permanente", "criado_em",    "DATETIME DEFAULT CURRENT_TIMESTAMP"),
]

# ── Tabelas que devem existir no banco de treino (criadas se ausentes) ────────
_SCHEMA_TREINO_GARANTIDO = """
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
CREATE INDEX IF NOT EXISTS idx_estudos_tema    ON estudos_autonomos(tema);
CREATE INDEX IF NOT EXISTS idx_estudos_user_id ON estudos_autonomos(user_id);
CREATE INDEX IF NOT EXISTS idx_estudos_criado  ON estudos_autonomos(criado_em);

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

CREATE TABLE IF NOT EXISTS memoria_permanente (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT    DEFAULT '',
    conteudo  TEXT,
    tema      TEXT,
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Tabelas que devem existir no banco pessoal ────────────────────────────────
_SCHEMA_PESSOAL_GARANTIDO = """
CREATE TABLE IF NOT EXISTS usuarios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    UNIQUE NOT NULL,
    nome       TEXT    NOT NULL,
    senha_hash TEXT,
    criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessoes (
    token         TEXT PRIMARY KEY,
    user_id       TEXT,
    nome          TEXT,
    criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
    ultimo_acesso DATETIME DEFAULT CURRENT_TIMESTAMP,
    expira_em     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_sessoes_user_id ON sessoes(user_id);

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
"""


# =============================================================================
# Motor de migração
# =============================================================================

def _colunas_existentes(conn: sqlite3.Connection, tabela: str) -> set[str]:
    """Retorna o conjunto de nomes de colunas de uma tabela (vazio se não existir)."""
    try:
        rows = conn.execute(f"PRAGMA table_info({tabela})").fetchall()
        return {r[1].lower() for r in rows}
    except Exception:
        return set()


def _tabela_existe(conn: sqlite3.Connection, tabela: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (tabela,),
    ).fetchone()
    return bool(r and r[0] > 0)


def _aplicar_migracoes(
    conn: sqlite3.Connection,
    migracoes: list[ColunaMigracao],
    banco_nome: str,
) -> tuple[int, int]:
    """
    Aplica as migrações em `migracoes` na conexão `conn`.
    Retorna (aplicadas, puladas).
    """
    aplicadas = 0
    puladas   = 0

    for m in migracoes:
        if not _tabela_existe(conn, m.tabela):
            # Tabela não existe ainda — será criada pelo schema garantido
            puladas += 1
            continue

        existentes = _colunas_existentes(conn, m.tabela)
        if m.coluna.lower() in existentes:
            puladas += 1
            continue

        try:
            conn.execute(
                f"ALTER TABLE {m.tabela} ADD COLUMN {m.coluna} {m.definicao}"
            )
            conn.commit()
            _ok(f"[{banco_nome}] {m.tabela}.{m.coluna} adicionada")
            aplicadas += 1
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                puladas += 1
            else:
                _err(f"[{banco_nome}] Erro em {m.tabela}.{m.coluna}: {e}")

    return aplicadas, puladas


def _renomear_estado_app_se_necessario(conn: sqlite3.Connection):
    """
    Renomeia estado_app → estado_sistema nos bancos legado.
    Só age se estado_app existe E estado_sistema não existe.
    """
    tem_app     = _tabela_existe(conn, "estado_app")
    tem_sistema = _tabela_existe(conn, "estado_sistema")

    if tem_app and not tem_sistema:
        try:
            conn.execute("ALTER TABLE estado_app RENAME TO estado_sistema")
            conn.commit()
            _ok("[pessoal] estado_app renomeada → estado_sistema")
        except Exception as e:
            _warn(f"Não foi possível renomear estado_app: {e}")
    elif tem_app and tem_sistema:
        # Ambas existem — copia dados e remove a legada
        try:
            conn.execute(
                "INSERT OR IGNORE INTO estado_sistema (user_id, chave, valor) "
                "SELECT user_id, chave, valor FROM estado_app"
            )
            conn.commit()
            _ok("[pessoal] Dados de estado_app copiados para estado_sistema")
        except Exception as e:
            _warn(f"Erro ao copiar estado_app → estado_sistema: {e}")


# =============================================================================
# Entry point principal
# =============================================================================

def migrar_tudo(verbose: bool = True) -> bool:
    """
    Executa todas as migrações nos dois bancos.
    Retorna True se concluiu sem erros críticos.
    """
    if verbose:
        print(f"\n{_C['bold']}{_C['info']}{'='*60}")
        print("  S.I.R.I.U.S. v5.2 — Migrador de Schema")
        print(f"{'='*60}{_C['rst']}")
        _info(f"Banco pessoal : {_DB_PESSOAL}")
        _info(f"Banco treino  : {_DB_TREINO}")
        print()

    sucesso = True

    # ── 1. Banco pessoal ──────────────────────────────────────────────────────
    if verbose: print(f"{_C['bold']}[ sirius_pessoal.db ]{_C['rst']}")
    try:
        conn_p = sqlite3.connect(_DB_PESSOAL)
        conn_p.execute("PRAGMA journal_mode=WAL")

        _renomear_estado_app_se_necessario(conn_p)
        conn_p.executescript(_SCHEMA_PESSOAL_GARANTIDO)   # cria tabelas ausentes
        conn_p.commit()

        ap, pp = _aplicar_migracoes(conn_p, _MIGRACOES_PESSOAL, "pessoal")
        conn_p.close()

        if verbose:
            _ok(f"Pessoal: {ap} coluna(s) adicionada(s), {pp} já existiam.")
    except Exception as e:
        _err(f"Falha no banco pessoal: {e}")
        sucesso = False

    # ── 2. Banco de treino ────────────────────────────────────────────────────
    if verbose: print(f"\n{_C['bold']}[ sirius_treino.db ]{_C['rst']}")
    try:
        conn_t = sqlite3.connect(_DB_TREINO)
        conn_t.execute("PRAGMA journal_mode=WAL")

        conn_t.executescript(_SCHEMA_TREINO_GARANTIDO)    # garante tabelas + criado_em
        conn_t.commit()

        at, pt = _aplicar_migracoes(conn_t, _MIGRACOES_TREINO, "treino")
        conn_t.close()

        if verbose:
            _ok(f"Treino: {at} coluna(s) adicionada(s), {pt} já existiam.")
    except Exception as e:
        _err(f"Falha no banco de treino: {e}")
        sucesso = False

    # ── 3. Verificação final ──────────────────────────────────────────────────
    if verbose:
        print()
        _verificar_schema_critico()
        print()
        status = "CONCLUÍDA COM SUCESSO" if sucesso else "CONCLUÍDA COM ERROS"
        cor    = _C["ok"] if sucesso else _C["err"]
        print(f"{cor}{_C['bold']}  Migração {status}{_C['rst']}\n")

    return sucesso


def _verificar_schema_critico():
    """
    Verifica explicitamente as colunas críticas apontadas no ponto 1.
    Imprime OK ou FALTANDO para cada uma.
    """
    print(f"{_C['bold']}[ Verificação de colunas críticas ]{_C['rst']}")

    checklist = [
        (_DB_TREINO,  "estudos_autonomos",  "criado_em"),
        (_DB_TREINO,  "estudos_autonomos",  "fonte"),
        (_DB_TREINO,  "conhecimento_geral", "criado_em"),
        (_DB_PESSOAL, "conversas",          "criado_em"),
        (_DB_PESSOAL, "estado_sistema",     "criado_em"),
        (_DB_PESSOAL, "sessoes",            "criado_em"),
        (_DB_PESSOAL, "usuarios",           "criado_em"),
    ]

    tudo_ok = True
    for db_path, tabela, coluna in checklist:
        db_nome = os.path.basename(db_path)
        try:
            conn = sqlite3.connect(db_path)
            cols = _colunas_existentes(conn, tabela)
            conn.close()
            if coluna.lower() in cols:
                _ok(f"{db_nome} → {tabela}.{coluna}")
            else:
                _warn(f"{db_nome} → {tabela}.{coluna}  ← COLUNA AUSENTE")
                tudo_ok = False
        except Exception as e:
            _err(f"{db_nome} → {tabela}: {e}")
            tudo_ok = False

    if tudo_ok:
        _ok("Todas as colunas críticas presentes.")
    else:
        _warn("Algumas colunas estão ausentes — re-execute o migrador.")


# =============================================================================
# Standalone
# =============================================================================

if __name__ == "__main__":
    migrar_tudo(verbose=True)