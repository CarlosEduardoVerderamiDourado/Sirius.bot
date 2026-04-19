"""
sirius_perfil.py — Perfil persistente do usuário

O Sirius lembra quem você é, o que você gosta e como prefere ser atendido.
Isso muda duas coisas fundamentais:
  1. As respostas são personalizadas — o Sirius fala seu nome, usa seu navegador,
     lembra suas preferências sem precisar perguntar de novo.
  2. O briefing matinal e os alertas ficam mais relevantes — cidade, horário de
     trabalho, apps favoritos já estão configurados.

Dados persistidos em: data/sirius_perfil.json
  nome           → como o Sirius te chama ("chefia" ou seu nome)
  cidade         → usada no clima sem precisar perguntar
  navegador      → abre automaticamente quando pede "abre o navegador"
  editor         → abre automaticamente quando pede "abre o editor"
  musica_app     → abre automaticamente quando pede "coloca uma música"
  hora_trabalho  → briefing não acorda antes desse horário
  temas_favoritos → assuntos que o autodidata prioriza
  apps_favoritos  → apps abertos com mais frequência
  estilo_resposta → "curto" | "normal" | "detalhado"
  sessoes         → contagem de sessões e último acesso

Comandos de voz reconhecidos:
  "sirius, meu nome é João"
  "sirius, me chama de chefe"
  "sirius, minha cidade é São Paulo"
  "sirius, meu navegador é firefox"
  "sirius, meu editor é vscode"
  "sirius, prefiro respostas curtas"
  "sirius, me faz perguntas sobre python"  → adiciona python aos temas
  "sirius, qual é meu perfil"             → mostra tudo

Integração no cerebro.py:
    from sirius_perfil import SiriusPerfil
    self._perfil = SiriusPerfil()
    # Personaliza saudação:
    nome = self._perfil.get("nome", "chefia")
    # Consulta preferências:
    nav  = self._perfil.get("navegador")
"""

import os
import sys
import re
import json
import time
import threading
import unicodedata
import sqlite3
from datetime import datetime
from typing import Any, Optional

diretorio_src  = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
CAMINHO_DATA   = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)

CAMINHO_PERFIL = os.path.join(CAMINHO_DATA, "sirius_perfil.json")
DB_PESSOAL     = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Perfil padrão — valores usados antes de qualquer configuração
# ---------------------------------------------------------------------------

PERFIL_PADRAO: dict = {
    # Identidade
    "nome":            "chefia",          # como o Sirius te chama
    "pronome":         "você",            # "você" ou "tu"

    # Localização
    "cidade":          "",                # São Paulo, Guarulhos, etc.

    # Apps preferidos
    "navegador":       "",                # chrome, firefox, edge, opera
    "editor":          "",                # vscode, notepad++, sublime, vim
    "musica_app":      "",                # spotify, youtube, deezer
    "terminal":        "",                # wt (Windows Terminal), cmd, powershell

    # Horários
    "hora_trabalho_inicio": 8,            # briefing não antes desse horário
    "hora_trabalho_fim":    18,           # sem alertas de trabalho depois
    "hora_dormir":          23,           # modo silencioso depois disso

    # Aprendizado autônomo — temas que o autodidata vai priorizar
    "temas_favoritos":  [],               # ["python", "pokemon", "nasa"]

    # Apps frequentes — aprendidos automaticamente pelo uso
    "apps_favoritos":   {},               # {"chrome": 15, "vscode": 8, ...}

    # Estilo de resposta
    "estilo_resposta":  "normal",         # "curto" | "normal" | "detalhado"
    "usar_girias":      True,             # True = estilo parça, False = mais formal

    # Briefing
    "briefing_ativo":   True,
    "briefing_clima":   True,
    "briefing_lembretes": True,

    # Metadados
    "criado_em":        "",
    "ultima_sessao":    "",
    "total_sessoes":    0,
    "total_mensagens":  0,
}

# Mapeamento de apelidos → campos
_CAMPO_ALIAS: dict[str, str] = {
    "nome":         "nome",
    "chame":        "nome",
    "me chama":     "nome",
    "me chamar":    "nome",
    "cidade":       "cidade",
    "navega":       "navegador",
    "navegador":    "navegador",
    "browser":      "navegador",
    "editor":       "editor",
    "ide":          "editor",
    "musica":       "musica_app",
    "música":       "musica_app",
    "player":       "musica_app",
    "terminal":     "terminal",
    "resposta curta":    "estilo_resposta",
    "resposta normal":   "estilo_resposta",
    "resposta detalhada":"estilo_resposta",
}

