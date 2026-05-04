## SiriusMail - Gerenciador de E-mail Inteligente

### 📧 Visão Geral

**SiriusMail** é um módulo de gerenciamento inteligente de e-mails que se integra ao Sirius usando **LangGraph Tools** e **IMAP**. 

Ao contrário de automações tradicionais com regras fixas (if/else), o Sirius **"aprende" como lidar com e-mails** baseado no histórico de interações armazenado em **SiriusMemory**.

---

### 🎯 Recursos Principais

| Feature | Descrição |
|---------|-----------|
| **Leitura IMAP** | Acesso automático a últimos 3 e-mails não lidos |
| **Inteligência sem código fixo** | LLM decide ações baseado em contexto histórico |
| **Detecção de Prioridade** | Analisa keywords, remetente, conteúdo |
| **Auditoria LGPD** | Registra todos os acessos com user_id |
| **Tool Factory LangGraph** | Integração perfeita com agentes autônomos |
| **Interrupção de Fluxo** | Para e avisa se encontrar e-mail urgente |
| **Segurança** | Credenciais via .env, sem hardcoding |

---

### 🔧 Setup & Configuração

#### 1. **Instalar Dependências**

```bash
pip install python-dotenv
# IMAP é nativo em Python, não precisa instalar
```

#### 2. **Configurar Credenciais (.env)**

Crie arquivo `.env` na raiz do projeto:

```env
SIRIUS_EMAIL_USER=seu_email@gmail.com
SIRIUS_EMAIL_PASSWORD=sua_senha_aplicacao
SIRIUS_IMAP_SERVER=imap.gmail.com
SIRIUS_IMAP_PORT=993
```

**Para Gmail (recomendado):**
1. Ative 2FA em sua conta Google
2. Vá em https://myaccount.google.com/apppasswords
3. Gere "Senha de app" para "Mail"
4. Use essa senha no `.env`

**Para outros provedores:**
- Outlook: `imap-mail.outlook.com:993`
- Yahoo: `imap.mail.yahoo.com:993`
- Zoho: `imap.zoho.com:993`

#### 3. **Adicionar .env ao .gitignore**

```bash
echo ".env" >> .gitignore
git add .gitignore
git commit -m "chore: Proteger arquivo .env"
```

---

### 📚 API do SiriusEmailManager

#### **Inicialização**

```python
from sirius_mail import SiriusEmailManager

manager = SiriusEmailManager(
    memoria=memoria,      # SiriusMemory instance
    user_id="carlos"      # LGPD compliance
)
```

#### **Conectar/Desconectar**

```python
# Conecta ao servidor IMAP
if manager.conectar():
    print("Conectado com sucesso")
else:
    print("Falha na conexão - verificar .env")

# Desconecta quando terminar
manager.desconectar()
```

#### **Listar E-mails Não Lidos**

```python
emails = manager.listar_nao_lidos(limite=3)
# Retorna: [
#   {
#     "email_id": "123",
#     "message_id": "msg@....",
#     "remetente": "professor@facu.edu.br",
#     "assunto": "Projeto da faculdade - Urgente",
#     "corpo": "Conteúdo do e-mail...",
#     "resumo": "Resumo curto do conteúdo",
#     "data": "2024-05-03"
#   },
#   ...
# ]
```

#### **Detectar Prioridade**

```python
nivel, score = manager.detectar_prioridade(
    remetente="professor@facu.edu.br",
    assunto="Projeto urgente - entrega hoje",
    corpo="Este é o conteúdo do e-mail..."
)
# Retorna: ("alta", 0.85)
# Niveis: "alta" (score >= 0.7), "media" (0.4-0.7), "baixa" (< 0.4)
```

#### **Processar E-mails com Contexto**

```python
resultado = manager.processar_emails(callback_ia=None)
# Retorna:
# {
#   "emails": [...],                    # E-mails analisados
#   "prioridade_maxima": "alta",       # Maior prioridade encontrada
#   "score_maximo": 0.85,              # Score máximo
#   "requer_interrupcao": True,        # Se deve interromper fluxo
#   "mensagem_usuario": "Carlos, você recebeu um e-mail importante..."
# }
```

