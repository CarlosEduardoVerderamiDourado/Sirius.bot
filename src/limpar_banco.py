"""
limpar_banco.py — Limpa registros ruins do banco do Sirius
Roda: python limpar_banco.py
"""

import os
import sys
import sqlite3
import re

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")

DB_PESSOAL = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO  = os.path.join(CAMINHO_DATA, "sirius_treino.db")


def _eh_lixo(texto: str) -> bool:
    """Detecta transcrições ruins: eco, repetição, ruído."""
    if not texto or len(texto.strip()) < 3:
        return True

    t = texto.strip().lower()

    # Texto muito curto (ruído)
    if len(t.split()) < 2:
        return True

    # Reticências / silêncio
    if re.match(r'^[.\s…]+$', t):
        return True

    # Contagem de palavras repetidas
    palavras = t.split()
    if len(palavras) >= 4:
        # Se mais de 50% das palavras são iguais = eco/repetição
        from collections import Counter
        freq     = Counter(palavras)
        mais_freq = freq.most_common(1)[0][1]
        if mais_freq / len(palavras) > 0.5:
            return True

    # Frases que se repetem (eco)
    # Ex: "fala gente. fala gente." → divide por . e verifica duplicatas
    sentencas = [s.strip() for s in re.split(r'[.!?]', t) if s.strip()]
    if len(sentencas) >= 2:
        unicas = set(sentencas)
        if len(unicas) < len(sentencas) * 0.6:
            return True

    # Padrões típicos de ruído/eco
    padroes_lixo = [
        r'^(um,?\s*){3,}',           # "um, um, um..."
        r'^(é,?\s*){3,}',            # "é, é, é..."
        r'(.{5,})\1{2,}',            # qualquer frase repetida 3x
        r'^[aeiou\s,]+$',            # só vogais e vírgulas
        r'^\W+$',                    # só pontuação
    ]
    for padrao in padroes_lixo:
        if re.search(padrao, t):
            return True

    return False


def limpar_duvidas():
    """Remove dúvidas que são claramente ruído."""
    try:
        conn   = sqlite3.connect(DB_PESSOAL)
        cursor = conn.cursor()
        cursor.execute("SELECT id, pergunta FROM duvidas")
        todas  = cursor.fetchall()

        removidas = 0
        for id_, pergunta in todas:
            if _eh_lixo(pergunta):
                cursor.execute("DELETE FROM duvidas WHERE id = ?", (id_,))
                removidas += 1

        conn.commit()
        conn.close()
        print(f"  Dúvidas: {removidas}/{len(todas)} registros ruins removidos.")
        return removidas
    except Exception as e:
        print(f"  Erro ao limpar dúvidas: {e}")
        return 0


def limpar_conversas():
    """Remove mensagens que são claramente ruído do histórico."""
    try:
        conn   = sqlite3.connect(DB_PESSOAL)
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, content FROM conversas")
        todas  = cursor.fetchall()

        removidas = 0
        for id_, role, content in todas:
            if role == "user" and _eh_lixo(content):
                # Remove o par (user + assistant seguinte)
                cursor.execute("DELETE FROM conversas WHERE id IN (?, ?)", (id_, id_ + 1))
                removidas += 1

        conn.commit()
        conn.close()
        print(f"  Conversas: ~{removidas} pares ruins removidos.")
        return removidas
    except Exception as e:
        print(f"  Erro ao limpar conversas: {e}")
        return 0


def limpar_conhecimento():
    """Remove conhecimento com temas/conteúdo que são ruído."""
    try:
        conn   = sqlite3.connect(DB_TREINO)
        cursor = conn.cursor()
        cursor.execute("SELECT id, tema, conteudo FROM conhecimento_geral")
        todos  = cursor.fetchall()

        removidos = 0
        for id_, tema, conteudo in todos:
            if _eh_lixo(tema) or _eh_lixo(conteudo):
                cursor.execute("DELETE FROM conhecimento_geral WHERE id = ?", (id_,))
                removidos += 1

        conn.commit()
        conn.close()
        print(f"  Conhecimento: {removidos}/{len(todos)} registros ruins removidos.")
        return removidos
    except Exception as e:
        print(f"  Erro ao limpar conhecimento: {e}")
        return 0


def main():
    print("\n" + "="*50)
    print("   LIMPEZA DO BANCO DO SIRIUS")
    print("="*50)

    print("\nLimpando registros ruins...")
    limpar_duvidas()
    limpar_conversas()
    limpar_conhecimento()

    # Compacta o banco após limpeza
    for db, nome in [(DB_PESSOAL, "pessoal"), (DB_TREINO, "treino")]:
        try:
            conn = sqlite3.connect(db)
            conn.execute("VACUUM")
            conn.close()
            tam = os.path.getsize(db) / 1024
            print(f"  ✓ {nome}.db compactado ({tam:.1f} KB)")
        except Exception as e:
            print(f"  Erro ao compactar {nome}: {e}")

    print("\n✓ Limpeza concluída!")
    print("  Rode 'python sirius_treinador.py --tudo' para retreinar com dados limpos.")
    print("="*50 + "\n")


if __name__ == "__main__":
    main()