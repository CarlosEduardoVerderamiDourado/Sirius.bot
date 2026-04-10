import sqlite3
import os

def preparar_dados_para_treino():
    # Pega o caminho da raiz (um nível acima de /src)
    diretorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_db = os.path.join(diretorio_raiz, "data", "sirius_treino.db")
    caminho_txt = os.path.join(diretorio_raiz, "data", "treino_sirius.txt")

    conn = sqlite3.connect(caminho_db) # Busca no lugar certo
    cursor = conn.cursor()
    
    # Pega tudo que o subconsciente minerou
    cursor.execute("SELECT tema, conteudo FROM conhecimento_geral WHERE tags = 'auto_learning'")
    dados = cursor.fetchall()
    
    with open(caminho_txt, "w", encoding="utf-8") as f: # Salva na /data
        for tema, conteudo in dados:
            f.write(f"TEMA: {tema} | CONTEUDO: {conteudo}\n")
    
    print(f"[SIRIUS]: {len(dados)} novos conhecimentos exportados para data/treino_sirius.txt")
    conn.close()

if __name__ == "__main__":
    preparar_dados_para_treino()