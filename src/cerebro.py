"""
cerebro.py - Cerebro do Sirius 100% proprio
"""

import os
import sys
import re
import time
import threading

diretorio_src = os.path.dirname(os.path.abspath(__file__))
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

from memoria       import SiriusMemory
from neuronio      import SiriusNeuronio
from filtro_zoeiro import SiriusFiltro
from controle_pc   import SiriusControl

_gerador    = None
_embeddings = None

def _get_gerador():
    global _gerador
    if _gerador is None:
        from sirius_gerador import SiriusGerador
        _gerador = SiriusGerador()
    return _gerador

def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from sirius_embeddings import SiriusEmbeddings
        _embeddings = SiriusEmbeddings()
    return _embeddings


# ---------------------------------------------------------------------------
# Respostas rapidas
# ---------------------------------------------------------------------------

RESPOSTAS_RAPIDAS = {
    "bom dia":   "Bom dia, chefia! To ligado e pronto.",
    "boa tarde": "Boa tarde! Pode mandar o que precisar.",
    "boa noite": "Boa noite! To aqui se precisar.",
    "oi":        "Oi! Manda bala, o que precisa?",
    "ola":       "Opa! To na escuta.",
    "opa":       "Opa! O que foi, chefia?",
    "salve":     "Salve! Pode mandar.",
    "tudo bem":  "Tudo certo por aqui! E voce?",
    "tudo bom":  "Tudo bom sim! Pode falar.",
    "e ai":      "E ai! To aqui, manda.",
    "valeu":     "Tmj, chefia! Qualquer coisa to aqui.",
    "obrigado":  "De nada! Tamo junto sempre.",
    "tchau":     "Ate mais! Fica na paz.",
    "flw":       "Flw mano! Qualquer coisa grita.",
}

INDICADORES_FALHA = {
    "nao sei", "nao encontrei", "desculpe", "nao tenho acesso",
    "nao consigo", "nao estou certo", "nao pude",
}

# ---------------------------------------------------------------------------
# Parser de mensagens
# Suporta dois modos:
#   "falando que X"   -> envia X como texto LITERAL
#   "falando sobre X" -> GERA um texto sobre X antes de enviar
# ---------------------------------------------------------------------------

_PLATAFORMAS_MENSAGEM = ["discord", "whatsapp", "telegram", "slack"]

# Keywords que indicam conteudo LITERAL
_KEYWORDS_LITERAL = ["falando que", "dizendo que", "dizendo:", "falando:", "que ", "dizendo ", "falando "]

# Keywords que indicam conteudo GERADO (tema)
_KEYWORDS_SOBRE = ["falando sobre", "dizendo sobre", "sobre ", "a respeito de", "relacionado a"]

_PADROES_MENSAGEM = [
    r"(?:manda|mande|envia|envie|escreve|escreva)(?:\s+uma?\s+mensagem)?\s+(?:para|pro|pra)\s+(.+?)\s+no\s+({plat})\s+((?:falando|dizendo|sobre|que|:).+)",
    r"(?:manda|mande|envia|envie|escreve|escreva)(?:\s+uma?\s+mensagem)?\s+no\s+({plat})\s+(?:para|pro|pra)\s+(.+?)\s+((?:falando|dizendo|sobre|que|:).+)",
    r"(?:fala|fale)\s+(?:para|pro|pra)\s+(.+?)\s+no\s+({plat})\s+((?:falando|dizendo|sobre|que|:).+)",
    r"no\s+({plat})\s+(?:manda|mande|fala|fale|envia|envie)\s+(?:para|pro|pra)\s+(.+?)\s+((?:falando|dizendo|sobre|que|:).+)",
    r"mensagem\s+(?:para|pro|pra)\s+(.+?)\s+no\s+({plat})\s*((?:falando|dizendo|sobre|que|:).+)",
    r"mensagem\s+no\s+({plat})\s+(?:para|pro|pra)\s+(.+?)\s*((?:falando|dizendo|sobre|que|:).+)",
]