# Navegadores conhecidos para normalização
_NAVEGADORES = {
    "chrome": "chrome", "google chrome": "chrome",
    "firefox": "firefox", "mozilla": "firefox",
    "edge": "edge", "microsoft edge": "edge",
    "opera": "opera", "brave": "brave",
    "safari": "safari",
}

# Editores conhecidos
_EDITORES = {
    "vscode": "code",      "vs code": "code",
    "visual studio code": "code",
    "notepad++": "notepad++", "notepad": "notepad",
    "sublime": "sublime_text",
    "vim": "vim",          "nvim": "nvim",
    "pycharm": "pycharm",  "intellij": "idea",
    "atom": "atom",        "nano": "nano",
}

# Apps de música
_MUSICA_APPS = {
    "spotify": "spotify",
    "youtube": "youtube",
    "youtube music": "youtube_music",
    "deezer": "deezer",
    "tidal": "tidal",
    "soundcloud": "soundcloud",
    "winamp": "winamp",
    "vlc": "vlc",
}


# ---------------------------------------------------------------------------
# Analisador de conversas — aprende preferências automaticamente
# ---------------------------------------------------------------------------

class AnalisadorConversa:
    """
    Monitora as conversas e extrai preferências implícitas sem o usuário
    precisar configurar nada.

    Aprende:
      - Apps usados com mais frequência (apps_favoritos)
      - Temas perguntados com mais frequência (temas_favoritos)
      - Horários de uso (para ajustar briefing)
    """

    # Padrões que revelam apps favoritos
    _PADROES_APP = [
        r"(?:abre|abrir|lança)\s+(?:o\s+)?(\w+)",
        r"(?:usa|usar)\s+(?:o\s+)?(\w+)",
    ]

    # Apps que não devem ser contados (muito genéricos)
    _APPS_IGNORAR = {
        "o", "a", "um", "uma", "meu", "minha",
        "arquivo", "pasta", "programa", "janela",
    }

    def analisar_comando(self, comando: str, perfil: "SiriusPerfil"):
        """Extrai dados de uso de um comando e atualiza o perfil."""
        t = _norm(comando)

        # Conta apps usados
        for padrao in self._PADROES_APP:
            for m in re.finditer(padrao, t):
                app = m.group(1).strip()
                if app and app not in self._APPS_IGNORAR and len(app) > 2:
                    perfil.incrementar_app(app)

        # Conta temas perguntados
        temas_pergunta = re.findall(
            r"(?:o que e|me fala sobre|conta sobre|explica)\s+(.+?)(?:\?|$)", t
        )
        for tema in temas_pergunta:
            tema = tema.strip()
            if tema and len(tema) > 3:
                perfil.adicionar_tema_frequente(tema.split()[0])  # primeira palavra


# ---------------------------------------------------------------------------
# SiriusPerfil — classe principal
# ---------------------------------------------------------------------------

