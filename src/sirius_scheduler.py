"""
sirius_scheduler.py — Gerenciador inteligente de aprendizado em background

Regras:
- CONVERSANDO  → aprendizado pausado, agentes em standby
- STANDBY      → aprendizado máximo (leitura + treino)
- CPU > 70%    → pausa tudo até CPU cair
- A cada 30s   → verifica o estado e decide o que fazer
"""

import os
import sys
import time
import threading
import psutil

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

# Tempo sem conversa para considerar em standby (segundos)
TEMPO_STANDBY   = 60
# CPU máxima permitida para rodar aprendizado
CPU_MAX         = 65.0
# Intervalo de verificação do scheduler
INTERVALO_CHECK = 30


class SiriusScheduler:
    """
    Monitora a atividade do Sirius e distribui recursos entre:
    - Conversa/ações (prioridade máxima)
    - Aprendizado autônomo (roda em standby)
    - Retreino das redes (roda quando CPU livre)
    """

    def __init__(self, cerebro=None):
        self.cerebro           = cerebro
        self._ultima_conversa  = time.time()
        self._em_standby       = False
        self._rodando          = False
        self._thread           = None
        self._lock             = threading.Lock()

        # Referências aos subsistemas (injetadas depois)
        self._coordenador  = None
        self._agentes      = None
        self._treinador    = None

        # Estatísticas
        self._ciclos_standby    = 0
        self._ciclos_ativo      = 0
        self._treinos_executados = 0

    # -----------------------------------------------------------------------
    # Injeção de dependências
    # -----------------------------------------------------------------------

    def registrar_coordenador(self, coordenador):
        self._coordenador = coordenador

    def registrar_agentes(self, agentes):
        self._agentes = agentes

    def registrar_treinador(self, treinador):
        self._treinador = treinador

    # -----------------------------------------------------------------------
    # Sinalização de atividade (chamado pelo cérebro a cada interação)
    # -----------------------------------------------------------------------

    def registrar_atividade(self):
        """Chamado toda vez que o Sirius processa uma mensagem."""
        with self._lock:
            self._ultima_conversa = time.time()
            if self._em_standby:
                self._em_standby = False
                self._pausar_aprendizado()
                print("\033[94m[SCHEDULER]: Conversa detectada → aprendizado pausado.\033[0m")

    # -----------------------------------------------------------------------
    # Monitoramento de recursos
    # -----------------------------------------------------------------------

    def _cpu_ok(self) -> bool:
        """Retorna True se a CPU estiver livre o suficiente."""
        try:
            uso = psutil.cpu_percent(interval=1)
            return uso < CPU_MAX
        except Exception:
            return True

    def _ram_ok(self) -> bool:
        """Retorna True se houver RAM disponível."""
        try:
            mem = psutil.virtual_memory()
            return mem.percent < 85.0
        except Exception:
            return True

    def _tempo_desde_conversa(self) -> float:
        with self._lock:
            return time.time() - self._ultima_conversa

    # -----------------------------------------------------------------------
    # Controle do aprendizado
    # -----------------------------------------------------------------------

    def _pausar_aprendizado(self):
        """Pausa o coordenador/autodidata durante conversa ativa."""
        # O autodidata tem sleep de 5min — não precisamos forçar parada,
        # apenas sinalizamos para ele não processar na próxima iteração
        if self._coordenador and hasattr(self._coordenador, '_autodidata'):
            ad = self._coordenador._autodidata
            if ad:
                # Injeta um flag de pausa sem matar a thread
                ad._pausado = True

    def _retomar_aprendizado(self):
        """Retoma o aprendizado quando entra em standby."""
        if self._coordenador and hasattr(self._coordenador, '_autodidata'):
            ad = self._coordenador._autodidata
            if ad:
                ad._pausado = False

    def _executar_treino_standby(self):
        """Executa treino completo durante standby se tiver dados."""
        if not self._treinador:
            return
        if not self._cpu_ok() or not self._ram_ok():
            print("[SCHEDULER]: CPU/RAM altos demais — treino adiado.")
            return

        try:
            dados = self._treinador._contar_dados()
            if dados["total"] >= 20:
                print("\033[93m[SCHEDULER]: Standby + dados suficientes → retreinando...\033[0m")
                threading.Thread(
                    target=self._treinador.treinar_tudo,
                    daemon=True
                ).start()
                self._treinos_executados += 1
        except Exception as e:
            print(f"[SCHEDULER]: Erro ao verificar dados para treino: {e}")

    def _executar_tarefa_agentes_standby(self):
        """Usa agentes para processar dúvidas pendentes durante standby."""
        if not self._agentes or not self.cerebro:
            return
        try:
            duvida = self.cerebro.memoria.buscar_duvida_pendente()
            if duvida:
                # FIX: marca como resolvida ANTES de processar — evita loop infinito
                self.cerebro.memoria.marcar_duvida_como_resolvida(duvida)
                print(f"\033[94m[SCHEDULER]: Processando dúvida pendente: '{duvida[:50]}'\033[0m")
                threading.Thread(
                    target=self._agentes.pesquisar_e_aprender,
                    args=(duvida,),
                    daemon=True
                ).start()
        except Exception as e:
            print(f"[SCHEDULER]: Erro ao processar dúvida: {e}")

    # -----------------------------------------------------------------------
    # Loop principal
    # -----------------------------------------------------------------------

    def _loop(self):
        print("\033[94m[SCHEDULER]: Monitor de atividade iniciado.\033[0m")

        # Contador para treino — só tenta treinar a cada 4 ciclos de standby
        _ciclo_treino = 0

        while self._rodando:
            time.sleep(INTERVALO_CHECK)

            tempo_inativo = self._tempo_desde_conversa()
            cpu_livre     = self._cpu_ok()
            ram_livre     = self._ram_ok()

            # --- STANDBY ---
            if tempo_inativo >= TEMPO_STANDBY:
                if not self._em_standby:
                    self._em_standby = True
                    self._retomar_aprendizado()
                    print(
                        f"\033[90m[SCHEDULER]: Standby após {tempo_inativo:.0f}s — "
                        f"aprendizado máximo ativado.\033[0m"
                    )
                    _ciclo_treino = 0  # reseta ciclo ao entrar em standby

                self._ciclos_standby += 1
                _ciclo_treino        += 1

                # Treino a cada 4 ciclos de standby (~2min) se CPU livre
                if _ciclo_treino % 4 == 0 and cpu_livre and ram_livre:
                    self._executar_treino_standby()

                # Dúvidas pendentes a cada 2 ciclos
                if _ciclo_treino % 2 == 0 and cpu_livre:
                    self._executar_tarefa_agentes_standby()

            # --- ATIVO ---
            else:
                self._ciclos_ativo += 1
                _ciclo_treino       = 0

                if not cpu_livre:
                    print(
                        f"\033[33m[SCHEDULER]: CPU alta ({psutil.cpu_percent()}%) "
                        f"— aprendizado adiado.\033[0m"
                    )

    # -----------------------------------------------------------------------
    # Controle
    # -----------------------------------------------------------------------

    def iniciar(self):
        if self._rodando:
            return
        self._rodando = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name="SiriusScheduler"
        )
        self._thread.start()

    def parar(self):
        self._rodando = False

    def em_standby(self) -> bool:
        return self._em_standby

    def status(self) -> dict:
        return {
            "em_standby":         self._em_standby,
            "tempo_inativo_s":    self._tempo_desde_conversa(),
            "ciclos_standby":     self._ciclos_standby,
            "ciclos_ativo":       self._ciclos_ativo,
            "treinos_executados": self._treinos_executados,
            "cpu_atual":          psutil.cpu_percent() if psutil else 0,
        }