def _classificar_conteudo_mensagem(conector_e_conteudo):
    """
    Recebe a parte final da frase.
    Retorna (modo, conteudo):
      "sobre"   -> conteudo e um tema, Sirius vai gerar o texto
      "literal" -> conteudo e a mensagem exata
    """
    t = conector_e_conteudo.lower().strip()

    # "sobre" tem prioridade — verifica primeiro
    for kw in _KEYWORDS_SOBRE:
        if t.startswith(kw):
            tema = t[len(kw):].strip()
            if tema:
                return "sobre", tema

    # Literal
    for kw in _KEYWORDS_LITERAL:
        if t.startswith(kw):
            msg = t[len(kw):].strip()
            if msg:
                return "literal", msg

    # Sem keyword reconhecida — trata como literal
    return "literal", t


def _gerar_mensagem_sobre_tema(tema):
    """
    Gera um texto curto sobre o tema para enviar como mensagem.
    Cascata: SiriusGerador -> embeddings/historico -> DuckDuckGo -> frase padrao
    """
    # 1. Gerador seq2seq proprio
    try:
        gerador = _get_gerador()
        if gerador.esta_treinado():
            resposta = gerador.gerar("me fala sobre {}".format(tema))
            if resposta and len(resposta) > 15:
                return resposta[:300].strip()
    except Exception:
        pass

    # 2. Busca semantica no historico
    try:
        embeddings = _get_embeddings()
        if embeddings.esta_treinado():
            mem = SiriusMemory()
            historico = mem.obter_historico_db(limit=50)
            respostas = [c for role, c in historico if role == "assistant" and len(c) > 10]
            if respostas:
                similar = embeddings.buscar_mais_similar("sobre {}".format(tema), respostas)
                if similar and len(similar) > 15:
                    return similar[:300].strip()
    except Exception:
        pass

    # 3. DuckDuckGo — pesquisa e extrai um resumo real
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            resultados = list(ddgs.text(tema, max_results=1))
        if resultados:
            res = resultados[0]
            corpo = res.get("body", "") if isinstance(res, dict) else ""
            if corpo and len(corpo) > 20:
                return corpo[:280].strip() + "..."
    except Exception:
        pass

    # 4. Frase padrao
    return "Ei, pesquisei aqui sobre {} e e um tema bem interessante! Vale a pena dar uma olhada.".format(tema)


def _parsear_mensagem(texto):
    """
    Extrai (plataforma, destinatario, mensagem_final) do texto natural.
    Retorna (plat, dest, msg) ou None.
    """
    t = texto.lower().strip()
    plat_regex = "|".join(_PLATAFORMAS_MENSAGEM)

    for padrao_template in _PADROES_MENSAGEM:
        padrao = padrao_template.replace("{plat}", plat_regex)
        m = re.search(padrao, t, re.IGNORECASE)
        if not m:
            continue

        grupos = m.groups()
        plat   = None
        outros = []
        for g in grupos:
            g_strip = g.strip() if g else ""
            if g_strip.lower() in _PLATAFORMAS_MENSAGEM:
                plat = g_strip.lower()
            else:
                outros.append(g_strip)

        if plat and len(outros) >= 2:
            dest              = outros[0].strip()
            conector_conteudo = outros[1].strip()

            # Limpa "no discord/whatsapp/etc" que possa ter sobrado no dest
            for p in _PLATAFORMAS_MENSAGEM:
                dest = re.sub(r"\s*no\s+" + p, "", dest, flags=re.IGNORECASE).strip()
                dest = re.sub(r"\s*na\s+" + p, "", dest, flags=re.IGNORECASE).strip()

            if not dest or not conector_conteudo:
                continue

            modo, conteudo = _classificar_conteudo_mensagem(conector_conteudo)

            if modo == "sobre":
                print("[CEREBRO]: Gerando mensagem sobre '{}'...".format(conteudo))
                mensagem_final = _gerar_mensagem_sobre_tema(conteudo)
            else:
                mensagem_final = conteudo

            return plat, dest, mensagem_final

    # Fallback simples
    for plat in _PLATAFORMAS_MENSAGEM:
        if plat not in t:
            continue
        m = re.search(r"(?:para|pro|pra)\s+(.+?)\s+((?:falando|dizendo|sobre|que|:)\s*.+)", t)
        if m:
            dest              = m.group(1).strip()
            conector_conteudo = m.group(2).strip()
            for p in _PLATAFORMAS_MENSAGEM:
                dest = re.sub(r"\s*(?:no|na)\s+" + p, "", dest, flags=re.IGNORECASE).strip()
            if not dest or not conector_conteudo:
                continue
            modo, conteudo = _classificar_conteudo_mensagem(conector_conteudo)
            if modo == "sobre":
                print("[CEREBRO]: Gerando mensagem sobre '{}'...".format(conteudo))
                mensagem_final = _gerar_mensagem_sobre_tema(conteudo)
            else:
                mensagem_final = conteudo
            return plat, dest, mensagem_final

    return None


