import os
import sys
import base64
from dotenv import load_dotenv

# --- LÓGICA DE CAMINHO (PARA EXE E CÓDIGO) ---
if getattr(sys, 'frozen', False):
    # Se rodando como .exe (PyInstaller), os arquivos .txt ficam na raiz temporária
    raiz_projeto = sys._MEIPASS
else:
    # Se rodando como script normal:
    # Como este arquivo está em 'SISTEMA_CHATBOT/config/config.py', 
    # precisamos subir UM nível para chegar na raiz 'SISTEMA_CHATBOT/'
    diretorio_deste_arquivo = os.path.dirname(os.path.abspath(__file__))
    raiz_projeto = os.path.abspath(os.path.join(diretorio_deste_arquivo, '..'))

dotenv_path = os.path.join(raiz_projeto, '.env')

# Carrega o arquivo .env se ele existir
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path, override=True)
    print("\033[92m[Config]: Arquivo .env carregado!\033[0m")
else:
    print("\033[34m[Config]: Modo de Produção (Recursos embutidos)\033[0m")

# --- FUNÇÕES DE APOIO ---
def ler_arquivo_txt(nome_arquivo):
    """Lê o conteúdo de um arquivo de texto na raiz do projeto"""
    caminho = os.path.join(raiz_projeto, nome_arquivo)
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"Erro ao ler {nome_arquivo}: {e}")
    return None

def decodificar_recurso(texto_b64):
    """Decodifica strings Base64 para bytes"""
    try:
        # Validação para evitar processar textos de exemplo ou vazios
        if texto_b64 and len(texto_b64) > 20 and "COLE_AQUI" not in texto_b64: 
            return base64.b64decode(texto_b64)
    except Exception as e:
        print(f"Erro ao decodificar recurso: {e}")
    return None

# --- PROCESSAMENTO DAS CHAVES ---

# 1. Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GEMINI_API_KEY:
    b64_gemini = ler_arquivo_txt("key_gemini.txt")
    backup = decodificar_recurso(b64_gemini)
    if backup:
        GEMINI_API_KEY = backup.decode('utf-8')

# 2. ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVENLABS_API_KEY:
    b64_eleven = ler_arquivo_txt("key_eleven.txt")
    backup = decodificar_recurso(b64_eleven)
    if backup:
        ELEVENLABS_API_KEY = backup.decode('utf-8')

# --- OUTROS RECURSOS ---
VOICE_ID = os.getenv("VOICE_ID") or "TX3LPaxmHKxFdv7VOQHJ"
LOGO_SIRIUS_B64 = ler_arquivo_txt("codigo_base64.txt")
LOGO_DATA = decodificar_recurso(LOGO_SIRIUS_B64)

# --- VERIFICAÇÕES DE SEGURANÇA ---
if not GEMINI_API_KEY:
    print("\033[31m[Erro Crítico]: Chave Gemini não encontrada!\033[0m")
if not ELEVENLABS_API_KEY:
    print("\033[33m[Aviso]: Chave ElevenLabs não encontrada!\033[0m")