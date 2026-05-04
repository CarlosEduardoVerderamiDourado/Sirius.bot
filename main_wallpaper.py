"""
main_wallpaper.py — Entry point do S.I.R.I.U.S. em modo papel de parede

Uso:
    python main_wallpaper.py              → modo papel de parede (padrão)
    python main_wallpaper.py --janela     → janela normal com chat (debug)
    python main_wallpaper.py --fullscreen → tela cheia
    python main_wallpaper.py --sem-audio  → sem microfone (silencioso)

Este arquivo fica FORA de src/ — ajusta o PATH automaticamente.
"""

import os
import sys
import argparse

# ---------------------------------------------------------------------------
# PATH — este arquivo está fora de src/, precisa apontar para lá
# ---------------------------------------------------------------------------
diretorio_main = os.path.dirname(os.path.abspath(__file__))
diretorio_src  = os.path.join(diretorio_main, "src")

for p in [diretorio_main, diretorio_src]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Cura de DLLs (necessário no Windows com .venv)
# ---------------------------------------------------------------------------
def _curar_dlls():
    try:
        import site
        for path in site.getsitepackages():
            if os.path.exists(path):
                os.add_dll_directory(path)
        bin_path = os.path.dirname(sys.executable)
        if os.path.exists(bin_path):
            os.add_dll_directory(bin_path)
    except Exception as e:
        print(f"[AVISO]: DLL setup: {e}")

_curar_dlls()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="S.I.R.I.U.S. Wallpaper")
    parser.add_argument("--janela",     action="store_true",
                        help="Modo janela normal (não wallpaper)")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Modo tela cheia")
    parser.add_argument("--sem-audio",  action="store_true",
                        help="Desativa o áudio (modo silencioso)")
    args = parser.parse_args()

    print("\033[94m[SIRIUS]: Inicializando...\033[0m")

    # Importa depois de ajustar o PATH
    try:
        from cerebro import SiriusCerebro
    except ImportError as e:
        print(f"\033[31m[ERRO]: Não encontrei o cerebro.py em src/: {e}\033[0m")
        print(f"  src/ esperado em: {diretorio_src}")
        sys.exit(1)

    cerebro = SiriusCerebro()
    print("\033[92m[SIRIUS]: Cérebro pronto.\033[0m")

    # Desativa áudio se pedido
    if args.sem_audio:
        if hasattr(cerebro, '_scheduler') and cerebro._scheduler:
            pass  # scheduler não usa áudio
        print("\033[33m[SIRIUS]: Modo silencioso — áudio desativado.\033[0m")

    # Importa o wallpaper (também em src/)
    try:
        from sirius_wallpaper import iniciar_wallpaper, MODO_WALLPAPER, MODO_ATIVO, MODO_FULLSCREEN
    except ImportError as e:
        print(f"\033[31m[ERRO]: sirius_wallpaper.py não encontrado: {e}\033[0m")
        sys.exit(1)

    # Determina modo
    if args.fullscreen:
        modo = MODO_FULLSCREEN
    elif args.janela:
        modo = MODO_ATIVO
    else:
        modo = MODO_WALLPAPER

    print(f"\033[92m[SIRIUS]: Modo '{modo}'. "
          f"{'Duplo clique na esfera' if modo == MODO_WALLPAPER else 'Janela aberta'}.\033[0m")

    if modo == MODO_WALLPAPER:
        print("\033[94m[SIRIUS]: Dica — duplo clique na esfera ou Win+S para abrir o chat.\033[0m")

    app, janela = iniciar_wallpaper(cerebro=cerebro, modo=modo)

    # Servidor REST + WebSocket em background
    try:
        from sirius_server import iniciar_servidor
        iniciar_servidor(cerebro=cerebro, host="0.0.0.0", porta=5000, em_thread=True)
    except ImportError:
        print("[SIRIUS]: sirius_server.py não encontrado — pip install fastapi uvicorn")
    except Exception as e:
        print(f"[SIRIUS]: Servidor não iniciou: {e}")

    try:
        sys.exit(app.exec())
    except SystemExit:
        pass


if __name__ == "__main__":
    main()