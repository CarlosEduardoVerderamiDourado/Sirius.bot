import base64

# Substitua pelas suas chaves REAIS aqui apenas para gerar o arquivo
key_gemini = "AIzaSyCbaWXAyvD-BI_PUoRbFpCu3mpeHpV_0Rg"
key_eleven = "sk_92caee349c486c29d63fa5cac8ec35299aab88f314528176"

with open("key_gemini.txt", "w") as f:
    f.write(base64.b64encode(key_gemini.encode()).decode())

with open("key_eleven.txt", "w") as f:
    f.write(base64.b64encode(key_eleven.encode()).decode())