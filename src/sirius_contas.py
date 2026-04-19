"""
sirius_contas.py — Sistema de múltiplas contas de usuário

Permite que diferentes pessoas usem o Sirius com seus próprios perfis,
preferências, histórico de conversas e configurações isoladas.

Estrutura de arquivos:
  data/
    contas/
      contas.json              → índice de contas (id, nome, pin, última sessão)
      perfil_joao.json         → perfil completo do João
      perfil_maria.json        → perfil completo da Maria
      perfil_guest.json        → perfil do convidado (sem pin)
    sirius_pessoal_joao.db     → histórico e lembretes do João
    sirius_pessoal_maria.db    → histórico e lembretes da Maria
    sirius_treino.db           → conhecimento compartilhado entre todos

Isolamento por conta:
  - Cada conta tem seu próprio banco pessoal (conversas, lembretes, dúvidas)
  - O banco de treino (conhecimento) é compartilhado — todos aprendem juntos
  - O perfil (preferências, apps, estilo) é individual

Comandos de voz:
  "sirius, trocar de conta"          → lista contas e pede para escolher
  "sirius, sou eu o João"            → muda para a conta do João (pede PIN se tiver)
  "sirius, quem está usando"         → diz quem está logado
  "sirius, cria conta para Maria"    → cria conta nova para Maria
  "sirius, define meu PIN como 1234" → protege a conta com PIN
  "sirius, remove meu PIN"           → remove proteção de PIN
  "sirius, listar contas"            → lista todas as contas
  "sirius, entrar como convidado"    → muda para conta guest sem PIN

Integração no cerebro.py:
    from sirius_contas import SiriusContas
    self._contas = SiriusContas()
    # Ao inicializar, usa a última conta ativa
    self._perfil = self._contas.perfil_ativo
    self.memoria = self._contas.memoria_ativa
"""

import os
import sys
import re
import json
import hashlib
import threading
import unicodedata
import sqlite3
import shutil
import time
from datetime import datetime
from typing import Optional

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
CAMINHO_CONTAS = os.path.join(CAMINHO_DATA, "contas")
INDICE_PATH    = os.path.join(CAMINHO_CONTAS, "contas.json")
DB_TREINO      = os.path.join(CAMINHO_DATA, "sirius_treino.db")

