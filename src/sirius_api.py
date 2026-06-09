"""
sirius_api.py — S.I.R.I.U.S. v5.2 — Servidor FastAPI
======================================================
Ponte entre os formulários de login/cadastro e o SiriusCerebro.

Endpoints:
  POST /chat          — Processa texto no cerebro e retorna resposta
  POST /auth/login    — Valida usuário no SQLite (tabela usuarios)
  POST /auth/cadastro — Registra novo usuário
  GET  /status        — Uso de CPU/RAM via controle.uso_cpu_ram()
  GET  /healthcheck   — Ping simples

Dependências:
    pip install fastapi uvicorn[standard] python-jose passlib[bcrypt]

Execução:
    cd src
    python sirius_api.py
    # ou
    uvicorn sirius_api:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    # Importados apenas para o type-checker — nunca executados em runtime
    from cerebro import SiriusCerebro as _SiriusCerebro
    from memoria import SiriusMemoria as _SiriusMemoria

# ── Garante que src/ está no path para imports locais ────────────────────────
_DIR_SRC = os.path.dirname(os.path.abspath(__file__))
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)

# ── FastAPI ───────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI, HTTPException, Depends, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel
    import uvicorn
except ImportError as e:
    raise SystemExit(
        f"[SIRIUS_API]: FastAPI não instalado — rode: pip install fastapi uvicorn[standard]\n{e}"
    )

# ── SiriusCerebro e SiriusMemoria ────────────────────────────────────────────
try:
    from cerebro import SiriusCerebro
    _CEREBRO_DISPONIVEL = True
except ImportError:
    SiriusCerebro = None
    _CEREBRO_DISPONIVEL = False
    print("[SIRIUS_API]: cerebro.py não encontrado — /chat desabilitado.")

try:
    from memoria import SiriusMemoria
    _MEMORIA_DISPONIVEL = True
except ImportError:
    SiriusMemoria = None
    _MEMORIA_DISPONIVEL = False
    print("[SIRIUS_API]: memoria.py não encontrado — auth desabilitada.")


# =============================================================================
# Instâncias globais (singleton por processo)
# =============================================================================

_cerebro: Any = None   # instância de SiriusCerebro em runtime
_memoria: Any = None   # instância de SiriusMemoria em runtime


def _get_cerebro() -> Any:
    global _cerebro
    if _cerebro is None and _CEREBRO_DISPONIVEL:
        print("[SIRIUS_API]: Inicializando SiriusCerebro...")
        _cerebro = SiriusCerebro()
    if _cerebro is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SiriusCerebro não disponível.",
        )
    return _cerebro


def _get_memoria() -> Any:
    global _memoria
    if _memoria is None and _MEMORIA_DISPONIVEL:
        _memoria = SiriusMemoria()
    if _memoria is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SiriusMemoria não disponível.",
        )
    return _memoria


# =============================================================================
# App FastAPI
# =============================================================================

app = FastAPI(
    title="S.I.R.I.U.S. API",
    version="5.2.0",
    description="Interface REST para o copiloto S.I.R.I.U.S. do Carlos.",
)

# CORS — permite chamadas dos formulários de login/cadastro no frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ajuste para domínio específico em produção
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer(auto_error=False)


# =============================================================================
# Schemas Pydantic
# =============================================================================

class ChatRequest(BaseModel):
    texto: str
    user_id: Optional[str] = ""
    session_id: Optional[str] = ""


class ChatResponse(BaseModel):
    resposta: str
    tempo_ms: float


class LoginRequest(BaseModel):
    user_id: str
    senha: str


class CadastroRequest(BaseModel):
    user_id: str
    nome: str
    senha: str


class AuthResponse(BaseModel):
    token: str
    user_id: str
    nome: str
    mensagem: str


class StatusResponse(BaseModel):
    cpu_percent: Optional[float]
    ram_percent: Optional[float]
    ram_usada_mb: Optional[float]
    cerebro_ativo: bool
    uptime_segundos: float
    versao: str


# =============================================================================
# Helpers
# =============================================================================

_INICIO = time.time()


def _hash_senha(senha: str) -> str:
    """SHA-256 simples. Em produção use bcrypt via passlib."""
    return hashlib.sha256(senha.encode()).hexdigest()


def _validar_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    mem: Any,
) -> Optional[dict]:
    if not credentials:
        return None
    return mem.validar_sessao(credentials.credentials)


# =============================================================================
# Endpoints
# =============================================================================

# ── GET /healthcheck ─────────────────────────────────────────────────────────

@app.get("/healthcheck", tags=["Sistema"])
def healthcheck():
    return {"status": "ok", "versao": "5.2.0"}


# ── GET /status ──────────────────────────────────────────────────────────────

@app.get("/status", response_model=StatusResponse, tags=["Sistema"])
def get_status():
    """
    Retorna uso de CPU/RAM via SiriusControle.uso_cpu_ram().
    Não requer autenticação — usado pelo dashboard.
    """
    cpu = None
    ram_pct = None
    ram_mb  = None

    try:
        cerebro = _get_cerebro()
        if cerebro.controle:
            info = cerebro.controle.uso_cpu_ram()
            # uso_cpu_ram() pode retornar dict ou string formatada
            if isinstance(info, dict):
                cpu     = info.get("cpu_percent")
                ram_pct = info.get("ram_percent")
                ram_mb  = info.get("ram_usada_mb")
            elif isinstance(info, str):
                # fallback: tenta parsear "CPU: 12% | RAM: 45% (3.2 GB)"
                import re
                m_cpu = re.search(r"CPU[:\s]+(\d+\.?\d*)", info, re.I)
                m_ram = re.search(r"RAM[:\s]+(\d+\.?\d*)", info, re.I)
                if m_cpu: cpu     = float(m_cpu.group(1))
                if m_ram: ram_pct = float(m_ram.group(1))
        ativo = True
    except HTTPException:
        ativo = False

    # Fallback: usa psutil diretamente se controle não disponível
    if cpu is None:
        try:
            import psutil
            cpu     = psutil.cpu_percent(interval=0.1)
            mem     = psutil.virtual_memory()
            ram_pct = mem.percent
            ram_mb  = mem.used / (1024 ** 2)
        except Exception:
            pass

    return StatusResponse(
        cpu_percent=cpu,
        ram_percent=ram_pct,
        ram_usada_mb=round(ram_mb, 1) if ram_mb else None,
        cerebro_ativo=ativo,
        uptime_segundos=round(time.time() - _INICIO, 1),
        versao="5.2.0",
    )


# ── POST /chat ───────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
def post_chat(
    req: ChatRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
):
    """
    Recebe JSON com 'texto', processa no SiriusCerebro e retorna a resposta.

    Body:
        {"texto": "Abre o bloco de notas", "user_id": "carlos", "session_id": "..."}

    Autenticação opcional via Bearer token no header Authorization.
    """
    if not req.texto.strip():
        raise HTTPException(status_code=400, detail="Campo 'texto' não pode ser vazio.")

    cerebro = _get_cerebro()

    # Injeta contexto de usuário se disponível
    if req.user_id:
        cerebro.memoria.user_id    = req.user_id
        cerebro.memoria.session_id = req.session_id or ""

    t0 = time.time()
    try:
        resposta = cerebro.processar(req.texto)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no cérebro: {e}")

    return ChatResponse(
        resposta=resposta or "...",
        tempo_ms=round((time.time() - t0) * 1000, 1),
    )


# ── POST /auth/login ─────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=AuthResponse, tags=["Auth"])
def post_login(req: LoginRequest):
    """
    Valida usuário contra a tabela 'usuarios' no banco SQLite.

    Body:
        {"user_id": "carlos", "senha": "minha_senha"}

    Retorna token de sessão válido por 24h.
    """
    if not req.user_id.strip() or not req.senha.strip():
        raise HTTPException(status_code=400, detail="user_id e senha são obrigatórios.")

    mem = _get_memoria()
    usuario = mem.validar_usuario(req.user_id.strip(), _hash_senha(req.senha))

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
        )

    token = mem.criar_sessao(usuario["user_id"], usuario["nome"])

    return AuthResponse(
        token=token,
        user_id=usuario["user_id"],
        nome=usuario["nome"],
        mensagem="Login realizado com sucesso.",
    )


# ── POST /auth/cadastro ──────────────────────────────────────────────────────

@app.post("/auth/cadastro", response_model=AuthResponse, tags=["Auth"])
def post_cadastro(req: CadastroRequest):
    """
    Registra novo usuário na tabela 'usuarios'.

    Body:
        {"user_id": "carlos", "nome": "Carlos", "senha": "minha_senha"}
    """
    if not req.user_id.strip() or not req.nome.strip() or not req.senha.strip():
        raise HTTPException(status_code=400, detail="user_id, nome e senha são obrigatórios.")

    import re
    if not re.match(r"^[a-zA-Z0-9_\-]{3,30}$", req.user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id deve ter 3-30 caracteres alfanuméricos.",
        )

    mem = _get_memoria()

    try:
        with mem._lock_p:
            conn = mem._conn_pessoal()
            conn.execute(
                "INSERT INTO usuarios (user_id, nome, senha_hash) VALUES (?,?,?)",
                (req.user_id.strip(), req.nome.strip(), _hash_senha(req.senha)),
            )
            conn.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="user_id já existe.")
        raise HTTPException(status_code=500, detail=f"Erro ao cadastrar: {e}")

    token = mem.criar_sessao(req.user_id, req.nome)

    return AuthResponse(
        token=token,
        user_id=req.user_id,
        nome=req.nome,
        mensagem="Cadastro realizado com sucesso.",
    )


# ── POST /auth/logout ────────────────────────────────────────────────────────

@app.post("/auth/logout", tags=["Auth"])
def post_logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Token não fornecido.")

    mem = _get_memoria()
    try:
        with mem._lock_p:
            conn = mem._conn_pessoal()
            conn.execute("DELETE FROM sessoes WHERE token=?", (credentials.credentials,))
            conn.commit()
    except Exception:
        pass

    return {"mensagem": "Logout realizado."}


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  S.I.R.I.U.S. API v5.2 — http://0.0.0.0:8000")
    print("  Docs: http://localhost:8000/docs")
    print("=" * 60)
    uvicorn.run(
        "sirius_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )