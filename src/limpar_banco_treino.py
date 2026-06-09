"""
limpar_banco_treino.py — Remove exemplos ruins do banco de treino do Sirius.

Problema detectado:
  O banco contém respostas contaminadas salvas pelo salvar_historico(),
  como "bom dia como estamos hj ?. Mensagem enviada para parça. Eae, mano!"
  O RAG recupera essas frases e o neurônio as reproduz.

Este script:
  1. Identifica registros com padrões de contaminação
  2. Mostra o que será removido antes de apagar
  3. Remove apenas os contaminados (não apaga tudo)
  4. Registra quantos foram removidos

Uso:
  python src/limpar_banco_treino.py          # mostra o que seria removido
  python src/limpar_banco_treino.py --apply  # aplica a limpeza
"""

import os
import sys
import sqlite3
import argparse

_DIR_SRC  = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_SRC)

DB_PESSOAL = os.path.join(_DIR_RAIZ, "data", "sirius_pessoal.db")
DB_TREINO  = os.path.join(_DIR_RAIZ, "data", "sirius_treino.db")

# ── Padrões que indicam contaminação ──────────────────────────────────────────
# Frases que NUNCA deveriam estar no banco como exemplo de resposta do Sirius
PADROES_CONTAMINADOS = [
    "mensagem enviada para",   # resposta automática de app de mensagens
    "parça",                   # gíria de chat (não é estilo Sirius)
    "eae, mano",               # gíria de chat
    "boa noite pra você também",  # resposta de chatbot genérico
    "como estamos hj",         # gíria/abreviação fora do padrão
]


def _verificar_db(db_path: str, tabelas: list[tuple[str, str]]) -> list[dict]:
    """
    Verifica registros contaminados em um banco.
    tabelas: [(nome_tabela, coluna_conteudo), ...]
    """
    contaminados = []
    if not os.path.exists(db_path):
        return contaminados

    try:
        conn = sqlite3.connect(db_path)
        for tabela, coluna in tabelas:
            try:
                # Verifica se tabela e coluna existem
                conn.execute(f"SELECT 1 FROM {tabela} LIMIT 1")
            except Exception:
                continue

            for padrao in PADROES_CONTAMINADOS:
                rows = conn.execute(
                    f"SELECT id, {coluna} FROM {tabela} WHERE LOWER({coluna}) LIKE ?",
                    (f"%{padrao.lower()}%",)
                ).fetchall()
                for row_id, conteudo in rows:
                    contaminados.append({
                        "db":      db_path,
                        "tabela":  tabela,
                        "coluna":  coluna,
                        "id":      row_id,
                        "padrao":  padrao,
                        "preview": conteudo[:100],
                    })
        conn.close()
    except Exception as e:
        print(f"[LIMPEZA]: Erro em {db_path}: {e}")

    return contaminados


def _remover(contaminados: list[dict]) -> int:
    """Remove os registros contaminados. Retorna quantidade removida."""
    removidos = 0
    # Agrupa por (db, tabela) para fazer em lote
    from collections import defaultdict
    grupos = defaultdict(list)
    for item in contaminados:
        grupos[(item["db"], item["tabela"])].append(item["id"])

    for (db, tabela), ids in grupos.items():
        ids_unicos = list(set(ids))
        try:
            conn = sqlite3.connect(db)
            placeholders = ",".join("?" * len(ids_unicos))
            conn.execute(f"DELETE FROM {tabela} WHERE id IN ({placeholders})", ids_unicos)
            conn.commit()
            removidos += conn.execute("SELECT changes()").fetchone()[0]
            conn.close()
            print(f"[LIMPEZA]: {removidos} registro(s) removido(s) de {os.path.basename(db)}.{tabela}")
        except Exception as e:
            print(f"[LIMPEZA]: Erro removendo de {db}.{tabela}: {e}")

    return removidos


def main():
    parser = argparse.ArgumentParser(
        description="Remove respostas contaminadas do banco de treino do Sirius"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Aplica a limpeza (sem este flag, apenas mostra o que seria removido)"
    )
    args = parser.parse_args()

    print("\n╔════════════════════════════════════════════╗")
    print("║   Sirius — Limpeza do Banco de Treino     ║")
    print("╚════════════════════════════════════════════╝\n")

    # ── Verifica os dois bancos ────────────────────────────────────────────────
    todos_contaminados = []

    todos_contaminados += _verificar_db(DB_PESSOAL, [
        ("conversas",    "content"),
        ("conversas",    "content"),
    ])
    todos_contaminados += _verificar_db(DB_TREINO, [
        ("conhecimento_geral",  "conteudo"),
        ("estudos_autonomos",   "conteudo"),
    ])

    # Remove duplicatas por (db, tabela, id)
    vistos = set()
    unicos = []
    for item in todos_contaminados:
        chave = (item["db"], item["tabela"], item["id"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)

    if not unicos:
        print("✅ Nenhum registro contaminado encontrado. Banco limpo!")
        return

    print(f"⚠️  {len(unicos)} registro(s) contaminado(s) encontrado(s):\n")
    for item in unicos:
        db_nome = os.path.basename(item["db"])
        print(f"  [{db_nome}] {item['tabela']} id={item['id']}")
        print(f"  Padrão: '{item['padrao']}'")
        print(f"  Preview: {item['preview']!r}")
        print()

    if args.apply:
        print("─" * 50)
        removidos = _remover(unicos)
        print(f"\n✅ Limpeza concluída — {removidos} registro(s) removido(s).")
        print("   Rode o treinador para atualizar os embeddings:")
        print("   python src/sirius_treinador.py --tudo")
    else:
        print("─" * 50)
        print("ℹ️  Modo DRY RUN — nada foi removido.")
        print(f"   Para aplicar: python src/limpar_banco_treino.py --apply")


if __name__ == "__main__":
    main()