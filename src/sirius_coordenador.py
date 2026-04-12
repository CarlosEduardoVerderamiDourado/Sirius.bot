"""
sirius_coordenador.py — Coordenador de aprendizado do Sirius

Faz o autodidata e o leitor trabalharem juntos:
- Quando o autodidata encontra um tema novo sem memória → chama o leitor
- Quando o leitor encontra um tema novo → avisa o autodidata
- Ambos compartilham a mesma fila de temas
- Evitam estudar o mesmo tema ao mesmo tempo
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
DB_TREINO    = os.path.join(CAMINHO_DATA, "sirius_treino.db")
DB_PESSOAL   = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


# ---------------------------------------------------------------------------
# Verificador de memória — checa se o Sirius já sabe sobre um tema
# ---------------------------------------------------------------------------

def _tem_memoria_sobre(tema: str, minimo: int = 3) -> bool:
    """
    Retorna True se o Sirius já tem conhecimento suficiente sobre o tema.
    minimo = quantidade mínima de registros para considerar que "sabe"
    """
    try:
        conn  = sqlite3.connect(DB_TREINO)
        # Busca registros onde o tema aparece no campo tema OU no conteúdo
        count = conn.execute(
            """
            SELECT COUNT(*) FROM conhecimento_geral
            WHERE LOWER(tema) LIKE ? OR LOWER(conteudo) LIKE ?
            """,
            (f"%{tema.lower()}%", f"%{tema.lower()}%")
        ).fetchone()[0]

        # Verifica também na memória permanente
        try:
            count += conn.execute(
                """
                SELECT COUNT(*) FROM memoria_permanente
                WHERE LOWER(tema) LIKE ? OR LOWER(conteudo) LIKE ?
                """,
                (f"%{tema.lower()}%", f"%{tema.lower()}%")
            ).fetchone()[0]
        except Exception:
            pass

        conn.close()
        return count >= minimo

    except Exception:
        return False


# ---------------------------------------------------------------------------
# Coordenador principal
# ---------------------------------------------------------------------------

class SiriusCoordenador:
    """
    Orquestra o SiriusAutodidata e o SiriusLeitor para aprenderem juntos.

    Regras de colaboração:
    1. Autodidata estuda um tema → verifica se tem memória suficiente
       - SEM memória suficiente → chama o leitor para aprofundar
       - COM memória suficiente → segue para o próximo tema

    2. Leitor encontra tema novo num livro → avisa o autodidata para
       fazer pesquisa rápida (Wikipedia/Web) antes de ler o livro completo

    3. Ambos compartilham a mesma fila de temas descobertos

    4. Nunca estudam o mesmo tema ao mesmo tempo (lock por tema)
    """

    def __init__(self, memoria):
        self.memoria    = memoria
        self._autodidata = None
        self._leitor     = None
        self._rodando    = False

        # Temas sendo estudados agora (evita duplicação)
        self._temas_em_estudo = set()
        self._lock_temas      = threading.Lock()

        # Fila compartilhada entre autodidata e leitor
        self._fila_compartilhada = []
        self._lock_fila          = threading.Lock()

        # Estatísticas
        self._temas_sem_memoria      = 0
        self._chamadas_leitor        = 0
        self._chamadas_autodidata    = 0

    # -----------------------------------------------------------------------
    # Inicialização dos motores
    # -----------------------------------------------------------------------

    def iniciar(self):
        """Inicializa autodidata e leitor com comunicação bidirecional."""
        print("\033[93m[COORDENADOR]: Iniciando sistema de aprendizado coordenado...\033[0m")

        # Inicializa autodidata
        try:
            from sirius_autodidata import SiriusAutodidata
            self._autodidata = SiriusAutodidata(
                memoria=self.memoria,
                cerebro=None
            )
            # Injeta callbacks de comunicação
            self._autodidata._on_tema_sem_memoria  = self._quando_sem_memoria
            self._autodidata._on_tema_descoberto   = self._quando_descoberto
            self._autodidata._coordenador          = self
            print("\033[92m[COORDENADOR]: Autodidata conectado.\033[0m")
        except Exception as e:
            print(f"\033[31m[COORDENADOR]: Falha ao iniciar autodidata: {e}\033[0m")

        # Inicializa leitor
        try:
            from sirius_leitor import SiriusLeitor
            self._leitor = SiriusLeitor(memoria=self.memoria)
            # Injeta callbacks
            self._leitor._on_tema_descoberto  = self._quando_descoberto
            self._leitor._coordenador         = self
            print("\033[92m[COORDENADOR]: Leitor conectado.\033[0m")
        except Exception as e:
            print(f"\033[33m[COORDENADOR]: Leitor não disponível (pip install playwright beautifulsoup4): {e}\033[0m")

        self._rodando = True

        # Sobe o loop de coordenação
        threading.Thread(
            target=self._loop_coordenacao,
            daemon=True,
            name="SiriusCoordenador"
        ).start()

        # Inicia os motores
        if self._autodidata:
            self._autodidata.iniciar()

        if self._leitor:
            self._leitor.iniciar()

        print("\033[92m[COORDENADOR]: Sistema de aprendizado coordenado ativo!\033[0m")

    # -----------------------------------------------------------------------
    # Callbacks — comunicação entre os motores
    # -----------------------------------------------------------------------

    def _quando_sem_memoria(self, tema: str):
        """
        Chamado pelo autodidata quando encontra um tema sem memória suficiente.
        Solicita ao leitor que busque um livro/texto longo sobre o tema.
        """
        self._temas_sem_memoria += 1

        with self._lock_temas:
            if tema in self._temas_em_estudo:
                return  # já sendo estudado por alguém
            self._temas_em_estudo.add(tema)

        print(f"\033[94m[COORDENADOR]: '{tema}' sem memória suficiente → chamando leitor...\033[0m")
        self._chamadas_leitor += 1

        def _ler_em_thread():
            try:
                if self._leitor:
                    salvos = self._leitor.ler_tema(tema)
                    if salvos > 0:
                        print(f"\033[92m[COORDENADOR]: Leitor absorveu {salvos} trechos sobre '{tema}'.\033[0m")
                    else:
                        print(f"\033[90m[COORDENADOR]: Leitor não encontrou material sobre '{tema}'.\033[0m")
            finally:
                with self._lock_temas:
                    self._temas_em_estudo.discard(tema)

        threading.Thread(target=_ler_em_thread, daemon=True).start()

    def _quando_descoberto(self, tema: str, origem: str):
        """
        Chamado quando qualquer motor descobre um tema novo.
        Avisa o outro motor para também estudar.
        origem: 'autodidata' ou 'leitor'
        """
        with self._lock_fila:
            if tema not in self._fila_compartilhada:
                self._fila_compartilhada.append(tema)

        print(f"\033[96m[COORDENADOR]: Novo tema descoberto por {origem} → '{tema}'\033[0m")

        # Se veio do leitor → autodidata faz pesquisa rápida (Wikipedia/Web)
        if origem == "leitor" and self._autodidata:
            self._chamadas_autodidata += 1

            def _pesquisa_rapida():
                with self._lock_temas:
                    if tema in self._temas_em_estudo:
                        return
                    self._temas_em_estudo.add(tema)
                try:
                    from sirius_autodidata import _buscar_wikipedia, _buscar_web, _salvar_conhecimento
                    itens  = _buscar_wikipedia(tema)
                    salvos, _ = _salvar_conhecimento(itens, self.memoria)
                    if salvos == 0:
                        itens  = _buscar_web(tema)
                        salvos, _ = _salvar_conhecimento(itens, self.memoria)
                    if salvos > 0:
                        print(f"\033[92m[COORDENADOR]: Autodidata absorveu {salvos} sobre '{tema}' (via leitor).\033[0m")
                except Exception as e:
                    print(f"[COORDENADOR]: Erro na pesquisa rápida: {e}")
                finally:
                    with self._lock_temas:
                        self._temas_em_estudo.discard(tema)

            threading.Thread(target=_pesquisa_rapida, daemon=True).start()

        # Se veio do autodidata → leitor agenda para leitura profunda depois
        elif origem == "autodidata" and self._leitor:
            self._leitor._fila.adicionar_descoberto(tema)

    # -----------------------------------------------------------------------
    # Loop de coordenação — monitora e distribui temas da fila compartilhada
    # -----------------------------------------------------------------------

    def _loop_coordenacao(self):
        """
        Monitora a fila compartilhada e distribui temas entre os motores.
        Roda a cada 2 minutos.
        """
        while self._rodando:
            time.sleep(120)

            with self._lock_fila:
                if not self._fila_compartilhada:
                    continue
                tema = self._fila_compartilhada.pop(0)

            with self._lock_temas:
                if tema in self._temas_em_estudo:
                    continue

            # Verifica se já tem memória
            if _tem_memoria_sobre(tema, minimo=3):
                print(f"\033[90m[COORDENADOR]: '{tema}' já está na memória, pulando.\033[0m")
                continue

            # Sem memória → estuda pelos dois motores
            print(f"\033[94m[COORDENADOR]: Tema sem memória na fila → '{tema}'\033[0m")

            # Autodidata faz pesquisa rápida
            if self._autodidata:
                self._autodidata._fila.adicionar_descoberto(tema)

            # Leitor faz leitura profunda
            if self._leitor:
                self._leitor._fila.adicionar_descoberto(tema)

    # -----------------------------------------------------------------------
    # Controle
    # -----------------------------------------------------------------------

    def parar(self):
        self._rodando = False
        if self._autodidata: self._autodidata.parar()
        if self._leitor:     self._leitor.parar()

    def status(self) -> dict:
        s = {
            "rodando":              self._rodando,
            "temas_em_estudo":      len(self._temas_em_estudo),
            "fila_compartilhada":   len(self._fila_compartilhada),
            "temas_sem_memoria":    self._temas_sem_memoria,
            "chamadas_ao_leitor":   self._chamadas_leitor,
            "chamadas_autodidata":  self._chamadas_autodidata,
        }
        if self._autodidata:
            s.update(self._autodidata.status())
        return s

    def imprimir_status(self):
        s = self.status()
        print("\n╔════════════════════════════════════════╗")
        print("║   Sirius Coordenador — Status           ║")
        print("╠════════════════════════════════════════╣")
        print(f"║  Rodando:             {'Sim' if s['rodando'] else 'Não':20s} ║")
        print(f"║  Estudando agora:     {str(s['temas_em_estudo']):20s} ║")
        print(f"║  Fila compartilhada:  {str(s['fila_compartilhada']):20s} ║")
        print(f"║  Sem memória (total): {str(s['temas_sem_memoria']):20s} ║")
        print(f"║  Chamadas ao leitor:  {str(s['chamadas_ao_leitor']):20s} ║")
        print(f"║  Chamadas autodidata: {str(s['chamadas_autodidata']):20s} ║")
        if "total_salvos" in s:
            print(f"║  Conhecimentos:       {str(s['total_salvos']):20s} ║")
        print("╚════════════════════════════════════════╝\n")