"""
S.I.R.I.U.S. v5.2 — BUILD EXECUTÁVEL WINDOWS (.exe) - VERSÃO CORRIGIDA
"""

import os
import sys
import subprocess
import shutil

NOME_APP = "SIRIUS"
VERSAO = "5.2"
ARQUIVO_PRINCIPAL = "src/main_residente.py"
DIRETORIO_OUTPUT = "dist"
DIRETORIO_BUILD = "build"

def print_header(titulo):
    print("\n" + "="*80)
    print(f"  {titulo}")
    print("="*80 + "\n")

def verificar_python():
    print_header("1️⃣  VERIFICANDO PYTHON")
    version = sys.version_info
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    if version.major < 3 or version.minor < 8:
        print("❌ Python 3.8+ requerido!")
        sys.exit(1)
    return True

def verificar_pyinstaller():
    print_header("2️⃣  VERIFICANDO PYINSTALLER")
    try:
        resultado = subprocess.run(
            ["pyinstaller", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if resultado.returncode == 0:
            versao = resultado.stdout.strip()
            print(f"✅ PyInstaller {versao}")
            return True
        else:
            print("❌ PyInstaller não encontrado!")
            return False
    except FileNotFoundError:
        print("❌ PyInstaller não está no PATH!")
        return False
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def verificar_arquivos():
    print_header("3️⃣  VERIFICANDO ARQUIVOS")
    arquivos_requeridos = [
        ARQUIVO_PRINCIPAL,
        "src/sirius_logging.py",
        "src/validador_resposta.py",
        "src/retry_manager.py",
    ]
    todos_ok = True
    for arquivo in arquivos_requeridos:
        if os.path.exists(arquivo):
            print(f"✅ {arquivo}")
        else:
            print(f"❌ {arquivo} NÃO ENCONTRADO!")
            todos_ok = False
    return todos_ok

def limpar_builds_antigos():
    print_header("4️⃣  LIMPANDO BUILDS ANTIGOS")
    for diretorio in [DIRETORIO_BUILD, DIRETORIO_OUTPUT]:
        if os.path.exists(diretorio):
            try:
                shutil.rmtree(diretorio)
                print(f"✅ Removido: {diretorio}/")
            except Exception as e:
                print(f"⚠️  Erro: {e}")

def gerar_exe():
    print_header("5️⃣  GERANDO EXECUTÁVEL")
    print("⏳ Isso pode levar 30-60 minutos...\n")
    
    cmd = [
        "pyinstaller",
        "--name=" + NOME_APP,
        "--onefile",
        "--windowed",
        "--add-data=src:src",
        "--add-data=config:config",
        "--add-data=logs:logs",
        "--hidden-import=neuronio",
        "--hidden-import=memoria",
        "--hidden-import=cerebro",
        "--hidden-import=sirius_logging",
        "--hidden-import=validador_resposta",
        "--hidden-import=retry_manager",
        "--hidden-import=torch",
        "--hidden-import=fastapi",
        "--hidden-import=pydantic",
        "--distpath=" + DIRETORIO_OUTPUT,
        "--buildpath=" + DIRETORIO_BUILD,
        ARQUIVO_PRINCIPAL,
    ]
    
    print(f"Executando PyInstaller...\n")
    
    try:
        resultado = subprocess.run(cmd, capture_output=False, text=True)
        if resultado.returncode == 0:
            print("\n✅ Executável gerado com sucesso!")
            return True
        else:
            print("\n❌ Erro ao gerar executável")
            return False
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        return False

def verificar_exe():
    print_header("6️⃣  VERIFICANDO EXECUTÁVEL")
    exe_path = os.path.join(DIRETORIO_OUTPUT, f"{NOME_APP}.exe")
    
    if os.path.exists(exe_path):
        try:
            tamanho_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"✅ {NOME_APP}.exe criado!")
            print(f"   Localização: {exe_path}")
            print(f"   Tamanho: {tamanho_mb:.1f} MB")
            return True
        except Exception as e:
            print(f"⚠️  Erro: {e}")
            return True
    else:
        print(f"❌ {NOME_APP}.exe NÃO ENCONTRADO!")
        return False

def gerar_relatorio_final():
    print_header("7️⃣  RELATÓRIO FINAL")
    exe_path = os.path.join(DIRETORIO_OUTPUT, f"{NOME_APP}.exe")
    
    if os.path.exists(exe_path):
        try:
            tamanho = os.path.getsize(exe_path) / (1024 * 1024)
        except:
            tamanho = "desconhecido"
        
        print(f"""
🎉 BUILD CONCLUÍDO COM SUCESSO!

📊 Informações do Executável:
   Nome: {NOME_APP}.exe
   Versão: {VERSAO}
   Localização: {exe_path}
   Tamanho: {tamanho} MB
   Tipo: GUI (sem console)

🚀 Como usar:
   1. Clicar duplo em {exe_path}
   2. Ou: .\\{exe_path}
   3. Sistema iniciará automaticamente

✅ Sistema pronto para produção!
        """)
        return True
    else:
        print("❌ BUILD FALHOU!")
        return False

def main():
    print("""
    
╔════════════════════════════════════════════════════════════════════╗
║   S.I.R.I.U.S. v5.2 — BUILDER DE EXECUTÁVEL WINDOWS (.exe)        ║
║   VERSÃO CORRIGIDA                                                 ║
╚════════════════════════════════════════════════════════════════════╝
    """)
    
    if not verificar_python():
        sys.exit(1)
    
    if not verificar_pyinstaller():
        print("\n❌ PyInstaller não está instalado.")
        sys.exit(1)
    
    if not verificar_arquivos():
        print("\n❌ Arquivos necessários não encontrados.")
        sys.exit(1)
    
    limpar_builds_antigos()
    
    if not gerar_exe():
        print("\n❌ Falha ao gerar executável.")
        sys.exit(1)
    
    if not verificar_exe():
        print("\n❌ Executável não foi criado corretamente.")
        sys.exit(1)
    
    gerar_relatorio_final()
    print("\n✅ Build completo!\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Build cancelado pelo usuário.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Erro inesperado: {e}")
        sys.exit(1)
