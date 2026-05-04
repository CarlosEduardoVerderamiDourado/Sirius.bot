"""
cerebro.py - Cerebro do Sirius 100% proprio
"""

import os
import sys
import re
import time
import threading
import json

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
    Cascata: AgentePesquisador (Wikipedia+DDG) -> DuckDuckGo direto -> frase padrao
    O SiriusGerador NAO e usado aqui — ainda e fraco para gerar textos coerentes.
    """
    # 1. AgentePesquisador — Wikipedia PT + DuckDuckGo (mais confiavel que o gerador)
    try:
        from sirius_agentes import AgentePesquisador
        mem        = SiriusMemory()
        pesquisador = AgentePesquisador(mem)
        resultado  = pesquisador.executar(tema)
        if resultado and len(resultado) > 30 and "nao encontrei" not in resultado.lower():
            return resultado[:400].strip()
    except Exception:
        pass

    # 2. DuckDuckGo direto
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            resultados = list(ddgs.text(tema, max_results=1))
        if resultados:
            res   = resultados[0]
            corpo = res.get("body", "") if isinstance(res, dict) else ""
            if corpo and len(corpo) > 20:
                return corpo[:300].strip() + "..."
    except Exception:
        pass

    # 3. Wikipedia direto
    try:
        import requests
        url  = "https://pt.wikipedia.org/api/rest_v1/page/summary/" + tema.replace(" ", "_")
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            extrato = resp.json().get("extract", "")
            if extrato and len(extrato) > 30:
                return extrato[:350].strip()
    except Exception:
        pass

    # 4. Frase padrao
    return "Ei, pesquisei aqui sobre {} e é um tema bem interessante! Vale a pena dar uma olhada.".format(tema)


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

    # Para .txt, .md — usa AgentePesquisador em vez do gerador fraco
    try:
        from sirius_agentes import AgentePesquisador
        mem        = SiriusMemory()
        pesquisador = AgentePesquisador(mem)
        resultado  = pesquisador.executar(tema)
        if resultado and len(resultado) > 30 and "nao encontrei" not in resultado.lower():
            if extensao == ".md":
                return "# {}\n\n{}".format(tema.title(), resultado)
            return resultado
    except Exception:
        pass

    # DuckDuckGo — busca conteudo real
    try:
        from ddgs import DDGS
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

def _normalizar(texto):
    """
    Remove acentos, normaliza espacos e converte para minusculo.
    Garante que "músíca", "musica", "musica" etc. todos funcionem.
    """
    import unicodedata
    t = unicodedata.normalize("NFD", texto.lower().strip())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    # Normaliza espacos multiplos
    t = " ".join(t.split())
    return t


def _parsear_controle_pc(texto, control):
    # t = versao normalizada (sem acentos, minusculo)
    # texto = original preservado para extrair nomes/conteudos
    t = _normalizar(texto)

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
    # Aceita: com/sem acento, formal/informal, voz/texto
    # -----------------------------------------------------------------------
    _DESLIGAR = {
        "desliga", "desligar", "desligue", "shutdown", "desligar o pc",
        "desliga o pc", "desligar o computador", "desliga o computador",
        "apaga o pc", "apagar o pc", "apaga o computador",
    }
    _CANCELAR_ENERGIA = {
        "cancela", "cancelar", "cancele", "abort", "cancela desligar",
        "nao desliga", "nao desligue", "para desligar",
    }
    if any(p in t for p in _DESLIGAR):
        if any(p in t for p in _CANCELAR_ENERGIA):
            return control.gerenciar_energia("cancelar")
        # Retorna token — cerebro.py salva ação pendente e pede confirmação
        from filtro_zoeiro import SiriusFiltro
        return "CONFIRMAR_DESLIGAR:" + SiriusFiltro.formatar_confirmacao(
            "desligar o PC em 60 segundos", reversivel=False
        )

    if any(p in t for p in {
        "reinicia", "reiniciar", "reinicie", "restart", "reboot",
        "reiniciar o pc", "reinicia o pc", "reiniciar o computador",
        "reinicia o computador", "reiniciar computador", "dar um restart",
    }):
        return control.gerenciar_energia("reiniciar")

    if any(p in t for p in {
        "suspende", "suspender", "suspenda", "sleep", "dormir", "modo sleep",
        "modo descanso", "suspender o pc", "modo hibernacao", "colocar pra dormir",
    }):
        return control.gerenciar_energia("suspender")

    if any(p in t for p in {
        "hiberna", "hibernar", "hibernate", "modo hibernar",
    }):
        return control.gerenciar_energia("hibernar")

    if any(p in t for p in {
        "bloqueia", "bloquear", "bloqueie", "travar tela", "travar o pc",
        "lock", "bloquear tela", "bloquear o computador", "bloqueia a tela",
        "tranca o pc", "tranca a tela",
    }):
        return control.gerenciar_energia("bloquear")

    # -----------------------------------------------------------------------
    # 4. VOLUME E MIDIA
    # -----------------------------------------------------------------------
    _TEM_VOLUME = {"volume", "som", "audio", "musica", "midia"}
    if any(p in t for p in _TEM_VOLUME):
        if any(p in t for p in {
            "mais", "aumenta", "aumentar", "sobe", "subir", "alto",
            "mais alto", "coloca mais", "deixa mais alto", "up",
        }):
            return control.controle_hardware("volume_mais", _extrair_numero(t, 3))
        if any(p in t for p in {
            "menos", "diminui", "diminuir", "baixa", "baixar", "baixo",
            "mais baixo", "coloca menos", "deixa mais baixo", "down",
        }):
            return control.controle_hardware("volume_menos", _extrair_numero(t, 3))
        if any(p in t for p in {
            "muta", "mutar", "mute", "silencia", "silenciar", "silencio",
            "cala", "tirar o som", "desligar o som", "sem som",
        }):
            return control.controle_hardware("mutar")

    if any(p in t for p in {
        "proxima musica", "proxima faixa", "pula musica", "pula faixa",
        "next track", "proxima", "pula essa", "passa essa", "pular musica",
        "avancar musica", "proximo", "skip",
    }):
        return control.controle_hardware("proxima_musica")

    if any(p in t for p in {
        "musica anterior", "faixa anterior", "volta musica", "volta faixa",
        "prev track", "anterior", "voltai musica", "musica de antes",
    }):
        return control.controle_hardware("musica_anterior")

    if any(p in t for p in {
        "pausa", "pausar", "pause", "play", "continua", "continuar",
        "para a musica", "continua a musica", "toca", "tocar",
        "retomar", "retoma",
    }):
        if any(p in t for p in {"musica", "audio", "video", "midia", "reproducao"}):
            return control.controle_hardware("pausar_musica")

    # -----------------------------------------------------------------------
    # 5. VISAO — analisa o que está na tela
    # -----------------------------------------------------------------------
    _TRIGGERS_VISAO = {
        "o que tem na tela", "o que esta na tela", "o que ta na tela",
        "leia a tela", "le a tela", "ler a tela", "leia o que esta",
        "analisa a tela", "analise a tela", "descreve a tela",
        "o que diz na tela", "o que esta escrito na tela",
        "o que voce ve", "ve a tela", "olha a tela",
        "qual e o erro na tela", "qual erro aparece",
        "o que tem aberto", "o que aparece na tela",
        "descreve o que ve",
    }
    if any(p in t for p in _TRIGGERS_VISAO):
        try:
            from sirius_visao import get_visao
            visao    = get_visao()
            resposta = visao.analisar_tela(t)
            return resposta
        except Exception as e:
            return f"Nao consegui analisar a tela: {e}. Instale: pip install pyautogui Pillow pytesseract"

    # 5b. SCREENSHOT (só tira print, sem análise)
    # -----------------------------------------------------------------------
    if any(p in t for p in {
        "screenshot", "print screen", "printscreen",
        "tira print", "tirar print", "tira screenshot", "tirar screenshot",
        "captura de tela", "capturar tela", "foto da tela", "foto do monitor",
        "salva a tela", "salvar tela", "registra a tela",
    }):
        return control.screenshot(_extrair_entre_aspas(t) or "")

    # -----------------------------------------------------------------------
    # 6. ABRIR
    # -----------------------------------------------------------------------
    _GATILHOS_ABRIR = {
        "abre ", "abrir ", "abra ", "executa ", "executar ",
        "inicia ", "iniciar ", "roda ", "rodar ",
        "abre o ", "abre a ", "abrir o ", "abrir a ",
        "lanca ", "lancar ", "abre esse ", "abrir esse ",
        "iniciar o ", "iniciar a ", "inicializa ", "inicializar ",
        "carrega ", "carregar ", "abre pra mim ", "abre ai ",
    }
    for gatilho in _GATILHOS_ABRIR:
        if gatilho in t:
            resto = t.split(gatilho, 1)[1].strip()
            for nome_pasta in {
                "documentos", "desktop", "downloads", "imagens",
                "musicas", "videos", "area de trabalho",
            }:
                if nome_pasta in resto:
                    return control.abrir_pasta(nome_pasta)
            if any(p in resto for p in ["http", "www.", ".com", ".br", ".org", ".io"]):
                return control.abrir_url(resto.split()[0])
            nome = resto.split()[0] if resto else ""
            if nome:
                resultado = control.abrir_programa(nome)
                # Padroniza feedback: "✓ Steam aberto."
                from filtro_zoeiro import SiriusFiltro
                if resultado and ("achei" in resultado.lower() or "abrindo" in resultado.lower() or "iniciando" in resultado.lower()):
                    return SiriusFiltro.formatar_feedback("aberto", nome.capitalize())
                return resultado

    url_m = re.search(r"https?://\S+|www\.\S+", t)
    if url_m:
        return control.abrir_url(url_m.group())

    # -----------------------------------------------------------------------
    # 7. FECHAR
    # -----------------------------------------------------------------------
    _GATILHOS_FECHAR = {
        "fecha ", "fechar ", "feche ", "encerra ", "encerrar ",
        "fecha o ", "fecha a ", "fechar o ", "fechar a ",
        "mata ", "matar ", "kill ", "mata o ", "matar o ",
        "para o ", "parar o ", "fecha esse ", "encerrar o ",
        "fecha isso ", "fecha ai ",
    }
    for gatilho in _GATILHOS_FECHAR:
        if gatilho in t:
            nome = t.split(gatilho, 1)[1].strip().split()[0]
            if nome:
                resultado = control.fechar_programa(nome)
                from filtro_zoeiro import SiriusFiltro
                if resultado and not "não" in resultado.lower():
                    return SiriusFiltro.formatar_feedback("fechado", nome.capitalize())
                return resultado

    # -----------------------------------------------------------------------
    # 8. JANELAS
    # -----------------------------------------------------------------------
    if any(p in t for p in {
        "minimiza", "minimizar", "minimize", "minimiza a janela",
        "minimizar a janela", "esconde a janela", "reduz a janela",
    }):
        nome = _extrair_nome_app(t, ["minimiza", "minimizar", "minimize"])
        return control.minimizar_janela(nome or "")

    if any(p in t for p in {
        "maximiza", "maximizar", "maximize", "maximiza a janela",
        "coloca em tela cheia", "tela cheia",
    }):
        nome = _extrair_nome_app(t, ["maximiza", "maximizar", "maximize"])
        return control.maximizar_janela(nome or "")

    if any(p in t for p in {
        "mover janela", "move janela", "mova janela",
        "mover para outro monitor", "move para outro monitor",
        "manda pra outro monitor", "jogar no outro monitor",
    }):
        direcao = "esquerda" if any(p in t for p in {"esquerda", "left"}) else "direita"
        return control.mover_janela(_extrair_entre_aspas(t) or "", direcao)

    if any(p in t for p in {
        "lista janelas", "listar janelas", "janelas abertas",
        "o que esta aberto", "o que ta aberto", "quais janelas",
        "mostra janelas", "ver janelas abertas", "quais programas abertos",
    }):
        return control.listar_janelas()

    if any(p in t for p in {
        "alterna janela", "alternar janela", "alt tab",
        "proxima janela", "trocar janela", "muda de janela",
    }):
        return control.alternar_janela()

    # -----------------------------------------------------------------------
    # 9. CLIPBOARD
    # -----------------------------------------------------------------------
    if any(p in t for p in {
        "copia ", "copiar ", "copie ", "copia isso", "copia o texto",
        "copiar texto", "copiar isso",
    }):
        trecho = _extrair_entre_aspas(t)
        if trecho:
            return control.copiar_texto(trecho)

    if any(p in t for p in {
        "cola ", "colar ", "cole ", "colar texto", "cola o texto",
        "cola aqui", "colar aqui",
    }):
        return control.colar_texto()

    if any(p in t for p in {
        "clipboard", "area de transferencia", "o que tem no clipboard",
        "ver clipboard", "ler clipboard", "conteudo clipboard",
    }):
        return control.obter_clipboard()

    # -----------------------------------------------------------------------
    # 10. PESQUISA NA WEB
    # -----------------------------------------------------------------------
    for gatilho in {
        "pesquisa ", "pesquisar ", "pesquise ", "busca ", "buscar ", "busque ",
        "googla ", "google ", "procura ", "procurar ", "procure ",
        "pesquisa na web ", "busca na internet ", "pesquisa no google ",
        "me mostra ", "mostra ", "pesquisa pra mim ",
    }:
        if gatilho in t:
            query = t.split(gatilho, 1)[1].strip()
            if query:
                return control.pesquisar_na_web(query)

    # -----------------------------------------------------------------------
    # 11. DIGITAR / TECLA
    # -----------------------------------------------------------------------
    for gatilho in {
        "digita ", "digitar ", "digite ", "escreve ", "escreva ",
        "escrever ", "digita ai ", "digita isso ", "escreve ai ",
        "cola ai ", "digita pra mim ",
    }:
        if gatilho in t:
            trecho = _extrair_entre_aspas(t) or t.split(gatilho, 1)[1].strip()
            if trecho:
                return control.digitar_texto(trecho)

    for gatilho in {
        "pressiona ", "pressionar ", "pressione ",
        "aperta ", "apertar ", "aperte ",
        "tecla ", "aperta a tecla ", "pressiona a tecla ",
    }:
        if gatilho in t:
            tecla = t.split(gatilho, 1)[1].strip().split()[0]
            if tecla:
                return control.pressionar_tecla(tecla)

    # -----------------------------------------------------------------------
    # 12. SISTEMA — status de CPU, RAM, disco, bateria
    # -----------------------------------------------------------------------
    _TRIGGERS_STATUS_SISTEMA = {
        # Direto
        "cpu", "ram", "memoria ram", "bateria", "memoria livre", "espaco livre",
        # Status geral — todas as variantes de fala
        "uso do sistema", "recursos do pc", "recursos do computador",
        "como ta o sistema", "como esta o sistema", "como anda o sistema",
        "como ta o pc", "como esta o pc", "como anda o pc",
        "como ta o computador", "como esta o computador",
        "ta pesado", "esta pesado", "to pesado",
        "ta lento", "esta lento", "to lento",
        "quanto ta usando", "quanto esta usando", "quanto to usando",
        "ta travando", "esta travando", "travando muito", "to travando",
        "status do sistema", "status do pc", "status do computador",
        "consumo de cpu", "consumo de ram", "consumo de memoria",
        "desempenho do pc", "desempenho do computador", "performance do pc",
        "como esta a memoria", "como ta a memoria",
        "quanto tem de ram", "quanto tem de cpu",
        "verificar sistema", "checar sistema", "checa o sistema",
        "ver uso de memoria", "ver uso de cpu",
        "ta ruim o pc", "ta bom o pc",
    }
    if any(p in t for p in _TRIGGERS_STATUS_SISTEMA):
        return control.uso_cpu_ram()

    if any(p in t for p in {
        "processos ativos", "o que esta rodando", "o que ta rodando",
        "quais processos", "top processos", "listar processos",
        "ver processos", "processos em execucao", "programas rodando",
    }):
        return control.processos_ativos()

    if any(p in t for p in {
        "info do sistema", "informacoes do sistema", "informacao do sistema",
        "sistema operacional", "qual windows", "qual o sistema",
        "versao do windows", "que windows e esse", "que sistema e esse",
    }):
        return control.info_sistema()

    # -----------------------------------------------------------------------
    # 13. SCROLL
    # -----------------------------------------------------------------------
    if any(p in t for p in {
        "rola", "rolar", "scroll", "rola a pagina", "rolar a pagina",
        "desce a pagina", "sobe a pagina", "rola pra baixo", "rola pra cima",
    }):
        direcao = "cima" if any(p in t for p in {"cima", "up", "subir", "pra cima"}) else "baixo"
        return control.rolar_pagina(direcao, _extrair_numero(t, 3))

    return None


def _validar_url_local(url: str) -> bool:
    url = url.strip()
    if not url or " " in url:
        return False
    if url.startswith(("http://", "https://")):
        return True
    if url.startswith("www.") and "." in url:
        return True
    if any(url.startswith(prefix) for prefix in ("file:", "javascript:", "data:")):
        return False
    return "." in url


def _detectar_comando_local_json(comando: str) -> dict | None:
    """Retorna JSON de comando local para ser enviado ao cliente de controle."""
    t = _normalizar(comando)

    if any(termo in t for termo in [
        "abre o chrome", "abre chrome", "abrir chrome",
        "abre o firefox", "abre firefox", "abrir firefox",
        "abre o edge", "abre edge", "abrir edge",
        "abre o navegador", "abre o browser", "abre o navegador padrao",
    ]):
        return {"tipo": "comando_local", "acao": "open_app", "alvo": "navegador"}

    if any(termo in t for termo in [
        "fecha o chrome", "fecha chrome", "fechar chrome",
        "fecha o firefox", "fecha firefox", "fechar firefox",
        "fecha o edge", "fecha edge", "fechar edge",
        "fecha o navegador", "fechar o navegador", "fecha o browser",
    ]):
        return {"tipo": "comando_local", "acao": "close_app", "alvo": "navegador"}

    if any(termo in t for termo in ["abre url", "abrir url", "abre o site", "abre o endereco"]):
        url = comando.strip().split()[-1].strip().rstrip(".,;")
        if _validar_url_local(url):
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            return {"tipo": "comando_local", "acao": "open_url", "alvo": url}
        return None

    match = re.search(r"abre(?:r)?\s+(?:o |a )?(?P<app>[\wçãõáéíóúâêô@.-]+)", t)
    if match:
        app = match.group("app").strip()
        if app and app not in {"o", "a", "navegador", "browser", "google", "site", "url"}:
            return {"tipo": "comando_local", "acao": "open_app", "alvo": app}

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

# Palavras INDIVIDUAIS que classificam como acao — verificadas por palavra inteira
_TRIGGERS_CONTROLE = {
    # Abrir/fechar
    "abre", "abrir", "abra", "fecha", "fechar", "feche",
    "encerra", "encerrar", "mata", "matar", "lanca", "lancar",
    "executa", "executar", "inicia", "iniciar", "roda", "rodar",
    # Energia
    "desliga", "desligar", "reinicia", "reiniciar", "reinicie",
    "suspende", "suspender", "hiberna", "hibernar", "bloqueia", "bloquear",
    # Janelas
    "minimiza", "minimizar", "maximiza", "maximizar",
    # Clipboard
    "copia", "copiar", "cola", "colar",
    # Teclado
    "digita", "digitar", "pressiona", "pressionar", "aperta", "apertar",
    # Web
    "pesquisa", "pesquisar", "busca", "buscar", "googla", "procura", "procurar",
    # Midia
    "screenshot", "printscreen", "print",
    "rola", "rolar", "scroll", "pausa", "pausar",
    # Mensagem
    "manda", "mande", "envia", "envie",
    # Audio
    "volume", "audio", "som", "musica", "midia",
    # Sistema
    "cpu", "ram", "processos", "bateria", "memoria",
    # Arquivo
    "cria", "crie", "criar", "gera", "gere", "gerar",
}

# FRASES COMPOSTAS que classificam como acao — verificadas como substring
# Sem estas, "como ta o sistema" nao teria nenhuma palavra individual no set acima
_TRIGGERS_SISTEMA_FRASES = {
    # Status do sistema — com e sem acento (texto ja eh normalizado quando chega aqui)
    "uso do sistema", "recursos do pc", "recursos do computador",
    "como ta o sistema", "como esta o sistema", "como anda o sistema",
    "como ta o pc", "como esta o pc", "como anda o pc",
    "como ta o computador", "como esta o computador",
    "ta pesado", "esta pesado", "ta lento", "esta lento",
    "to pesado", "to lento", "to travando",
    "quanto ta usando", "quanto esta usando",
    "ta travando", "esta travando", "travando muito",
    "status do sistema", "status do pc",
    "consumo de cpu", "consumo de ram", "consumo de memoria",
    "desempenho do pc", "performance do pc", "espaco livre",
    "checar sistema", "checa o sistema", "verificar sistema",
    "como esta a memoria", "como ta a memoria",
    "ta ruim o pc", "ta bom o pc",
    # Controle de volume em frases compostas
    "volume mais", "volume menos", "mais volume", "menos volume",
    "aumentar volume", "diminuir volume", "volume alto", "volume baixo",
    # Energia em frases compostas
    "desligar o pc", "reiniciar o pc", "reiniciar o computador",
    "desligar o computador", "apagar o pc",
    # Screenshot em frases
    "tira print", "tirar print", "captura de tela",
    # Midia em frases
    "proxima musica", "proxima faixa", "pula musica",
    "musica anterior", "faixa anterior",
}

def _classificar_intencao(texto, neuronio):
    # Normaliza sem acentos para que os triggers funcionem igualmente
    # com "música", "musica", "musíca" etc.
    t = _normalizar(texto)

    if any(p in t for p in ["treina", "aprende", "evolui"]):
        return "treinar"

    # Tempo real — hora e clima (interceptado antes do classificador no processar())
    # Listado aqui para que o neuronio nao confunda com "conhecimento" e dispare agentes
    _TEMPO_REAL_KW = {
        "que horas", "que hora", "horas sao", "hora atual",
        "que dia", "qual dia", "que data", "data de hoje",
        "clima", "temperatura", "vai chover", "previsao", "chuva",
        "faz frio", "faz calor", "ta frio", "ta quente",
    }
    if any(kw in t for kw in _TEMPO_REAL_KW):
        return "tempo_real"

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

    # Frases compostas de sistema — verificadas antes dos triggers individuais
    # Evita que "como ta o sistema" vire pesquisa de conhecimento sobre "sistema"
    if any(frase in t for frase in _TRIGGERS_SISTEMA_FRASES):
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

    # Padrões de conhecimento — verifica em qualquer posição da frase
    _TRIGGERS_CONHECIMENTO = {
        "o que e", "oque e", "o que é", "oque é",
        "que e ", "que é ",           # "que e pokemon", "que é anime"
        "quem e", "quem é",
        "como funciona", "como e ", "como é ",
        "me fala", "me fale", "me conta", "me explica",
        "explica ", "explique",
        "conta sobre", "fala sobre",
        "o que sao", "o que são",
        "historia de", "história de", "historia do", "história do",
        "o que foi", "o que são",
        "what is", "tell me about",
        "pra que serve", "para que serve",
        "como se chama", "quais sao", "quais são",
    }
    if any(p in t for p in _TRIGGERS_CONHECIMENTO):
        return "conhecimento"

    return "conversa"


# ---------------------------------------------------------------------------
# Geracao de resposta de conhecimento
# ---------------------------------------------------------------------------

def _responder_conhecimento(texto, memoria):
    """
    Cascata de respostas — da mais confiável para a menos:
    1. AgentePesquisador (Wikipedia + DDG) — sempre disponível, resposta real
    2. Embeddings + histórico — busca semântica no que já foi respondido
    3. SiriusGerador — só usado se tiver dados suficientes E resposta longa o bastante
    """
    # 1. AgentePesquisador — fonte mais confiável para perguntas de conhecimento
    # Evita usar o gerador que ainda é fraco
    try:
        from sirius_agentes import AgentePesquisador
        pesquisador = AgentePesquisador(memoria)
        resultado   = pesquisador.executar(texto)
        if resultado and len(resultado) > 40 and "nao encontrei" not in resultado.lower():
            return resultado
    except Exception as e:
        print("[CEREBRO]: AgentePesquisador falhou: {}".format(e))

    # 2. Busca semântica no histórico
    try:
        embeddings = _get_embeddings()
        if embeddings.esta_treinado():
            historico  = memoria.obter_historico_db(limit=50)
            respostas  = [
                cont for role, cont in historico
                if role == "assistant" and len(cont) > 20
                # Filtra respostas que são do gerador fraco (contêm frases repetidas)
                and cont.count("mano") < 3
                and "motor local tá fora" not in cont
            ]
            if respostas:
                similar = embeddings.buscar_mais_similar(texto, respostas)
                if similar and len(similar) > 20:
                    return similar
    except Exception as e:
        print("[CEREBRO]: Busca semantica falhou: {}".format(e))

    # 3. SiriusGerador — só usa se tiver dados suficientes
    # Com poucos dados o gerador memoriza e repete, gerando lixo
    try:
        import sqlite3, os
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "sirius_pessoal.db")
        conn = sqlite3.connect(db)
        n_conversas = conn.execute("SELECT COUNT(*) FROM conversas").fetchone()[0]
        conn.close()

        if n_conversas >= 300:  # só usa o gerador com dados suficientes
            gerador = _get_gerador()
            if gerador.esta_treinado():
                resposta = gerador.gerar(texto)
                # Valida qualidade — descarta resposta com repetição
                if resposta and len(resposta) > 20:
                    palavras = resposta.split()
                    from collections import Counter
                    freq = Counter(palavras)
                    mais_freq = freq.most_common(1)[0][1] if palavras else 0
                    if mais_freq / max(len(palavras), 1) < 0.3:  # menos de 30% repetição
                        return resposta
    except Exception as e:
        print("[CEREBRO]: Gerador falhou: {}".format(e))

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

        # Estado de confirmação pendente (para ações destrutivas)
        self._acao_pendente = None  # {"tipo": "desligar", "fn": callable}

        # Visao computacional — carrega lazy
        self._visao = None
        try:
            from sirius_visao import get_visao
            self._visao = get_visao()
            print("\033[92m[CEREBRO]: Visao computacional ativa.\033[0m")
        except Exception as e:
            print(f"\033[33m[CEREBRO]: Visao indisponivel: {e}\033[0m")

        # --- Módulos opcionais (carregados lazy) ---
        self._agentes    = None
        self._scheduler  = None
        self._arquivos   = None
        self._proativo   = None
        self._tempo_real = None
        self._contas     = None   # Sistema de múltiplas contas
        self._perfil     = None   # Perfil da conta ativa
        self._apps       = None   # Controle de apps externos
        self._paralelo   = None   # Execução paralela de tarefas
        self._camera     = None   # Visão por câmera

        # Flag de bloqueio durante gravação de amostras de voz.
        self._gravando_voz = False

        # Callbacks de UI — injetados pela janela ativa (normal ou wallpaper)
        self._callback_falar = None
        self._callback_log   = None

        # --- Contexto de sessão em RAM (últimas N trocas) ---
        self._contexto_sessao: list[dict] = []
        self._MAX_CONTEXTO = 15

        self._inicializar_modulos()
        print("\033[92m[CEREBRO]: Cerebro 100% proprio inicializado.\033[0m")

    def _inicializar_modulos(self):
        """Inicializa agentes, scheduler e leitor de arquivos."""
        # Sistema de contas — carregado primeiro, fornece perfil e memória por conta
        try:
            from sirius_contas import SiriusContas
            self._contas = SiriusContas()
            if self._contas.memoria_ativa:
                self.memoria = self._contas.memoria_ativa
            self._perfil = self._contas.perfil_ativo
            conta_nome = self._contas.conta_ativa.nome if self._contas.conta_ativa else "?"
            print(f"\033[92m[CEREBRO]: Contas ativas — conta atual: '{conta_nome}'.\033[0m")

            # Identificação de voz — opcional, depende de resemblyzer
            try:
                from sirius_voz_id import SiriusVozID
                self._voz_id = SiriusVozID(self._contas)
                print("\033[92m[CEREBRO]: Identificação de voz ativa.\033[0m")
            except Exception as e_voz:
                self._voz_id = None
                print(f"[CEREBRO]: Voz ID indisponível: {e_voz}")

        except Exception as e:
            self._contas = None
            self._voz_id = None
            # Fallback: perfil único sem contas
            try:
                from sirius_perfil import SiriusPerfil
                self._perfil = SiriusPerfil()
                print(f"\033[92m[CEREBRO]: Perfil carregado ({self._perfil.nome_usuario()}).\033[0m")
            except Exception as e2:
                self._perfil = None
            print(f"[CEREBRO]: Contas indisponível: {e}")

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

        # Tempo real (hora, clima)
        try:
            from sirius_tempo_real import processar_tempo_real, _e_pergunta_tempo_real
            self._tempo_real_fn        = processar_tempo_real
            self._e_tempo_real_fn      = _e_pergunta_tempo_real
            print("\033[92m[CEREBRO]: Módulo de tempo real ativado.\033[0m")
        except Exception as e:
            self._tempo_real_fn   = None
            self._e_tempo_real_fn = None
            print(f"[CEREBRO]: Tempo real indisponível: {e}")

        # Proativo (lembretes e alertas)
        try:
            from sirius_proativo import SiriusProativo
            self._proativo = SiriusProativo()
            # Injeta perfil no briefing matinal para personalizar saudação
            if self._perfil and hasattr(self._proativo, '_briefing'):
                self._proativo._perfil = self._perfil
            self._proativo.iniciar()
            print("\033[92m[CEREBRO]: Sistema proativo ativado.\033[0m")
        except Exception as e:
            print(f"[CEREBRO]: Proativo indisponível: {e}")

        # MoE — Hierarchical Mixture of Experts
        try:
            from sirius_moe import SiriusMoE
            self._moe = SiriusMoE(
                memoria=self.memoria,
                agentes=self._agentes,
                control=self.control,
            )
        except Exception as e:
            self._moe = None
            print(f"[CEREBRO]: MoE indisponível: {e}")

        # Paralelo — execução simultânea de múltiplas tarefas
        try:
            from sirius_paralelo import SiriusParalelo
            self._paralelo = SiriusParalelo()
            print("\033[92m[CEREBRO]: Sistema de execução paralela ativo.\033[0m")
        except Exception as e:
            self._paralelo = None
            print(f"[CEREBRO]: Paralelo indisponível: {e}")

        # Apps externos — Spotify, YouTube, WhatsApp, Discord, navegador, etc.
        try:
            from sirius_apps import SiriusApps
            self._apps = SiriusApps()
            print("\033[92m[CEREBRO]: Controle de aplicativos externos ativo.\033[0m")
        except Exception as e:
            self._apps = None
            print(f"[CEREBRO]: Apps externos indisponível: {e}")

        # Câmera — identificação facial, QR code, expressões
        try:
            from sirius_camera import SiriusCamera
            self._camera = SiriusCamera(
                contas=self._contas,
                callback_troca_conta=self._ao_identificar_rosto
            )
            # Inicia monitor passivo apenas se houver rostos cadastrados
            if self._camera._identificador.tem_rostos:
                self._camera.iniciar_monitor()
            print("\033[92m[CEREBRO]: Sistema de câmera ativo.\033[0m")
        except Exception as e:
            self._camera = None
            print(f"[CEREBRO]: Câmera indisponível: {e}")

    def registrar_callback(self, callback_falar=None, callback_log=None):
        """
        Injeta callbacks de UI.
        Chamado tanto pela interface normal (interface.py) quanto
        pelo papel de parede (sirius_wallpaper.py) após inicializar.
        """
        if callback_falar:
            self._callback_falar = callback_falar
        if callback_log:
            self._callback_log = callback_log
        # Repassa para módulos que precisam falar diretamente
        if hasattr(self, "_proativo") and self._proativo:
            self._proativo.registrar_callback(
                callback_falar=callback_falar,
                callback_log=callback_log
            )
        if hasattr(self, "_paralelo") and self._paralelo:
            self._paralelo.registrar_callbacks(
                callback_log=callback_log,
                callback_falar=callback_falar
            )

    def _registrar_scheduler(self, coordenador, treinador):
        """Chamado pelo main após inicializar coordenador e treinador."""
        if self._scheduler:
            self._scheduler.registrar_coordenador(coordenador)
            self._scheduler.registrar_treinador(treinador)

    def _ao_identificar_rosto(self, conta_id: str, nome: str, confianca: float):
        """
        Callback chamado pelo monitor da câmera quando reconhece um rosto.
        Troca a conta ativa automaticamente e notifica.
        """
        try:
            if self._contas:
                ok, _ = self._contas.trocar_conta(nome)
                if ok:
                    if self._contas.memoria_ativa:
                        self.memoria = self._contas.memoria_ativa
                    self._perfil = self._contas.perfil_ativo
                    msg = f"Reconheci você, {nome}."
                    print(f"\033[94m[CEREBRO]: Câmera → conta trocada para '{nome}' ({confianca:.0%}).\033[0m")
                    if hasattr(self, "_callback_falar") and self._callback_falar:
                        self._callback_falar(msg)
        except Exception as e:
            print(f"[CEREBRO]: Erro ao trocar conta por rosto: {e}")

    def _extrair_comando(self, texto):
        limpo = re.sub(r"[,!\.\s]*sirius[,!\.\s]*", " ", texto).strip()
        return limpo if limpo else None

    def _resposta_rapida(self, texto):
        for gatilho, resposta in RESPOSTAS_RAPIDAS.items():
            if gatilho in texto:
                return resposta
        return None

    def _resposta_rapida_personalizada(self, texto: str) -> str | None:
        """
        Versão personalizada da resposta rápida.
        Usa o nome real do usuário nas saudações.
        """
        resp = self._resposta_rapida(texto)
        if not resp:
            return None
        if self._perfil:
            nome = self._perfil.nome_usuario()
            if nome and nome != "chefia":
                resp = resp.replace("chefia", nome)
        return resp

    def _personalizar_resposta_final(self, resposta: str) -> str:
        """
        Aplica as preferências de estilo do perfil na resposta final.
        Chamado antes de retornar qualquer resposta de conhecimento.
        """
        if not self._perfil or not resposta:
            return resposta
        return self._perfil.personalizar_resposta(resposta)

    def _detectar_comando_demonstracao(self, comando: str):
        """Detecta um comando para executar uma demonstração visual salva."""
        try:
            from sirius_autodidata import listar_demonstracoes_visuais, obter_demonstracao_visual
        except Exception:
            return None

        usuario = self.memoria.user_id if hasattr(self, 'memoria') and self.memoria else 'guest'
        t = _normalizar(comando)

        if any(p in t for p in [
            'listar demonstracoes', 'listar demonstrações', 'minhas demonstrações',
            'meus demos', 'listar demos', 'mostrar demonstrações', 'quais demonstrações',
            'minhas rotinas', 'minhas tarefas', 'listar rotinas', 'listar tarefas'
        ]):
            demos = listar_demonstracoes_visuais(usuario)
            if not demos:
                return 'Ainda não tenho demonstrações salvas para este usuário.'
            nomes = ', '.join(d['nome'] for d in demos)
            return f'Tenho as seguintes demonstrações salvas: {nomes}.'

        match = re.search(
            r'^(?:sirius,\s*)?(?:fa[çc]a|execute|executa|reproduz|reproduza|rode|rodar|segue|abre?)\s+(?:a\s+)?(?:demonstra[cç][aã]o\s+de\s+|demonstra[cç][aã]o\s+|tarefa\s+de\s+|rotina\s+de\s+|macro\s+de\s+)?(.+)$',
            t,
            re.IGNORECASE
        )
        if match:
            nome = match.group(1).strip()
            if not nome:
                return None
            demo = obter_demonstracao_visual(usuario, nome)
            if demo:
                sequencia = []
                try:
                    sequencia = json.loads(demo.get('sequencia_json') or '[]')
                except Exception:
                    sequencia = []
                return {
                    'tipo': 'comando_local',
                    'acao': 'executar_demonstracao',
                    'tarefa': demo.get('nome'),
                    'descricao': demo.get('descricao', ''),
                    'sequencia': sequencia,
                }
        return None

    def _processar_acao_simples(self, comando: str):
        """
        Processa um sub-comando simples sem entrar no loop de detecção paralela.
        Usado pelo SiriusParalelo para evitar recursão infinita.

        Ordem: apps externos → controle PC → agentes → MoE
        """
        # Apps externos primeiro (Spotify, YouTube, WhatsApp, etc.)
        try:
            if self._apps and self._apps.e_comando_app(comando):
                r = self._apps.processar(comando)
                if r: return r
        except Exception:
            pass
        # Controle do PC (abrir/fechar apps, volume, etc.)
        try:
            from controle_pc import _parsear_controle_pc
            r = _parsear_controle_pc(comando, self.control)
            if r: return r
        except Exception:
            pass
        # Agentes (pesquisa, Wikipedia, etc.)
        try:
            if self._agentes:
                r = self._agentes.executar(comando)
                if r and not self._eh_falha(r): return r
        except Exception:
            pass
        # MoE — especialistas de domínio
        try:
            if self._moe:
                r = self._moe.processar(comando, "")
                if r and not self._eh_falha(r): return r
        except Exception:
            pass
        return None

    def _eh_falha(self, texto):
        return any(ind in texto.lower() for ind in INDICADORES_FALHA)

    def _filtrar_resposta_agente(self, resposta: str) -> str | None:
        """
        Filtra respostas cruas dos agentes:
        - Remove prefixos de fonte ([Web], [Wikipedia])
        - Descarta respostas muito curtas ou claramente em ingles sem contexto
        - Encurta para no maximo 400 chars para manter conversa fluida
        """
        if not resposta or len(resposta) < 15:
            return None

        # Remove prefixos de fonte
        import re as _re
        resposta = _re.sub(r"^\[(?:Web|Wikipedia|Fonte)\]\s*", "", resposta).strip()

        # Se resposta e muito longa, pega so o primeiro paragrafo util
        if len(resposta) > 400:
            # Tenta achar primeiro ponto final apos 150 chars
            idx = resposta.find(".", 150)
            if 150 < idx < 400:
                resposta = resposta[:idx + 1].strip()
            else:
                resposta = resposta[:397].strip() + "..."

        # Detecta se e principalmente ingles (heuristica simples)
        # Palavras comuns em ingles que raramente aparecem em pt-BR
        palavras_en = {"the", "is", "are", "was", "were", "has", "have",
                       "this", "that", "with", "from", "they", "their",
                       "which", "also", "been", "will", "would", "could"}
        palavras = set(resposta.lower().split())
        n_en = len(palavras & palavras_en)
        total = len(palavras)
        if total > 5 and n_en / total > 0.18:
            # Mais de 18% das palavras sao ingles — descarta, nao responde em ingles
            print(f"[CEREBRO]: Resposta em ingles descartada ({n_en}/{total} palavras EN)")
            return None

        return resposta

    def _tentar_agentes(self, comando: str) -> str | None:
        """Tenta resolver o comando usando os agentes especializados."""
        if not self._agentes:
            return None
        try:
            resposta = self._agentes.executar(comando)
            return self._filtrar_resposta_agente(resposta)
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

    def _adicionar_contexto(self, role: str, content: str):
        """Adiciona mensagem ao contexto de sessão em RAM."""
        self._contexto_sessao.append({"role": role, "content": content})
        # Mantém apenas as últimas _MAX_CONTEXTO trocas (user+assistant = 2 itens por troca)
        if len(self._contexto_sessao) > self._MAX_CONTEXTO * 2:
            self._contexto_sessao = self._contexto_sessao[-(self._MAX_CONTEXTO * 2):]

    def _contexto_para_texto(self) -> str:
        """Converte o histórico de sessão em texto para dar contexto ao agente."""
        if not self._contexto_sessao:
            return ""
        linhas = []
        for msg in self._contexto_sessao[-10:]:  # últimas 10 mensagens
            prefixo = "Você" if msg["role"] == "user" else "Sirius"
            linhas.append(f"{prefixo}: {msg['content']}")
        return "\n".join(linhas)

    def _e_referencia_contextual(self, texto: str) -> bool:
        """Detecta se a mensagem faz referência ao contexto anterior."""
        t = texto.lower()
        return any(p in t for p in [
            "isso", "esse", "essa", "aquilo", "ele", "ela",
            "o que disse", "o que falou", "antes", "anterior",
            "me explica mais", "mais sobre isso", "continua",
            "e sobre", "e esse", "e ela", "e ele",
        ])

    def tarefa_background(self, fn, nome: str = "",
                           callback=None) -> None:
        """
        Submete uma tarefa para rodar em background sem bloquear a conversa.
        Usado por módulos internos (autodidata, agentes, etc.)

        Exemplo:
            self.tarefa_background(
                fn=lambda: pesquisar_e_aprender(tema),
                nome=f"Pesquisa '{tema}'",
                callback=lambda r: audio.falar("Pesquisa concluída.")
            )
        """
        if self._paralelo:
            self._paralelo.background(fn, nome=nome, callback=callback)
        else:
            # Fallback sem o módulo paralelo — thread daemon simples
            t = threading.Thread(target=fn, daemon=True)
            t.start()

    def registrar_callback_fala(self, callback_falar):
        """Injeta o callback de fala no módulo proativo (chamado pelo main/interface)."""
        if self._proativo:
            self._proativo._falar = callback_falar
        # Paralelo também precisa do callback para notificar quando tarefas terminam
        if self._paralelo:
            self._paralelo.registrar_callbacks(callback_falar=callback_falar)
        self._callback_falar = callback_falar

    def registrar_callback(self, callback_falar=None, callback_log=None):
        """Alias compatível com a assinatura do sirius_proativo.registrar_callback."""
        if self._proativo:
            self._proativo.registrar_callback(
                callback_falar=callback_falar,
                callback_log=callback_log
            )
        if self._paralelo:
            self._paralelo.registrar_callbacks(
                callback_falar=callback_falar,
                callback_log=callback_log
            )

    def processar(self, texto_usuario, forcar_processamento=False, executar_controle_localmente=False):
        if isinstance(texto_usuario, list):
            texto_usuario = texto_usuario[0] if texto_usuario else ""
        texto_lower = str(texto_usuario).lower().strip()

        if not texto_lower:
            return None

        # --- BLOQUEIO DE GRAVAÇÃO DE VOZ ---
        # Durante gravar_amostras(), o usuário fala frases guia que contêm
        # "Sirius". Sem esse bloqueio, essas frases viram comandos normais
        # e geram respostas aleatórias por cima da gravação.
        if self._gravando_voz:
            print(f"\033[90m[CEREBRO]: Descartado (gravando voz): '{texto_lower[:40]}'\033[0m")
            return None

        # Descarta transcrições ruins (eco, repetição, ruído do microfone)
        from collections import Counter as _Counter
        import re as _re
        palavras = texto_lower.split()
        _ruim = False
        if len(palavras) >= 4:
            freq = _Counter(palavras)
            if freq.most_common(1)[0][1] / len(palavras) > 0.5:
                _ruim = True
        sentencas = [s.strip() for s in _re.split(r'[.!?]', texto_lower) if len(s.strip()) > 3]
        if len(sentencas) >= 2 and len(set(sentencas)) < len(sentencas) * 0.6:
            _ruim = True
        if _ruim:
            print(f"\033[90m[CEREBRO]: Descartado (ruido): '{texto_lower[:40]}'\033[0m")
            return None

        if "sirius" in texto_lower:
            comando = self._extrair_comando(texto_lower)
            if not comando:
                return "Diga la, chefia. To ouvindo."
        elif forcar_processamento:
            comando = texto_lower
        else:
            return None

        # Sinaliza atividade ao scheduler e ao monitor de concentração
        if self._scheduler:
            self._scheduler.registrar_atividade()
        if self._proativo and hasattr(self._proativo, "registrar_atividade_usuario"):
            self._proativo.registrar_atividade_usuario()

        # Adiciona ao contexto de sessão
        self._adicionar_contexto("user", comando)

        # --- Confirma ação pendente (desligar, etc.) ---
        if self._acao_pendente:
            if any(p in comando for p in ["sim", "pode", "confirma", "confirmo", "vai", "faz isso", "ok"]):
                fn   = self._acao_pendente.get("fn")
                tipo = self._acao_pendente.get("tipo", "")
                self._acao_pendente = None
                try:
                    resultado = fn() if fn else None
                    from filtro_zoeiro import SiriusFiltro
                    return resultado or SiriusFiltro.formatar_feedback(tipo)
                except Exception as e:
                    return f"Erro ao executar: {e}"
            else:
                # Cancelou
                self._acao_pendente = None
                return "Cancelado."

        # --- CONTAS + VOZ ID (prioridade máxima — antes de qualquer classificador) ---
        # Detector robusto que captura variações de infinitivo e frases completas
        _cmd_n = _normalizar(comando)

        _TRIGGERS_CONTA_DIRETO = {
            # Criar conta
            "cria conta", "criar conta", "nova conta", "crie conta",
            "cria uma conta", "criar uma conta", "adicionar conta",
            "cria conta para", "criar conta para", "nova conta para",
            # Trocar conta / identificação
            "sou eu o", "sou eu a", "eu sou o", "eu sou a",
            "sou o", "sou a",  # "sou o carlos", "sou a maria"
            "entrar como", "logar como", "mudar para", "trocar para",
            "trocar de conta", "mudar de conta",
            # Listar / status
            "listar contas", "lista contas", "ver contas", "quais contas",
            "quem esta usando", "qual conta", "quem sou eu",
            # PIN
            "define meu pin", "meu pin e", "remove meu pin", "tirar pin",
            # Convidado
            "conta convidado", "modo convidado", "entrar como convidado",
        }
        _TRIGGERS_VOZ_DIRETO = {
            # Variações com infinitivo E imperativo
            "registrar minha voz", "registra minha voz",
            "cadastrar minha voz", "cadastra minha voz",
            "gravar minha voz", "grava minha voz",
            "treinar minha voz", "treina minha voz",
            "aprender minha voz", "aprende minha voz",
            "apagar minha voz", "apaga minha voz",
            "remover minha voz", "remove minha voz",
            "deletar minha voz", "deleta minha voz",
            "minha voz", "reconhecimento de voz",
            "status da voz", "voz cadastrada",
        }

        if any(tr in _cmd_n for tr in _TRIGGERS_CONTA_DIRETO):
            if self._contas:
                resp_conta = self._contas.processar_comando(comando)
                if resp_conta:
                    nova_conta = self._contas.conta_ativa
                    self._perfil = self._contas.perfil_ativo
                    if self._contas.memoria_ativa:
                        self.memoria = self._contas.memoria_ativa
                    self._adicionar_contexto("assistant", resp_conta)
                    self.memoria.salvar_historico(comando, resp_conta)
                    return resp_conta

        if any(tr in _cmd_n for tr in _TRIGGERS_VOZ_DIRETO):
            if self._voz_id:
                conta_ativa = self._contas.conta_ativa if self._contas else None
                resp_voz = self._voz_id.processar_comando(
                    comando,
                    conta_ativa=conta_ativa,
                    callback_falar=getattr(self, "_callback_falar", None)
                )
                if resp_voz:
                    self._adicionar_contexto("assistant", resp_voz)
                    self.memoria.salvar_historico(comando, resp_voz)
                    return resp_voz
            else:
                return (
                    "Sistema de contas não está ativo. "
                    "Verifique se sirius_contas.py está na pasta src/."
                )

        # --- CONTAS (prioridade máxima — PIN em andamento ou troca de conta) ---
        if self._contas:
            # Verifica bloqueio por tentativas erradas
            if self._contas._auth.esta_bloqueado():
                s = self._contas._auth.segundos_bloqueado()
                return f"Conta bloqueada por {s}s devido a tentativas erradas de PIN."

            # Fluxo de PIN ativo — qualquer entrada pode ser o PIN
            if self._contas.aguardando_pin:
                ok, resp_pin = self._contas.processar_pin(comando)
                if resp_pin:
                    if ok:
                        self._perfil = self._contas.perfil_ativo
                        if self._contas.memoria_ativa:
                            self.memoria = self._contas.memoria_ativa
                    self._adicionar_contexto("assistant", resp_pin)
                    self.memoria.salvar_historico(comando, resp_pin)
                    return resp_pin

            # Comando de conta normal
            elif self._contas.e_comando_conta(comando):
                resp_conta = self._contas.processar_comando(comando)
                if resp_conta:
                    nova_conta = self._contas.conta_ativa
                    self._perfil = self._contas.perfil_ativo
                    if self._contas.memoria_ativa:
                        self.memoria = self._contas.memoria_ativa
                    self._adicionar_contexto("assistant", resp_conta)
                    self.memoria.salvar_historico(comando, resp_conta)
                    return resp_conta

        # --- VOZ ID — comandos de registro/status de voz ---
        if self._voz_id and self._voz_id.e_comando_voz(comando):
            conta_ativa = self._contas.conta_ativa if self._contas else None
            # callback_travar_cerebro: liga/desliga _gravando_voz durante gravação.
            # Com _gravando_voz=True, processar() descarta tudo sem responder,
            # evitando que as frases guia virem respostas aleatórias.
            def _travar_cerebro(ligar: bool):
                self._gravando_voz = ligar
                estado = "TRAVADO" if ligar else "DESTRAVADO"
                print(f"\033[94m[CEREBRO]: {estado} (gravação de voz).\033[0m")

            resp_voz = self._voz_id.processar_comando(
                comando,
                conta_ativa               = conta_ativa,
                callback_falar            = getattr(self, "_callback_falar", None),
                callback_travar_cerebro   = _travar_cerebro
            )
            if resp_voz:
                self._adicionar_contexto("assistant", resp_voz)
                self.memoria.salvar_historico(comando, resp_voz)
                return resp_voz

        # --- PERFIL (alta prioridade) ---
        if self._perfil and self._perfil.e_comando_perfil(comando):
            resp_perfil = self._perfil.processar_comando(comando)
            if resp_perfil:
                self._adicionar_contexto("assistant", resp_perfil)
                self.memoria.salvar_historico(comando, resp_perfil)
                return resp_perfil

        # Registra uso para aprender preferências implícitas
        if self._perfil:
            self._perfil.registrar_uso(comando)

        # Resposta rápida instantânea — personaliza com nome do usuário
        resp_rapida = self._resposta_rapida_personalizada(comando)
        if resp_rapida:
            self._adicionar_contexto("assistant", resp_rapida)
            self.memoria.salvar_historico(comando, resp_rapida)
            return resp_rapida

        # --- TEMPO REAL (hora / clima) — antes do classificador ---
        try:
            from sirius_tempo_real import processar_tempo_real
            resp_tempo = processar_tempo_real(comando)
            if resp_tempo:
                self._adicionar_contexto("assistant", resp_tempo)
                self.memoria.salvar_historico(comando, resp_tempo)
                return resp_tempo
        except Exception as e:
            print("[CEREBRO]: sirius_tempo_real falhou: {}".format(e))

        # --- PROATIVO (lembretes) ---
        if self._proativo and self._proativo.e_comando_proativo(comando):
            resp_proativo = self._proativo.processar_comando(comando)
            if resp_proativo:
                self._adicionar_contexto("assistant", resp_proativo)
                self.memoria.salvar_historico(comando, resp_proativo)
                return resp_proativo

        # --- CÂMERA — identificação facial, QR code, expressões ---
        # Interceptado antes do classificador para que "quem está na minha frente"
        # não vire pesquisa de conhecimento
        if self._camera and self._camera.e_comando_camera(comando):
            conta_ativa = self._contas.conta_ativa if self._contas else None
            resp_cam    = self._camera.processar_comando(
                comando,
                conta_ativa    = conta_ativa,
                callback_falar = getattr(self, "_callback_falar", None)
            )
            if resp_cam:
                self._adicionar_contexto("assistant", resp_cam)
                self.memoria.salvar_historico(comando, resp_cam)
                return resp_cam

        # --- PARALELO — status/cancelamento de tarefas ---
        if self._paralelo and self._paralelo.e_comando_paralelo(comando):
            resp_par = self._paralelo.processar_comando(comando)
            if resp_par:
                self._adicionar_contexto("assistant", resp_par)
                self.memoria.salvar_historico(comando, resp_par)
                return resp_par

        # --- PARALELO — detecta se o comando pede múltiplas ações simultâneas ---
        # Exemplos: "abre o chrome e o spotify ao mesmo tempo"
        #           "pesquisa python e depois abre o vscode"
        if self._paralelo and self._paralelo.detectar_paralelismo(comando):
            resp_par = self._paralelo.processar_paralelo(comando, self)
            if resp_par:
                self._adicionar_contexto("assistant", resp_par)
                self.memoria.salvar_historico(comando, resp_par)
                return resp_par

        # --- APPS EXTERNOS (Spotify, YouTube, WhatsApp, Discord...) ---
        # Verificado antes do classificador para que "toca uma música"
        # não caia no parser de controle de PC
        if self._apps and self._apps.e_comando_app(comando):
            resp_app = self._apps.processar(comando)
            if resp_app:
                self._adicionar_contexto("assistant", resp_app)
                self.memoria.salvar_historico(comando, resp_app)
                return resp_app

        print("[CEREBRO]: Classificando: '{}'".format(comando))
        intencao = _classificar_intencao(comando, self.neuronio)
        print("[CEREBRO]: Intencao -> {}".format(intencao))

        # --- Retreino ---
        if intencao == "treinar":
            self.tarefa_background(
                fn=lambda: __import__("sirius_treinador").SiriusTreinador().treinar_tudo(),
                nome="Treino das redes neurais",
                callback=lambda _: (
                    self._callback_falar("Treino concluído, chefe.")
                    if hasattr(self, "_callback_falar") and self._callback_falar else None
                )
            )
            return "Iniciando evolução do cérebro. Vou avisar quando terminar."

        # --- PERFIL E CONTAS (comandos de status) ---
        cmd_norm = _normalizar(comando)
        if any(p in cmd_norm for p in [
            "status do perfil", "meu perfil", "ver perfil",
            "minhas preferencias", "minhas preferências",
        ]):
            if self._perfil:
                return self._perfil._formatar_perfil()

        if any(p in cmd_norm for p in [
            "status das contas", "listar contas", "lista contas",
            "quais contas existem", "ver contas", "status contas",
        ]):
            if self._contas:
                return self._contas._listar_contas()
            return "Sistema de contas não disponível."

        # --- Arquivo mencionado? ---
        resposta_arquivo = self._tentar_ler_arquivo(comando)
        if resposta_arquivo:
            self._adicionar_contexto("assistant", resposta_arquivo)
            self.memoria.salvar_historico(comando, resposta_arquivo)
            return resposta_arquivo

        # --- Enriquece o comando com contexto se for referência ---
        # Ex: "e esse?" após "que e pokemon" → "e esse pokemon?"
        if self._e_referencia_contextual(comando) and self._contexto_sessao:
            ctx = self._contexto_para_texto()
            comando_enriquecido = f"[Contexto da conversa:\n{ctx}\n]\n{comando}"
        else:
            comando_enriquecido = comando

        # --- Controle do PC (acao) ---
        if intencao == "acao":
            if not executar_controle_localmente:
                comando_demonstracao = self._detectar_comando_demonstracao(comando)
                if comando_demonstracao:
                    if isinstance(comando_demonstracao, dict):
                        self._adicionar_contexto("assistant", "[COMANDO_LOCAL]")
                        self.memoria.salvar_historico(comando, "[COMANDO_LOCAL]")
                        self.memoria.salvar_amostra_treino(comando, "[COMANDO_LOCAL]")
                        return comando_demonstracao
                    return comando_demonstracao

                comando_local = _detectar_comando_local_json(comando)
                if comando_local:
                    self._adicionar_contexto("assistant", "[COMANDO_LOCAL]")
                    self.memoria.salvar_historico(comando, "[COMANDO_LOCAL]")
                    self.memoria.salvar_amostra_treino(comando, "[COMANDO_LOCAL]")
                    return comando_local

            resposta = _parsear_controle_pc(comando, self.control)

            # Intercepta tokens de confirmação — salva ação pendente
            if resposta and resposta.startswith("CONFIRMAR_DESLIGAR:"):
                self._acao_pendente = {
                    "tipo": "desligamento",
                    "fn":   lambda: self.control.gerenciar_energia("desligar")
                }
                resposta = resposta[len("CONFIRMAR_DESLIGAR:"):]

            if resposta is None:
                resposta = self._tentar_agentes(comando)

            if resposta is None:
                resposta = (
                    "Entendi que voce quer fazer algo, mas nao consegui identificar o que. "
                    "Exemplos: 'abre o chrome', 'le o arquivo doc.pdf', "
                    "'manda mensagem para Joao no discord falando oi'."
                )
            self._adicionar_contexto("assistant", resposta)
            self.memoria.salvar_historico(comando, resposta)
            self.memoria.salvar_amostra_treino(comando, resposta)
            return resposta

        # --- MoE — tenta especialista hierárquico antes dos agentes genéricos ---
        if self._moe:
            ctx_texto = self._contexto_para_texto()
            resp_moe  = self._moe.processar(comando_enriquecido, ctx_texto)
            if resp_moe and not self._eh_falha(resp_moe):
                resp_final = self.filtro.aplicar_zoeira(resp_moe)
                self._adicionar_contexto("assistant", resp_final)
                self.memoria.salvar_historico(comando, resp_final)
                self.memoria.salvar_amostra_treino(comando, resp_moe)
                return resp_final

        # --- Agentes para conhecimento ---
        resposta_agente = self._tentar_agentes(comando_enriquecido)
        if resposta_agente and not self._eh_falha(resposta_agente):
            resposta_final = self.filtro.aplicar_zoeira(resposta_agente)
            self._adicionar_contexto("assistant", resposta_final)
            self.memoria.salvar_historico(comando, resposta_final)
            self.memoria.salvar_amostra_treino(comando, resposta_agente)
            return resposta_final

        # --- Conhecimento / conversa (gerador + embeddings) ---
        resposta_ia = _responder_conhecimento(comando_enriquecido, self.memoria)

        if resposta_ia and not self._eh_falha(resposta_ia):
            resposta_zoeira = self.filtro.aplicar_zoeira(resposta_ia)
            # Aplica estilo do perfil (curto/normal/detalhado, sem gírias etc.)
            resposta_final = self._personalizar_resposta_final(resposta_zoeira)
        else:
            _nao_estudavel = any(p in comando for p in [
                "que horas", "que dia", "qual data", "tudo bem", "como voce",
                "bom dia", "boa tarde", "boa noite", "tchau", "valeu", "flw",
            ]) or any(comando.startswith(v) for v in [
                "abre ", "abrir ", "fecha ", "manda ", "mande ", "envia ",
                "cria ", "desliga ", "executa ", "liga ", "mostra ", "volume",
            ])
            if not _nao_estudavel:
                self.memoria.adicionar_duvida(comando)
                if self._agentes:
                    _tema = comando
                    self.tarefa_background(
                        fn=lambda: self._agentes.pesquisar_e_aprender(_tema),
                        nome=f"Pesquisa: {_tema[:30]}"
                    )
            resposta_final = (
                "Não tenho essa informação ainda. Já anotei para pesquisar."
            )

        self._adicionar_contexto("assistant", resposta_final)
        self.memoria.salvar_historico(comando, resposta_final)
        if resposta_ia:
            self.memoria.salvar_amostra_treino(comando, resposta_ia)

        return resposta_final