# ---------------------------------------------------------------------------
# Gerador de arquivos
# Suporta: txt, py, html, md, json, csv, bat
# Comportamento:
#   - "cria um arquivo txt chamado teste"          -> cria vazio
#   - "cria um arquivo txt sobre python"           -> gera conteudo sobre python
#   - "cria um arquivo py com um hello world"      -> conteudo literal
# ---------------------------------------------------------------------------

_EXTENSOES_SUPORTADAS = {
    "txt": ".txt", "texto": ".txt",
    "py":  ".py",  "python": ".py",
    "html": ".html", "htm": ".html",
    "md":  ".md",  "markdown": ".md",
    "json": ".json",
    "csv":  ".csv",
    "bat":  ".bat", "batch": ".bat",
    "js":   ".js",  "javascript": ".js",
    "css":  ".css",
}

# Templates minimos por extensao (quando nao ha conteudo especificado)
_TEMPLATES = {
    ".py":   "# Arquivo gerado pelo Sirius\n\n",
    ".html": "<!DOCTYPE html>\n<html>\n<head><meta charset='utf-8'><title>Sirius</title></head>\n<body>\n\n</body>\n</html>\n",
    ".md":   "# Documento\n\n",
    ".json": "{}\n",
    ".csv":  "coluna1,coluna2,coluna3\n",
    ".bat":  "@echo off\n",
    ".js":   "// Arquivo gerado pelo Sirius\n\n",
    ".css":  "/* Arquivo gerado pelo Sirius */\n\n",
    ".txt":  "",
}

def _gerar_conteudo_arquivo(tema, extensao):
    """
    Gera conteudo adequado para o arquivo com base no tema e na extensao.
    Cascata: gerador -> DuckDuckGo -> template padrao
    """
    # Para .py, .html, .js, .css — gera codigo
    if extensao in (".py", ".html", ".js", ".css", ".bat"):
        try:
            gerador = _get_gerador()
            if gerador.esta_treinado():
                prompt = "escreve um codigo {} sobre {}".format(extensao[1:], tema)
                resp = gerador.gerar(prompt)
                if resp and len(resp) > 20:
                    return resp
        except Exception:
            pass
        # Fallback: template com comentario do tema
        base = _TEMPLATES.get(extensao, "")
        return base + "# Tema: {}\n# TODO: implemente aqui\n".format(tema)

    # Para .txt, .md — gera texto sobre o tema
    try:
        gerador = _get_gerador()
        if gerador.esta_treinado():
            resp = gerador.gerar("escreve sobre {}".format(tema))
            if resp and len(resp) > 20:
                return resp
    except Exception:
        pass

    # DuckDuckGo — busca conteudo real
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            resultados = list(ddgs.text(tema, max_results=3))
        if resultados:
            linhas = []
            if extensao == ".md":
                linhas.append("# {}\n".format(tema.title()))
            else:
                linhas.append("Sobre: {}\n\n".format(tema.title()))
            for res in resultados:
                corpo = res.get("body", "") if isinstance(res, dict) else ""
                link  = res.get("href", "") if isinstance(res, dict) else ""
                if corpo:
                    linhas.append(corpo.strip())
                    if link and extensao == ".md":
                        linhas.append("\nFonte: {}".format(link))
                    linhas.append("\n")
            return "\n".join(linhas)
    except Exception:
        pass

    # Fallback final
    return _TEMPLATES.get(extensao, "") + "Conteudo sobre: {}\n".format(tema)