#### **Marcar Como Lido**

```python
manager.marcar_como_lido(email_id="123")
```

---

### 🤖 Integração com LangGraph

#### **Exemplo 1: Agent Simples**

```python
from langgraph.graph import StateGraph, START, END
from sirius_mail import email_tool_factory

# Criar tool
email_tool = email_tool_factory(memoria=memoria, user_id="carlos")

# Acessar funções
processar = email_tool["functions"]["processar_emails"]
resultado = processar()

print(f"E-mails encontrados: {len(resultado['emails'])}")
print(f"Requer interrupção: {resultado['requer_interrupcao']}")
```

#### **Exemplo 2: StateGraph com Nó de Decisão**

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class EmailState(TypedDict):
    emails: list
    requer_interrupcao: bool
    acao: str

# Inicializar
email_tool = email_tool_factory(memoria=memoria, user_id="carlos")
email_manager = email_tool["instance"]

# Nó 1: Verificar e-mails
def verificar_emails(state):
    resultado = email_manager.processar_emails()
    state["emails"] = resultado["emails"]
    state["requer_interrupcao"] = resultado["requer_interrupcao"]
    return state

# Nó 2: Decidir ação
def decidir_acao(state):
    if state["requer_interrupcao"]:
        state["acao"] = "INTERROMPER_E_NOTIFICAR"
    else:
        state["acao"] = "CONTINUAR_FLUXO"
    return state

# Nó 3: Executar ação
def executar_acao(state):
    if state["acao"] == "INTERROMPER_E_NOTIFICAR":
        print("🔴 INTERRUPÇÃO: E-mail urgente detectado!")
        # Aqui entra lógica de notificação
    return state

# Construir grafo
graph = StateGraph(EmailState)
graph.add_node("verificar", verificar_emails)
graph.add_node("decidir", decidir_acao)
graph.add_node("executar", executar_acao)

graph.add_edge(START, "verificar")
graph.add_edge("verificar", "decidir")
graph.add_edge("decidir", "executar")
graph.add_edge("executar", END)

# Executar
result = graph.compile().invoke({"emails": [], "requer_interrupcao": False, "acao": ""})
```

---

### 📊 Banco de Dados

#### **Tabela: emails_processados**

Registra todos os e-mails processados com análise de prioridade.

```sql
CREATE TABLE emails_processados (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,              -- LGPD: segregação por usuário
    message_id TEXT,
    remetente TEXT,
    assunto TEXT,
    data_recebimento DATETIME,
    resumo TEXT,
    prioridade_detectada TEXT,          -- "alta", "media", "baixa"
    score_prioridade FLOAT,             -- 0-1
    lido BOOLEAN,
    acao_tomada TEXT,                   -- Ação decidida pelo LLM
    processado_em DATETIME
);
```

#### **Tabela: auditoria_email**

Auditoria completa para conformidade LGPD.

```sql
CREATE TABLE auditoria_email (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,              -- LGPD: sempre rastreia usuário
    acao TEXT,                          -- "conectar", "listar_nao_lidos", etc
    quantidade_emails INTEGER,
    resultado TEXT,
    erro TEXT,
    timestamp DATETIME
);
```

---

### 🧠 Lógica de Prioridade (sem código fixo)

O SiriusMail usa detecção **contextual** de prioridade:

```python
def detectar_prioridade(remetente, assunto, corpo):
    score = 0.5  # baseline
    
    # Palavras-chave de urgência
    if "urgente" in assunto.lower():
        score += 0.3
    
    # Contexto específico (projeto, faculdade)
    if "projeto" in assunto.lower():
        score += 0.2
    
    # Histórico (de SiriusMemory)
    if remetente in memoria.remetentes_importantes():
        score += 0.1
    
    # Normaliza
    return min(score, 1.0)
