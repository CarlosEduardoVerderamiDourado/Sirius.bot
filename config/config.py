import os
from dotenv import load_dotenv

# --- LÓGICA DE CAMINHO ---
diretorio_config = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(diretorio_config, '.env')

# Carrega o arquivo específico
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path, override=True)
    print("\033[92m[Config]: Arquivo .env carregado com sucesso!\033[0m")
else:
    print(f"\033[31m[Erro]: Arquivo .env NÃO encontrado em: {dotenv_path}\033[0m")

# --- CHAVE DO GEMINI ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- CHAVE DA ELEVENLABS ---
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# --- ID DA VOZ (LIAM) ---
# Tenta pegar 'VOICE_ID' ou 'voice_id' do .env. Se não achar, usa o ID do Liam direto.
VOICE_ID = os.getenv("VOICE_ID") or os.getenv("voice_id")

if not VOICE_ID:
    # ID do Liam (TX3LPaxmHKxFdv7VOQHJ) injetado como fallback seguro
    VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ" 
    print(f"\033[34m[Info]: Usando voice_id padrão (Liam): {VOICE_ID}\033[0m")

# --- VERIFICAÇÕES DE SEGURANÇA ---
if not GEMINI_API_KEY:
    print("\033[33m[Aviso]: GEMINI_API_KEY não encontrada no .env\033[0m")

if not ELEVENLABS_API_KEY:
    print("\033[33m[Aviso]: ELEVENLABS_API_KEY não encontrada no .env\033[0m")