def _parsear_criar_arquivo(texto):
    """
    Detecta pedido de criacao de arquivo e retorna
    (nome, extensao, conteudo_ou_None, modo)
    modo = "vazio" | "literal" | "sobre"
    Retorna None se nao for pedido de arquivo.
    """
    t = texto.lower().strip()

    # Gatilhos de criacao de arquivo
    gatilhos = ["cria ", "crie ", "criar ", "gera ", "gere ", "gerar ",
                "novo arquivo", "cria um arquivo", "gerar um arquivo",
                "criar um arquivo", "cria arquivo"]
    if not any(g in t for g in gatilhos):
        return None

    # Detecta extensao/tipo
    ext = None
    for nome_ext, valor_ext in _EXTENSOES_SUPORTADAS.items():
        if nome_ext in t:
            ext = valor_ext
            break
    if ext is None:
        ext = ".txt"  # padrao

    # Detecta nome do arquivo (entre aspas ou apos "chamado"/"nomeado")
    nome = None
    m = re.search(r'(?:chamado|nomeado|nome|chama)\s+["\']?([a-zA-Z0-9_\-\s]+)["\']?', t)
    if m:
        nome = m.group(1).strip().replace(" ", "_")
    else:
        m = re.search(r'"([^"]+)"', texto)
        if m:
            nome = m.group(1).strip().replace(" ", "_")
    if not nome:
        nome = "sirius_arquivo"

    if not nome.endswith(ext):
        nome = nome + ext

    # Detecta modo: vazio, literal ou sobre
    if any(p in t for p in ["vazio", "em branco", "sem conteudo", "so o arquivo"]):
        return nome, ext, None, "vazio"

    # "sobre X" ou "com conteudo sobre X"
    m_sobre = re.search(r"(?:sobre|a respeito de|relacionado a)\s+(.+?)(?:\s+na\s+pasta|\s+em\s+|$)", t)
    if m_sobre:
        tema = m_sobre.group(1).strip()
        return nome, ext, tema, "sobre"

    # "com X" ou "contendo X" = conteudo literal
    m_literal = re.search(r"(?:com|contendo|escrevendo|dizendo|escrito)\s+['\"](.+?)['\"]", texto)
    if m_literal:
        return nome, ext, m_literal.group(1).strip(), "literal"

    # Nao tem conteudo especificado — cria com template
    return nome, ext, None, "vazio"


# ---------------------------------------------------------------------------
# Parser principal de controle do PC
# ---------------------------------------------------------------------------

