import base64
import os

# Define a estrutura de pastas
diretorio_atual = os.path.dirname(os.path.abspath(__file__)) # Se rodar da src
raiz_projeto = os.path.dirname(diretorio_atual)
pasta_config = os.path.join(raiz_projeto, "config")

# Garante que a pasta config exista
if not os.path.exists(pasta_config):
    os.makedirs(pasta_config)

# Substitua pelas suas chaves REAIS
key_gemini = "key_gemini"
key_eleven = "key_eleven"

# Caminhos completos
caminho_gemini = os.path.join(pasta_config, "key_gemini.txt")
caminho_eleven = os.path.join(pasta_config, "key_eleven.txt")

with open(caminho_gemini, "w") as f:
    f.write(base64.b64encode(key_gemini.encode()).decode())

with open(caminho_eleven, "w") as f:
    f.write(base64.b64encode(key_eleven.encode()).decode())

print(f"Chaves geradas com sucesso em: {pasta_config}")