class SiriusPerfil:
    """
    Gerencia o perfil persistente do usuário.

    Uso básico:
        perfil = SiriusPerfil()

        # Ler preferência
        nome = perfil.get("nome", "chefia")
        nav  = perfil.get("navegador") or "chrome"

        # Salvar preferência
        perfil.set("cidade", "São Paulo")

        # Processar comando de configuração
        resp = perfil.processar_comando("meu nome é João")

        # Saudação personalizada
        saudacao = perfil.saudacao()

        # Personalizar resposta
        resposta = perfil.personalizar_resposta(texto_original, contexto)
    """

    def __init__(self, caminho_json: str = None):
        """
        caminho_json: caminho do arquivo JSON deste perfil.
        Se None, usa o caminho padrão (compatibilidade com uso sem contas).
        """
        self._caminho = caminho_json or CAMINHO_PERFIL
        self._dados   = dict(PERFIL_PADRAO)
        self._lock    = threading.Lock()
        self._analisador = AnalisadorConversa()
        self._carregar()
        self._atualizar_sessao()

    # -----------------------------------------------------------------------
    # Persistência
    # -----------------------------------------------------------------------

    def _carregar(self):
        """Carrega perfil do disco. Se não existir, usa o padrão."""
        try:
            if os.path.exists(self._caminho):
                with open(self._caminho, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                # Mescla com padrão (garante campos novos em versões futuras)
                for chave, valor_padrao in PERFIL_PADRAO.items():
                    if chave not in dados:
                        dados[chave] = valor_padrao
                with self._lock:
                    self._dados = dados
                print(f"\033[92m[PERFIL]: Perfil carregado "
                      f"({self._dados.get('nome', 'sem nome')}).\033[0m")
            else:
                # Primeira execução — detecta cidade por IP automaticamente
                self._detectar_cidade_inicial()
                print("\033[92m[PERFIL]: Perfil novo criado.\033[0m")
        except Exception as e:
            print(f"[PERFIL]: Erro ao carregar perfil: {e}")

    def _salvar(self):
        """Persiste o perfil em disco."""
        try:
            with self._lock:
                dados = dict(self._dados)
            os.makedirs(os.path.dirname(self._caminho), exist_ok=True)
            with open(self._caminho, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PERFIL]: Erro ao salvar: {e}")

    def _detectar_cidade_inicial(self):
        """Detecta cidade por IP na primeira execução."""
        try:
            from sirius_tempo_real import _detectar_cidade_por_ip
            cidade = _detectar_cidade_por_ip()
            if cidade and cidade != "São Paulo":
                self._dados["cidade"] = cidade
                print(f"[PERFIL]: Cidade detectada automaticamente: {cidade}")
        except Exception:
            pass

    def _atualizar_sessao(self):
        """Atualiza contadores de sessão."""
        with self._lock:
            self._dados["ultima_sessao"] = datetime.now().isoformat()
            self._dados["total_sessoes"] = self._dados.get("total_sessoes", 0) + 1
            if not self._dados.get("criado_em"):
                self._dados["criado_em"] = datetime.now().isoformat()
        # Conta mensagens do banco
        try:
            conn = sqlite3.connect(DB_PESSOAL)
            n = conn.execute(
                "SELECT COUNT(*) FROM conversas WHERE role='user'"
            ).fetchone()[0]
            conn.close()
            with self._lock:
                self._dados["total_mensagens"] = n
        except Exception:
            pass
        self._salvar()

    # -----------------------------------------------------------------------
    # API de dados
    # -----------------------------------------------------------------------

    def get(self, chave: str, padrao: Any = None) -> Any:
        """Retorna um valor do perfil."""
        with self._lock:
            return self._dados.get(chave, padrao)

    def set(self, chave: str, valor: Any, salvar: bool = True):
        """Define um valor no perfil."""
        with self._lock:
            self._dados[chave] = valor
        if salvar:
            self._salvar()
        print(f"\033[92m[PERFIL]: {chave} = {valor!r}\033[0m")

    def incrementar_app(self, app: str):
        """Incrementa contador de uso de um app."""
        with self._lock:
            apps = self._dados.get("apps_favoritos", {})
            apps[app] = apps.get(app, 0) + 1
            self._dados["apps_favoritos"] = apps
        # Salva de forma assíncrona para não atrasar o processamento
        threading.Thread(target=self._salvar, daemon=True).start()

    def adicionar_tema_frequente(self, tema: str):
        """Adiciona tema à lista de favoritos se ainda não estiver."""
        t = _norm(tema)
        with self._lock:
            temas = self._dados.get("temas_favoritos", [])
            if t not in [_norm(x) for x in temas] and len(temas) < 20:
                temas.append(tema)
                self._dados["temas_favoritos"] = temas

    def top_apps(self, n: int = 5) -> list[tuple[str, int]]:
        """Retorna os N apps mais usados."""
        apps = self.get("apps_favoritos", {})
        return sorted(apps.items(), key=lambda x: x[1], reverse=True)[:n]

    def nome_usuario(self) -> str:
        """Retorna o nome/apelido do usuário."""
        return self.get("nome", "chefia")

    def cidade_usuario(self) -> str:
        """Retorna a cidade do usuário."""
        return self.get("cidade", "")

    def app_preferido(self, tipo: str) -> str:
        """
        Retorna o app preferido para um tipo.
        tipo: "navegador", "editor", "musica_app", "terminal"
        """
        return self.get(tipo, "")

    # -----------------------------------------------------------------------
    # Personalização de respostas
    # -----------------------------------------------------------------------

    def saudacao(self, hora: int = None) -> str:
        """
        Retorna saudação personalizada com nome do usuário.
        Exemplos:
          "Bom dia, João! Tô ligado."
          "Boa tarde, chefia! Pode mandar."
        """
        if hora is None:
            hora = datetime.now().hour

        if 5  <= hora < 12: periodo = "Bom dia"
        elif 12 <= hora < 18: periodo = "Boa tarde"
        elif 18 <= hora < 23: periodo = "Boa noite"
        else:                  periodo = "Boa madrugada"

        nome = self.nome_usuario()
        return f"{periodo}, {nome}! Tô ligado."

    def personalizar_resposta(self, resposta: str, contexto: str = "") -> str:
        """
        Aplica preferências do usuário na resposta:
          - estilo_resposta = "curto"    → encurta se possível
          - estilo_resposta = "detalhado"→ não trunca
          - usar_girias = False          → remove gírias
        """
        if not resposta:
            return resposta

        estilo = self.get("estilo_resposta", "normal")

        if estilo == "curto" and len(resposta) > 200:
            # Pega só a primeira sentença útil
            idx = resposta.find(".", 50)
            if 50 < idx < 200:
                resposta = resposta[:idx + 1].strip()
            else:
                resposta = resposta[:197] + "..."

        elif estilo == "detalhado":
            # Não trunca — deixa a resposta completa
            pass

        if not self.get("usar_girias", True):
            # Remove marcadores de gíria do filtro_zoeiro
            girias = {
                "Eae mano,": "", "Papo reto,": "", "Seguinte,": "",
                "Ó o que apareceu": "Veja o que encontrei",
                "Mano,": "", "chefia": "você", "Tmj": "De nada",
            }
            for gir, sub in girias.items():
                resposta = resposta.replace(gir, sub)
            resposta = resposta.strip()

        return resposta

    def enriquecer_briefing(self, partes: list[str]) -> list[str]:
        """
        Personaliza o briefing matinal com dados do perfil.
        Adiciona informações relevantes baseadas nas preferências.
        """
        nome  = self.nome_usuario()
        temas = self.get("temas_favoritos", [])
        apps  = self.top_apps(3)

        resultado = []

        # Substitui "chefia" pelo nome real na primeira parte
        for i, parte in enumerate(partes):
            if i == 0 and "chefia" in parte and nome != "chefia":
                parte = parte.replace("chefia", nome)
            resultado.append(parte)

        # Adiciona dica de tema favorito (uma vez por semana)
        if temas:
            import random
            dia_semana = datetime.now().weekday()
            if dia_semana == 0:  # Segunda-feira
                tema = random.choice(temas)
                resultado.append(
                    f"Dica: você costuma perguntar sobre {tema}. "
                    f"Já pesquisei novidades sobre isso."
                )

        return resultado

    # -----------------------------------------------------------------------
    # Resolução de apps preferidos
    # -----------------------------------------------------------------------

    def resolver_navegador(self, fallback: str = "chrome") -> str:
        """
        Retorna o comando do navegador preferido do usuário.
        Usado pelo controle_pc quando o usuário diz "abre o navegador".
        """
        nav = self.get("navegador", "")
        if nav:
            return _NAVEGADORES.get(_norm(nav), nav)
        # Detecta do registro do Windows como fallback
        try:
            from controle_pc import SiriusControl
            nav_detectado = SiriusControl()._obter_navegador_padrao()
            if nav_detectado:
                # Salva para não precisar detectar de novo
                self.set("navegador", nav_detectado, salvar=True)
                return nav_detectado
        except Exception:
            pass
        return fallback

    def resolver_editor(self, fallback: str = "notepad") -> str:
        """Retorna o editor preferido do usuário."""
        editor = self.get("editor", "")
        if editor:
            return _EDITORES.get(_norm(editor), editor)
        return fallback

    def resolver_musica(self, fallback: str = "spotify") -> str:
        """Retorna o app de música preferido do usuário."""
        musica = self.get("musica_app", "")
        if musica:
            return _MUSICA_APPS.get(_norm(musica), musica)
        return fallback

    # -----------------------------------------------------------------------
    # Parser de comandos de configuração
    # -----------------------------------------------------------------------

    # Triggers que identificam um comando de configuração de perfil
    _TRIGGERS_PERFIL = {
        "meu nome e", "meu nome é", "me chama de", "me chame de",
        "pode me chamar de", "me chama",
        "minha cidade e", "minha cidade é", "eu moro em", "sou de",
        "meu navegador e", "meu navegador é", "uso o navegador",
        "prefiro o navegador", "meu browser e", "meu browser é",
        "meu editor e", "meu editor é", "uso o editor",
        "prefiro o editor", "minha ide e", "minha ide é",
        "meu app de musica", "uso o spotify", "uso o deezer",
        "prefiro respostas curtas", "prefiro respostas detalhadas",
        "prefiro respostas normais", "quero respostas curtas",
        "sem girias", "sem gírias", "fala mais formal",
        "pode usar girias", "pode usar gírias",
        "horario de trabalho", "horário de trabalho",
        "hora de dormir", "hora que durmo",
        "meus temas", "adiciona o tema", "gosto de",
        "qual e meu perfil", "qual é meu perfil", "meu perfil",
        "mostra meu perfil", "ver perfil", "minhas preferencias",
        "reseta o perfil", "limpa o perfil",
    }

    def e_comando_perfil(self, texto: str) -> bool:
        """Retorna True se o texto é um comando de configuração de perfil."""
        t = _norm(texto)
        return any(trigger in t for trigger in self._TRIGGERS_PERFIL)

    def processar_comando(self, texto: str) -> str:
        """
        Processa um comando de configuração e retorna a resposta.
        """
        t    = _norm(texto)
        orig = texto.lower().strip()

        # ── Ver perfil ────────────────────────────────────────────────────
        if any(p in t for p in [
            "qual e meu perfil", "qual e o meu perfil",
            "meu perfil", "mostra meu perfil", "ver perfil",
            "minhas preferencias",
        ]):
            return self._formatar_perfil()

        # ── Resetar perfil ────────────────────────────────────────────────
        if any(p in t for p in ["reseta o perfil", "limpa o perfil", "reset perfil"]):
            with self._lock:
                sessoes = self._dados.get("total_sessoes", 0)
                msgs    = self._dados.get("total_mensagens", 0)
                self._dados = dict(PERFIL_PADRAO)
                self._dados["total_sessoes"]  = sessoes
                self._dados["total_mensagens"] = msgs
                self._dados["criado_em"]      = datetime.now().isoformat()
            self._salvar()
            return "Perfil resetado. Começando do zero, chefia."

        # ── Nome ──────────────────────────────────────────────────────────
        nome = self._extrair_valor(t, orig, [
            "meu nome e ", "meu nome é ", "me chama de ", "me chame de ",
            "pode me chamar de ", "me chama ",
        ])
        if nome:
            nome = nome.strip().title()
            self.set("nome", nome)
            return f"Anotado! Vou te chamar de {nome} daqui pra frente."

        # ── Cidade ────────────────────────────────────────────────────────
        cidade = self._extrair_valor(t, orig, [
            "minha cidade e ", "minha cidade é ", "eu moro em ", "sou de ",
        ])
        if cidade:
            cidade = cidade.strip().title()
            self.set("cidade", cidade)
            return (f"Beleza! Cidade configurada como {cidade}. "
                    f"Vou usar isso no clima e nas buscas.")

        # ── Navegador ─────────────────────────────────────────────────────
        nav = self._extrair_valor(t, orig, [
            "meu navegador e ", "meu navegador é ", "uso o navegador ",
            "prefiro o navegador ", "meu browser e ", "meu browser é ",
        ])
        if nav:
            nav_norm = _NAVEGADORES.get(_norm(nav), _norm(nav))
            self.set("navegador", nav_norm)
            return f"Navegador salvo: {nav.strip()}. Vou abrir direto quando você pedir."

        # Detecta "uso o chrome", "uso o firefox" etc.
        for nav_nome in _NAVEGADORES:
            if f"uso o {nav_nome}" in t or f"prefiro o {nav_nome}" in t:
                self.set("navegador", _NAVEGADORES[nav_nome])
                return f"Navegador salvo: {nav_nome}."

        # ── Editor ────────────────────────────────────────────────────────
        editor = self._extrair_valor(t, orig, [
            "meu editor e ", "meu editor é ", "uso o editor ",
            "prefiro o editor ", "minha ide e ", "minha ide é ",
        ])
        if editor:
            ed_norm = _EDITORES.get(_norm(editor), _norm(editor))
            self.set("editor", ed_norm)
            return f"Editor salvo: {editor.strip()}."

        for ed_nome in _EDITORES:
            if f"uso o {ed_nome}" in t or f"prefiro o {ed_nome}" in t:
                self.set("editor", _EDITORES[ed_nome])
                return f"Editor salvo: {ed_nome}."

        # ── App de música ─────────────────────────────────────────────────
        for mus_nome in _MUSICA_APPS:
            if f"uso o {mus_nome}" in t or f"prefiro o {mus_nome}" in t:
                self.set("musica_app", _MUSICA_APPS[mus_nome])
                return f"App de música salvo: {mus_nome}."

        musica = self._extrair_valor(t, orig, [
            "meu app de musica e ", "meu app de música é ",
        ])
        if musica:
            mus_norm = _MUSICA_APPS.get(_norm(musica), _norm(musica))
            self.set("musica_app", mus_norm)
            return f"App de música salvo: {musica.strip()}."

        # ── Estilo de resposta ────────────────────────────────────────────
        if any(p in t for p in [
            "prefiro respostas curtas", "quero respostas curtas",
            "respostas mais curtas", "resposta curta", "menos detalhes",
        ]):
            self.set("estilo_resposta", "curto")
            return "Beleza! Vou ser mais direto ao ponto."

        if any(p in t for p in [
            "prefiro respostas detalhadas", "quero mais detalhes",
            "resposta detalhada", "explica mais", "mais completo",
        ]):
            self.set("estilo_resposta", "detalhado")
            return "Certo! Vou dar mais detalhes nas respostas."

        if any(p in t for p in [
            "prefiro respostas normais", "resposta normal", "resposta padrao",
        ]):
            self.set("estilo_resposta", "normal")
            return "Voltei ao estilo normal."

        # ── Gírias ────────────────────────────────────────────────────────
        if any(p in t for p in [
            "sem girias", "sem gírias", "fala mais formal",
            "sem informalidade", "fala serio",
        ]):
            self.set("usar_girias", False)
            return "Entendido. Vou falar de forma mais formal."

        if any(p in t for p in [
            "pode usar girias", "pode usar gírias", "volta ao normal",
            "fala informal", "fica a vontade",
        ]):
            self.set("usar_girias", True)
            return "Boa! Voltando ao estilo natural."

        # ── Horário de trabalho ───────────────────────────────────────────
        m_trab = re.search(
            r"(?:horario de trabalho|horario de comecar|comecar a trabalhar)"
            r"\s+(?:as?|às?)\s+(\d{1,2})",
            t
        )
        if m_trab:
            hora = int(m_trab.group(1))
            self.set("hora_trabalho_inicio", hora)
            return f"Horário de início de trabalho: {hora:02d}h. Briefing antes disso é silencioso."

        m_dormir = re.search(
            r"(?:hora de dormir|hora que durmo|vou dormir)\s+(?:as?|às?)\s+(\d{1,2})",
            t
        )
        if m_dormir:
            hora = int(m_dormir.group(1))
            self.set("hora_dormir", hora)
            return f"Hora de dormir: {hora:02d}h. Sem alertas depois disso."

        # ── Temas favoritos ───────────────────────────────────────────────
        tema = self._extrair_valor(t, orig, [
            "adiciona o tema ", "gosto de aprender sobre ", "me interesso por ",
            "adiciona ", "quero aprender sobre ", "pesquisa mais sobre ",
        ])
        if tema and len(tema) > 2:
            self.adicionar_tema_frequente(tema.strip())
            self._salvar()
            return (f"Adicionei '{tema.strip()}' aos seus temas de interesse. "
                    f"O autodidata vai priorizar esse assunto.")

        return "Não entendi o que quer configurar. Tente: 'meu nome é João' ou 'minha cidade é SP'."

    def _extrair_valor(self, t_norm: str, texto_orig: str,
                       prefixos: list[str]) -> Optional[str]:
        """Extrai o valor após um prefixo de comando."""
        for prefixo in prefixos:
            if prefixo in t_norm:
                # Usa o texto original para preservar capitalização
                idx_orig = texto_orig.lower().find(prefixo.strip())
                if idx_orig >= 0:
                    valor = texto_orig[idx_orig + len(prefixo):].strip()
                else:
                    idx = t_norm.find(prefixo)
                    valor = t_norm[idx + len(prefixo):].strip()
                # Remove pontuação final
                valor = re.sub(r"[.,!?]+$", "", valor).strip()
                if valor:
                    return valor
        return None

    def _formatar_perfil(self) -> str:
        """Formata o perfil completo para exibição."""
        d = self._dados
        linhas = [f"Perfil de {d.get('nome', 'chefia')}:"]

        campos = [
            ("cidade",         "Cidade"),
            ("navegador",      "Navegador"),
            ("editor",         "Editor"),
            ("musica_app",     "Música"),
            ("estilo_resposta","Estilo de resposta"),
        ]
        for chave, label in campos:
            val = d.get(chave, "")
            if val:
                linhas.append(f"  {label}: {val}")

        temas = d.get("temas_favoritos", [])
        if temas:
            linhas.append(f"  Temas: {', '.join(temas[:5])}")

        apps = self.top_apps(3)
        if apps:
            top = ", ".join(f"{a}({n}x)" for a, n in apps)
            linhas.append(f"  Apps frequentes: {top}")

        linhas.append(
            f"  Sessões: {d.get('total_sessoes', 0)} | "
            f"Mensagens: {d.get('total_mensagens', 0)}"
        )

        criado = d.get("criado_em", "")
        if criado:
            try:
                dt = datetime.fromisoformat(criado)
                linhas.append(f"  Desde: {dt.strftime('%d/%m/%Y')}")
            except Exception:
                pass

        return "\n".join(linhas)

    # -----------------------------------------------------------------------
    # Integração com análise de uso
    # -----------------------------------------------------------------------

    def registrar_uso(self, comando: str):
        """
        Chamado pelo cerebro.py para aprender preferências implícitas.
        Analisa o comando sem precisar de configuração explícita.
        """
        try:
            self._analisador.analisar_comando(comando, self)
        except Exception:
            pass

    def status(self) -> dict:
        with self._lock:
            return {
                "nome":          self._dados.get("nome", "chefia"),
                "cidade":        self._dados.get("cidade", ""),
                "navegador":     self._dados.get("navegador", ""),
                "estilo":        self._dados.get("estilo_resposta", "normal"),
                "temas":         len(self._dados.get("temas_favoritos", [])),
                "apps_rastreados": len(self._dados.get("apps_favoritos", {})),
                "sessoes":       self._dados.get("total_sessoes", 0),
            }


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_perfil_instance: Optional[SiriusPerfil] = None

def get_perfil() -> SiriusPerfil:
    global _perfil_instance
    if _perfil_instance is None:
        _perfil_instance = SiriusPerfil()
    return _perfil_instance


# ---------------------------------------------------------------------------
# Standalone — visualiza e edita o perfil
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Perfil do usuário do Sirius")
    parser.add_argument("--ver",      action="store_true", help="Mostra o perfil")
    parser.add_argument("--status",   action="store_true", help="Status resumido")
    parser.add_argument("--set",      nargs=2, metavar=("CAMPO", "VALOR"),
                        help="Define um campo: --set nome João")
    parser.add_argument("--reset",    action="store_true", help="Reseta o perfil")
    parser.add_argument("--cmd",      type=str, metavar="COMANDO",
                        help="Testa processamento de comando")
    args = parser.parse_args()

    perfil = SiriusPerfil()

    if args.ver or not any([args.status, args.set, args.reset, args.cmd]):
        print("\n" + perfil._formatar_perfil())

    if args.status:
        s = perfil.status()
        print("\n[STATUS]")
        for k, v in s.items():
            print(f"  {k}: {v}")

    if args.set:
        campo, valor = args.set
        perfil.set(campo, valor)
        print(f"✓ {campo} = {valor}")

    if args.reset:
        confirm = input("Confirma reset do perfil? (s/n): ")
        if confirm.lower() == "s":
            perfil.processar_comando("reseta o perfil")
            print("✓ Perfil resetado.")

    if args.cmd:
        if perfil.e_comando_perfil(args.cmd):
            resp = perfil.processar_comando(args.cmd)
            print(f"\nResposta: {resp}")
        else:
            print(f"\nNão detectado como comando de perfil.")