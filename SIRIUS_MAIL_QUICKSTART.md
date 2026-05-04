## 🚀 Quick Start - SiriusMail

Começar a usar o gerenciador de e-mail inteligente do Sirius em 5 minutos.

---

### 1️⃣ Setup Inicial (2 min)

#### Passo 1: Copiar arquivo de configuração

```bash
cd c:\Users\carlos\Documents\projetofacu\Sistema_ChatBot
copy .env.example .env
```

#### Passo 2: Configurar credenciais (Gmail recomendado)

Abra `.env` e preencha:

```env
SIRIUS_EMAIL_USER=seu_email@gmail.com
SIRIUS_EMAIL_PASSWORD=sua_senha_app
SIRIUS_IMAP_SERVER=imap.gmail.com
SIRIUS_IMAP_PORT=993
```

**Como gerar App Password no Gmail:**
1. Vá em https://myaccount.google.com/apppasswords
2. Selecione "Mail" e "Windows Computer"
3. Gere a senha (16 caracteres)
4. Cole no `.env` em `SIRIUS_EMAIL_PASSWORD`

#### Passo 3: Proteger arquivo

```bash
# Já está no .gitignore, mas confirme:
git status  # .env não deve aparecer
```

---

### 2️⃣ Primeira Execução (1 min)

#### Teste simples em Python:

```python
from sirius_mail import SiriusEmailManager

# Criar gerenciador
manager = SiriusEmailManager(user_id="carlos")

# Conectar
if manager.conectar():
    print("✅ Conectado!")
    
    # Listar e-mails
    emails = manager.listar_nao_lidos(limite=3)
    
    for email in emails:
        print(f"\n📧 {email['assunto']}")
        print(f"   De: {email['remetente']}")
        print(f"   Resumo: {email['resumo']}")
    
    # Desconectar
    manager.desconectar()
else:
    print("❌ Erro na conexão - verificar .env")
```

---

### 3️⃣ Usar com LangGraph (2 min)

#### Criar um agent simples:

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from sirius_mail import email_tool_factory
from memoria import SiriusMemory

# Setup
memoria = SiriusMemory()
email_tool = email_tool_factory(memoria=memoria, user_id="carlos")
manager = email_tool["instance"]

# State
class EmailState(TypedDict):
    emails: list
    acao: str

# Nodes
def verificar(state):
    resultado = manager.processar_emails()
    state["emails"] = resultado["emails"]
    
    if resultado["requer_interrupcao"]:
        state["acao"] = "URGENTE"
        print("🔴 E-mail urgente detectado!")
    else:
        state["acao"] = "NORMAL"
    
    return state

# Graph
graph = StateGraph(EmailState)
graph.add_node("verificar", verificar)
graph.add_edge(START, "verificar")
graph.add_edge("verificar", END)

# Run
result = graph.compile().invoke({"emails": [], "acao": ""})
print(f"Ação: {result['acao']}")
```

---

### 4️⃣ Troubleshooting Rápido

| Erro | Solução |
|------|---------|
| `"Credenciais não configuradas"` | Preencher `.env` com SIRIUS_EMAIL_USER e SIRIUS_EMAIL_PASSWORD |
| `"Authentication failed"` | Usar App Password (não senha normal) para Gmail |
| `"Connection timeout"` | Verificar firewall, testar conexão manualmente |
| `"Nenhum e-mail encontrado"` | Verificar INBOX, pode não ter e-mails não lidos |
| `"SSL error"` | Tentar puerto 993 (já é padrão) |

---

### 5️⃣ Próximas Ações

✅ **Feito**: Básico funcionando
⏳ **Próximo**: Integrar ao sirius_nucleo.py
⏳ **Depois**: Criar UI de consentimento para ações automáticas

---

### 📚 Documentação Completa

Veja [SIRIUS_MAIL_README.md](SIRIUS_MAIL_README.md) para:
- API completa
- Exemplos avançados
- Banco de dados
- Segurança & LGPD
- Troubleshooting detalhado

---

### 💡 Dicas Rápidas

```python
# Processar e-mails COM análise de prioridade
resultado = manager.processar_emails()
print(f"Prioridade máxima: {resultado['prioridade_maxima']}")  # "alta", "media", "baixa"

# Detectar prioridade manualmente
nivel, score = manager.detectar_prioridade(
    remetente="prof@facu.edu.br",
    assunto="Projeto URGENTE",
    corpo="..."
)
print(f"Nível: {nivel}, Score: {score:.2f}")  # "alta", 0.85

# Marcar como lido
manager.marcar_como_lido("123")

# Desconectar quando terminar
manager.desconectar()
```

---

**Pronto! 🎉 Seu Sirius agora pode gerenciar e-mails inteligentemente!**