def _parsear_controle_pc(texto, control):
    t = texto.lower().strip()

    # -----------------------------------------------------------------------
    # 1. MENSAGENS — prioridade maxima
    # -----------------------------------------------------------------------
    verbos_envio   = ["manda", "mande", "envia", "envie", "fala", "fale", "escreve", "mensagem"]
    tem_plataforma = any(p in t for p in _PLATAFORMAS_MENSAGEM)
    tem_verbo      = any(v in t for v in verbos_envio)

    if tem_plataforma and tem_verbo:
        resultado = _parsear_mensagem(t)
        if resultado:
            plat, dest, msg = resultado
            return control.enviar_mensagem_universal(plat, dest, msg)
        plat_detectada = next((p for p in _PLATAFORMAS_MENSAGEM if p in t), "")
        return (
            "Entendi que quer mandar mensagem no {}. "
            "Me fala assim: 'manda mensagem para [nome] no {} falando [mensagem]'"
        ).format(plat_detectada, plat_detectada)

    # -----------------------------------------------------------------------
    # 2. CRIAR ARQUIVO
    # -----------------------------------------------------------------------
    resultado_arquivo = _parsear_criar_arquivo(texto)
    if resultado_arquivo:
        nome, ext, conteudo_tema, modo = resultado_arquivo

        # Detecta pasta de destino
        pasta = "documentos"
        for p in ["desktop", "downloads", "documentos"]:
            if p in t:
                pasta = p
                break

        if modo == "vazio":
            # Cria com template minimo
            conteudo = _TEMPLATES.get(ext, "")
            return control.criar_arquivo_com_conteudo(nome, conteudo, pasta)

        elif modo == "literal":
            return control.criar_arquivo_com_conteudo(nome, conteudo_tema, pasta)

        elif modo == "sobre":
            print("[CEREBRO]: Gerando conteudo sobre '{}' para arquivo {}...".format(
                conteudo_tema, ext))
            conteudo = _gerar_conteudo_arquivo(conteudo_tema, ext)
            return control.criar_arquivo_com_conteudo(nome, conteudo, pasta)

    # -----------------------------------------------------------------------
    # 3. ENERGIA
    # -----------------------------------------------------------------------
    if any(p in t for p in ["desliga", "desligar", "shutdown"]):
        if any(p in t for p in ["cancela", "cancelar", "abort"]):
            return control.gerenciar_energia("cancelar")
        return control.gerenciar_energia("desligar")

    if any(p in t for p in ["reinicia", "reiniciar", "restart", "reboot"]):
        return control.gerenciar_energia("reiniciar")

    if any(p in t for p in ["suspende", "suspender", "sleep", "dormir"]):
        return control.gerenciar_energia("suspender")

    if any(p in t for p in ["hiberna", "hibernar", "hibernate"]):
        return control.gerenciar_energia("hibernar")

    if any(p in t for p in ["bloqueia", "bloquear", "travar tela", "lock"]):
        return control.gerenciar_energia("bloquear")

    # -----------------------------------------------------------------------
    # 4. VOLUME E MIDIA
    # -----------------------------------------------------------------------
    if any(p in t for p in ["volume", "som", "audio"]):
        if any(p in t for p in ["mais", "aumenta", "sobe"]):
            return control.controle_hardware("volume_mais", _extrair_numero(t, 3))
        if any(p in t for p in ["menos", "diminui", "baixa"]):
            return control.controle_hardware("volume_menos", _extrair_numero(t, 3))
        if any(p in t for p in ["muta", "silencia", "mute"]):
            return control.controle_hardware("mutar")

    if any(p in t for p in ["proxima musica", "pula musica", "next track"]):
        return control.controle_hardware("proxima_musica")
    if any(p in t for p in ["musica anterior", "faixa anterior"]):
        return control.controle_hardware("musica_anterior")
    if any(p in t for p in ["pausa", "pausar", "pause", "play"]):
        if any(p in t for p in ["musica", "audio", "video"]):
            return control.controle_hardware("pausar_musica")

    # -----------------------------------------------------------------------
    # 5. SCREENSHOT
    # -----------------------------------------------------------------------
    if any(p in t for p in ["screenshot", "print screen", "printscreen",
                              "tira print", "tirar print", "captura de tela"]):
        return control.screenshot(_extrair_entre_aspas(t) or "")

    # -----------------------------------------------------------------------
    # 6. ABRIR
    # -----------------------------------------------------------------------
    for gatilho in ["abre ", "abrir ", "abra ", "executa ", "executar ",
                     "inicia ", "iniciar ", "roda ", "rodar ",
                     "abre o ", "abre a ", "abrir o ", "abrir a "]:
        if gatilho in t:
            resto = t.split(gatilho, 1)[1].strip()
            for nome_pasta in ["documentos","desktop","downloads","imagens","musicas","videos"]:
                if nome_pasta in resto:
                    return control.abrir_pasta(nome_pasta)
            if any(p in resto for p in ["http", "www.", ".com", ".br", ".org"]):
                return control.abrir_url(resto.split()[0])
            nome = resto.split()[0] if resto else ""
            if nome:
                return control.abrir_programa(nome)

    url_m = re.search(r"https?://\S+|www\.\S+", t)
    if url_m:
        return control.abrir_url(url_m.group())

    # -----------------------------------------------------------------------
    # 7. FECHAR
    # -----------------------------------------------------------------------
    for gatilho in ["fecha ", "fechar ", "feche ", "encerra ", "encerrar ",
                     "fecha o ", "fecha a "]:
        if gatilho in t:
            nome = t.split(gatilho, 1)[1].strip().split()[0]
            if nome:
                return control.fechar_programa(nome)

    # -----------------------------------------------------------------------
    # 8. JANELAS
    # -----------------------------------------------------------------------
    if any(p in t for p in ["minimiza", "minimizar"]):
        nome = _extrair_nome_app(t, ["minimiza", "minimizar"])
        return control.minimizar_janela(nome or "")

    if any(p in t for p in ["maximiza", "maximizar"]):
        nome = _extrair_nome_app(t, ["maximiza", "maximizar"])
        return control.maximizar_janela(nome or "")

    if any(p in t for p in ["mover janela", "move janela"]):
        direcao = "esquerda" if "esquerda" in t else "direita"
        return control.mover_janela(_extrair_entre_aspas(t) or "", direcao)

    if any(p in t for p in ["lista janelas", "janelas abertas", "o que esta aberto"]):
        return control.listar_janelas()

    if any(p in t for p in ["alterna janela", "alt tab"]):
        return control.alternar_janela()

    # -----------------------------------------------------------------------
    # 9. CLIPBOARD
    # -----------------------------------------------------------------------
    if any(p in t for p in ["copia ", "copiar "]):
        trecho = _extrair_entre_aspas(t)
        if trecho:
            return control.copiar_texto(trecho)
    if any(p in t for p in ["cola ", "colar "]):
        return control.colar_texto()
    if "clipboard" in t:
        return control.obter_clipboard()

    # -----------------------------------------------------------------------
    # 10. PESQUISA
    # -----------------------------------------------------------------------
    for gatilho in ["pesquisa ", "pesquisar ", "busca ", "buscar ", "googla "]:
        if gatilho in t:
            query = t.split(gatilho, 1)[1].strip()
            if query:
                return control.pesquisar_na_web(query)

    # -----------------------------------------------------------------------
    # 11. DIGITAR / TECLA
    # -----------------------------------------------------------------------
    for gatilho in ["digita ", "digitar ", "digite ", "escreve ", "escreva "]:
        if gatilho in t:
            trecho = _extrair_entre_aspas(t) or t.split(gatilho, 1)[1].strip()
            if trecho:
                return control.digitar_texto(trecho)

    for gatilho in ["pressiona ", "pressione ", "aperta ", "aperte ", "tecla "]:
        if gatilho in t:
            tecla = t.split(gatilho, 1)[1].strip().split()[0]
            if tecla:
                return control.pressionar_tecla(tecla)

    # -----------------------------------------------------------------------
    # 12. SISTEMA
    # -----------------------------------------------------------------------
    if any(p in t for p in ["cpu", "ram", "uso do sistema", "recursos do pc"]):
        return control.uso_cpu_ram()
    if any(p in t for p in ["processos ativos", "o que esta rodando"]):
        return control.processos_ativos()
    if any(p in t for p in ["info do sistema", "sistema operacional"]):
        return control.info_sistema()

    # -----------------------------------------------------------------------
    # 13. SCROLL
    # -----------------------------------------------------------------------
    if any(p in t for p in ["rola", "rolar", "scroll"]):
        direcao = "cima" if any(p in t for p in ["cima", "up"]) else "baixo"
        return control.rolar_pagina(direcao, _extrair_numero(t, 3))

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extrair_numero(texto, padrao=3):
    m = re.search(r"\d+", texto)
    return int(m.group()) if m else padrao

