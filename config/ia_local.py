import requests
import json

class SiriusLocal:
    def __init__(self, modelo="llama3.1"):
        self.url_base = "http://localhost:11434/api/generate"
        self.modelo = modelo

    def responder(self, prompt):
        """Envia a pergunta para o Llama 3 local e retorna a resposta"""
        payload = {
            "model": self.modelo,
            "prompt": f"Você é o Sirius, um assistente IA prestativo. Responda de forma curta e em português: {prompt}",
            "stream": False # Mantemos False para receber a frase inteira de uma vez
        }

        try:
            # Timeout de 15 segundos para não travar o Sirius se o PC estiver lento
            response = requests.post(self.url_base, json=payload, timeout=15)
            
            if response.status_code == 200:
                dados = response.json()
                return dados.get("response", "Desculpe, perdi o fio da meada localmente.")
            else:
                return f"[Erro Ollama]: Status {response.status_code}"
                
        except requests.exceptions.ConnectionError:
            return "[Erro]: O Ollama não parece estar rodando. Certifique-se de abri-lo."
        except Exception as e:
            return f"[Erro Local]: {str(e)}"