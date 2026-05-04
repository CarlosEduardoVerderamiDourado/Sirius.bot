"""
sirius_server.py — Servidor do S.I.R.I.U.S. (REST + WebSocket)

Permite que qualquer dispositivo na rede local converse com o Sirius:
  - Celular, tablet, outro PC — basta abrir o endereço no navegador
  - Integração com apps externos via API REST
  - Chat em tempo real via WebSocket (mesmo protocolo do WhatsApp Web)

Arquitetura:
  ┌─────────────────────────────────────────────────────────────┐
  │  Clientes (navegador, app, outro PC)                        │
  │  ws://192.168.x.x:5000/ws   ← WebSocket (chat tempo real)  │
  │  http://192.168.x.x:5000/   ← Interface web embutida       │
  └──────────────────────────┬──────────────────────────────────┘
                             │
  ┌──────────────────────────▼──────────────────────────────────┐
  │  sirius_server.py  (FastAPI + Uvicorn)                      │
  │  ├─ POST /comando       ← REST: envia comando, recebe resp  │
  │  ├─ GET  /status        ← estado atual do Sirius            │
  │  ├─ GET  /historico     ← últimas N conversas               │
  │  ├─ POST /lembrete      ← cria lembrete via API             │
  │  ├─ GET  /lembretes     ← lista lembretes ativos            │
  │  ├─ WS   /ws            ← WebSocket bidirecional            │
  │  └─ GET  /              ← interface web embutida            │
  └──────────────────────────┬──────────────────────────────────┘
                             │
  ┌──────────────────────────▼──────────────────────────────────┐
  │  SiriusCerebro  (cerebro.py)                                │
  │  processar(texto) → resposta                                │
  └─────────────────────────────────────────────────────────────┘

Instalação:
    pip install fastapi uvicorn websockets

Uso:
    # Inicia o servidor (integrado ao cerebro existente)
    python sirius_server.py

    # Ou importa de outro módulo
    from sirius_server import iniciar_servidor
    iniciar_servidor(cerebro=meu_cerebro, host="0.0.0.0", porta=5000)

    # Acesso de outro dispositivo na rede:
    http://SEU_IP:5000        ← interface web
    ws://SEU_IP:5000/ws       ← WebSocket
    POST http://SEU_IP:5000/comando   {"texto": "que horas são"}
"""

import os
import sys
import json
import base64
import time
import asyncio
import threading
import socket
import unicodedata
import re
from datetime import datetime
from typing import Optional, List, Dict

# ── Path ──────────────────────────────────────────────────────────────────────
diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
for p in [diretorio_src, diretorio_raiz]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

# ── FastAPI ───────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
    from memoria import SiriusMemory
    from sirius_contas import _verificar_pin, BancoContas
except ImportError:
    print("\033[31m[SERVIDOR]: FastAPI não instalado.\033[0m")
    print("  pip install fastapi uvicorn")
    sys.exit(1)

# =============================================================================
# Modelos Pydantic (validação automática dos payloads)
# =============================================================================

class ComandoRequest(BaseModel):
    texto: str
    conta: Optional[str] = None      # opcional: muda de conta antes de processar
    token: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None

class LoginRequest(BaseModel):
    usuario: str
    pin: Optional[str] = None

class LoginResponse(BaseModel):
    token: str
    user_id: str
    nome: str
    expires_at: str

class LembreteRequest(BaseModel):
    descricao: str
    hora: int
    minuto: int
    repetir: bool = False

class RespostaModel(BaseModel):
    resposta: str
    estado:   str
    timestamp: str
    comando_local: Optional[dict] = None

class DemonstracaoRequest(BaseModel):
    nome: str
    descricao: Optional[str] = ""
    imagem_base64: str
    clicks: Optional[List[Dict[str, object]]] = None
    token: Optional[str] = None

class DemonstracaoResponse(BaseModel):
    nome: str
    descricao: str
    imagem: str
    sequencia: List[Dict[str, object]]
    elementos: List[Dict[str, object]]
    imagem_referencia: str
    mensagem: str

class DemonstracaoListResponse(BaseModel):
    demonstracoes: List[Dict[str, object]]

# =============================================================================
# Gerenciador de conexões WebSocket
# =============================================================================

# =============================================================================
# Gerenciador de túnel (acesso externo)
# =============================================================================