def _extrair_entre_aspas(texto):
    m = re.search(r'["\']([^"\']+)["\']', texto)
    return m.group(1) if m else None

def _extrair_nome_app(texto, gatilhos):
    for g in gatilhos:
        if g in texto:
            resto = texto.split(g, 1)[1].strip()
            return _extrair_entre_aspas(resto) or (resto.split()[0] if resto else None)
    return None


# ---------------------------------------------------------------------------
# Classificador de intencao
# ---------------------------------------------------------------------------

_TRIGGERS_CONTROLE = {
    "abre", "abrir", "abra", "fecha", "fechar", "feche",
    "encerra", "encerrar", "desliga", "desligar", "reinicia", "reiniciar",
    "suspende", "suspender", "hiberna", "hibernar", "bloqueia", "bloquear",
    "minimiza", "minimizar", "maximiza", "maximizar",
    "copia", "copiar", "cola", "colar", "digita", "digitar",
    "pressiona", "pressionar", "aperta", "apertar",
    "pesquisa", "pesquisar", "busca", "buscar", "googla",
    "screenshot", "printscreen",
    "rola", "rolar", "scroll",
    "manda", "mande", "envia", "envie",
    "volume", "audio", "som", "musica", "faixa",
    "cpu", "ram", "processos",
    "cria", "crie", "criar", "gera", "gere", "gerar",
}

