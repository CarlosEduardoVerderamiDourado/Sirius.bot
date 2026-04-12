import sqlite3
import os

class SiriusMemory:
    def __init__(self):
        diretorio_src = os.path.dirname(os.path.abspath(__file__))
        diretorio_raiz = os.path.dirname(diretorio_src)
        
        caminho_data = os.path.join(diretorio_raiz, "data")
        if not os.path.exists(caminho_data):
            os.makedirs(caminho_data)
        self.db_pessoal = os.path.join(caminho_data, "sirius_pessoal.db")
        self.db_treino = os.path.join(caminho_data, "sirius_treino.db")
        
        self.inicializar_bancos()

    def inicializar_bancos(self):
        # --- BANCO PESSOAL ---
        conn_p = sqlite3.connect(self.db_pessoal)
        cursor_p = conn_p.cursor()
        
        # Tabela de Conversas
        cursor_p.execute('''
            CREATE TABLE IF NOT EXISTS conversas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                sessao TEXT
            )
        ''')
        
        # Tabela de Macros
        cursor_p.execute('''
            CREATE TABLE IF NOT EXISTS macros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE,
                comandos TEXT,
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabela de Dúvidas Pendentes
        cursor_p.execute('''
            CREATE TABLE IF NOT EXISTS duvidas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pergunta TEXT UNIQUE,
                status TEXT DEFAULT 'pendente',
                data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn_p.commit()
        conn_p.close()

        # --- BANCO DE TREINO ---
        conn_t = sqlite3.connect(self.db_treino)
        cursor_t = conn_t.cursor()
        
        cursor_t.execute('''
            CREATE TABLE IF NOT EXISTS conhecimento_geral (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tema TEXT,
                conteudo TEXT,
                validado_por TEXT,
                data_estudo DATETIME DEFAULT CURRENT_TIMESTAMP,
                tags TEXT
            )
        ''')

        cursor_t.execute('''
            CREATE TABLE IF NOT EXISTS memoria_permanente 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, conteudo TEXT, tema TEXT)
        ''')
        
        conn_t.commit()
        conn_t.close()

    # --- MÉTODO CORRIGIDO (O que estava faltando) ---
    def obter_historico_db(self, limit=15):
        """Recupera as últimas conversas para dar contexto ao Sirius"""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            # Pega os últimos registros
            cursor.execute("SELECT role, content FROM conversas ORDER BY id DESC LIMIT ?", (limit,))
            linhas = cursor.fetchall()
            conn.close()

            # Inverte para que a conversa fique na ordem cronológica (antiga -> nova)
            historico = []
            for role, content in reversed(linhas):
                historico.append((role, content))
            
            return historico
        except Exception as e:
            print(f"[ERRO MEMORIA]: Falha ao obter histórico: {e}")
            return []

    # --- MÉTODOS DE DÚVIDAS ---

    def adicionar_duvida(self, pergunta):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO duvidas (pergunta) VALUES (?)", (pergunta.strip(),))
            conn.commit()
            conn.close()
            return True
        except: return False

    def buscar_duvida_pendente(self):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("SELECT pergunta FROM duvidas WHERE status = 'pendente' ORDER BY id ASC LIMIT 1")
            resultado = cursor.fetchone()
            conn.close()
            return resultado[0] if resultado else None
        except: return None

    def marcar_duvida_como_resolvida(self, pergunta):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM duvidas WHERE pergunta = ?", (pergunta,))
            conn.commit()
            conn.close()
        except: pass

    # --- MÉTODOS DE PERSISTÊNCIA ---

    def salvar_historico(self, pergunta, resposta):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO conversas (role, content, sessao) VALUES (?, ?, ?)", ("user", pergunta, "geral"))
            cursor.execute("INSERT INTO conversas (role, content, sessao) VALUES (?, ?, ?)", ("assistant", resposta, "geral"))
            conn.commit()
            conn.close()
        except Exception as e: 
            print(f"[ERRO SQLITE PESSOAL]: {e}")

    def salvar_estudo_autonomo(self, tema, conteudo, tags="geral"):
        try:
            conn = sqlite3.connect(self.db_treino)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO conhecimento_geral (tema, conteudo, validado_por, tags) VALUES (?, ?, ?, ?)",
                (tema.lower().strip(), conteudo, "Gemini-Filtro", tags)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e: return False

    def buscar_macro(self, nome):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("SELECT comandos FROM macros WHERE nome = ?", (nome.lower().strip(),))
            resultado = cursor.fetchone()
            conn.close()
            return resultado[0] if resultado else None
        except: return None

    def salvar_macro(self, nome, comandos):
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO macros (nome, comandos) VALUES (?, ?)", 
                           (nome.lower().strip(), comandos.strip()))
            conn.commit()
            conn.close()
            return True
        except: return False
    # Alias (Apelido) para compatibilidade com o neuronio.py
    def salvar_amostra_treino(self, tema, conteudo):
        """Redireciona chamadas antigas para o novo método de estudo"""
        return self.salvar_estudo_autonomo(tema, conteudo, tags="reforco_manual")