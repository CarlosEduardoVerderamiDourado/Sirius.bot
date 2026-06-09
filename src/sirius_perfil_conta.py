"""
sirius_perfil_conta.py — Integração unificada: Conta + Perfil + Voz

Resolve o problema de ter três sistemas separados que precisam
se comunicar. Este módulo é o único ponto de entrada para o cerebro.py.

Fluxo completo ao criar conta:
  1. Cria a conta (sirius_contas.py)
  2. Cria perfil personalizado para essa conta (sirius_perfil.py)
  3. Dispara gravação de voz automaticamente (sirius_voz_id.py)
  4. Troca a memória ativa para o banco da nova conta

Fluxo ao fazer login:
  1. Verifica PIN (se tiver)
  2. Carrega perfil da conta
  3. Tenta identificar pela voz (se resemblyzer instalado)
  4. Troca banco de memória para o da conta

Uso no cerebro.py:
    from sirius_perfil_conta import SiriusSessao
    self._sessao = SiriusSessao(callback_falar=audio.falar)

    # Acesso unificado
    self._sessao.nome_usuario        → "João" ou "chefia"
    self._sessao.memoria             → SiriusMemoria da conta ativa
    self._sessao.perfil              → SiriusPerfil da conta ativa
    self._sessao.processar(comando)  → trata comandos de conta/perfil/voz
"""

import os
import sys
import re
import json
import threading
import unicodedata
import time
from typing import Optional, Callable

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
CAMINHO_CONTAS = os.path.join(CAMINHO_DATA, "contas")
os.makedirs(CAMINHO_CONTAS, exist_ok=True)
os.makedirs(CAMINHO_DATA,   exist_ok=True)


