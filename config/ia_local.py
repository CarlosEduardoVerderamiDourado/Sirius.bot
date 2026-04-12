import requests
import json

class SiriusLocal:
    def __init__(self, modelo="llama3.1"):
        self.url_base = "http://localhost:11434/api/generate"
        self.modelo = modelo

    def responder(self, prompt):
        payload = {
            "model": self.modelo,
            # Adicionei instruções de personalidade mais fortes
            "prompt": f"Contexto: Você é o Sirius, um parça brasileiro que usa gírias. Responda: {prompt}",
            "stream": False,
            "options": {
                "temperature": 0.7, # Deixa ele mais 'vivo'
                "num_predict": 100  # Limita para respostas rápidas
            }
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