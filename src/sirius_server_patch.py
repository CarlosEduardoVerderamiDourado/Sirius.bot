"""
sirius_server_patch.py — Status: ✅ JÁ APLICADO
================================================
Este patch foi criado para modificar o sirius_server.py original,
mas as modificações JÁ FORAM APLICADAS no arquivo atual.

VERIFICAÇÃO:
  ✓ Função _html_interface() já serve templates/index.html externo
  ✓ Rota /static já está configurada (linha 1130)
  ✓ Fallback para HTML embutido já existe

VOCÊ NÃO PRECISA EXECUTAR ESTE ARQUIVO.

Se por algum motivo você precisar re-aplicar o patch (ex: arquivo
foi revertido), execute:
    python sirius_server_patch.py

Caso contrário, apenas:
  1. Crie a pasta src/templates/
  2. Coloque o index.html dentro dela
  3. Execute: python sirius_server.py
  4. Acesse: http://SEU_IP:5000

---

CÓDIGO DO PATCH (para referência ou re-aplicação)
"""

import os
import sys
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Verificador — checa se o patch já foi aplicado
# ---------------------------------------------------------------------------

def verificar_patch_aplicado(server_path: str) -> dict:
    """
    Verifica se as modificações do patch já estão no sirius_server.py.
    
    Retorna dict com status de cada modificação.
    """
    if not os.path.exists(server_path):
        return {
            "arquivo_existe": False,
            "html_interface_ok": False,
            "static_route_ok": False,
            "status": "❌ ARQUIVO NÃO ENCONTRADO"
        }
    
    with open(server_path, "r", encoding="utf-8") as f:
        conteudo = f.read()
    
    # Verifica _html_interface com suporte a templates
    html_ok = (
        'os.path.join(diretorio_src, "templates", "index.html")' in conteudo
        and "for caminho in candidatos:" in conteudo
    )
    
    # Verifica rota /static
    static_ok = (
        "StaticFiles" in conteudo
        and 'app.mount("/static"' in conteudo
    )
    
    if html_ok and static_ok:
        status = "✅ PATCH JÁ APLICADO"
    elif html_ok or static_ok:
        status = "🟡 PARCIALMENTE APLICADO"
    else:
        status = "❌ PATCH NÃO APLICADO"
    
    return {
        "arquivo_existe": True,
        "html_interface_ok": html_ok,
        "static_route_ok": static_ok,
        "status": status
    }


# ---------------------------------------------------------------------------
# Novo conteúdo que substitui _html_interface() no sirius_server.py
# ---------------------------------------------------------------------------

_NOVA_FUNCAO = '''
def _html_interface(ip: str) -> str:
    """
    Serve o index.html externo (templates/index.html).
    Fallback para HTML embutido mínimo se o arquivo não for encontrado.
    """
    # Procura em:  src/templates/index.html  ou  src/index.html
    candidatos = [
        os.path.join(diretorio_src, "templates", "index.html"),
        os.path.join(diretorio_src, "index.html"),
        os.path.join(diretorio_raiz, "templates", "index.html"),
        os.path.join(diretorio_raiz, "index.html"),
    ]
    for caminho in candidatos:
        if os.path.isfile(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                return f.read()

    # Fallback mínimo se o arquivo não existir
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>S.I.R.I.U.S.</title>
<style>
  body {{ background:#000a14; color:#5de2ff; font-family:monospace;
          display:flex; align-items:center; justify-content:center;
          height:100vh; flex-direction:column; gap:16px; }}
  h1 {{ letter-spacing:6px; font-size:20px; }}
  p  {{ opacity:.5; font-size:12px; }}
  a  {{ color:#00ff88; }}
</style>
</head>
<body>
  <h1>⬛ S.I.R.I.U.S.</h1>
  <p>Arquivo <code>templates/index.html</code> não encontrado.</p>
  <p>IP local: <a href="http://{ip}:5000">http://{ip}:5000</a></p>
</body>
</html>"""
'''

_NOVA_ROTA_STATIC = '''
        # ── GET /static/<path> — serve arquivos estáticos (CSS, JS, imgs) ──
        from fastapi.staticfiles import StaticFiles as _StaticFiles
        _static_dir = os.path.join(diretorio_src, "static")
        if os.path.isdir(_static_dir):
            app.mount("/static", _StaticFiles(directory=_static_dir), name="static")
            print(f"[SERVIDOR]: /static → {_static_dir}")
'''


# ---------------------------------------------------------------------------
# Patcher automático (só aplica se necessário)
# ---------------------------------------------------------------------------