def _classificar_intencao(texto, neuronio):
    t = texto.lower()

    if any(p in t for p in ["treina", "aprende", "evolui"]):
        return "treinar"

    # Mensagem — detecta antes de tudo
    tem_plataforma  = any(p in t for p in _PLATAFORMAS_MENSAGEM)
    tem_verbo_envio = any(p in t for p in ["manda", "mande", "envia", "envie",
                                            "fala", "fale", "mensagem"])
    if tem_plataforma and tem_verbo_envio:
        return "acao"

    # Arquivo
    if any(p in t for p in ["cria", "crie", "criar", "gera", "gere", "gerar"]):
        if any(ext in t for ext in _EXTENSOES_SUPORTADAS):
            return "acao"
        if any(p in t for p in ["arquivo", "file", "documento"]):
            return "acao"

    # Triggers diretos (verifica palavra inteira)
    palavras = set(t.split())
    for trigger in _TRIGGERS_CONTROLE:
        if trigger in palavras:
            return "acao"

    # Rede neural
    try:
        tema = neuronio.predizer(texto)
        if tema and "Indefinido" not in tema and "Erro" not in tema:
            tema_l = tema.lower()
            if any(a in tema_l for a in ["acao", "controle", "pc", "arquivo", "programa"]):
                return "acao"
            if any(c in tema_l for c in ["conhecimento", "pergunta", "info"]):
                return "conhecimento"
    except Exception:
        pass

    if any(t.startswith(p) for p in ["o que e", "quem e", "como funciona",
                                      "me fala", "explica", "conta sobre"]):
        return "conhecimento"

    return "conversa"


# ---------------------------------------------------------------------------
# Geracao de resposta de conhecimento
# ---------------------------------------------------------------------------

def _responder_conhecimento(texto, memoria):
    try:
        gerador = _get_gerador()
        if gerador.esta_treinado():
            resposta = gerador.gerar(texto)
            if resposta and len(resposta) > 5:
                return resposta
    except Exception as e:
        print("[CEREBRO]: Gerador falhou: {}".format(e))

    try:
        embeddings = _get_embeddings()
        if embeddings.esta_treinado():
            historico = memoria.obter_historico_db(limit=50)
            respostas = [c for role, c in historico if role == "assistant" and len(c) > 10]
            if respostas:
                similar = embeddings.buscar_mais_similar(texto, respostas)
                if similar:
                    return similar
    except Exception as e:
        print("[CEREBRO]: Busca semantica falhou: {}".format(e))

    return None


# ---------------------------------------------------------------------------
# Cerebro principal
# ---------------------------------------------------------------------------

