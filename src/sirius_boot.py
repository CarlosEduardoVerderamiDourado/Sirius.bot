"""
sirius_boot.py — S.I.R.I.U.S. v5.2 — Orquestrador de Boot
===========================================================
Sobe o ecossistema completo na ordem correta, resolvendo os
3 pontos críticos identificados na análise de integração:

  Ponto 1 — Schema:       roda sirius_migrator antes de qualquer módulo
  Ponto 2 — Fluxo daemon: API em processo separado + autodidata em background
  Ponto 3 — Dependências: verifica (e opcionalmente instala) antes de subir

Modos de execução:
    python sirius_boot.py                  # tudo: API + autodidata
    python sirius_boot.py --so-api         # só a API FastAPI
    python sirius_boot.py --so-autodidata  # só o motor de aprendizado
    python sirius_boot.py --batch 20       # processa 20 temas e sai
    python sirius_boot.py --fix-deps       # instala dependências faltando

Ordem garantida de boot:
  1. sirius_check     — verifica/instala dependências
  2. sirius_migrator  — migra o schema dos bancos existentes
  3. SiriusMemoria    — inicializa conexões e cria tabelas novas
  4. SiriusCerebro    — carrega modelos (RAG, gerador, controle)
  5. SiriusAutodidata — sobe motor de aprendizado em thread daemon
  6. uvicorn          — sobe API em thread separada (não bloqueia)
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

# =============================================================================
# Cores ANSI (sem dependência externa)
# =============================================================================

_C = {
    "ok":   "\033[92m",
    "warn": "\033[93m",
    "err":  "\033[91m",
    "info": "\033[96m",
    "bold": "\033[1m",
    "dim":  "\033[2m",
    "rst":  "\033[0m",
}

def _ok(msg):     print(f"{_C['ok']}  ✓ {msg}{_C['rst']}")
def _warn(msg):   print(f"{_C['warn']}  ⚠ {msg}{_C['rst']}")
def _err(msg):    print(f"{_C['err']}  ✗ {msg}{_C['rst']}")
def _info(msg):   print(f"{_C['info']}  → {msg}{_C['rst']}")
def _secao(msg):  print(f"\n{_C['bold']}{_C['info']}[ {msg} ]{_C['rst']}")
def _sep():       print(f"{_C['dim']}  {'─'*56}{_C['rst']}")

_T0 = time.time()
def _elapsed() -> str:
    return f"{time.time() - _T0:.1f}s"


# =============================================================================
# Garante que src/ está no path
# =============================================================================

_DIR_SRC = os.path.dirname(os.path.abspath(__file__))
if _DIR_SRC not in sys.path:
    sys.path.insert(0, _DIR_SRC)


# =============================================================================
# Etapa 1 — Verificação de dependências
# =============================================================================

def _etapa_dependencias(auto_fix: bool) -> bool:
    _secao("Etapa 1/5 — Dependências")
    try:
        from sirius_check import verificar_dependencias
        return verificar_dependencias(auto_fix=auto_fix, quiet=True)
    except ImportError:
        _warn("sirius_check.py não encontrado — pulando verificação.")
        return True   # não bloqueia o boot


# =============================================================================
# Etapa 2 — Migração de schema
# =============================================================================

def _etapa_migracao() -> bool:
    _secao("Etapa 2/5 — Migração de Schema")
    try:
        from sirius_migrator import migrar_tudo
        ok = migrar_tudo(verbose=True)
        if ok:
            _ok(f"Schema sincronizado. ({_elapsed()})")
        else:
            _err("Migração reportou erros — verifique os logs acima.")
        return ok
    except ImportError:
        _warn("sirius_migrator.py não encontrado — pulando migração.")
        _warn("RISCO: a tabela estudos_autonomos pode não ter a coluna criado_em.")
        return True
    except Exception as e:
        _err(f"Erro inesperado na migração: {e}")
        return False


# =============================================================================
# Etapa 3 — SiriusMemoria
# =============================================================================

def _etapa_memoria():
    _secao("Etapa 3/5 — SiriusMemoria")
    try:
        from memoria import SiriusMemoria
        mem = SiriusMemoria()
        _ok(f"SiriusMemoria inicializada. ({_elapsed()})")
        return mem
    except Exception as e:
        _err(f"Falha ao inicializar SiriusMemoria: {e}")
        return None


# =============================================================================
# Etapa 4 — SiriusCerebro
# =============================================================================

def _etapa_cerebro(mem):
    _secao("Etapa 4/5 — SiriusCerebro")
    try:
        from cerebro import SiriusCerebro
        cerebro = SiriusCerebro()
        # Injeta a instância de memória já criada (evita dupla inicialização)
        if mem and hasattr(cerebro, "memoria"):
            cerebro.memoria = mem
        _ok(f"SiriusCerebro pronto. ({_elapsed()})")
        _info(f"  controle : {'✓' if cerebro.controle else '✗'}")
        _info(f"  rag      : {'✓' if cerebro._rag else '✗'}")
        _info(f"  gerador  : {'✓' if cerebro._gerador else '✗'}")
        return cerebro
    except Exception as e:
        _err(f"Falha ao inicializar SiriusCerebro: {e}")
        return None


# =============================================================================
# Etapa 5A — SiriusAutodidata (daemon thread)
# =============================================================================

# Importação defensiva — boot não falha se o arquivo não existir
try:
    from sirius_autodidata import SiriusAutodidata as _SiriusAutodidata
    import sirius_autodidata as _mod_autodidata
    _AUTODIDATA_DISPONIVEL = True
except ImportError:
    _SiriusAutodidata      = None   # type: ignore[assignment,misc]
    _mod_autodidata        = None   # type: ignore[assignment]
    _AUTODIDATA_DISPONIVEL = False


def _etapa_autodidata(mem, cerebro, batch: int = 0):
    _secao("Etapa 5A/5 — SiriusAutodidata")

    if not _AUTODIDATA_DISPONIVEL:
        _warn("sirius_autodidata.py não encontrado — modo de aprendizado desabilitado.")
        _warn("O restante do sistema (API, cérebro) funcionará normalmente.")
        return None

    try:
        bot = _SiriusAutodidata(memoria=mem, cerebro=cerebro)

        if batch > 0:
            _info(f"Modo batch: processando {batch} temas e encerrando.")
            resultado = bot.processar_batch_agora(n=batch)
            _ok(f"Batch concluído: {resultado}")
            return bot

        total_temas = len(getattr(_mod_autodidata, "TODOS_OS_TEMAS", []))
        bot.iniciar()   # thread daemon — não bloqueia
        _ok(f"Autodidata ativo em background ({total_temas} temas). ({_elapsed()})")
        return bot

    except Exception as e:
        _err(f"Falha ao inicializar SiriusAutodidata: {e}")
        _warn("Sistema continuará sem aprendizado autônomo.")
        return None


# =============================================================================
# Etapa 5B — API FastAPI (thread separada)
# =============================================================================

_api_thread: threading.Thread | None = None
_api_rodando = threading.Event()


def _subir_api(host: str = "0.0.0.0", porta: int = 8000):
    """Roda uvicorn em thread — não bloqueia o processo principal."""
    global _api_thread

    def _run():
        try:
            import uvicorn
            _api_rodando.set()
            uvicorn.run(
                "sirius_api:app",
                host=host,
                port=porta,
                reload=False,     # reload=True incompatível com threading
                log_level="warning",
            )
        except Exception as e:
            _err(f"[API] Uvicorn encerrou: {e}")
            _api_rodando.clear()

    _api_thread = threading.Thread(target=_run, daemon=True, name="SiriusAPI")
    _api_thread.start()

    # Aguarda até 5s para confirmar que subiu
    for _ in range(50):
        if _api_rodando.is_set():
            break
        time.sleep(0.1)


def _etapa_api(host: str, porta: int):
    _secao("Etapa 5B/5 — API FastAPI")

    # Valida que sirius_api.py existe antes de tentar subir
    api_path = os.path.join(_DIR_SRC, "sirius_api.py")
    if not os.path.isfile(api_path):
        _err(f"sirius_api.py não encontrado em {_DIR_SRC}")
        _warn("API desabilitada — coloque sirius_api.py na pasta src/")
        return False

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        _err("uvicorn não instalado — rode: pip install uvicorn[standard]")
        return False

    try:
        _subir_api(host=host, porta=porta)

        # Timeout real: aguarda até 8s para o servidor responder no socket
        import socket
        subiu = False
        for _ in range(80):                   # 80 × 0.1s = 8s máximo
            time.sleep(0.1)
            try:
                with socket.create_connection((host if host != "0.0.0.0" else "127.0.0.1", porta), timeout=0.2):
                    subiu = True
                    break
            except OSError:
                continue

        if subiu:
            _ok(f"API respondendo em http://{host}:{porta}  ({_elapsed()})")
        else:
            _warn(f"API pode ainda estar subindo — verifique http://localhost:{porta}/healthcheck")

        _info(f"  Docs  : http://localhost:{porta}/docs")
        _info(f"  Rotas : POST /chat | POST /auth/login | GET /status")
        return True

    except Exception as e:
        _err(f"Falha ao subir API: {e}")
        return False


# =============================================================================
# Monitor de status (loop principal)
# =============================================================================

def _loop_monitor(autodidata, intervalo: int = 300):
    """
    Imprime status periódico do autodidata enquanto o boot roda em daemon.
    Encerra quando o processo recebe Ctrl+C.
    """
    print(f"\n{_C['bold']}{'='*60}")
    print("  S.I.R.I.U.S. v5.2 — Sistema operacional")
    print(f"{'='*60}{_C['rst']}")
    _info("Ctrl+C para encerrar graciosamente.")
    print()

    try:
        ciclo = 0
        while True:
            time.sleep(intervalo)
            ciclo += 1
            if autodidata:
                s = autodidata.status()
                print(
                    f"{_C['dim']}[{time.strftime('%H:%M:%S')}] "
                    f"Autodidata | ciclos={s['ciclos_completados']} | "
                    f"salvos={s['total_salvos']} | "
                    f"descobertos={s['temas_descobertos']}{_C['rst']}"
                )
    except KeyboardInterrupt:
        pass


# =============================================================================
# Encerramento gracioso
# =============================================================================

def _shutdown(autodidata, cerebro):
    print(f"\n{_C['warn']}  Encerrando S.I.R.I.U.S...{_C['rst']}")
    if autodidata:
        try:
            autodidata.parar()
            _ok("Autodidata parado.")
        except Exception:
            pass
    if cerebro:
        try:
            cerebro.parar()
            _ok("Cérebro encerrado.")
        except Exception:
            pass
    _ok("Bye, Carlos.")


# =============================================================================
# Entry point
# =============================================================================

def _smoke_test(cerebro, mem, autodidata) -> bool:
    """
    Valida os componentes críticos após o boot completo.
    Retorna True se tudo OK, False se algum componente falhou.
    """
    _secao("Smoke Test")
    testes = {
        "SiriusMemoria":   mem      is not None,
        "SiriusCerebro":   cerebro  is not None,
        "controle_pc":     cerebro  is not None and bool(getattr(cerebro, "controle",  None)),
        "rag":             cerebro  is not None and bool(getattr(cerebro, "_rag",      None)),
        "gerador":         cerebro  is not None and bool(getattr(cerebro, "_gerador",  None)),
        "autodidata":      autodidata is not None,
    }

    falhas = [k for k, v in testes.items() if not v]

    for nome, ok in testes.items():
        if ok:
            _ok(f"{nome}")
        else:
            _warn(f"{nome}  ← não disponível")

    if falhas:
        _warn(f"Componentes opcionais ausentes: {', '.join(falhas)}")
        _warn("Sistema funciona em modo degradado.")
    else:
        _ok("Todos os componentes operacionais.")

    # Teste funcional mínimo: gera uma resposta simples
    if cerebro:
        try:
            r = cerebro.processar("ping")
            if r:
                _ok(f'Teste funcional: cerebro.processar("ping") → OK')
            else:
                _warn('Teste funcional: cerebro.processar retornou vazio')
        except Exception as e:
            _warn(f"Teste funcional falhou: {e}")

    return len(falhas) == 0


def main():
    parser = argparse.ArgumentParser(
        description="S.I.R.I.U.S. v5.2 — Boot do ecossistema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python sirius_boot.py                   # API + autodidata (modo daemon)
  python sirius_boot.py --so-api          # só a API
  python sirius_boot.py --so-autodidata   # só o autodidata
  python sirius_boot.py --batch 20        # processa 20 temas e sai
  python sirius_boot.py --fix-deps        # instala dependências e sobe
  python sirius_boot.py --version         # exibe versão e sai
        """,
    )
    parser.add_argument("--so-api",        action="store_true", help="Sobe só a API FastAPI")
    parser.add_argument("--so-autodidata", action="store_true", help="Sobe só o autodidata")
    parser.add_argument("--batch",         type=int, default=0, metavar="N",
                        help="Processa N temas imediatamente e encerra")
    parser.add_argument("--fix-deps",      action="store_true", help="Instala dependências faltando")
    parser.add_argument("--host",          default="0.0.0.0",   help="Host da API (padrão: 0.0.0.0)")
    parser.add_argument("--porta",         type=int, default=8000, help="Porta da API (padrão: 8000)")
    parser.add_argument("--sem-migracao",  action="store_true", help="Pula a migração de schema")
    parser.add_argument("--sem-smoke",     action="store_true", help="Pula o smoke test pós-boot")
    parser.add_argument("--version",       action="version",    version="S.I.R.I.U.S. v5.2.0")
    args = parser.parse_args()

    # ── Banner ────────────────────────────────────────────────────────────────
    print(f"\n{_C['bold']}{_C['info']}")
    print("  ╔═══════════════════════════════════════════════╗")
    print("  ║      S.I.R.I.U.S. v5.2 — Boot Sequence       ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print(f"{_C['rst']}")

    subir_api        = not args.so_autodidata
    subir_autodidata = not args.so_api

    # ── Etapa 1: Dependências ─────────────────────────────────────────────────
    deps_ok = _etapa_dependencias(auto_fix=args.fix_deps)
    if not deps_ok:
        _warn("Dependências obrigatórias ausentes. Rode com --fix-deps ou instale manualmente.")
        _warn("Continuando mesmo assim — alguns módulos podem falhar.")

    # ── Etapa 2: Migração ─────────────────────────────────────────────────────
    if not args.sem_migracao:
        migr_ok = _etapa_migracao()
        if not migr_ok:
            _err("Migração falhou. Interrompendo boot.")
            sys.exit(1)
    else:
        _warn("Migração pulada (--sem-migracao). Risco de erro de schema.")

    # ── Etapa 3: Memória ──────────────────────────────────────────────────────
    mem = _etapa_memoria()

    # ── Etapa 4: Cérebro ──────────────────────────────────────────────────────
    cerebro = _etapa_cerebro(mem)

    # ── Etapa 5A: Autodidata ──────────────────────────────────────────────────
    autodidata = None
    if subir_autodidata:
        autodidata = _etapa_autodidata(mem, cerebro, batch=args.batch)
        if args.batch > 0:
            _ok("Batch concluído. Encerrando.")
            sys.exit(0)

    # ── Etapa 5B: API ─────────────────────────────────────────────────────────
    if subir_api:
        _etapa_api(host=args.host, porta=args.porta)

    # ── Smoke test ────────────────────────────────────────────────────────────
    if not args.sem_smoke:
        _smoke_test(cerebro, mem, autodidata)

    _sep()

    # ── Registra handlers de sinal ────────────────────────────────────────────
    def _handler_sinal(sig, frame):
        _shutdown(autodidata, cerebro)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handler_sinal)
    signal.signal(signal.SIGTERM, _handler_sinal)

    # ── Loop principal ────────────────────────────────────────────────────────
    _loop_monitor(autodidata, intervalo=300)
    _shutdown(autodidata, cerebro)


if __name__ == "__main__":
    main()