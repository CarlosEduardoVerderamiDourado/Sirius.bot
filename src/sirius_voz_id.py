"""
sirius_voz_id.py — Identificação de voz local do Sirius

Reconhece quem está falando pela voz e troca automaticamente de conta.
100% local, sem API externa.

Tecnologia:
    resemblyzer → embeddings de voz (Google's Voice Encoder)
    numpy       → similaridade cosseno entre embeddings
    pyaudio     → gravação das amostras de referência

Como funciona:
    1. Cada conta grava 5-10 amostras de voz de referência
    2. resemblyzer converte cada amostra em um vetor de 256 dimensões
    3. Na conversa, cada fala capturada é comparada com os vetores salvos
    4. Se a similaridade > threshold → identifica o usuário e troca de conta

Precisão estimada:
    ~90% em ambiente silencioso
    ~75% com ruído de fundo moderado
    Melhora com mais amostras de referência (ideal: 10+ por pessoa)

Instalação:
    pip install resemblyzer pyaudio

Uso:
    from sirius_voz_id import SiriusVozID
    voz_id = SiriusVozID(contas)
    voz_id.iniciar()  # monitor passivo em background

    # No audio_handler, após capturar áudio:
    usuario = voz_id.identificar(caminho_wav)
    if usuario and usuario != conta_atual:
        contas.trocar_para(usuario)

Comandos de voz:
    "sirius, registra minha voz"      → grava amostras para conta ativa
    "sirius, registra voz do João"    → grava para conta específica
    "sirius, apaga minha voz"         → remove impressão vocal
    "sirius, reconhecimento de voz"   → mostra status
"""

import os
import sys
import time
import json
import threading
import tempfile
import numpy as np
import unicodedata
import re

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
CAMINHO_VOZ    = os.path.join(CAMINHO_DATA, "voz_id")
os.makedirs(CAMINHO_VOZ, exist_ok=True)

# Threshold de similaridade — acima disso considera o usuário identificado
# 0.75 = equilibrado (recomendado)
# 0.80 = mais restritivo (menos falsos positivos)
# 0.70 = mais permissivo (mais falsos positivos mas erra menos)
THRESHOLD_SIMILARIDADE = 0.75

# Amostras mínimas para considerar o perfil confiável
MIN_AMOSTRAS = 3

# Taxa de amostragem do resemblyzer
SAMPLE_RATE = 16000


def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Extrator de embeddings — resemblyzer
# ---------------------------------------------------------------------------