```

**O LLM (via SiriusMemory) pode aprender:**
- Qual remetente é importante
- Que keywords indicam urgência
- Que tipos de e-mail requerem ação imediata

---

### 🔐 Segurança & Conformidade

| Aspecto | Implementação |
|---------|--------------|
| **LGPD** | user_id em todas as tabelas + auditoria |
| **Credenciais** | Apenas via .env, nunca hardcoded |
| **Erros** | Try-catch em todas as operações IMAP |
| **Timeout** | Não bloqueia se servidor for lento |
| **Encriptação** | IMAP usa SSL/TLS automático (porta 993) |

---

### 🚀 Exemplos de Uso

#### **Exemplo 1: Uso Simples**

```python
from sirius_mail import SiriusEmailManager

manager = SiriusEmailManager(user_id="carlos")

if manager.conectar():
    emails = manager.listar_nao_lidos(limite=3)
    
    for email in emails:
        print(f"📧 {email['assunto']}")
        print(f"   De: {email['remetente']}")
        print(f"   {email['resumo']}\n")
    
    manager.desconectar()
```

#### **Exemplo 2: Com Prioridade**

```python
from sirius_mail import SiriusEmailManager

manager = SiriusEmailManager(user_id="carlos")

if manager.conectar():
    resultado = manager.processar_emails()
    
    if resultado["requer_interrupcao"]:
        print("🔴 URGENTE!")
        print(resultado["mensagem_usuario"])
    else:
        print(f"✅ {len(resultado['emails'])} e-mails para revisar")
    
    manager.desconectar()
```

#### **Exemplo 3: Integração com sirius_nucleo.py**

```python
# Em sirius_nucleo.py:

def _carregar_email(self):
    try:
        from sirius_mail import email_tool_factory
        self._email_tool = email_tool_factory(
            memoria=self.memoria,
            user_id="sistema"
        )
        print("[NUCLEO]: E-mail module loaded")
    except Exception as e:
        print(f"[NUCLEO]: Email indisponível: {e}")

# No processar():
def processar(self, texto: str) -> str:
    # ... código existente ...
    
    # Verificar e-mails se fizer parte da conversação
    if "email" in texto.lower() and self._email_tool:
        resultado = self._email_tool["functions"]["processar_emails"]()
        if resultado["requer_interrupcao"]:
            return resultado["mensagem_usuario"]
```

---

### 📋 Troubleshooting

| Problema | Solução |
|----------|---------|
| **"Credenciais não configuradas"** | Preencher .env com SIRIUS_EMAIL_USER e SIRIUS_EMAIL_PASSWORD |
| **"Falha na autenticação"** | Para Gmail, usar App Password (não a senha normal) |
| **"Connection timeout"** | Verificar firewall, ISP pode bloquear porta 993 |
| **"Nenhum e-mail encontrado"** | Verificar se há e-mails não lidos na INBOX |
| **"AttributeError: SiriusMemory"** | Certificar que memoria é passado ao inicializar |

---

### 🎯 Roadmap

- [ ] Suporte a múltiplas contas de e-mail
- [ ] Envio de e-mails (SMTP)
- [ ] Filtros customizáveis por usuário
- [ ] Integração com Labels do Gmail
- [ ] Machine Learning para detecção de spam
- [ ] Webhook para e-mails recebidos em tempo real
- [ ] Dashboard de e-mails processados

---

### 📖 Documentação Adicional

- [IMAP RFC](https://tools.ietf.org/html/rfc3501)
- [Gmail IMAP Setup](https://support.google.com/mail/answer/7126229)
- [LangGraph Docs](https://langchain-ai.github.io/langgraph/)
- [LGPD Lei](http://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm)

---

### 👤 Suporte

Para dúvidas sobre:
- **Setup**: Verificar .env e credenciais
- **IMAP**: Consultar documentação do provedor de e-mail
- **LangGraph**: Verificar exemplos em INTEGRACAO_AGENTES.py
- **LGPD**: Consultar auditoria em sirius_pessoal.db

---

**Status**: ✅ Production-Ready
**Última atualização**: Maio 2024
**Compatível com**: Python 3.9+, LangGraph 0.0.20+
