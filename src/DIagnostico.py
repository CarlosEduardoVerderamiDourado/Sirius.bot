"""
diagnostico.py — Verifica o estado real do aprendizado do Sirius
Roda: python diagnostico.py
"""

import os
import sys
import sqlite3

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO  = os.path.join(CAMINHO_DATA, "sirius_treino.db")

def linha(char="─", n=50):
    print(char * n)

def checar_banco(caminho, nome):
    if not os.path.exists(caminho):
        print(f"  ✗ {nome} — NÃO EXISTE")
        return False
    tam = os.path.getsize(caminho) / 1024
    print(f"  ✓ {nome} ({tam:.1f} KB)")
    return True

def main():
    print("\n" + "="*50)
    print("   DIAGNÓSTICO DO SIRIUS — APRENDIZADO")
    print("="*50)

    # --- Bancos ---
    print("\n[1] Bancos de dados:")
    checar_banco(DB_PESSOAL, "sirius_pessoal.db")
    checar_banco(DB_TREINO,  "sirius_treino.db")

    # --- Conversas ---
    print("\n[2] Histórico de conversas:")
    try:
        conn = sqlite3.connect(DB_PESSOAL)
        total = conn.execute("SELECT COUNT(*) FROM conversas").fetchone()[0]
        user  = conn.execute("SELECT COUNT(*) FROM conversas WHERE role='user'").fetchone()[0]
        bot   = conn.execute("SELECT COUNT(*) FROM conversas WHERE role='assistant'").fetchone()[0]
        print(f"  Total: {total} mensagens ({user} suas + {bot} do Sirius)")

        print("\n  Últimas 5 perguntas suas:")
        rows = conn.execute(
            "SELECT content FROM conversas WHERE role='user' ORDER BY id DESC LIMIT 5"
        ).fetchall()
        for i, r in enumerate(rows, 1):
            print(f"    {i}. {r[0][:70]}")
        conn.close()
    except Exception as e:
        print(f"  Erro: {e}")

    # --- Conhecimento autodidata ---
    print("\n[3] Conhecimento autodidata:")
    try:
        conn = sqlite3.connect(DB_TREINO)
        total_geral = conn.execute(
            "SELECT COUNT(*) FROM conhecimento_geral"
        ).fetchone()[0]

        por_tag = conn.execute(
            "SELECT tags, COUNT(*) FROM conhecimento_geral GROUP BY tags"
        ).fetchall()

        print(f"  Total no banco: {total_geral}")
        for tag, count in por_tag:
            print(f"    [{tag}]: {count}")

        print("\n  Últimos 5 temas estudados:")
        rows = conn.execute(
            "SELECT tema, tags FROM conhecimento_geral ORDER BY id DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            print(f"    • {r[0]} [{r[1]}]")

        mem_perm = conn.execute(
            "SELECT COUNT(*) FROM memoria_permanente"
        ).fetchone()[0]
        print(f"\n  Memória permanente: {mem_perm} registros")
        conn.close()
    except Exception as e:
        print(f"  Erro: {e}")

    # --- Modelos treinados ---
    print("\n[4] Modelos neurais:")
    arquivos = {
        "RedeSirius (classificador)": "sirius_model.pth",
        "SiriusGerador (seq2seq)":    "sirius_gerador.pth",
        "SiriusEmbeddings (word2vec)":"sirius_embeddings.pkl",
        "Vocabulário do gerador":     "sirius_vocab.pkl",
        "Vectorizer TF-IDF":          "vectorizer.pkl",
    }
    for nome, arquivo in arquivos.items():
        caminho = os.path.join(CAMINHO_DATA, arquivo)
        if os.path.exists(caminho):
            tam = os.path.getsize(caminho) / 1024
            print(f"  ✓ {nome} ({tam:.1f} KB)")
        else:
            print(f"  ✗ {nome} — NÃO TREINADO")

    # --- Dúvidas pendentes ---
    print("\n[5] Dúvidas pendentes (o que ele ainda não sabe):")
    try:
        conn = sqlite3.connect(DB_PESSOAL)
        rows = conn.execute(
            "SELECT pergunta FROM duvidas WHERE status='pendente' LIMIT 5"
        ).fetchall()
        if rows:
            for r in rows:
                print(f"  • {r[0][:70]}")
        else:
            print("  Nenhuma dúvida pendente.")
        conn.close()
    except Exception as e:
        print(f"  Erro: {e}")

    # --- Diagnóstico e recomendações ---
    print("\n[6] O que fazer agora:")
    linha()

    try:
        conn    = sqlite3.connect(DB_TREINO)
        n_dados = conn.execute("SELECT COUNT(*) FROM conhecimento_geral").fetchone()[0]
        conn.close()
    except Exception:
        n_dados = 0

    gerador_path = os.path.join(CAMINHO_DATA, "sirius_gerador.pth")
    modelo_path  = os.path.join(CAMINHO_DATA, "sirius_model.pth")

    if n_dados < 5:
        print("  ⚠ Poucos dados. Deixe o autodidata rodar por ~30min primeiro.")
        print("  → python sirius_autodidata.py")
    elif not os.path.exists(modelo_path):
        print("  ⚠ RedeSirius não treinada. Rode:")
        print("  → python sirius_treinador.py --classificador")
    elif not os.path.exists(gerador_path):
        print(f"  ⚠ SiriusGerador não treinado ({n_dados} dados disponíveis). Rode:")
        print("  → python sirius_treinador.py --tudo")
    else:
        print("  ✓ Tudo treinado! O Sirius está aprendendo.")
        print(f"  → Continue usando normalmente. ({n_dados} dados no banco)")
        if n_dados > 50:
            print("  → Com tantos dados, rode um retreino completo:")
            print("     python sirius_treinador.py --tudo")

    print("="*50 + "\n")

if __name__ == "__main__":
    main()