class ExtratorVoz:
    """
    Converte áudio de voz em vetores de 256 dimensões (embeddings).
    Usa o modelo VoiceEncoder do resemblyzer (pré-treinado pelo Google).

    Embeddings de mesma voz têm similaridade cosseno > 0.75.
    Embeddings de vozes diferentes ficam abaixo de 0.60.
    """

    def __init__(self):
        self._encoder    = None
        self._disponivel = False
        self._lock       = threading.Lock()
        self._inicializar()

    def _inicializar(self):
        try:
            from resemblyzer import VoiceEncoder
            self._encoder    = VoiceEncoder()
            self._disponivel = True
            print("\033[92m[VOZ ID]: resemblyzer carregado — identificação de voz ativa.\033[0m")
        except ImportError:
            print("\033[33m[VOZ ID]: resemblyzer não instalado. pip install resemblyzer\033[0m")
        except Exception as e:
            print(f"\033[33m[VOZ ID]: Falha ao carregar resemblyzer: {e}\033[0m")

    def extrair(self, caminho_wav: str) -> np.ndarray | None:
        """Extrai embedding de 256 dimensões de um arquivo WAV."""
        if not self._disponivel:
            return None

        with self._lock:
            try:
                from resemblyzer import preprocess_wav
                wav = preprocess_wav(caminho_wav)
                if len(wav) < SAMPLE_RATE * 0.5:  # menos de 0.5s → inválido
                    return None
                embedding = self._encoder.embed_utterance(wav)
                return embedding
            except Exception as e:
                print(f"[VOZ ID]: Erro ao extrair embedding: {e}")
                return None

    def similaridade(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Similaridade cosseno entre dois embeddings. Range: -1 a 1."""
        if emb1 is None or emb2 is None:
            return 0.0
        n1 = np.linalg.norm(emb1)
        n2 = np.linalg.norm(emb2)
        if n1 == 0 or n2 == 0:
            return 0.0
        return float(np.dot(emb1, emb2) / (n1 * n2))

    @property
    def disponivel(self) -> bool:
        return self._disponivel


# ---------------------------------------------------------------------------
# Perfil vocal — embeddings de referência de um usuário
# ---------------------------------------------------------------------------

class PerfilVocal:
    """
    Armazena os embeddings de referência de um usuário.
    O embedding médio é usado na comparação.
    """

    def __init__(self, conta_id: str, conta_nome: str):
        self.conta_id   = conta_id
        self.conta_nome = conta_nome
        self.embeddings: list[np.ndarray] = []   # amostras de referência
        self._embedding_medio: np.ndarray | None = None
        self._caminho   = os.path.join(CAMINHO_VOZ, f"voz_{conta_id[:8]}.npy")
        self._meta_path = os.path.join(CAMINHO_VOZ, f"voz_{conta_id[:8]}_meta.json")
        self._carregar()

    def _carregar(self):
        try:
            if os.path.exists(self._caminho):
                dados = np.load(self._caminho, allow_pickle=True)
                self.embeddings = list(dados)
                self._recalcular_medio()
                print(f"[VOZ ID]: {len(self.embeddings)} amostras carregadas para '{self.conta_nome}'.")
        except Exception as e:
            print(f"[VOZ ID]: Erro ao carregar perfil vocal: {e}")

    def _salvar(self):
        try:
            np.save(self._caminho, np.array(self.embeddings))
            meta = {
                "conta_id":   self.conta_id,
                "conta_nome": self.conta_nome,
                "n_amostras": len(self.embeddings),
                "atualizado": time.time(),
            }
            with open(self._meta_path, "w") as f:
                json.dump(meta, f)
        except Exception as e:
            print(f"[VOZ ID]: Erro ao salvar perfil vocal: {e}")

    def _recalcular_medio(self):
        if self.embeddings:
            self._embedding_medio = np.mean(self.embeddings, axis=0)
            # Normaliza
            n = np.linalg.norm(self._embedding_medio)
            if n > 0:
                self._embedding_medio /= n

    def adicionar_amostra(self, embedding: np.ndarray):
        self.embeddings.append(embedding)
        self._recalcular_medio()
        self._salvar()

    def remover(self):
        """Remove todos os dados de voz desta conta."""
        self.embeddings.clear()
        self._embedding_medio = None
        for caminho in [self._caminho, self._meta_path]:
            try:
                if os.path.exists(caminho):
                    os.remove(caminho)
            except Exception:
                pass

    @property
    def embedding_medio(self) -> np.ndarray | None:
        return self._embedding_medio

    @property
    def tem_perfil(self) -> bool:
        return len(self.embeddings) >= MIN_AMOSTRAS

    @property
    def n_amostras(self) -> int:
        return len(self.embeddings)

    def __repr__(self):
        return f"PerfilVocal({self.conta_nome!r}, {self.n_amostras} amostras)"


# ---------------------------------------------------------------------------
# Motor de identificação de voz
# ---------------------------------------------------------------------------

class SiriusVozID:
    """
    Identifica quem está falando e troca de conta automaticamente.

    Integração com audio_handler:
        voz_id = SiriusVozID(contas)

        # Após transcrever, passa o WAV para identificação:
        conta_id = voz_id.identificar_de_wav(caminho_wav)
        if conta_id and conta_id != contas.conta_ativa.id:
            contas.trocar_para_id(conta_id)

    Integração com sirius_contas:
        voz_id.registrar_conta(conta_id, conta_nome)  # ao criar conta
        voz_id.apagar_conta(conta_id)                 # ao remover conta
    """

    def __init__(self, contas=None):
        self._contas   = contas
        self._extrator = ExtratorVoz()
        self._perfis:  dict[str, PerfilVocal] = {}   # conta_id → PerfilVocal
        self._lock     = threading.Lock()

        # Estatísticas
        self._identificacoes  = 0
        self._trocas_auto     = 0
        self._ultima_id_conta = ""  # última conta identificada

        # Cooldown — não reidentifica na mesma conta a cada fala
        self._ultimo_check    = 0.0
        self._COOLDOWN_S      = 10.0   # segundos entre verificações

        self._carregar_perfis()

    # -----------------------------------------------------------------------
    # Carregamento de perfis
    # -----------------------------------------------------------------------

    def _carregar_perfis(self):
        """Carrega todos os perfis vocais existentes."""
        if not os.path.exists(CAMINHO_VOZ):
            return
        for arquivo in os.listdir(CAMINHO_VOZ):
            if arquivo.endswith("_meta.json"):
                try:
                    with open(os.path.join(CAMINHO_VOZ, arquivo)) as f:
                        meta = json.load(f)
                    conta_id   = meta["conta_id"]
                    conta_nome = meta["conta_nome"]
                    perfil     = PerfilVocal(conta_id, conta_nome)
                    if perfil.tem_perfil:
                        self._perfis[conta_id] = perfil
                except Exception as e:
                    print(f"[VOZ ID]: Erro ao carregar meta {arquivo}: {e}")

        if self._perfis:
            nomes = ", ".join(p.conta_nome for p in self._perfis.values())
            print(f"\033[92m[VOZ ID]: {len(self._perfis)} perfis vocais ativos: {nomes}\033[0m")

    # -----------------------------------------------------------------------
    # Identificação
    # -----------------------------------------------------------------------

    def identificar_de_wav(self, caminho_wav: str) -> str | None:
        """
        Identifica o usuário a partir de um arquivo WAV.
        Retorna o conta_id do usuário ou None se não identificar.

        Cooldown: não reidentifica por COOLDOWN_S segundos após uma identificação.
        """
        if not self._extrator.disponivel:
            return None

        if not self._perfis:
            return None  # ninguém tem perfil vocal cadastrado

        # Cooldown — evita verificar toda fala
        agora = time.time()
        if agora - self._ultimo_check < self._COOLDOWN_S:
            return self._ultima_id_conta or None
        self._ultimo_check = agora

        embedding = self._extrator.extrair(caminho_wav)
        if embedding is None:
            return None

        return self._comparar(embedding)

    def _comparar(self, embedding: np.ndarray) -> str | None:
        """Compara embedding com todos os perfis e retorna o melhor match."""
        melhor_id    = None
        melhor_score = THRESHOLD_SIMILARIDADE  # só aceita acima do threshold

        with self._lock:
            for conta_id, perfil in self._perfis.items():
                if perfil.embedding_medio is None:
                    continue
                score = self._extrator.similaridade(embedding, perfil.embedding_medio)
                print(f"\033[90m[VOZ ID]: {perfil.conta_nome}: {score:.3f}\033[0m")
                if score > melhor_score:
                    melhor_score = score
                    melhor_id    = conta_id

        if melhor_id:
            self._identificacoes += 1
            nome = self._perfis[melhor_id].conta_nome
            print(f"\033[94m[VOZ ID]: Identificado → {nome} (score={melhor_score:.3f})\033[0m")
            self._ultima_id_conta = melhor_id

        return melhor_id

    # -----------------------------------------------------------------------
    # Gravação de amostras
    # -----------------------------------------------------------------------

    def gravar_amostras(self, conta_id: str, conta_nome: str,
                         n_amostras: int = 7,
                         callback_status=None,
                         callback_travar_cerebro=None) -> bool:
        """
        Grava amostras de voz para uma conta.

        callback_status(msg):          mensagens de progresso (TTS + print).
        callback_travar_cerebro(bool): True=trava / False=destrava o cerebro.
            Impede que frases guia virem comandos durante a gravação.
            O cerebro.py injeta: lambda b: setattr(self, '_gravando_voz', b)

        n_amostras: 7-10 para boa precisão.
        """
        if not self._extrator.disponivel:
            if callback_status:
                callback_status("resemblyzer não instalado. pip install resemblyzer")
            return False

        try:
            import pyaudio
            import wave
        except ImportError:
            if callback_status:
                callback_status("pyaudio não instalado. pip install pyaudio")
            return False

        if conta_id not in self._perfis:
            self._perfis[conta_id] = PerfilVocal(conta_id, conta_nome)

        perfil  = self._perfis[conta_id]
        pa      = pyaudio.PyAudio()
        sucesso = 0
        RATE    = SAMPLE_RATE
        CHUNK   = 1024
        SEGUNDOS  = 3
        N_FRAMES  = int(RATE / CHUNK * SEGUNDOS)

        # Frases guia SEM a palavra "Sirius" para não disparar a wake word.
        # resemblyzer só precisa da voz — o conteúdo não importa.
        _frases = [
            "Olá, tudo certo por aqui",
            "Pode me ajudar com uma coisa",
            "Bom dia, como vai você hoje",
            "Estou aqui, pode continuar",
            "Que horas são agora por favor",
            "Abre o computador pra mim",
            "Qual é a previsão do tempo",
            "Manda mensagem pro João",
            "Toca uma música relaxante",
            "Desliga o monitor por favor",
        ]

        msg_inicio = (
            f"Vou gravar {n_amostras} amostras da sua voz para te reconhecer. "
            "Fala as frases em voz normal, como se estivesse conversando."
        )
        if callback_status:
            callback_status(msg_inicio)

        # Trava o cerebro ANTES de começar a gravar.
        # Sem isso, as frases guia disparam a wake word e viram comandos.
        if callback_travar_cerebro:
            callback_travar_cerebro(True)
            print("[VOZ ID]: Cérebro travado durante gravação.")

        time.sleep(1.8)  # aguarda TTS terminar de falar o msg_inicio

        for i in range(n_amostras):
            frase = _frases[i % len(_frases)]
            if callback_status:
                callback_status(f"Amostra {i+1}/{n_amostras}: diga '{frase}'")

            time.sleep(1.2)  # pausa para o usuário se preparar

            tmp_path = None
            try:
                stream = pa.open(
                    format=pyaudio.paInt16, channels=1,
                    rate=RATE, input=True, frames_per_buffer=CHUNK
                )
                frames = [stream.read(CHUNK, exception_on_overflow=False)
                          for _ in range(N_FRAMES)]
                stream.stop_stream()
                stream.close()

                # Salva WAV temporário
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp_path = f.name
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(RATE)
                    wf.writeframes(b"".join(frames))

                # Extrai embedding
                embedding = self._extrator.extrair(tmp_path)
                if embedding is not None:
                    perfil.adicionar_amostra(embedding)
                    sucesso += 1
                    if callback_status:
                        callback_status(f"✓ Amostra {i+1} gravada.")
                else:
                    if callback_status:
                        callback_status(f"✗ Amostra {i+1} inválida (muito curta?). Tente de novo.")

            except Exception as e:
                if callback_status:
                    callback_status(f"✗ Erro na amostra {i+1}: {e}")
            finally:
                if tmp_path:
                    try: os.remove(tmp_path)
                    except: pass

            time.sleep(0.8)

        pa.terminate()

        # Destrava o cerebro sempre — mesmo se der erro
        if callback_travar_cerebro:
            callback_travar_cerebro(False)
            print("[VOZ ID]: Cérebro destravado.")

        if sucesso >= MIN_AMOSTRAS:
            msg = (
                f"Perfeito! {sucesso} amostras gravadas. "
                f"Agora consigo te reconhecer pela voz."
            )
            if callback_status:
                callback_status(msg)
            return True
        else:
            msg = (
                f"Só consegui {sucesso} amostras válidas "
                f"(mínimo {MIN_AMOSTRAS}). Tenta de novo."
            )
            if callback_status:
                callback_status(msg)
            return False


    # -----------------------------------------------------------------------
    # Gerenciamento de perfis
    # -----------------------------------------------------------------------

    def registrar_conta(self, conta_id: str, conta_nome: str):
        """Inicializa um perfil vocal vazio para uma conta nova."""
        if conta_id not in self._perfis:
            self._perfis[conta_id] = PerfilVocal(conta_id, conta_nome)

    def apagar_perfil(self, conta_id: str) -> str:
        """Remove o perfil vocal de uma conta."""
        with self._lock:
            if conta_id in self._perfis:
                self._perfis[conta_id].remover()
                del self._perfis[conta_id]
                return "Impressão vocal removida."
            return "Essa conta não tem perfil vocal cadastrado."

    def status_perfil(self, conta_id: str) -> dict:
        perfil = self._perfis.get(conta_id)
        if not perfil:
            return {"tem_perfil": False, "n_amostras": 0}
        return {
            "tem_perfil": perfil.tem_perfil,
            "n_amostras": perfil.n_amostras,
            "conta_nome": perfil.conta_nome,
        }

    def status(self) -> dict:
        return {
            "disponivel":          self._extrator.disponivel,
            "perfis_ativos":       len(self._perfis),
            "usuarios_com_voz":    [p.conta_nome for p in self._perfis.values() if p.tem_perfil],
            "threshold":           THRESHOLD_SIMILARIDADE,
            "identificacoes":      self._identificacoes,
            "trocas_automaticas":  self._trocas_auto,
        }

    # -----------------------------------------------------------------------
    # Comandos de voz
    # -----------------------------------------------------------------------

    _TRIGGERS = {
        # Imperativo (como o usuário normalmente fala)
        "registra minha voz", "cadastra minha voz", "grava minha voz",
        "registra voz", "treina minha voz", "aprende minha voz",
        "apaga minha voz", "remove minha voz", "deleta minha voz",
        # Infinitivo (como o usuário às vezes fala)
        "registrar minha voz", "cadastrar minha voz", "gravar minha voz",
        "registrar voz", "treinar minha voz", "aprender minha voz",
        "apagar minha voz", "remover minha voz", "deletar minha voz",
        # Status
        "reconhecimento de voz", "status da voz", "identificacao de voz",
        "minha voz esta cadastrada", "voz cadastrada", "minha voz",
    }

    def e_comando_voz(self, texto: str) -> bool:
        t = _norm(texto)
        return any(tr in t for tr in self._TRIGGERS)

    def processar_comando(self, texto: str, conta_ativa=None,
                           callback_falar=None,
                           callback_travar_cerebro=None) -> str:
        """
        Processa comando de voz relacionado à identificação.
        callback_falar:           função para falar durante a gravação.
        callback_travar_cerebro:  lambda b: setattr(cerebro, '_gravando_voz', b)
            Trava o cerebro.processar() durante toda a gravação para que
            as frases guia não sejam processadas como comandos.
        """
        t = _norm(texto)

        # Status
        if any(p in t for p in [
            "reconhecimento de voz", "status da voz",
            "identificacao de voz", "minha voz esta cadastrada",
        ]):
            s = self.status()
            if not s["disponivel"]:
                return "resemblyzer não instalado. pip install resemblyzer"
            if not conta_ativa:
                return f"Reconhecimento ativo. {len(s['perfis_ativos'])} usuários cadastrados."
            p = self.status_perfil(conta_ativa.id)
            if p["tem_perfil"]:
                return (
                    f"Sua voz está cadastrada com {p['n_amostras']} amostras. "
                    "Reconhecimento ativo."
                )
            return (
                "Sua voz ainda não foi cadastrada. "
                "Diz: 'registra minha voz' para começar."
            )

        # Apagar voz
        if any(p in t for p in ["apaga minha voz", "remove minha voz", "deleta minha voz"]):
            if not conta_ativa:
                return "Nenhuma conta ativa para apagar a voz."
            return self.apagar_perfil(conta_ativa.id)

        # Registrar voz
        if any(p in t for p in [
            "registra minha voz", "cadastra minha voz", "grava minha voz",
            "registra voz", "treina minha voz", "aprende minha voz",
            "registrar minha voz", "cadastrar minha voz", "gravar minha voz",
        ]):
            if not conta_ativa:
                return "Nenhuma conta ativa para registrar a voz."

            def _gravar():
                def _falar(msg):
                    print(f"[VOZ ID]: {msg}")
                    if callback_falar:
                        try: callback_falar(msg)
                        except: pass

                # callback_travar_cerebro injeta o flag _gravando_voz no cerebro
                # Isso bloqueia processar() durante toda a gravação
                cb_travar = callback_travar_cerebro  # captura do closure

                ok = self.gravar_amostras(
                    conta_ativa.id,
                    conta_ativa.nome,
                    n_amostras=7,
                    callback_status=_falar,
                    callback_travar_cerebro=cb_travar
                )
                if ok:
                    _falar(
                        f"Pronto! Agora consigo te reconhecer, {conta_ativa.nome}. "
                        "Quando você falar, vou saber que é você."
                    )
                # Garante destrava mesmo se ok=False
                if cb_travar:
                    cb_travar(False)

            threading.Thread(target=_gravar, daemon=True).start()
            return "Iniciando gravação das amostras de voz. Vou te guiar!"

        return "Comando de voz não reconhecido."