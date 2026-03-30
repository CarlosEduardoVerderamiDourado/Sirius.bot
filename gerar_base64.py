import base64
import os

def transformar_em_base64(nome_arquivo, caminho_relativo):
    if os.path.exists(caminho_relativo):
        with open(caminho_relativo, "rb") as image_file:
            # Transforma a imagem em código de texto
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            print(f"\n=== CÓDIGO BASE64 DE: {nome_arquivo} ===")
            print(encoded_string)
            print("=" * 40)
    else:
        print(f"Erro: O arquivo {caminho_relativo} não foi encontrado!")

# --- EXECUTE PARA A LOGO ---
# Ajuste o caminho se sua logo estiver em outro lugar
transformar_em_base64("LOGO_SIRIUS", "src/img/logo_sirius.png")

# --- OPCIONAL: EXECUTE PARA AS CHAVES (Se quiser esconder no código) ---
# chave_gemini = "SUA_CHAVE_AQUI"
# print("\n=== BASE64 DA CHAVE GEMINI ===")
# print(base64.b64encode(chave_gemini.encode()).decode())