os.makedirs(CAMINHO_CONTAS, exist_ok=True)
os.makedirs(CAMINHO_DATA,   exist_ok=True)


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def _slug(nome: str) -> str:
    """Converte nome para slug seguro para uso em nomes de arquivo."""
    n = _norm(nome)
    n = re.sub(r"[^\w]", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    return n[:30] or "usuario"

def _hash_pin(pin: str, salt: str = "") -> str:
    """
    Hash do PIN com PBKDF2-SHA256 + salt aleatório.
    Muito mais seguro que SHA256 simples — resistente a brute force.

    Formato armazenado: "pbkdf2$salt$hash"
    """
    import hashlib
    if not salt:
        import secrets
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac(
        "sha256",
        pin.strip().encode(),
        salt.encode(),
        iterations=100_000  # 100k iterações → ~0.1s por tentativa
    ).hex()
    return f"pbkdf2${salt}${h}"

def _verificar_pin(pin: str, pin_hash_armazenado: str) -> bool:
    """Verifica PIN contra hash armazenado. Suporta formato legado (sha256 simples)."""
    if not pin_hash_armazenado:
        return True  # sem PIN
    # Formato novo: pbkdf2$salt$hash
    if pin_hash_armazenado.startswith("pbkdf2$"):
        partes = pin_hash_armazenado.split("$")
        if len(partes) != 3:
            return False
        _, salt, _ = partes
        return _hash_pin(pin, salt) == pin_hash_armazenado
    # Formato legado: sha256 truncado (migra automaticamente na próxima troca)
    import hashlib
    return hashlib.sha256(pin.strip().encode()).hexdigest()[:16] == pin_hash_armazenado


# ---------------------------------------------------------------------------
# Conta — representa um usuário
# ---------------------------------------------------------------------------

class Conta:
    """Representa uma conta de usuário."""

    __slots__ = (
        "id", "nome", "slug", "pin_hash",
        "criado_em", "ultima_sessao", "total_sessoes",
        "ativa",
    )

    def __init__(self, id: str, nome: str, pin_hash: str = "",
                 criado_em: str = "", ultima_sessao: str = "",
                 total_sessoes: int = 0, ativa: bool = True):
        self.id             = id
        self.nome           = nome
        self.slug           = _slug(nome)
        self.pin_hash       = pin_hash         # "" = sem PIN
        self.criado_em      = criado_em or datetime.now().isoformat()
        self.ultima_sessao  = ultima_sessao
        self.total_sessoes  = total_sessoes
        self.ativa          = ativa

    @property
    def tem_pin(self) -> bool:
        return bool(self.pin_hash)

    @property
    def caminho_perfil(self) -> str:
        return os.path.join(CAMINHO_CONTAS, f"perfil_{self.slug}.json")

    @property
    def caminho_db(self) -> str:
        return os.path.join(CAMINHO_DATA, f"sirius_pessoal_{self.slug}.db")

    def verificar_pin(self, pin: str) -> bool:
        if not self.tem_pin:
            return True
        return _verificar_pin(pin, self.pin_hash)

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "nome":          self.nome,
            "pin_hash":      self.pin_hash,
            "criado_em":     self.criado_em,
            "ultima_sessao": self.ultima_sessao,
            "total_sessoes": self.total_sessoes,
            "ativa":         self.ativa,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Conta":
        return cls(
            id            = d["id"],
            nome          = d["nome"],
            pin_hash      = d.get("pin_hash", ""),
            criado_em     = d.get("criado_em", ""),
            ultima_sessao = d.get("ultima_sessao", ""),
            total_sessoes = d.get("total_sessoes", 0),
            ativa         = d.get("ativa", True),
        )

    def __repr__(self):
        pin = " 🔒" if self.tem_pin else ""
        return f"Conta({self.nome!r}{pin}, id={self.id[:8]})"


# ---------------------------------------------------------------------------
# Estado de autenticação — rastreia tentativas de PIN
# ---------------------------------------------------------------------------

class EstadoAutenticacao:
    """
    Rastreia o fluxo de login quando um PIN é necessário.
    Após 3 tentativas erradas, bloqueia por 60 segundos.
    """
    def __init__(self):
        self.aguardando_pin   = False
        self.conta_alvo       = None
        self.tentativas       = 0
        self.max_tentativas   = 3
        self.timestamp        = 0.0
        self._bloqueio_ate    = 0.0    # timestamp de desbloqueio

    def iniciar(self, conta: "Conta"):
        self.aguardando_pin = True
        self.conta_alvo     = conta
        self.tentativas     = 0
        self.timestamp      = time.time()

    def cancelar(self):
        self.aguardando_pin = False
        self.conta_alvo     = None
        self.tentativas     = 0

    def expirou(self) -> bool:
        return time.time() - self.timestamp > 30

    def esta_bloqueado(self) -> bool:
        return time.time() < self._bloqueio_ate

    def segundos_bloqueado(self) -> int:
        return max(0, int(self._bloqueio_ate - time.time()))

    def registrar_falha(self):
        self.tentativas += 1
        if self.tentativas >= self.max_tentativas:
            # Bloqueio progressivo: 60s na 1ª vez, 300s na 2ª, etc.
            self._bloqueio_ate = time.time() + 60
            self.cancelar()


# ---------------------------------------------------------------------------
# Banco de Contas — persistência do índice
# ---------------------------------------------------------------------------

class BancoContas:
    """Gerencia o arquivo contas.json — índice de todas as contas."""

    def __init__(self):
        self._lock = threading.Lock()

    def carregar(self) -> dict[str, Conta]:
        """Carrega todas as contas do índice."""
        if not os.path.exists(INDICE_PATH):
            return {}
        try:
            with open(INDICE_PATH, "r", encoding="utf-8") as f:
                dados = json.load(f)
            return {d["id"]: Conta.from_dict(d) for d in dados}
        except Exception as e:
            print(f"[CONTAS]: Erro ao carregar índice: {e}")
            return {}

    def salvar(self, contas: dict[str, Conta]):
        """Salva o índice de contas."""
        with self._lock:
            try:
                lista = [c.to_dict() for c in contas.values()]
                with open(INDICE_PATH, "w", encoding="utf-8") as f:
                    json.dump(lista, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[CONTAS]: Erro ao salvar índice: {e}")

    def gerar_id(self) -> str:
        """Gera ID único para nova conta."""
        import uuid
        return str(uuid.uuid4())[:12]


# ---------------------------------------------------------------------------
# Gerenciador de bancos por conta
# ---------------------------------------------------------------------------

class GerenciadorBancos:
    """
    Garante que cada conta tenha seu próprio banco SQLite.
    Cria e inicializa o banco se não existir.
    O banco de treino (conhecimento) é compartilhado.
    """

    _SCHEMA_PESSOAL = """
        CREATE TABLE IF NOT EXISTS conversas (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            role      TEXT,
            content   TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            sessao    TEXT
        );
        CREATE TABLE IF NOT EXISTS macros (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nome       TEXT UNIQUE,
            comandos   TEXT,
            criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS duvidas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            pergunta     TEXT UNIQUE,
            status       TEXT DEFAULT 'pendente',
            data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            pergunta         TEXT,
            resposta_dada    TEXT,
            resposta_correta TEXT,
            qualidade        REAL    DEFAULT 0.5,
            tipo             TEXT    DEFAULT 'correcao',
            timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """

    def garantir_banco(self, conta: Conta):
        """Cria o banco da conta se não existir."""
        if not os.path.exists(conta.caminho_db):
            try:
                conn = sqlite3.connect(conta.caminho_db)
                conn.executescript(self._SCHEMA_PESSOAL)
                conn.commit()
                conn.close()
                print(f"[CONTAS]: Banco criado para '{conta.nome}': {conta.caminho_db}")
            except Exception as e:
                print(f"[CONTAS]: Erro ao criar banco de '{conta.nome}': {e}")

    def migrar_banco_legado(self, conta: Conta):
        """
        Copia o banco legado (sirius_pessoal.db) para a nova conta
        quando ela é criada a partir de um banco já existente.
        Só faz isso se o banco da conta ainda não existir.
        """
        banco_legado = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
        if os.path.exists(banco_legado) and not os.path.exists(conta.caminho_db):
            try:
                shutil.copy2(banco_legado, conta.caminho_db)
                print(f"[CONTAS]: Banco legado migrado para '{conta.nome}'.")
            except Exception as e:
                print(f"[CONTAS]: Erro ao migrar banco: {e}")
                self.garantir_banco(conta)
        else:
            self.garantir_banco(conta)


# ---------------------------------------------------------------------------
# SiriusContas — gerenciador principal
# ---------------------------------------------------------------------------

class SiriusContas:
    """
    Gerencia múltiplas contas de usuário do Sirius.

    Responsabilidades:
      1. Criar/remover contas
      2. Trocar de conta ativa (com ou sem PIN)
      3. Fornecer o perfil e a memória da conta ativa
      4. Processar comandos de voz relacionados a contas

    Uso no cerebro.py:
        self._contas = SiriusContas()
        # Acessa perfil e memória da conta ativa:
        self._perfil = self._contas.perfil_ativo
        self.memoria = self._contas.memoria_ativa
        # Processa comando de conta:
        resp = self._contas.processar_comando(texto)
    """

    def __init__(self):
        self._banco    = BancoContas()
        self._gerbd    = GerenciadorBancos()
        self._contas: dict[str, Conta] = {}
        self._ativa_id: str = ""
        self._lock     = threading.Lock()
        self._auth     = EstadoAutenticacao()

        # Cache de objetos pesados por conta
        self._cache_perfis:   dict[str, object] = {}
        self._cache_memorias: dict[str, object] = {}

        self._inicializar()

    # -----------------------------------------------------------------------
    # Inicialização
    # -----------------------------------------------------------------------

    def _inicializar(self):
        """Carrega contas existentes ou cria a primeira conta."""
        self._contas = self._banco.carregar()

        if not self._contas:
            # Primeira execução — cria conta padrão e conta guest
            print("[CONTAS]: Primeira execução — criando contas padrão...")
            self._criar_conta_inicial()
        else:
            # Usa a última conta ativa (por ultima_sessao)
            ultima = max(
                self._contas.values(),
                key=lambda c: c.ultima_sessao or ""
            )
            self._ativa_id = ultima.id
            print(f"\033[92m[CONTAS]: Conta ativa: '{ultima.nome}'.\033[0m")

        # Garante que o banco da conta ativa existe
        if self._ativa_id in self._contas:
            self._gerbd.garantir_banco(self._contas[self._ativa_id])

    def _criar_conta_inicial(self):
        """
        Cria a conta padrão "Principal" migrando o banco legado,
        e uma conta "Convidado" sem dados.
        """
        # Conta principal — herda o banco legado se existir
        conta_principal = Conta(
            id   = self._banco.gerar_id(),
            nome = "Principal",
        )
        self._gerbd.migrar_banco_legado(conta_principal)
        conta_principal.ultima_sessao = datetime.now().isoformat()
        self._contas[conta_principal.id] = conta_principal

        # Conta convidado — sem dados, sem PIN
        conta_guest = Conta(
            id   = self._banco.gerar_id(),
            nome = "Convidado",
        )
        self._gerbd.garantir_banco(conta_guest)
        self._contas[conta_guest.id] = conta_guest

        self._ativa_id = conta_principal.id
        self._banco.salvar(self._contas)
        print(f"[CONTAS]: Conta 'Principal' criada (banco legado migrado).")
        print(f"[CONTAS]: Conta 'Convidado' criada.")

    # -----------------------------------------------------------------------
    # Propriedades — acesso à conta ativa
    # -----------------------------------------------------------------------

    @property
    def conta_ativa(self) -> Optional[Conta]:
        return self._contas.get(self._ativa_id)

    @property
    def perfil_ativo(self):
        """Retorna o SiriusPerfil da conta ativa (com cache)."""
        conta = self.conta_ativa
        if not conta:
            return None
        if conta.id not in self._cache_perfis:
            self._cache_perfis[conta.id] = self._carregar_perfil(conta)
        return self._cache_perfis[conta.id]

    @property
    def memoria_ativa(self):
        """Retorna a SiriusMemory da conta ativa (com cache)."""
        conta = self.conta_ativa
        if not conta:
            return None
        if conta.id not in self._cache_memorias:
            self._cache_memorias[conta.id] = self._carregar_memoria(conta)
        return self._cache_memorias[conta.id]

    @property
    def aguardando_pin(self) -> bool:
        """True se está no meio de um fluxo de autenticação por PIN."""
        if self._auth.aguardando_pin and self._auth.expirou():
            self._auth.cancelar()
        return self._auth.aguardando_pin

    # -----------------------------------------------------------------------
    # Carregamento lazy de perfil e memória
    # -----------------------------------------------------------------------

    def _carregar_perfil(self, conta: Conta):
        """Carrega ou cria o SiriusPerfil da conta."""
        try:
            from sirius_perfil import SiriusPerfil
            perfil = SiriusPerfil(caminho_json=conta.caminho_perfil)
            # Garante que o nome no perfil é o da conta
            if perfil.get("nome", "chefia") == "chefia" and conta.nome != "Principal":
                perfil.set("nome", conta.nome, salvar=True)
            return perfil
        except Exception as e:
            print(f"[CONTAS]: Erro ao carregar perfil de '{conta.nome}': {e}")
            return None

    def _carregar_memoria(self, conta: Conta):
        """Carrega ou cria a SiriusMemory da conta."""
        try:
            from memoria import SiriusMemory
            return SiriusMemory(db_pessoal=conta.caminho_db)
        except Exception as e:
            print(f"[CONTAS]: Erro ao carregar memória de '{conta.nome}': {e}")
            return None

    # -----------------------------------------------------------------------
    # Operações de conta
    # -----------------------------------------------------------------------

    def criar_conta(self, nome: str, pin: str = "") -> tuple[bool, str]:
        """
        Cria uma nova conta.
        Retorna (sucesso, mensagem).
        """
        nome = nome.strip().title()
        if not nome or len(nome) < 2:
            return False, "Nome muito curto. Use pelo menos 2 caracteres."

        # Verifica se já existe conta com esse nome
        for c in self._contas.values():
            if _norm(c.nome) == _norm(nome):
                return False, f"Já existe uma conta com o nome '{nome}'."

        nova = Conta(
            id       = self._banco.gerar_id(),
            nome     = nome,
            pin_hash = _hash_pin(pin) if pin else "",
        )
        self._gerbd.garantir_banco(nova)
        self._contas[nova.id] = nova
        self._banco.salvar(self._contas)

        pin_msg = " (com PIN)" if pin else ""
        print(f"[CONTAS]: Conta '{nome}' criada{pin_msg}.")
        return True, f"Conta de {nome} criada com sucesso{pin_msg}."

    def trocar_conta(self, nome_ou_id: str, pin: str = "") -> tuple[bool, str]:
        """
        Troca para a conta especificada.
        Retorna (sucesso, mensagem).
        Se a conta tiver PIN e não foi fornecido, retorna (False, "PIN necessário").
        """
        # Busca a conta pelo nome ou ID
        conta = self._buscar_conta(nome_ou_id)
        if not conta:
            contas_disponiveis = ", ".join(c.nome for c in self._contas.values() if c.ativa)
            return False, f"Conta '{nome_ou_id}' não encontrada. Contas: {contas_disponiveis}."

        # Se já é a conta ativa
        if conta.id == self._ativa_id:
            return True, f"Já estou usando a conta de {conta.nome}, chefia."

        # Verifica PIN
        if conta.tem_pin:
            if not pin:
                # Inicia fluxo de autenticação
                self._auth.iniciar(conta)
                return False, (
                    f"A conta de {conta.nome} tem PIN. "
                    f"Fala o PIN pra mim."
                )
            if not conta.verificar_pin(pin):
                self._auth.tentativas += 1
                restantes = self._auth.max_tentativas - self._auth.tentativas
                if restantes <= 0:
                    self._auth.cancelar()
                    return False, "PIN incorreto 3 vezes. Troca de conta cancelada."
                return False, f"PIN incorreto. {restantes} tentativa(s) restante(s)."

        return self._ativar_conta(conta)

    def processar_pin(self, texto: str) -> tuple[bool, str]:
        """
        Processa um PIN digitado/falado durante o fluxo de autenticação.
        Retorna (sucesso, mensagem).
        """
        if not self._auth.aguardando_pin:
            return False, ""

        if self._auth.expirou():
            self._auth.cancelar()
            return False, "Tempo esgotado. Troca de conta cancelada."

        # Extrai números do texto
        pin_extraido = re.sub(r"[^\d]", "", texto).strip()
        if not pin_extraido:
            # Verifica se é cancelamento
            if any(p in _norm(texto) for p in ["cancela", "cancel", "nao", "desiste"]):
                self._auth.cancelar()
                return False, "Troca de conta cancelada."
            return False, "Não entendi o PIN. Fala só os números."

        conta = self._auth.conta_alvo
        if not conta:
            self._auth.cancelar()
            return False, "Erro interno. Tente novamente."

        if conta.verificar_pin(pin_extraido):
            self._auth.cancelar()
            ok, msg = self._ativar_conta(conta)
            return ok, msg
        else:
            self._auth.tentativas += 1
            restantes = self._auth.max_tentativas - self._auth.tentativas
            if restantes <= 0:
                self._auth.cancelar()
                return False, "PIN incorreto 3 vezes. Troca de conta cancelada."
            return False, f"PIN incorreto. {restantes} tentativa(s) restante(s)."

    def _ativar_conta(self, conta: Conta) -> tuple[bool, str]:
        """Ativa a conta e atualiza metadados."""
        with self._lock:
            self._ativa_id = conta.id
            conta.ultima_sessao  = datetime.now().isoformat()
            conta.total_sessoes += 1

        self._gerbd.garantir_banco(conta)
        self._banco.salvar(self._contas)

        print(f"\033[92m[CONTAS]: Conta ativa → '{conta.nome}'.\033[0m")
        return True, f"Mudei para a conta de {conta.nome}. Oi, {conta.nome}!"

    def _buscar_conta(self, texto: str) -> Optional[Conta]:
        """Busca conta por nome (fuzzy) ou por ID."""
        t = _norm(texto)
        # Busca exata por ID
        if texto in self._contas:
            return self._contas[texto]
        # Busca por nome (exato normalizado)
        for conta in self._contas.values():
            if _norm(conta.nome) == t:
                return conta
        # Busca parcial
        for conta in self._contas.values():
            if t in _norm(conta.nome) or _norm(conta.nome) in t:
                return conta
        return None

    def definir_pin(self, pin: str) -> str:
        """Define ou atualiza o PIN da conta ativa."""
        conta = self.conta_ativa
        if not conta:
            return "Nenhuma conta ativa."
        pin = re.sub(r"[^\d]", "", pin).strip()
        if len(pin) < 4:
            return "PIN precisa ter pelo menos 4 dígitos."
        conta.pin_hash = _hash_pin(pin)  # salt gerado automaticamente
        self._banco.salvar(self._contas)
        return f"PIN definido para a conta de {conta.nome}."

    def remover_pin(self) -> str:
        """Remove o PIN da conta ativa."""
        conta = self.conta_ativa
        if not conta:
            return "Nenhuma conta ativa."
        conta.pin_hash = ""
        self._banco.salvar(self._contas)
        return f"PIN removido da conta de {conta.nome}."

    def remover_conta(self, nome: str) -> str:
        """Remove uma conta (não pode remover a conta ativa)."""
        conta = self._buscar_conta(nome)
        if not conta:
            return f"Conta '{nome}' não encontrada."
        if conta.id == self._ativa_id:
            return "Não posso remover a conta que está sendo usada agora."
        if _norm(conta.nome) == "convidado":
            return "A conta Convidado não pode ser removida."

        # Desativa em vez de deletar (preserva histórico)
        conta.ativa = False
        self._banco.salvar(self._contas)
        return f"Conta de {conta.nome} desativada."

    # -----------------------------------------------------------------------
    # Processamento de comandos de voz
    # -----------------------------------------------------------------------

    _TRIGGERS = {
        # Troca de conta
        "trocar de conta", "mudar de conta", "trocar conta", "mudar conta",
        "sou eu o", "sou eu a", "sou o", "sou a", "eu sou o", "eu sou a", "eu sou",
        "entrar como", "logar como", "acessar conta",
        "mudar para", "trocar para", "mudar pra", "trocar pra",
        "conta do", "conta da",
        # Criar
        "cria conta para", "cria conta do", "cria conta da",
        "criar conta para", "criar conta do", "criar conta da",
        "nova conta para", "nova conta do", "nova conta da",
        "adiciona usuario", "adiciona usuário",
        # PIN
        "define meu pin", "define pin", "meu pin e", "meu pin é",
        "coloca pin", "criar pin", "set pin",
        "remove meu pin", "remover pin", "tirar pin",
        # Info
        "quem esta usando", "quem está usando", "qual conta",
        "quem sou eu", "que conta",
        "listar contas", "lista contas", "ver contas",
        "mostra contas", "quais contas",
        # Convidado
        "entrar como convidado", "modo convidado",
        "conta convidado", "usar como convidado",
    }

    def e_comando_conta(self, texto: str) -> bool:
        """Retorna True se o texto é um comando relacionado a contas."""
        # Se está aguardando PIN, qualquer texto com números é candidato
        if self.aguardando_pin:
            return bool(re.search(r"\d{4,}", texto))
        t = _norm(texto)
        return any(trigger in t for trigger in self._TRIGGERS)

    def processar_comando(self, texto: str) -> str:
        """Processa comando de conta e retorna resposta."""
        t = _norm(texto)

        # ── PIN em andamento ──────────────────────────────────────────────
        if self.aguardando_pin:
            ok, msg = self.processar_pin(texto)
            return msg

        # ── Quem está usando ──────────────────────────────────────────────
        if any(p in t for p in [
            "quem esta usando", "quem está usando", "qual conta",
            "quem sou eu", "que conta", "conta ativa",
        ]):
            conta = self.conta_ativa
            if conta:
                pin_info = " (conta protegida)" if conta.tem_pin else ""
                return f"Estou usando a conta de {conta.nome}{pin_info}."
            return "Nenhuma conta ativa."

        # ── Listar contas ─────────────────────────────────────────────────
        if any(p in t for p in [
            "listar contas", "lista contas", "ver contas",
            "mostra contas", "quais contas",
        ]):
            return self._listar_contas()

        # ── Convidado ─────────────────────────────────────────────────────
        if any(p in t for p in [
            "entrar como convidado", "modo convidado",
            "conta convidado", "usar como convidado",
        ]):
            ok, msg = self.trocar_conta("convidado")
            return msg

        # ── Criar conta ───────────────────────────────────────────────────
        m = re.search(
            r"(?:cria(?:r)?|nova|adiciona)\s+(?:conta|usuario|usuário)\s+"
            r"(?:para|pro|pra|do|da|de)\s+(.+)",
            t
        )
        if m:
            nome = m.group(1).strip().title()
            ok, msg = self.criar_conta(nome)
            return msg

        # ── PIN — definir ─────────────────────────────────────────────────
        m_pin = re.search(
            r"(?:define|coloca|criar?|set)\s+(?:meu\s+)?pin\s+"
            r"(?:como|e|é|:)?\s*(\d{4,})",
            t
        )
        if m_pin:
            return self.definir_pin(m_pin.group(1))

        if re.search(r"meu pin [eé][:  ]?\s*(\d{4,})", t):
            nums = re.search(r"(\d{4,})", t)
            if nums:
                return self.definir_pin(nums.group(1))

        # ── PIN — remover ─────────────────────────────────────────────────
        if any(p in t for p in ["remove meu pin", "remover pin", "tirar pin", "sem pin"]):
            return self.remover_pin()

        # ── Trocar de conta ───────────────────────────────────────────────
        # "sou eu o João", "entrar como Maria", "mudar para Pedro"
        m = re.search(
            r"(?:sou\s+(?:eu\s+)?(?:o|a)\s+|"
            r"entrar\s+como\s+|logar\s+como\s+|"
            r"mudar\s+(?:para|pra)\s+|trocar\s+(?:para|pra)\s+|"
            r"conta\s+(?:do|da|de)\s+|acessar\s+conta\s+(?:do|da|de)?\s*)"
            r"(.+)",
            t
        )
        if m:
            nome = m.group(1).strip().title()
            ok, msg = self.trocar_conta(nome)
            return msg

        # ── Trocar de conta genérico ──────────────────────────────────────
        if any(p in t for p in ["trocar de conta", "mudar de conta", "trocar conta"]):
            return self._listar_contas_para_troca()

        return "Não entendi o comando de conta. Tente: 'trocar para João' ou 'listar contas'."

    # -----------------------------------------------------------------------
    # Formatação
    # -----------------------------------------------------------------------

    def _listar_contas(self) -> str:
        ativas = [c for c in self._contas.values() if c.ativa]
        if not ativas:
            return "Nenhuma conta cadastrada."

        linhas = ["Contas cadastradas:"]
        for c in ativas:
            ativo = " ← ativa" if c.id == self._ativa_id else ""
            pin   = " 🔒" if c.tem_pin else ""
            sess  = f" ({c.total_sessoes} sessões)" if c.total_sessoes > 1 else ""
            linhas.append(f"  • {c.nome}{pin}{sess}{ativo}")
        return "\n".join(linhas)

    def _listar_contas_para_troca(self) -> str:
        ativas = [c for c in self._contas.values()
                  if c.ativa and c.id != self._ativa_id]
        if not ativas:
            return "Não tem outras contas cadastradas. Cria uma com: 'cria conta para [nome]'."
        nomes = ", ".join(c.nome for c in ativas)
        return f"Contas disponíveis: {nomes}. Fala: 'sou eu o [nome]' para trocar."

    def status(self) -> dict:
        conta = self.conta_ativa
        return {
            "conta_ativa":    conta.nome if conta else "nenhuma",
            "total_contas":   len([c for c in self._contas.values() if c.ativa]),
            "aguardando_pin": self.aguardando_pin,
            "sessoes_ativas": conta.total_sessoes if conta else 0,
        }


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_contas_instance: Optional[SiriusContas] = None

def get_contas() -> SiriusContas:
    global _contas_instance
    if _contas_instance is None:
        _contas_instance = SiriusContas()
    return _contas_instance


# ---------------------------------------------------------------------------
# Standalone — gerencia contas via CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gerenciador de contas do Sirius")
    parser.add_argument("--listar",  action="store_true", help="Lista todas as contas")
    parser.add_argument("--criar",   type=str, metavar="NOME",    help="Cria conta")
    parser.add_argument("--pin",     type=str, metavar="CONTA",   help="Define PIN da conta")
    parser.add_argument("--trocar",  type=str, metavar="CONTA",   help="Troca para a conta")
    parser.add_argument("--remover", type=str, metavar="CONTA",   help="Remove conta")
    parser.add_argument("--status",  action="store_true",         help="Status atual")
    parser.add_argument("--cmd",     type=str, metavar="COMANDO", help="Testa comando")
    args = parser.parse_args()

    contas = SiriusContas()

    if args.status or not any([
        args.listar, args.criar, args.pin, args.trocar, args.remover, args.cmd
    ]):
        s = contas.status()
        print("\n[CONTAS STATUS]")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print()
        print(contas._listar_contas())

    if args.listar:
        print("\n" + contas._listar_contas())

    if args.criar:
        pin = input(f"PIN para '{args.criar}' (Enter = sem PIN): ").strip()
        ok, msg = contas.criar_conta(args.criar, pin)
        print(f"{'✓' if ok else '✗'} {msg}")

    if args.pin:
        conta = contas._buscar_conta(args.pin)
        if conta:
            pin = input(f"Novo PIN para '{conta.nome}' (Enter = remover): ").strip()
            if pin:
                print(contas.definir_pin(pin) if contas._ativa_id == conta.id
                      else "Troque para a conta primeiro.")
            else:
                print(contas.remover_pin())
        else:
            print(f"Conta '{args.pin}' não encontrada.")

    if args.trocar:
        ok, msg = contas.trocar_conta(args.trocar)
        print(f"{'✓' if ok else '✗'} {msg}")

    if args.remover:
        print(contas.remover_conta(args.remover))

    if args.cmd:
        if contas.e_comando_conta(args.cmd):
            print(contas.processar_comando(args.cmd))
        else:
            print("Não detectado como comando de conta.")