def _patch_server(server_path: str, forcar: bool = False) -> bool:
    """
    Substitui _html_interface() e adiciona /static no sirius_server.py.
    Retorna True se bem-sucedido.
    
    Se forcar=False, verifica antes se já foi aplicado.
    """
    # Verifica status atual
    status = verificar_patch_aplicado(server_path)
    
    if not forcar:
        if status["html_interface_ok"] and status["static_route_ok"]:
            print(f"\n{status['status']}")
            print("Não é necessário aplicar o patch novamente.")
            print("\nPróximos passos:")
            print("  1. Crie src/templates/index.html")
            print("  2. Execute: python sirius_server.py")
            print("  3. Acesse: http://SEU_IP:5000")
            return True
    
    with open(server_path, "r", encoding="utf-8") as f:
        src = f.read()

    modificado = False

    # 1. Substitui _html_interface() se necessário
    if not status["html_interface_ok"] or forcar:
        pattern = r'(def _html_interface\(ip: str\) -> str:.*?)(?=\n\n# =|\n\ndef |\nclass |\Z)'
        match = re.search(pattern, src, re.DOTALL)
        if not match:
            print("[PATCH] Função _html_interface() não encontrada. Adicionando ao final...")
            src += "\n\n" + _NOVA_FUNCAO
        else:
            src = src[:match.start()] + _NOVA_FUNCAO.strip() + src[match.end():]
            print("[PATCH] _html_interface() substituída com sucesso.")
        modificado = True

    # 2. Adiciona rota /static se necessário
    if not status["static_route_ok"] or forcar:
        if "/static" not in src or "StaticFiles" not in src:
            # Procura por @app.get("/ip") ou outro ponto de inserção
            pontos_insercao = [
                '@app.get("/ip")',
                '@app.get("/status")',
                'def _registrar_rotas'
            ]
            inserido = False
            for ponto in pontos_insercao:
                idx = src.find(ponto)
                if idx != -1:
                    src = src[:idx] + _NOVA_ROTA_STATIC.strip() + "\n\n        " + src[idx:]
                    print("[PATCH] Rota /static adicionada.")
                    modificado = True
                    inserido = True
                    break
            
            if not inserido:
                print("[PATCH] ⚠️ Não encontrei local para inserir /static. Adicione manualmente.")

    if not modificado:
        print("\n✅ Todas as modificações já estão aplicadas.")
        return True

    # Backup
    backup = server_path + ".bak"
    if os.path.exists(backup):
        # Se já existe backup, cria com timestamp
        import time
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup = f"{server_path}.bak.{timestamp}"
    
    shutil.copy2(server_path, backup)
    print(f"[PATCH] Backup salvo em {backup}")

    with open(server_path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"[PATCH] {server_path} atualizado.")
    return True


def _copiar_index(server_path: str) -> bool:
    """Copia o index.html para a pasta templates/ ao lado do server."""
    server_dir   = os.path.dirname(os.path.abspath(server_path))
    templates    = os.path.join(server_dir, "templates")
    os.makedirs(templates, exist_ok=True)

    # Tenta encontrar o index.html gerado
    candidatos = [
        os.path.join(server_dir, "index.html"),
        os.path.join(os.path.dirname(server_dir), "index.html"),
        os.path.join(os.getcwd(), "index.html"),
    ]
    for src_file in candidatos:
        if os.path.isfile(src_file):
            dst = os.path.join(templates, "index.html")
            shutil.copy2(src_file, dst)
            print(f"[PATCH] index.html copiado para {dst}")
            return True

    print("[PATCH] index.html não encontrado automaticamente.")
    print(f"        Copie manualmente para: {templates}/index.html")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Patch para sirius_server.py (suporte a templates externos)"
    )
    parser.add_argument(
        "arquivo",
        nargs="?",
        default="sirius_server.py",
        help="Caminho do sirius_server.py"
    )
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="Apenas verifica se o patch já foi aplicado"
    )
    parser.add_argument(
        "--forcar",
        action="store_true",
        help="Aplica o patch mesmo se já tiver sido aplicado"
    )
    args = parser.parse_args()

    # Localiza sirius_server.py
    server = args.arquivo
    if not os.path.isfile(server):
        # Tenta encontrar no diretório atual e no src/
        for candidate in [
            os.path.join(os.getcwd(), server),
            os.path.join(os.getcwd(), "src", server),
        ]:
            if os.path.isfile(candidate):
                server = candidate
                break
        else:
            print(f"\n❌ Arquivo '{server}' não encontrado.\n")
            sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  Verificando: {server}")
    print(f"{'='*70}\n")
    
    # Verifica status
    status = verificar_patch_aplicado(server)
    
    print(f"Status Geral:       {status['status']}")
    print(f"_html_interface():  {'✅' if status['html_interface_ok'] else '❌'}")
    print(f"Rota /static:       {'✅' if status['static_route_ok'] else '❌'}")
    
    if args.verificar:
        print("\n✅ Verificação concluída.")
        sys.exit(0)
    
    # Aplica patch se necessário
    print()
    ok = _patch_server(server, forcar=args.forcar)
    
    if ok:
        print("\n✅ Patch aplicado/verificado com sucesso!")
        print("\nPróximos passos:")
        print("  1. Certifique-se que templates/index.html existe")
        print("  2. Reinicie o servidor:  python sirius_server.py")
        print("  3. Acesse:  http://SEU_IP:5000\n")
    else:
        print("\n❌ Patch falhou. Verifique os erros acima.\n")
        sys.exit(1)