def _norm(texto: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# SiriusSessao — ponto central de integração
# ---------------------------------------------------------------------------

class SiriusSessao:
    """
    Gerencia a sessão ativa do Sirius unificando conta, perfil e voz.

    Substitui o uso separado de:
        self._contas  = SiriusContas()
        self._perfil  = SiriusPerfil()
        self._voz_id  = SiriusVozID()

    Por um único:
        self._sessao = SiriusSessao(callback_falar=audio.falar)
    """

    def __init__(self, callback_falar: Callable = None,
                 callback_log: Callable = None):
        self._callback_falar = callback_falar
        self._callback_log   = callback_log
        self._lock           = threading.Lock()

        # Subsistemas
        self._contas   = None
        self._voz_id   = None
        self._perfil   = None
        self._memoria  = None

        self._inicializar()

    # -----------------------------------------------------------------------
    # Inicialização
    # -----------------------------------------------------------------------

    def _inicializar(self):
        # 1. Sistema de contas
        try:
            from sirius_contas import SiriusContas
            self._contas = SiriusContas()

            # Conecta callback: quando conta mudar → recarrega perfil e memória
            self._contas._on_troca = self._ao_trocar_conta
            self._contas._on_criar = self._ao_criar_conta

            print("\033[92m[SESSAO]: Sistema de contas ativo.\033[0m")
        except Exception as e:
            print(f"\033[33m[SESSAO]: Contas indisponível: {e}\033[0m")

        # 2. Carrega perfil e memória da conta ativa
        self._recarregar_perfil_e_memoria()

        # 3. Sistema de voz (opcional — não trava se não instalado)
        try:
            from sirius_voz_id import SiriusVozID
            self._voz_id = SiriusVozID(
                contas=self._contas,
                callback_troca=self._ao_identificar_voz
            )
            # Carrega perfis de voz de todas as contas existentes
            if self._contas:
                for conta in self._contas._contas.values():
                    if conta.ativa:
                        self._voz_id.registrar_conta(conta.id, conta.nome)
            print("\033[92m[SESSAO]: Identificação de voz ativa.\033[0m")
        except ImportError:
            print("\033[33m[SESSAO]: resemblyzer não instalado — voz_id desabilitado.\033[0m")
            print("  pip install resemblyzer")
        except Exception as e:
            print(f"\033[33m[SESSAO]: Voz ID indisponível: {e}\033[0m")

    def _recarregar_perfil_e_memoria(self):
        """Recarrega perfil e memória baseado na conta ativa."""
        from sirius_perfil import SiriusPerfil, PERFIL_PADRAO
        from memoria import SiriusMemoria

        conta = self._conta_ativa
        if conta:
            # Perfil isolado por conta — arquivo JSON separado
            caminho_json = os.path.join(CAMINHO_CONTAS, f"perfil_{conta.slug}.json")
            self._perfil = SiriusPerfil(caminho_json=caminho_json)

            # Memória isolada por conta — banco SQLite separado
            db_path = os.path.join(CAMINHO_DATA, f"sirius_pessoal_{conta.slug}.db")
            self._memoria = SiriusMemoria(db_path=db_path)

            print(f"\033[92m[SESSAO]: Perfil de '{conta.nome}' carregado.\033[0m")
        else:
            # Sem contas — usa perfil e memória padrão
            self._perfil  = SiriusPerfil()
            self._memoria = SiriusMemoria()

    # -----------------------------------------------------------------------
    # Callbacks de eventos
    # -----------------------------------------------------------------------

    def _ao_trocar_conta(self, conta_nova):
        """Chamado pelo SiriusContas quando a conta ativa muda."""
        self._recarregar_perfil_e_memoria()
        nome = conta_nova.nome if conta_nova else "desconhecido"
        msg  = f"Conta trocada para {nome}. Olá, {nome}!"
        print(f"\033[94m[SESSAO]: {msg}\033[0m")
        self._falar(msg)

    def _ao_criar_conta(self, conta_nova):
        """
        Chamado quando uma nova conta é criada.
        Dispara gravação de voz automaticamente.
        """
        nome = conta_nova.nome
        self._recarregar_perfil_e_memoria()

        # Seta nome no perfil novo automaticamente
        if self._perfil:
            self._perfil.set("nome", nome)

        msg = (
            f"Conta criada para {nome}! "
            "Vou gravar sua voz agora para te reconhecer automaticamente."
        )
        self._falar(msg)
        print(f"\033[92m[SESSAO]: Nova conta '{nome}' — iniciando captura de voz.\033[0m")

        # Dispara gravação em background
        if self._voz_id:
            def _gravar():
                time.sleep(1.5)  # aguarda o TTS terminar de falar
                self._voz_id.gravar_amostras(
                    conta_id     = conta_nova.id,
                    conta_nome   = conta_nova.nome,
                    n_amostras   = 5,
                    callback_status = self._falar
                )
            threading.Thread(target=_gravar, daemon=True).start()
        else:
            self._falar(
                "resemblyzer não instalado, então não vou reconhecer sua voz automaticamente. "
                "Instale com: pip install resemblyzer"
            )

    def _ao_identificar_voz(self, conta_id: str, conta_nome: str, similaridade: float):
        """
        Chamado pelo SiriusVozID quando identifica uma voz.
        Troca de conta automaticamente se a voz for de outro usuário.
        """
        conta_atual = self._conta_ativa
        if conta_atual and conta_atual.id == conta_id:
            return  # já está na conta certa

        if self._contas:
            ok, msg = self._contas.trocar_conta(conta_nome)
            if ok:
                print(f"\033[94m[SESSAO]: Voz de '{conta_nome}' identificada "
                      f"(sim={similaridade:.0%}) → troca automática.\033[0m")
                self._falar(f"Oi {conta_nome}! Reconheci sua voz.")

    # -----------------------------------------------------------------------
    # Acesso unificado — propriedades
    # -----------------------------------------------------------------------

    @property
    def _conta_ativa(self):
        return self._contas.conta_ativa if self._contas else None

    @property
    def nome_usuario(self) -> str:
        """Nome para usar nas respostas — do perfil ou da conta."""
        if self._perfil:
            nome = self._perfil.get("nome")
            if nome and nome != "chefia":
                return nome
        conta = self._conta_ativa
        if conta and conta.nome.lower() not in ("convidado", "guest"):
            return conta.nome
        return "chefia"

    @property
    def memoria(self):
        """SiriusMemoria da conta ativa."""
        return self._memoria

    @property
    def perfil(self):
        """SiriusPerfil da conta ativa."""
        return self._perfil

    @property
    def conta(self):
        """Conta ativa atual."""
        return self._conta_ativa

    # -----------------------------------------------------------------------
    # Identificação por voz — chamado pelo audio_handler após cada fala
    # -----------------------------------------------------------------------

    def identificar_voz(self, caminho_wav: str):
        """
        Tenta identificar quem falou pelo áudio.
        Chamado pelo audio_handler após cada transcrição.
        """
        if self._voz_id and self._voz_id.disponivel:
            threading.Thread(
                target=self._voz_id.identificar,
                args=(caminho_wav,),
                daemon=True
            ).start()

    # -----------------------------------------------------------------------
    # Processamento de comandos unificado
    # -----------------------------------------------------------------------

    # Triggers que este módulo trata (conta + perfil + voz)
    _TRIGGERS_SESSAO = {
        # Conta
        "trocar de conta", "mudar de conta", "sou eu o", "sou eu a",
        "entrar como", "logar como", "listar contas", "lista contas",
        "quem esta usando", "qual conta", "cria conta", "criar conta",
        "nova conta", "define meu pin", "meu pin e", "remove meu pin",
        "conta convidado", "entrar como convidado",
        # Voz
        "registra minha voz", "cadastra minha voz", "grava minha voz",
        "apaga minha voz", "reconhecimento de voz", "status da voz",
        "minha voz esta cadastrada",
        # Perfil
        "meu nome e", "meu nome é", "me chama de", "minha cidade e",
        "minha cidade é", "meu navegador e", "meu editor e",
        "prefiro respostas", "qual e meu perfil", "qual é meu perfil",
        "meus temas favoritos", "adiciona tema",
    }

    def e_comando_sessao(self, texto: str) -> bool:
        t = _norm(texto)
        return any(trigger in t for trigger in self._TRIGGERS_SESSAO)

    def processar(self, comando: str) -> str | None:
        """
        Ponto único de entrada para comandos de conta, perfil e voz.
        """
        t = _norm(comando)

        # --- Comandos de conta ---
        if self._contas and self._contas.e_comando_conta(comando):
            resposta = self._contas.processar_comando(comando)
            # Após criar conta, ao_criar_conta já foi chamado via callback
            return resposta

        # --- Comandos de voz ---
        if self._voz_id and self._voz_id.e_comando_voz(comando):
            return self._voz_id.processar_comando(
                comando,
                conta_ativa   = self._conta_ativa,
                callback_falar = self._callback_falar
            )

        # --- Comandos de perfil ---
        if self._perfil and self._perfil.e_comando_perfil(comando):
            return self._perfil.processar_comando(comando)

        return None

    # -----------------------------------------------------------------------
    # Utilitários internos
    # -----------------------------------------------------------------------

    def _falar(self, texto: str):
        if self._callback_falar:
            try:
                self._callback_falar(texto)
            except Exception:
                pass
        if self._callback_log:
            try:
                self._callback_log(texto)
            except Exception:
                pass

    def registrar_callbacks(self, callback_falar: Callable = None,
                             callback_log: Callable = None):
        """Injeta callbacks após a inicialização (chamado pela interface)."""
        if callback_falar:
            self._callback_falar = callback_falar
        if callback_log:
            self._callback_log = callback_log

    def status(self) -> dict:
        conta  = self._conta_ativa
        return {
            "conta_ativa":    conta.nome if conta else "nenhuma",
            "nome_usuario":   self.nome_usuario,
            "contas_total":   self._contas.status()["total_contas"] if self._contas else 0,
            "voz_id_ativo":   bool(self._voz_id and self._voz_id.disponivel),
            "perfil_carregado": bool(self._perfil),
            "memoria_ativa":  bool(self._memoria),
        }

    def imprimir_status(self):
        s = self.status()
        print("\n[SESSAO STATUS]")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print()