class _GerenciadorTunel:
    """
    Abre um túnel para acesso externo sem precisar mexer no roteador.

    Tenta em ordem:
      1. pyngrok  → pip install pyngrok   (precisa de conta gratuita ngrok.com)
      2. cloudflared → binário gratuito, sem conta necessária

    Após abrir o túnel, a URL pública fica disponível em self.url_publica.
    """

    def __init__(self):
        self.url_publica: str | None = None
        self._processo   = None

    def iniciar(self, porta: int = 5000) -> str | None:
        """Tenta abrir túnel. Retorna a URL pública ou None."""
        url = self._tentar_ngrok(porta)
        if not url:
            url = self._tentar_cloudflared(porta)
        self.url_publica = url
        return url

    def _tentar_ngrok(self, porta: int) -> str | None:
        try:
            from pyngrok import ngrok, conf as ngrok_conf
            # Desativa logs do ngrok para não poluir o terminal
            import logging
            logging.getLogger("pyngrok").setLevel(logging.ERROR)

            tunnel = ngrok.connect(porta, "http")
            url    = tunnel.public_url
            # Garante https
            url = url.replace("http://", "https://")
            print(f"[92m[TÚNEL]: ngrok ativo → {url}[0m")
            print(f"         WebSocket  → {url.replace('https://','wss://')}/ws")
            return url
        except ImportError:
            return None
        except Exception as e:
            print(f"[TÚNEL]: ngrok falhou ({e})")
            return None

    def _tentar_cloudflared(self, porta: int) -> str | None:
        """
        Usa cloudflared (Cloudflare Tunnel) — gratuito, sem conta.
        Baixa e executa o binário automaticamente se não estiver instalado.
        """
        import subprocess
        import re

        # Tenta encontrar cloudflared no PATH
        cmd = "cloudflared"
        try:
            resultado = subprocess.run(
                [cmd, "tunnel", "--url", f"http://localhost:{porta}"],
                capture_output=True, text=True, timeout=15
            )
            # cloudflared imprime a URL no stderr
            match = re.search(r"https://[\w-]+\.trycloudflare\.com", resultado.stderr)
            if match:
                url = match.group(0)
                print(f"[92m[TÚNEL]: Cloudflare Tunnel ativo → {url}[0m")
                return url
        except FileNotFoundError:
            print("[TÚNEL]: cloudflared não encontrado.")
            print("         Para acesso externo instale uma das opções:")
            print("         1. pip install pyngrok  (requer conta gratuita em ngrok.com)")
            print("         2. https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        except Exception as e:
            print(f"[TÚNEL]: cloudflared falhou ({e})")
        return None

    def parar(self):
        try:
            from pyngrok import ngrok
            ngrok.disconnect(self.url_publica)
            ngrok.kill()
        except Exception:
            pass
        if self._processo:
            try:
                self._processo.terminate()
            except Exception:
                pass


_tunel_global = _GerenciadorTunel()


class GerenciadorWS:
    """Mantém todas as conexões WebSocket ativas e faz broadcast."""

    def __init__(self):
        self.clientes: dict[str, dict[str, dict]] = {}
        self.status_presenca: dict[str, dict[str, dict]] = {}
        self._ws_to_keys: dict[WebSocket, tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    def _normalizar_device_id(self, device_name: str) -> str:
        texto = unicodedata.normalize("NFKD", (device_name or "").lower())
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.strip().replace(" ", "_")
        return texto or "device"

    async def conectar(
        self,
        ws: WebSocket,
        user_id: str,
        device_id: str | None = None,
        device_name: str | None = None,
        session_id: str | None = None,
    ):
        if not device_name:
            device_name = device_id or "dispositivo"
        if not device_id:
            device_id = self._normalizar_device_id(device_name)

        async with self._lock:
            if user_id not in self.clientes:
                self.clientes[user_id] = {}
            self.clientes[user_id][device_id] = {
                "ws": ws,
                "device_name": device_name,
                "session_id": session_id,
                "connected_at": datetime.now().isoformat(),
            }
            self._ws_to_keys[ws] = (user_id, device_id)
            self.status_presenca.setdefault(user_id, {})[device_id] = {
                "device_name": device_name,
                "device_id": device_id,
                "ultima_atividade": time.time(),
                "ultima_atividade_iso": datetime.now().isoformat(),
                "focado": False,
            }

        print(
            f"\033[92m[SERVIDOR]: Usuário {user_id} conectado via Dispositivo {device_name}. "
            f"({len(self.clientes[user_id])} dispositivo(s) ativos).\033[0m"
        )

    async def desconectar(self, ws: WebSocket):
        async with self._lock:
            keys = self._ws_to_keys.pop(ws, None)
            if keys:
                user_id, device_id = keys
                if user_id in self.clientes:
                    self.clientes[user_id].pop(device_id, None)
                    if not self.clientes[user_id]:
                        self.clientes.pop(user_id, None)
                if user_id in self.status_presenca:
                    self.status_presenca[user_id].pop(device_id, None)
                    if not self.status_presenca[user_id]:
                        self.status_presenca.pop(user_id, None)
        total = sum(len(devs) for devs in self.clientes.values())
        print(f"[SERVIDOR]: Cliente WS desconectado ({total} dispositivo(s) ativos).")

    async def broadcast(self, mensagem: dict, session_id: str | None = None):
        """Envia para todos os clientes da sessão indicada ou para todos."""
        payload = json.dumps(mensagem, ensure_ascii=False)
        mortos = []
        async with self._lock:
            copia = [meta for devices in self.clientes.values() for meta in devices.values()]
        for meta in copia:
            if session_id is not None and meta.get("session_id") != session_id:
                continue
            ws = meta["ws"]
            try:
                await ws.send_text(payload)
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            await self.desconectar(ws)

    async def enviar(self, ws: WebSocket, mensagem: dict):
        """Envia para UM cliente específico."""
        try:
            await ws.send_text(json.dumps(mensagem, ensure_ascii=False))
        except Exception:
            await self.desconectar(ws)

    async def enviar_para_dispositivo(self, user_id: str, device_id: str, mensagem: dict) -> bool:
        """Envia uma mensagem apenas para o dispositivo indicado."""
        device_id = self._normalizar_device_id(device_id)
        async with self._lock:
            user_devices = self.clientes.get(user_id)
            if not user_devices:
                return False
            meta = user_devices.get(device_id)
            if not meta:
                return False
            ws = meta.get("ws")
        try:
            await ws.send_text(json.dumps(mensagem, ensure_ascii=False))
            return True
        except Exception:
            await self.desconectar(ws)
            return False

    async def atualizar_presenca(self, user_id: str, device_id: str, focado: bool = False,
                                 device_name: str | None = None, timestamp: float | None = None):
        """Atualiza o último heartbeat e o estado de foco do dispositivo."""
        device_id = self._normalizar_device_id(device_id)
        agora = timestamp or time.time()
        async with self._lock:
            if user_id not in self.status_presenca:
                self.status_presenca[user_id] = {}
            registro = self.status_presenca[user_id].setdefault(device_id, {})
            registro["device_id"] = device_id
            registro["device_name"] = device_name or registro.get("device_name") or device_id
            registro["ultima_atividade"] = agora
            registro["ultima_atividade_iso"] = datetime.fromtimestamp(agora).isoformat()
            registro["focado"] = bool(focado)

    async def obter_dispositivo_ativo(self, user_id: str, max_interval: float = 40.0) -> str | None:
        agora = time.time()
        async with self._lock:
            user_status = self.status_presenca.get(user_id, {})
            if not user_status:
                return None
            candidatos = [
                (dev_id, info)
                for dev_id, info in user_status.items()
                if agora - float(info.get("ultima_atividade", 0)) <= max_interval
            ]
            if not candidatos:
                return None
            focados = [item for item in candidatos if item[1].get("focado")]
            selecionados = focados if focados else candidatos
            selecionados.sort(key=lambda item: item[1].get("ultima_atividade", 0), reverse=True)
            return selecionados[0][0]

    async def obter_status_presenca(self) -> dict:
        async with self._lock:
            return {
                user_id: {
                    device_id: dict(info)
                    for device_id, info in dispositivos.items()
                }
                for user_id, dispositivos in self.status_presenca.items()
            }

    async def enviar_proativo_para_usuarios_ativos(self, mensagem: dict) -> bool:
        async with self._lock:
            user_ids = list(self.clientes.keys())
        enviados = False
        for user_id in user_ids:
            device_id = await self.obter_dispositivo_ativo(user_id)
            if device_id:
                if await self.enviar_para_dispositivo(user_id, device_id, mensagem):
                    enviados = True
                    continue
            # Fallback: envia para todos os dispositivos conectados do usuário
            async with self._lock:
                metas = list(self.clientes.get(user_id, {}).values())
            for meta in metas:
                await self.enviar(meta["ws"], mensagem)
                enviados = True
        return enviados

    @property
    def n_conexoes(self) -> int:
        return sum(len(devs) for devs in self.clientes.values())


# =============================================================================
# SiriusServidor — wrapper que integra FastAPI com o cerebro
# =============================================================================

class SiriusServidor:
    """
    Integra o SiriusCerebro com FastAPI.

    Fluxo de um comando:
      Cliente → POST /comando  →  processar_comando()
                              →  cerebro.processar(texto)
                              →  broadcast via WS (todos veem a resposta)
                              →  retorna JSON ao cliente REST

    O WebSocket recebe TUDO:
      - Respostas a comandos REST (todos os clientes veem)
      - Alertas proativos do cerebro
      - Mudanças de estado (ouvindo, processando, falando)
    """

    def __init__(self, cerebro=None):
        self._cerebro     = cerebro
        self._gerente     = GerenciadorWS()
        self._estado      = "STANDBY"
        self._historico: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tunel_ativo = False   # definido em iniciar()
        self._porta       = 5000    # atualizado em iniciar()
        self._session_memoria = SiriusMemory()
        self._session_memorias: dict[str, SiriusMemory] = {}
        self._process_lock = asyncio.Lock()
        from contextlib import asynccontextmanager


        # lifespan substitui on_event (deprecated no FastAPI >= 0.93)
        @asynccontextmanager
        async def _lifespan(app: FastAPI):
            # startup
            self._loop = asyncio.get_running_loop()
            if self._tunel_ativo:
                self._loop.run_in_executor(
                    None, lambda: _tunel_global.iniciar(self._porta)
                )
            yield
            # shutdown
            _tunel_global.parar()

        self.app = FastAPI(
            title="S.I.R.I.U.S. API",
            description="Servidor do assistente S.I.R.I.U.S.",
            version="1.0.0",
            lifespan=_lifespan,
        )

        # CORS — permite qualquer origem (rede local, apps, etc.)
        # AVISO DE SEGURANÇA: Se o servidor for exposto publicamente, restrinja as origens permitidas
        # usando SIRIUS_ALLOWED_ORIGINS ou configure allow_origins com uma lista específica.
        allowed_origins = os.getenv("SIRIUS_ALLOWED_ORIGINS", "").strip()
        if allowed_origins:
            allow_origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
        else:
            allow_origins = ["*"]
            print("[SERVIDOR]: CORS liberado para todas as origens (use SIRIUS_ALLOWED_ORIGINS para restringir).")

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._registrar_rotas()

        # Injeta callbacks no cerebro para receber proativos e logs
        if cerebro:
            self._injetar_callbacks()

    # ── Injeção de callbacks ──────────────────────────────────────────────────

    def _injetar_callbacks(self):
        """
        Conecta o cerebro ao servidor:
          - Quando o cerebro falar algo proativo → broadcast WS
          - Quando o estado mudar → broadcast WS
        """
        def _cb_falar(texto: str):
            """Chamado quando o Sirius fala algo proativamente."""
            msg = {
                "tipo":      "proativo",
                "texto":     texto,
                "timestamp": datetime.now().isoformat(),
            }
            self._historico.append(msg)
            if self._loop and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._gerente.enviar_proativo_para_usuarios_ativos(msg),
                    self._loop,
                )

        def _cb_log(texto: str):
            """Chamado para logs do sistema."""
            self._agendar_broadcast({
                "tipo":      "log",
                "texto":     texto,
                "timestamp": datetime.now().isoformat(),
            })

        try:
            if hasattr(self._cerebro, "registrar_callback"):
                self._cerebro.registrar_callback(
                    callback_falar=_cb_falar,
                    callback_log=_cb_log,
                )
        except Exception as e:
            print(f"[SERVIDOR]: Aviso ao injetar callbacks: {e}")

    def _agendar_broadcast(self, msg: dict, session_id: str | None = None):
        """Thread-safe: agenda um broadcast no loop assíncrono."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._gerente.broadcast(msg, session_id=session_id), self._loop
            )

    def _canonical_usuario(self, texto: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFKD", (texto or "").lower())
            if not unicodedata.combining(c)
        ).strip()

    def _parse_token(self, authorization: Optional[str], token: Optional[str] = None) -> str | None:
        if token and token.strip():
            return token.strip()
        if not authorization:
            return None
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return authorization.strip()

    def _obter_memoria_por_token(self, token: Optional[str], require: bool = False) -> SiriusMemory | None:
        token = (token or "").strip()
        if not token:
            if require:
                raise HTTPException(401, "Token necessário.")
            return None
        sess = self._session_memoria.validar_sessao(token)
        if not sess:
            raise HTTPException(401, "Token inválido ou expirado.")
        if token not in self._session_memorias:
            self._session_memorias[token] = SiriusMemory()
        memoria = self._session_memorias[token]
        memoria.definir_usuario(sess["user_id"], token)
        return memoria

    def _set_estado(self, estado: str):
        self._estado = estado
        self._agendar_broadcast({
            "tipo":   "estado",
            "estado": estado,
            "timestamp": datetime.now().isoformat(),
        })

    # ── Processamento de comandos ─────────────────────────────────────────────

    async def processar_comando(self, texto: str, memoria: SiriusMemory | None = None, executar_controle_localmente: bool = False) -> str | dict:
        """Processa um comando no cerebro (thread-safe com asyncio)."""
        if not self._cerebro:
            return "Cerebro não inicializado."

        self._set_estado("PROCESSANDO")
        loop = asyncio.get_event_loop()

        mem_attr_exists = hasattr(self._cerebro, "memoria")
        memoria_anterior = getattr(self._cerebro, "memoria", None)
        if memoria is not None:
            self._cerebro.memoria = memoria

        try:
            resposta = await loop.run_in_executor(
                None,
                lambda: self._cerebro.processar(texto)
            )
        finally:
            if memoria is not None:
                if mem_attr_exists:
                    self._cerebro.memoria = memoria_anterior
                else:
                    try:
                        delattr(self._cerebro, "memoria")
                    except Exception:
                        pass

        self._set_estado("STANDBY")
        return resposta or "Sem resposta."

    # ── Rotas REST ────────────────────────────────────────────────────────────

    def _registrar_rotas(self):
        app = self.app

        # ── GET / — interface web embutida ────────────────────────────────────
        @app.get("/", response_class=HTMLResponse)
        async def raiz():
            ip = _obter_ip_local()
            return HTMLResponse(_html_interface(ip))

        # ── GET /status ───────────────────────────────────────────────────────
        @app.get("/status")
        async def status():
            cerebro_ok = self._cerebro is not None
            conta = None
            if cerebro_ok and hasattr(self._cerebro, "_sessao"):
                try:
                    conta = self._cerebro._sessao.conta_atual
                except Exception:
                    pass
            # Detalhes do núcleo se disponível
            nucleo_status = None
            if cerebro_ok and hasattr(self._cerebro, "status"):
                try:
                    nucleo_status = self._cerebro.status()
                except Exception:
                    pass

            presenca = await self._gerente.obter_status_presenca()
            return {
                "online":       True,
                "estado":       self._estado,
                "cerebro":      cerebro_ok,
                "tipo_cerebro": type(self._cerebro).__name__ if self._cerebro else None,
                "nucleo":       nucleo_status,
                "conta":        conta,
                "ws_clientes":  self._gerente.n_conexoes,
                "status_presenca": presenca,
                "url_tunel":    _tunel_global.url_publica,
                "timestamp":    datetime.now().isoformat(),
            }

        # ── POST /comando ─────────────────────────────────────────────────────
        @app.post("/login", response_model=LoginResponse)
        async def login(req: LoginRequest):
            usuario = (req.usuario or "").strip()
            if not usuario:
                raise HTTPException(400, "Usuário vazio.")

            contas = BancoContas().carregar()
            if not contas:
                raise HTTPException(503, "Nenhuma conta cadastrada.")

            conta = None
            usuario_norm = self._canonical_usuario(usuario)
            for c in contas.values():
                if c.id == usuario or self._canonical_usuario(c.nome) == usuario_norm:
                    conta = c
                    break

            if not conta:
                raise HTTPException(404, "Conta não encontrada.")

            if conta.tem_pin and not req.pin:
                raise HTTPException(401, "PIN necessário.")
            if not conta.verificar_pin(req.pin or ""):
                raise HTTPException(401, "PIN inválido.")

            sess = self._session_memoria.criar_sessao(conta.id, conta.nome)
            if not sess:
                raise HTTPException(500, "Falha ao criar sessão.")

            return LoginResponse(
                token=sess["token"],
                user_id=sess["user_id"],
                nome=conta.nome,
                expires_at=sess["expira_em"],
            )

        @app.post("/comando", response_model=RespostaModel)
        async def comando(req: ComandoRequest, authorization: Optional[str] = Header(None)):
            if not req.texto.strip():
                raise HTTPException(400, "Texto vazio.")

            token = self._parse_token(authorization, req.token)
            memoria = self._obter_memoria_por_token(token, require=False)
            if req.device_id and memoria is None:
                raise HTTPException(401, "Token necessário para enviar comando local ao dispositivo.")
            texto = req.texto.strip()
            session_id = token if memoria else None

            # Broadcast: "usuário enviou"
            await self._gerente.broadcast({
                "tipo":      "usuario",
                "texto":     texto,
                "timestamp": datetime.now().isoformat(),
            }, session_id=session_id)

            async with self._process_lock:
                resposta = await self.processar_comando(
                    texto, memoria, executar_controle_localmente=False
                )

            timestamp = datetime.now().isoformat()
            self._historico.append({
                "usuario": texto,
                "sirius":  resposta if not isinstance(resposta, dict) else "[COMANDO_LOCAL]",
                "timestamp": timestamp,
            })

            if memoria:
                memoria.salvar_historico(texto, resposta if not isinstance(resposta, dict) else "[COMANDO_LOCAL]")

            if isinstance(resposta, dict) and resposta.get("tipo") == "comando_local":
                sent = False
                user_id = memoria.user_id if memoria else None
                if user_id and req.device_id:
                    sent = await self._gerente.enviar_para_dispositivo(
                        user_id=user_id,
                        device_id=req.device_id,
                        mensagem=resposta,
                    )
                if not sent and req.device_id:
                    print(f"[SERVIDOR]: Não encontrei o dispositivo {req.device_id} para user {user_id or token}.")
                assistente_texto = "Comando local enviado para o seu dispositivo."
                if user_id and req.device_id and sent:
                    await self._gerente.enviar_para_dispositivo(
                        user_id=user_id,
                        device_id=req.device_id,
                        mensagem={
                            "tipo": "sirius",
                            "texto": assistente_texto,
                            "timestamp": timestamp,
                        },
                    )
                return RespostaModel(
                    resposta=assistente_texto,
                    estado=self._estado,
                    timestamp=timestamp,
                    comando_local=resposta,
                )

            msg = {
                "tipo":      "sirius",
                "texto":     resposta,
                "timestamp": timestamp,
            }
            await self._gerente.broadcast(msg, session_id=session_id)

            return RespostaModel(
                resposta=str(resposta),
                estado=self._estado,
                timestamp=timestamp,
                comando_local=None,
            )

        @app.post("/visao/demonstracao", response_model=DemonstracaoResponse)
        async def salvar_demonstracao(req: DemonstracaoRequest, authorization: Optional[str] = Header(None)):
            token = self._parse_token(authorization, req.token)
            memoria = self._obter_memoria_por_token(token, require=True)  # Exigir token para salvar demonstrações
            user_id = memoria.user_id

            nome = (req.nome or "").strip()
            if not nome or len(nome) > 100:
                raise HTTPException(400, "Nome da demonstração é obrigatório e deve ter no máximo 100 caracteres.")

            imagem_b64 = (req.imagem_base64 or "").strip()
            if not imagem_b64:
                raise HTTPException(400, "Imagem em Base64 é obrigatória.")

            # Validar tamanho da imagem (máx 10MB)
            tamanho_estimado = len(imagem_b64) * 3 // 4  # Aproximação do tamanho decodificado
            if tamanho_estimado > 10 * 1024 * 1024:
                raise HTTPException(400, "Imagem muito grande (máximo 10MB).")

            if imagem_b64.startswith("data:"):
                imagem_b64 = imagem_b64.split(",", 1)[-1]
            try:
                dados_imagem = base64.b64decode(imagem_b64)
            except Exception:
                raise HTTPException(400, "Base64 inválida para a imagem.")

            # Validar se é uma imagem válida usando PIL
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(dados_imagem))
                img.verify()  # Verifica se é uma imagem válida
            except Exception:
                raise HTTPException(400, "Arquivo enviado não é uma imagem válida.")

            nome_arquivo = re.sub(r"[^\w\d_-]", "_", nome.lower())[:50] or f"demonstracao_{int(time.time())}"
            caminho_imagem = os.path.join(
                diretorio_raiz,
                "data",
                "screenshots",
                f"demo_{user_id}_{nome_arquivo}_{int(time.time())}.png"
            )
            os.makedirs(os.path.dirname(caminho_imagem), exist_ok=True)

            # Salvar imagem de forma assíncrona para não bloquear
            def salvar_imagem():
                with open(caminho_imagem, "wb") as f:
                    f.write(dados_imagem)
            await asyncio.get_event_loop().run_in_executor(None, salvar_imagem)

            # Processamento de visão em executor para não bloquear
            try:
                from sirius_visao import get_visao
                from sirius_autodidata import salvar_demonstracao_visual
            except Exception as e:
                raise HTTPException(500, f"Erro interno ao carregar módulo de visão: {e}")

            visao = get_visao()
            analisado = await asyncio.get_event_loop().run_in_executor(None, visao.identificar_botoes_em_imagem, caminho_imagem)
            elementos = analisado.get("elementos", []) if isinstance(analisado, dict) else []

            sequencia = req.clicks or []
            if not sequencia and elementos:
                sequencia = [
                    {
                        "texto": el.get("texto", ""),
                        "x": el.get("centro", [0, 0])[0],
                        "y": el.get("centro", [0, 0])[1],
                        "largura": el.get("largura"),
                        "altura": el.get("altura"),
                    }
                    for el in elementos
                ]

            if not sequencia:
                raise HTTPException(400, "Não foi possível extrair uma sequência de cliques. Informe clicks na carga ou envie uma imagem com texto visível.")

            # Salvar demonstração em executor
            sucesso = await asyncio.get_event_loop().run_in_executor(
                None,
                salvar_demonstracao_visual,
                user_id,
                nome,
                req.descricao or "",
                json.dumps(sequencia, ensure_ascii=False),
                caminho_imagem
            )
            if not sucesso:
                raise HTTPException(500, "Falha ao salvar a demonstração visual.")

            return DemonstracaoResponse(
                nome=nome,
                descricao=req.descricao or "",
                imagem=os.path.basename(caminho_imagem),
                sequencia=sequencia,
                elementos=elementos,
                imagem_referencia=caminho_imagem,
                mensagem=f"Demonstração '{nome}' salva para o usuário {user_id}."
            )

        @app.get("/visao/demonstracoes", response_model=DemonstracaoListResponse)
        async def listar_demonstracoes(authorization: Optional[str] = Header(None), token: Optional[str] = None):
            token = self._parse_token(authorization, token)
            memoria = self._obter_memoria_por_token(token, require=True)  # Exigir token
            user_id = memoria.user_id
            try:
                from sirius_autodidata import listar_demonstracoes_visuais
            except Exception as e:
                raise HTTPException(500, f"Erro interno ao carregar módulo de visão: {e}")
            demonstracoes = listar_demonstracoes_visuais(user_id)
            return DemonstracaoListResponse(demonstracoes=demonstracoes)

        @app.get("/visao/demonstracao")
        async def obter_demonstracao(nome: str, authorization: Optional[str] = Header(None), token: Optional[str] = None):
            token = self._parse_token(authorization, token)
            memoria = self._obter_memoria_por_token(token, require=True)  # Exigir token
            user_id = memoria.user_id
            try:
                from sirius_autodidata import obter_demonstracao_visual
            except Exception as e:
                raise HTTPException(500, f"Erro interno ao carregar módulo de visão: {e}")
            demo = obter_demonstracao_visual(user_id, nome)
            if not demo:
                raise HTTPException(404, "Demonstração não encontrada.")
            return demo

        # ── GET /historico ────────────────────────────────────────────────────
        @app.get("/historico")
        async def historico(limit: int = 20, authorization: Optional[str] = Header(None), token: Optional[str] = None):
            auth_token = self._parse_token(authorization, token)
            memoria = self._obter_memoria_por_token(auth_token, require=False)
            if memoria:
                historico = memoria.obter_historico_db(limit)
            else:
                historico = self._historico[-limit:]
            return {"historico": historico}

        # ── POST /lembrete ────────────────────────────────────────────────────
        @app.post("/lembrete")
        async def criar_lembrete(req: LembreteRequest):
            if not self._cerebro:
                raise HTTPException(503, "Cerebro indisponível.")
            texto = (
                f"me lembra às {req.hora:02d}h{req.minuto:02d} "
                f"de {req.descricao}"
            )
            if req.repetir:
                texto += " todo dia"
            resposta = await self.processar_comando(texto)
            return {"resposta": resposta}

        # ── GET /lembretes ────────────────────────────────────────────────────
        @app.get("/lembretes")
        async def listar_lembretes():
            if not self._cerebro:
                raise HTTPException(503, "Cerebro indisponível.")
            resposta = await self.processar_comando("meus lembretes")
            return {"resposta": resposta}

        # ── WS /ws — WebSocket bidirecional ───────────────────────────────────
        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            token = ws.query_params.get("token")
            session_id = self._parse_token(None, token)
            sess = None
            if session_id:
                sess = self._session_memoria.validar_sessao(session_id)
                if not sess:
                    await ws.close(code=1008)
                    return

            await ws.accept()

            user_id = sess["user_id"] if sess else None
            device_id = None
            device_name = None
            pending_text = None

            try:
                initial_text = await ws.receive_text()
                initial_payload = json.loads(initial_text)
                device_name = str(initial_payload.get("device_name", "")).strip()
                device_id = str(initial_payload.get("device_id", "")).strip() or None
                const_user_id = str(initial_payload.get("user_id", "")).strip()
                if user_id:
                    if const_user_id and const_user_id != user_id:
                        print(f"[SERVIDOR]: user_id enviado ({const_user_id}) difere da sessão ({user_id}); usando sessão.")
                else:
                    if const_user_id and const_user_id.lower() != "guest":
                        print(f"[SERVIDOR]: Ignorando user_id não autenticado ({const_user_id}); usando guest.")
                    user_id = "guest"
                if not device_name and initial_payload.get("texto"):
                    pending_text = str(initial_payload.get("texto", "")).strip()
                if not device_name and not pending_text:
                    device_name = "desconhecido"
            except json.JSONDecodeError:
                pending_text = initial_text
                user_id = user_id or "guest"
                device_name = device_name or "desconhecido"
            except Exception:
                user_id = user_id or "guest"
                device_name = device_name or "desconhecido"

            await self._gerente.conectar(
                ws,
                user_id=user_id,
                device_id=device_id,
                device_name=device_name,
                session_id=session_id,
            )

            # Envia estado atual ao conectar
            await self._gerente.enviar(ws, {
                "tipo":      "bem_vindo",
                "mensagem":  "Conectado ao S.I.R.I.U.S.",
                "estado":    self._estado,
                "timestamp": datetime.now().isoformat(),
            })

            if pending_text:
                await self._gerente.broadcast({
                    "tipo":      "usuario",
                    "texto":     pending_text,
                    "timestamp": datetime.now().isoformat(),
                }, session_id=session_id)

                memoria = self._obter_memoria_por_token(session_id, require=False)
                async with self._process_lock:
                    resposta = await self.processar_comando(pending_text, memoria)

                if memoria:
                    memoria.salvar_historico(pending_text, resposta)

                await self._gerente.broadcast({
                    "tipo":      "sirius",
                    "texto":     resposta,
                    "timestamp": datetime.now().isoformat(),
                }, session_id=session_id)

            try:
                while True:
                    dados_raw = await ws.receive_text()

                    try:
                        dados = json.loads(dados_raw)
                        if isinstance(dados, dict) and dados.get("tipo") == "heartbeat":
                            foco = bool(dados.get("has_focus", False))
                            heartbeat_device = str(dados.get("device_id", "") or device_id).strip() or device_id
                            if heartbeat_device:
                                await self._gerente.atualizar_presenca(
                                    user_id=user_id,
                                    device_id=heartbeat_device,
                                    focado=foco,
                                    device_name=str(dados.get("device_name", device_name) or device_name),
                                )
                            continue
                        texto = dados.get("texto", "").strip()
                    except json.JSONDecodeError:
                        texto = dados_raw.strip()

                    if not texto:
                        continue

                    # Eco: confirma recebimento
                    await self._gerente.broadcast({
                        "tipo":      "usuario",
                        "texto":     texto,
                        "timestamp": datetime.now().isoformat(),
                    }, session_id=session_id)

                    memoria = self._obter_memoria_por_token(session_id, require=False)
                    async with self._process_lock:
                        resposta = await self.processar_comando(texto, memoria)

                    if memoria:
                        memoria.salvar_historico(texto, resposta)

                    await self._gerente.broadcast({
                        "tipo":      "sirius",
                        "texto":     resposta,
                        "timestamp": datetime.now().isoformat(),
                    }, session_id=session_id)

            except WebSocketDisconnect:
                await self._gerente.desconectar(ws)
            except Exception as e:
                print(f"[SERVIDOR WS]: {e}")
                await self._gerente.desconectar(ws)

        # ── GET /ip — retorna o IP da máquina para facilitar configuração ─────
        @app.get("/ip")
        async def meu_ip():
            return {"ip": _obter_ip_local(), "porta": 5000}

    # ── Inicialização ─────────────────────────────────────────────────────────

    async def _capturar_loop(self):
        """Captura o event loop para uso em callbacks síncronos."""
        self._loop = asyncio.get_running_loop()

    def iniciar(self, host: str = "0.0.0.0", porta: int = 5000,
                log_level: str = "warning", tunel: bool = False):
        """Inicia o servidor (bloqueante). Use iniciar_em_thread() para não bloquear."""
        from contextlib import asynccontextmanager

        self._tunel_ativo = tunel
        self._porta       = porta

        @asynccontextmanager
        async def _lifespan(app):
            # startup
            self._loop = asyncio.get_running_loop()
            if self._tunel_ativo:
                self._loop.run_in_executor(
                    None, lambda: _tunel_global.iniciar(porta)
                )
            yield
            # shutdown
            _tunel_global.parar()

        self.app.router.lifespan_context = _lifespan

        ip = _obter_ip_local()
        print(f"\033[92m[SERVIDOR]: S.I.R.I.U.S. online!\033[0m")
        print(f"  Local:      http://localhost:{porta}")
        print(f"  Rede local: http://{ip}:{porta}")
        print(f"  WebSocket:  ws://{ip}:{porta}/ws")
        print(f"  API docs:   http://{ip}:{porta}/docs")
        if tunel:
            print(f"  Túnel:      abrindo... (aguarde alguns segundos)")
        else:
            print(f"  Túnel:      desativado  (use --tunel para acesso externo)")

        uvicorn.run(self.app, host=host, port=porta, log_level=log_level)
    def iniciar_em_thread(self, host: str = "0.0.0.0", porta: int = 5000, tunel: bool = False):
        """
        Inicia o servidor em thread daemon.
        Não bloqueia o processo principal.
        Uso: quando o Sirius já está rodando (wallpaper, interface, etc.)
        """
        t = threading.Thread(
            target=self.iniciar,
            kwargs={"host": host, "porta": porta, "tunel": tunel},
            daemon=True,
            name="SiriusServidor",
        )
        t.start()
        print(f"\033[92m[SERVIDOR]: Iniciando em background "
              f"({_obter_ip_local()}:{porta})...\033[0m")
        return t


# =============================================================================
# Utilitários
# =============================================================================

def _obter_ip_local() -> str:
    """Retorna o IP da máquina na rede local."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _html_interface(ip: str) -> str:
    """Interface web embutida — acessível de qualquer dispositivo na rede."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>S.I.R.I.U.S.</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #000a12;
    color: #5de2ff;
    font-family: 'Consolas', monospace;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }}
  #header {{
    padding: 12px 20px;
    border-bottom: 1px solid #1a3a4a;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: rgba(0,20,40,0.8);
  }}
  #titulo {{ font-size: 14px; font-weight: bold; letter-spacing: 4px; }}
  #status-dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: #5de2ff; display: inline-block;
    margin-right: 6px; animation: pulsar 2s infinite;
  }}
  @keyframes pulsar {{
    0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }}
  }}
  #chat {{
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .msg {{
    max-width: 80%;
    padding: 10px 14px;
    border-radius: 10px;
    font-size: 13px;
    line-height: 1.5;
    word-break: break-word;
  }}
  .msg.usuario {{
    align-self: flex-end;
    background: rgba(93,226,255,0.12);
    border: 1px solid rgba(93,226,255,0.3);
    color: #fff;
  }}
  .msg.sirius {{
    align-self: flex-start;
    background: rgba(0,30,50,0.8);
    border: 1px solid #1a4a5a;
    color: #5de2ff;
  }}
  .msg.sistema {{
    align-self: center;
    color: rgba(93,226,255,0.4);
    font-size: 11px;
    border: none;
    background: none;
  }}
  .hora {{ font-size: 10px; opacity: 0.4; margin-top: 4px; }}
  #input-area {{
    padding: 12px 16px;
    border-top: 1px solid #1a3a4a;
    display: flex;
    gap: 8px;
    background: rgba(0,10,20,0.9);
  }}
  #input {{
    flex: 1;
    background: rgba(93,226,255,0.06);
    border: 1px solid rgba(93,226,255,0.3);
    border-radius: 8px;
    color: #fff;
    padding: 10px 14px;
    font-family: inherit;
    font-size: 14px;
    outline: none;
  }}
  #input:focus {{ border-color: #5de2ff; }}
  #btn {{
    background: rgba(93,226,255,0.12);
    border: 1px solid #5de2ff;
    border-radius: 8px;
    color: #5de2ff;
    padding: 0 18px;
    font-size: 18px;
    cursor: pointer;
    transition: background 0.2s;
  }}
  #btn:hover {{ background: rgba(93,226,255,0.25); }}
  #conectado {{ font-size: 11px; color: rgba(93,226,255,0.5); }}
  ::-webkit-scrollbar {{ width: 4px; }}
  ::-webkit-scrollbar-thumb {{ background: rgba(93,226,255,0.2); border-radius: 2px; }}
</style>
</head>
<body>
<div id="header">
  <div id="titulo">⬛ S.I.R.I.U.S.</div>
  <div><span id="status-dot"></span><span id="conectado">conectando...</span></div>
</div>
<div id="chat"></div>
<div id="input-area">
  <input id="input" placeholder="Digite um comando ou pergunta..." autocomplete="off">
  <button id="btn" onclick="enviar()">▶</button>
</div>

<script>
// URL dinâmica — funciona tanto na rede local quanto via túnel (ngrok, cloudflare)
// window.location.host já contém host:porta corretamente
const proto  = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = proto + '//' + window.location.host + '/ws';
let ws;

function conectar() {{
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {{
    const storage = window.localStorage;
    const userId = storage.getItem('sirius_user_id') || prompt('Informe seu user_id (ex: joao)', 'joao') || 'guest';
    const deviceName = storage.getItem('sirius_device_name') || prompt('Nome deste dispositivo', navigator.platform || 'Navegador') || 'Navegador';
    storage.setItem('sirius_user_id', userId);
    storage.setItem('sirius_device_name', deviceName);
    ws.send(JSON.stringify({{ user_id: userId, device_name: deviceName }}));

    document.getElementById('conectado').textContent = 'conectado';
    document.getElementById('status-dot').style.background = '#00ff88';
    adicionarMsg('Conectado ao S.I.R.I.U.S.', 'sistema');
  }};

  ws.onmessage = (e) => {{
    try {{
      const d = JSON.parse(e.data);
      if (d.tipo === 'sirius' || d.tipo === 'proativo') {{
        adicionarMsg(d.texto, 'sirius', d.timestamp);
      }} else if (d.tipo === 'usuario') {{
        // Só mostra se não foi a gente (evita duplicata)
        // (o eco do servidor confirma recebimento)
      }} else if (d.tipo === 'bem_vindo') {{
        adicionarMsg(d.mensagem, 'sistema');
      }} else if (d.tipo === 'estado') {{
        atualizarEstado(d.estado);
      }} else if (d.tipo === 'log') {{
        console.log('[LOG]', d.texto);
      }}
    }} catch(ex) {{
      adicionarMsg(e.data, 'sirius');
    }}
  }};

  ws.onclose = () => {{
    document.getElementById('conectado').textContent = 'desconectado — reconectando...';
    document.getElementById('status-dot').style.background = '#ff4444';
    setTimeout(conectar, 3000);
  }};

  ws.onerror = () => {{
    document.getElementById('conectado').textContent = 'erro de conexão';
    document.getElementById('status-dot').style.background = '#ffd700';
  }};
}}

function atualizarEstado(estado) {{
  const cores = {{
    STANDBY: '#5de2ff', OUVINDO: '#00ff88',
    PROCESSANDO: '#ffd700', FALANDO: '#fff'
  }};
  document.getElementById('status-dot').style.background = cores[estado] || '#5de2ff';
  document.getElementById('conectado').textContent = estado.toLowerCase();
}}

function adicionarMsg(texto, tipo, timestamp) {{
  const chat = document.getElementById('chat');
  const div  = document.createElement('div');
  div.className = 'msg ' + tipo;

  const hora = timestamp
    ? new Date(timestamp).toLocaleTimeString('pt-BR', {{hour:'2-digit', minute:'2-digit'}})
    : new Date().toLocaleTimeString('pt-BR', {{hour:'2-digit', minute:'2-digit'}});

  div.innerHTML = `<div>${{texto.replace(/\\n/g,'<br>')}}</div>
    ${{tipo !== 'sistema' ? `<div class="hora">${{hora}}</div>` : ''}}`;

  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}}

function enviar() {{
  const input = document.getElementById('input');
  const texto = input.value.trim();
  if (!texto) return;

  adicionarMsg(texto, 'usuario');
  input.value = '';

  if (ws && ws.readyState === WebSocket.OPEN) {{
    ws.send(JSON.stringify({{texto}}));
  }}
}}

document.getElementById('input').addEventListener('keydown', e => {{
  if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); enviar(); }}
}});

conectar();
</script>
</body>
</html>"""


# =============================================================================
# Função pública de inicialização
# =============================================================================

_servidor_global: SiriusServidor | None = None


def iniciar_servidor(
    cerebro=None,
    host: str    = "0.0.0.0",
    porta: int   = 5000,
    em_thread: bool = True,
    tunel: bool  = False,
) -> SiriusServidor:
    """
    Inicializa o servidor do Sirius.

    Parâmetros:
        cerebro:   instância de SiriusCerebro (ou None para criar uma nova)
        host:      interface de rede ("0.0.0.0" = todas as interfaces)
        porta:     porta TCP (padrão: 5000)
        em_thread: True = não bloqueia | False = bloqueia o processo atual
        tunel:     True = abre ngrok ou cloudflared para acesso externo
                   pip install pyngrok   ← opção 1 (requer conta gratuita)
                   cloudflared no PATH   ← opção 2 (sem conta)

    Acesso rede local:
        http://SEU_IP:5000
    Acesso externo (tunel=True):
        URL impressa no terminal após alguns segundos
    """
    global _servidor_global

    if cerebro is None:
        print("[SERVIDOR]: Criando núcleo leve (memória + processamento + aprendizado)...")
        try:
            # SiriusNucleo: só memória, neurônio e aprendizado — sem câmera/voz/UI
            from sirius_nucleo import SiriusNucleo
            cerebro = SiriusNucleo()
        except ImportError:
            # Fallback para o cerebro completo se o nucleo não existir
            try:
                from cerebro import SiriusCerebro
                cerebro = SiriusCerebro()
                print("[SERVIDOR]: Usando SiriusCerebro completo (nucleo não encontrado).")
            except ImportError as e:
                print(f"[SERVIDOR]: Não encontrei cerebro nem nucleo: {e}")

    _servidor_global = SiriusServidor(cerebro=cerebro)

    if em_thread:
        _servidor_global.iniciar_em_thread(host=host, porta=porta, tunel=tunel)
    else:
        _servidor_global.iniciar(host=host, porta=porta, tunel=tunel)

    return _servidor_global


# =============================================================================
# Entry point standalone
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="S.I.R.I.U.S. — Servidor")
    parser.add_argument("--porta",     type=int, default=5000,
                        help="Porta TCP (padrão: 5000)")
    parser.add_argument("--host",      type=str, default="0.0.0.0",
                        help="Host (padrão: 0.0.0.0 = todas as interfaces)")
    parser.add_argument("--sem-audio", action="store_true",
                        help="Não inicializa o áudio (modo silencioso)")
    parser.add_argument("--tunel",     action="store_true",
                        help="Abre túnel ngrok/cloudflared para acesso externo")
    args = parser.parse_args()

    print("\033[94m[SERVIDOR]: Inicializando S.I.R.I.U.S...\033[0m")

    try:
        from sirius_nucleo import SiriusNucleo
        cerebro = SiriusNucleo()
        print("\033[92m[SERVIDOR]: Núcleo leve pronto (memória + aprendizado).\033[0m")
    except ImportError:
        try:
            from cerebro import SiriusCerebro
            cerebro = SiriusCerebro()
            print("\033[92m[SERVIDOR]: Cérebro completo carregado.\033[0m")
        except Exception as e:
            print(f"\033[31m[SERVIDOR]: Erro ao carregar: {e}\033[0m")
            cerebro = None

    # Bloqueia — este é o processo principal
    iniciar_servidor(cerebro=cerebro, host=args.host,
                     porta=args.porta, em_thread=False,
                     tunel=args.tunel)