class SiriusCerebro:
    def __init__(self):
        self.memoria  = SiriusMemory()
        self.filtro   = SiriusFiltro()
        self.neuronio = SiriusNeuronio()
        self.control  = SiriusControl()

        # --- Módulos opcionais (carregados lazy) ---
        self._agentes   = None
        self._scheduler = None
        self._arquivos  = None

        self._inicializar_modulos()
        print("\033[92m[CEREBRO]: Cerebro 100% proprio inicializado.\033[0m")

    def _inicializar_modulos(self):
        """Inicializa agentes, scheduler e leitor de arquivos."""
        # Agentes
        try:
            from sirius_agentes import SiriusAgentes
            self._agentes = SiriusAgentes(self.memoria)
            print("\033[92m[CEREBRO]: Agentes ativados.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: Agentes indisponíveis: {e}")

        # Leitor de arquivos
        try:
            from sirius_arquivos import SiriusArquivos
            self._arquivos = SiriusArquivos()
            print("\033[92m[CEREBRO]: Leitor de arquivos ativado.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: Leitor de arquivos indisponível: {e}")

        # Scheduler (inicia monitoramento de atividade)
        try:
            from sirius_scheduler import SiriusScheduler
            self._scheduler = SiriusScheduler(cerebro=self)
            self._scheduler.registrar_agentes(self._agentes)
            self._scheduler.iniciar()
            print("\033[92m[CEREBRO]: Scheduler de aprendizado ativado.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: Scheduler indisponível: {e}")

    def _registrar_scheduler(self, coordenador, treinador):
        """Chamado pelo main após inicializar coordenador e treinador."""
        if self._scheduler:
            self._scheduler.registrar_coordenador(coordenador)
            self._scheduler.registrar_treinador(treinador)

    def _extrair_comando(self, texto):
        limpo = re.sub(r"[,!\.\s]*sirius[,!\.\s]*", " ", texto).strip()
        return limpo if limpo else None

    def _resposta_rapida(self, texto):
        for gatilho, resposta in RESPOSTAS_RAPIDAS.items():
            if gatilho in texto:
                return resposta
        return None

    def _eh_falha(self, texto):
        return any(ind in texto.lower() for ind in INDICADORES_FALHA)

    def _tentar_agentes(self, comando: str) -> str | None:
        """Tenta resolver o comando usando os agentes especializados."""
        if not self._agentes:
            return None
        try:
            return self._agentes.executar(comando)
        except Exception as e:
            print(f"[CEREBRO]: Agente falhou: {e}")
            return None

    def _tentar_ler_arquivo(self, comando: str) -> str | None:
        """Detecta e lê um arquivo mencionado no comando."""
        if not self._arquivos:
            return None
        try:
            caminho = self._arquivos.detectar_arquivo_no_comando(comando)
            if not caminho:
                return None
            resultado = self._arquivos.ler_e_salvar_no_banco(caminho, self.memoria)
            if resultado.sucesso:
                return resultado.resumo
            return f"Não consegui ler '{resultado.nome}': {resultado.erro}"
        except Exception as e:
            print(f"[CEREBRO]: Erro ao ler arquivo: {e}")
            return None

    def processar(self, texto_usuario, forcar_processamento=False):
        if isinstance(texto_usuario, list):
            texto_usuario = texto_usuario[0] if texto_usuario else ""
        texto_lower = str(texto_usuario).lower().strip()

        if not texto_lower:
            return None

        if "sirius" in texto_lower:
            comando = self._extrair_comando(texto_lower)
            if not comando:
                return "Diga la, chefia. To ouvindo."
        elif forcar_processamento:
            comando = texto_lower
        else:
            return None

        # Sinaliza atividade ao scheduler
        if self._scheduler:
            self._scheduler.registrar_atividade()

        # Resposta rápida instantânea
        resp_rapida = self._resposta_rapida(comando)
        if resp_rapida:
            self.memoria.salvar_historico(comando, resp_rapida)
            return resp_rapida

        print("[CEREBRO]: Classificando: '{}'".format(comando))
        intencao = _classificar_intencao(comando, self.neuronio)
        print("[CEREBRO]: Intencao -> {}".format(intencao))

        # --- Retreino ---
        if intencao == "treinar":
            def _treinar():
                from sirius_treinador import SiriusTreinador
                SiriusTreinador().treinar_tudo()
            threading.Thread(target=_treinar, daemon=True).start()
            return "Certo, vou evoluir meu cerebro agora! Isso leva alguns minutos."

        # --- Arquivo mencionado? ---
        resposta_arquivo = self._tentar_ler_arquivo(comando)
        if resposta_arquivo:
            self.memoria.salvar_historico(comando, resposta_arquivo)
            return resposta_arquivo

        # --- Controle do PC (acao) ---
        if intencao == "acao":
            resposta = _parsear_controle_pc(comando, self.control)

            # Se o controle_pc não resolveu, tenta os agentes
            if resposta is None:
                resposta = self._tentar_agentes(comando)

            if resposta is None:
                resposta = (
                    "Entendi que voce quer fazer algo, mas nao consegui identificar o que. "
                    "Exemplos: 'abre o chrome', 'lê o arquivo doc.pdf', "
                    "'manda mensagem para Joao no discord falando oi'."
                )
            self.memoria.salvar_historico(comando, resposta)
            self.memoria.salvar_amostra_treino(comando, resposta)
            return resposta

        # --- Agentes para conhecimento ---
        resposta_agente = self._tentar_agentes(comando)
        if resposta_agente and not self._eh_falha(resposta_agente):
            resposta_final = self.filtro.aplicar_zoeira(resposta_agente)
            self.memoria.salvar_historico(comando, resposta_final)
            self.memoria.salvar_amostra_treino(comando, resposta_agente)
            return resposta_final

        # --- Conhecimento / conversa (gerador + embeddings) ---
        resposta_ia = _responder_conhecimento(comando, self.memoria)

        if resposta_ia and not self._eh_falha(resposta_ia):
            resposta_final = self.filtro.aplicar_zoeira(resposta_ia)
        else:
            self.memoria.adicionar_duvida(comando)
            # Dispara pesquisa em background pelo agente pesquisador
            if self._agentes:
                threading.Thread(
                    target=self._agentes.pesquisar_e_aprender,
                    args=(comando,),
                    daemon=True
                ).start()
            resposta_final = (
                "Mano, ainda nao sei responder isso direito. "
                "Ja anotei pra estudar e melhorar!"
            )

        self.memoria.salvar_historico(comando, resposta_final)
        if resposta_ia:
            self.memoria.salvar_amostra_treino(comando, resposta_ia)

        return resposta_final