"""
SiriusTreinador — Pipeline de aprendizado contínuo
Treina todas as redes do Sirius juntas de forma coordenada.
Pode ser chamado manualmente ou rodar em background.
"""

import os
import sys
import time
import threading
import sqlite3

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
DB_PESSOAL   = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
DB_TREINO    = os.path.join(CAMINHO_DATA, "sirius_treino.db")


class SiriusTreinador:
    def __init__(self):
        # Importa lazy para não travar a inicialização
        self._neuronio  = None
        self._gerador   = None
        self._embeddings = None
        self._lock      = threading.Lock()
        self._treinando = False

    # -----------------------------------------------------------------------
    # Lazy loading — só carrega o que precisar
    # -----------------------------------------------------------------------

    def _get_neuronio(self):
        if self._neuronio is None:
            from neuronio import SiriusNeuronio
            self._neuronio = SiriusNeuronio()
        return self._neuronio

    def _get_gerador(self):
        if self._gerador is None:
            from sirius_gerador import SiriusGerador
            self._gerador = SiriusGerador()
        return self._gerador

    def _get_embeddings(self):
        if self._embeddings is None:
            from sirius_embeddings import SiriusEmbeddings
            self._embeddings = SiriusEmbeddings()
        return self._embeddings

    # -----------------------------------------------------------------------
    # Contagem de dados disponíveis
    # -----------------------------------------------------------------------

    def _contar_dados(self) -> dict:
        contagem = {"conversas": 0, "conhecimento": 0, "total": 0}
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            contagem["conversas"] = conn.execute(
                "SELECT COUNT(*) FROM conversas"
            ).fetchone()[0]
            conn.close()
        except Exception:
            pass

        try:
            conn = sqlite3.connect(DB_TREINO)
            contagem["conhecimento"] = conn.execute(
                "SELECT COUNT(*) FROM conhecimento_geral"
            ).fetchone()[0]
            conn.close()
        except Exception:
            pass

        contagem["total"] = contagem["conversas"] + contagem["conhecimento"]
        return contagem

    # -----------------------------------------------------------------------
    # Treinos individuais
    # -----------------------------------------------------------------------

    def treinar_embeddings(self, epocas: int = 20):
        print("\033[94m[TREINADOR]: → Treinando SiriusEmbeddings...\033[0m")
        try:
            emb = self._get_embeddings()
            emb.treinar(epocas=epocas)
            print("\033[92m[TREINADOR]: ✓ Embeddings atualizados!\033[0m")
            return True
        except Exception as e:
            print(f"\033[31m[TREINADOR]: ✗ Embeddings falharam: {e}\033[0m")
            return False

    def treinar_classificador(self):
        print("\033[94m[TREINADOR]: → Treinando RedeSirius (classificador)...\033[0m")
        try:
            neuronio = self._get_neuronio()
            neuronio.treinar()
            print("\033[92m[TREINADOR]: ✓ Classificador atualizado!\033[0m")
            return True
        except Exception as e:
            print(f"\033[31m[TREINADOR]: ✗ Classificador falhou: {e}\033[0m")
            return False

    def treinar_gerador(self, epocas: int = 30):
        print("\033[94m[TREINADOR]: → Treinando SiriusGerador (seq2seq)...\033[0m")
        try:
            gerador = self._get_gerador()
            gerador.treinar(epocas=epocas)
            print("\033[92m[TREINADOR]: ✓ Gerador atualizado!\033[0m")
            return True
        except Exception as e:
            print(f"\033[31m[TREINADOR]: ✗ Gerador falhou: {e}\033[0m")
            return False

    # -----------------------------------------------------------------------
    # Treino completo coordenado
    # -----------------------------------------------------------------------

    def treinar_tudo(self, forcar: bool = False):
        """
        Treina todas as redes na ordem correta:
        1. Embeddings (base léxica)
        2. Classificador (usa TF-IDF próprio)
        3. Gerador (usa vocab próprio)
        """
        with self._lock:
            if self._treinando and not forcar:
                print("[TREINADOR]: Já está treinando, aguarde.")
                return
            self._treinando = True

        try:
            contagem = self._contar_dados()
            print(f"\n{'='*50}")
            print(f"\033[93m[TREINADOR]: Iniciando ciclo completo de evolução\033[0m")
            print(f"  Dados disponíveis: {contagem['conversas']} conversas | "
                  f"{contagem['conhecimento']} conhecimentos")
            print(f"{'='*50}\n")

            inicio = time.time()

            # Etapa 1
            ok_emb = self.treinar_embeddings()

            # Etapa 2
            ok_cls = self.treinar_classificador()

            # Etapa 3 — mais pesado, só roda se tiver dados suficientes
            if contagem["total"] >= 10:
                ok_ger = self.treinar_gerador()
            else:
                print("[TREINADOR]: Poucos dados para treinar o gerador. "
                      "Continue usando o Sirius para acumular mais conversas.")
                ok_ger = False

            duracao = time.time() - inicio
            print(f"\n{'='*50}")
            print(f"\033[92m[TREINADOR]: Ciclo concluído em {duracao:.1f}s\033[0m")
            print(f"  Embeddings:   {'✓' if ok_emb else '✗'}")
            print(f"  Classificador: {'✓' if ok_cls else '✗'}")
            print(f"  Gerador:      {'✓' if ok_ger else '✗ (dados insuficientes)'}")
            print(f"{'='*50}\n")

        finally:
            with self._lock:
                self._treinando = False

    # -----------------------------------------------------------------------
    # Ciclo autônomo em background
    # -----------------------------------------------------------------------

    def iniciar_ciclo_autonomo(self, intervalo_horas: float = 2.0):
        """
        Roda em background e retreina automaticamente a cada N horas
        quando há novos dados suficientes.
        """
        def _loop():
            print(f"\033[94m[TREINADOR]: Ciclo autônomo iniciado "
                  f"(intervalo: {intervalo_horas}h)\033[0m")
            while True:
                time.sleep(intervalo_horas * 3600)
                contagem = self._contar_dados()
                if contagem["total"] >= 20:
                    print("\n[TREINADOR]: Novos dados detectados. Evoluindo...")
                    self.treinar_tudo()
                else:
                    print(f"[TREINADOR]: Aguardando mais dados "
                          f"({contagem['total']}/20 mínimo)...")

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        return t

    # -----------------------------------------------------------------------
    # Verificação de saúde dos modelos
    # -----------------------------------------------------------------------

    def status(self) -> dict:
        """Retorna o estado atual de todos os modelos."""
        import os

        def arquivo_existe(path):
            return os.path.exists(path) and os.path.getsize(path) > 0

        GERADOR_PATH  = os.path.join(CAMINHO_DATA, "sirius_gerador.pth")
        MODELO_PATH   = os.path.join(CAMINHO_DATA, "sirius_model.pth")
        VOCAB_PATH    = os.path.join(CAMINHO_DATA, "sirius_vocab.pkl")
        EMBED_PATH    = os.path.join(CAMINHO_DATA, "sirius_embeddings.pkl")

        contagem = self._contar_dados()
        return {
            "embeddings_treinados":    arquivo_existe(EMBED_PATH),
            "classificador_treinado":  arquivo_existe(MODELO_PATH),
            "gerador_treinado":        arquivo_existe(GERADOR_PATH),
            "vocab_gerador_existe":    arquivo_existe(VOCAB_PATH),
            "total_dados":             contagem["total"],
            "conversas":               contagem["conversas"],
            "conhecimento":            contagem["conhecimento"],
        }

    def imprimir_status(self):
        s = self.status()
        print("\n╔══════════════════════════════════╗")
        print("║     Status das Redes do Sirius    ║")
        print("╠══════════════════════════════════╣")
        print(f"║  Embeddings:    {'✓ treinado' if s['embeddings_treinados'] else '✗ não treinado':20s} ║")
        print(f"║  Classificador: {'✓ treinado' if s['classificador_treinado'] else '✗ não treinado':20s} ║")
        print(f"║  Gerador:       {'✓ treinado' if s['gerador_treinado'] else '✗ não treinado':20s} ║")
        print("╠══════════════════════════════════╣")
        print(f"║  Dados: {s['conversas']} conversas + {s['conhecimento']} conhecimentos")
        print("╚══════════════════════════════════╝\n")


# ---------------------------------------------------------------------------
# CLI — treino manual
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Treina as redes neurais do Sirius")
    parser.add_argument("--tudo",          action="store_true", help="Treina tudo")
    parser.add_argument("--embeddings",    action="store_true", help="Só embeddings")
    parser.add_argument("--classificador", action="store_true", help="Só classificador")
    parser.add_argument("--gerador",       action="store_true", help="Só gerador")
    parser.add_argument("--status",        action="store_true", help="Mostra status")
    parser.add_argument("--epocas",        type=int, default=30, help="Épocas do gerador")
    args = parser.parse_args()

    treinador = SiriusTreinador()

    if args.status or not any([args.tudo, args.embeddings, args.classificador, args.gerador]):
        treinador.imprimir_status()

    if args.tudo:
        treinador.treinar_tudo(forcar=True)
    elif args.embeddings:
        treinador.treinar_embeddings()
    elif args.classificador:
        treinador.treinar_classificador()
    elif args.gerador:
        treinador.treinar_gerador(epocas=args.epocas)