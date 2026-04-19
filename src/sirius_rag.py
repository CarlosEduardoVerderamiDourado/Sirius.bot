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

def _responder_conhecimento(texto, memoria, rag=None):
    """
    Cascata de respostas — da mais confiável para a menos:

    1. RAG local (FAISS) — busca vetorial no banco acumulado
       Funciona offline, melhora com o tempo, nao alucina.
       Ativado quando score >= 0.45 para respostas de qualidade.

    2. AgentePesquisador (Wikipedia + DDG) — resposta real da internet
       Usado quando o RAG nao tem confianca suficiente ou banco pequeno.

    3. Embeddings Word2Vec + historico — busca semantica no historico
       Fallback quando agentes falham.

    4. SiriusGerador (GRU seq2seq) — ultimo recurso
       Usado apenas com 300+ conversas para evitar geracao de lixo.
    """

    # 1. RAG local — prioridade maxima quando disponivel e confiante
    # Retorna apenas se score alto o suficiente (evita respostas irrelevantes)
    if rag is not None:
        try:
            resp_rag = rag.responder(texto, qualidade_min=0.4)
            if resp_rag and len(resp_rag) > 40:
                print("[CEREBRO]: RAG respondeu com confianca.")
                return resp_rag
        except Exception as e:
            print("[CEREBRO]: RAG falhou: {}".format(e))

    # 2. AgentePesquisador — Wikipedia PT > Wikipedia EN > DuckDuckGo
    # Usado quando RAG nao tem material suficiente sobre o tema
    try:
        from sirius_agentes import AgentePesquisador
        pesquisador = AgentePesquisador(memoria)
        resultado   = pesquisador.executar(texto)
        if resultado and len(resultado) > 40 and "nao encontrei" not in resultado.lower():
            # Salva no banco para que o RAG aprenda para a proxima vez
            if memoria:
                try:
                    memoria.salvar_estudo_autonomo(
                        tema=texto[:80],
                        conteudo=resultado,
                        tags="agente_pesquisador"
                    )
                except Exception:
                    pass
            return resultado
    except Exception as e:
        print("[CEREBRO]: AgentePesquisador falhou: {}".format(e))

    # 3. Busca semantica no historico (embeddings Word2Vec)
    try:
        embeddings = _get_embeddings()
        if embeddings.esta_treinado():
            historico = memoria.obter_historico_db(limit=50)
            respostas = [
                cont for role, cont in historico
                if role == "assistant" and len(cont) > 20
                and cont.count("mano") < 3
                and "motor local ta fora" not in cont
                and "ainda nao sei responder" not in cont
            ]
            if respostas:
                similar = embeddings.buscar_mais_similar(texto, respostas)
                if similar and len(similar) > 20:
                    return similar
    except Exception as e:
        print("[CEREBRO]: Busca semantica falhou: {}".format(e))

    # 4. SiriusGerador — ultimo recurso, so com dados suficientes
    try:
        import sqlite3, os as _os
        db = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "data", "sirius_pessoal.db")
        conn = sqlite3.connect(db)
        n_conversas = conn.execute("SELECT COUNT(*) FROM conversas").fetchone()[0]
        conn.close()

        if n_conversas >= 300:
            gerador = _get_gerador()
            if gerador.esta_treinado():
                resposta = gerador.gerar(texto)
                if resposta and len(resposta) > 20:
                    palavras = resposta.split()
                    from collections import Counter
                    freq = Counter(palavras)
                    mais_freq = freq.most_common(1)[0][1] if palavras else 0
                    if mais_freq / max(len(palavras), 1) < 0.3:
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
        self._rag        = None   # RAG local — busca vetorial FAISS

        # --- Contexto de sessão em RAM (últimas N trocas) ---
        self._contexto_sessao: list[dict] = []
        self._MAX_CONTEXTO = 15

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

        # RAG — busca vetorial FAISS (substitui o gerador fraco)
        # Inicializa em background para nao travar a startup
        try:
            from sirius_rag import SiriusRAG
            self._rag = SiriusRAG(memoria=self.memoria)
            print("\033[92m[CEREBRO]: RAG local iniciado.\033[0m")
        except Exception as e:
            self._rag = None
            print(f"[CEREBRO]: RAG indisponível (pip install faiss-cpu): {e}")

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

    def _processar_feedback(self, comando: str) -> str | None:
        """
        Detecta quando o usuario corrige o Sirius e salva no RAG com qualidade 1.0.

        Formatos reconhecidos:
          "isso ta errado, a resposta certa e: X"
          "errou, na verdade e: X"
          "nao e isso, e: X"
          "corrige isso: X"
          "aprende isso: X"
          "memoriza que X"
        """
        t = comando.lower().strip()

        _TRIGGERS_CORRECAO = [
            "ta errado", "esta errado", "errou",
            "nao e isso", "na verdade e", "na verdade:",
            "corrige isso", "aprende isso", "aprende que",
            "memoriza que", "memoriza isso",
            "a resposta certa e", "a resposta correta e",
            "nao e assim", "e assim que funciona",
        ]

        _SEPARADORES = [":", "que", "e que", "seria", ", o correto e"]

        for trigger in _TRIGGERS_CORRECAO:
            if trigger in t:
                # Extrai a parte correta apos o trigger ou separador
                resto = t
                # Remove o trigger
                idx_trigger = t.find(trigger)
                resto = t[idx_trigger + len(trigger):].strip()

                # Remove separadores do inicio
                for sep in [",", ".", " e ", " e:", ":", " é", " que"]:
                    if resto.startswith(sep):
                        resto = resto[len(sep):].strip()

                if len(resto) > 10:
                    # Recupera o ultimo comando do contexto (o que estava errado)
                    pergunta_original = ""
                    if self._contexto_sessao:
                        for msg in reversed(self._contexto_sessao):
                            if msg["role"] == "user" and msg["content"] != comando:
                                pergunta_original = msg["content"]
                                break

                    # Salva no RAG com qualidade maxima
                    if self._rag:
                        self._rag.adicionar_feedback(
                            pergunta=pergunta_original or comando,
                            resposta_correta=resto
                        )

                    # Salva no banco direto tambem
                    self.memoria.salvar_estudo_autonomo(
                        tema=pergunta_original[:80] or "correcao",
                        conteudo=resto,
                        tags="feedback_usuario_qualidade_1"
                    )

                    print(f"[CEREBRO]: Feedback salvo: '{resto[:50]}'")
                    return f"Anotado, chefia! Aprendi que: {resto[:200]}."

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

    def registrar_callback_fala(self, callback_falar):
        """Injeta o callback de fala no módulo proativo (chamado pelo main/interface)."""
        if self._proativo:
            self._proativo._falar = callback_falar

    def registrar_callback(self, callback_falar=None, callback_log=None):
        """Alias compatível com a assinatura do sirius_proativo.registrar_callback."""
        if self._proativo:
            self._proativo.registrar_callback(
                callback_falar=callback_falar,
                callback_log=callback_log
            )

    def processar(self, texto_usuario, forcar_processamento=False):
        if isinstance(texto_usuario, list):
            texto_usuario = texto_usuario[0] if texto_usuario else ""
        texto_lower = str(texto_usuario).lower().strip()

        if not texto_lower:
            return None

        # Descarta transcrições ruins (eco, repetição, ruído do microfone)
        from collections import Counter as _Counter
        import re as _re
        palavras = texto_lower.split()

        # Muito curto pós wake word — provavelmente ruído
        if len(palavras) < 2:
            print(f"\033[90m[CEREBRO]: Descartado (muito curto): '{texto_lower}'\033[0m")
            return None

        _ruim = False

        # Mais de 50% palavras iguais = eco
        if len(palavras) >= 4:
            freq = _Counter(palavras)
            if freq.most_common(1)[0][1] / len(palavras) > 0.5:
                _ruim = True

        # Frases repetidas = eco do TTS
        sentencas = [s.strip() for s in _re.split(r'[.!?]', texto_lower) if len(s.strip()) > 3]
        if len(sentencas) >= 2 and len(set(sentencas)) < len(sentencas) * 0.6:
            _ruim = True

        # Texto sem vogais suficientes = lixo de transcrição
        n_vogais = sum(1 for c in texto_lower if c in "aeiouáéíóúãõ")
        if len(texto_lower) > 10 and n_vogais / len(texto_lower.replace(" ", "")) < 0.2:
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

        # Sinaliza atividade ao scheduler
        if self._scheduler:
            self._scheduler.registrar_atividade()

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

        # --- FEEDBACK DE CORRECAO (alta prioridade) ---
        # "Sirius, isso ta errado. A resposta certa e: X"
        # Salva no RAG com qualidade 1.0 — ensina o Sirius
        resp_feedback = self._processar_feedback(comando)
        if resp_feedback:
            self._adicionar_contexto("assistant", resp_feedback)
            self.memoria.salvar_historico(comando, resp_feedback)
            return resp_feedback

        # Resposta rápida instantânea
        resp_rapida = self._resposta_rapida(comando)
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

        # --- Rebuild do RAG manual ---
        if any(p in _normalizar(comando) for p in [
            "rebuilda o rag", "reconstroi o rag", "rebuild rag",
            "atualiza o rag", "reconstroi meu banco",
            "atualiza minha memoria", "indexa o banco"
        ]):
            if self._rag:
                def _rebuild():
                    ok = self._rag.rebuild()
                    print("[CEREBRO]: RAG rebuild {}".format("OK" if ok else "falhou"))
                threading.Thread(target=_rebuild, daemon=True).start()
                s = self._rag.status()
                return (f"Rebuilding o RAG com {s['docs_no_banco']} documentos. "
                        f"Fica pronto em alguns segundos.")
            return "RAG nao disponivel. Instale: pip install faiss-cpu"

        # --- Status do RAG ---
        if any(p in _normalizar(comando) for p in [
            "status do rag", "status rag", "quantos documentos", "quantos docs",
            "tamanho do banco rag", "rag ta funcionando"
        ]):
            if self._rag:
                s = self._rag.status()
                return (
                    f"RAG: {'ativo' if s['indice_construido'] else 'sem indice'}. "
                    f"{s['docs_no_indice']} docs indexados / {s['docs_no_banco']} no banco. "
                    f"{'Indice desatualizado — rebuilda logo.' if s['indice_desatualizado'] else 'Indice atualizado.'}"
                )
            return "RAG nao disponivel."

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

        # --- Conhecimento / conversa (RAG + agentes + gerador) ---
        resposta_ia = _responder_conhecimento(
            comando_enriquecido, self.memoria, rag=self._rag
        )

        if resposta_ia and not self._eh_falha(resposta_ia):
            resposta_final = self.filtro.aplicar_zoeira(resposta_ia)
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
                    threading.Thread(
                        target=self._agentes.pesquisar_e_aprender,
                        args=(comando,),
                        daemon=True
                    ).start()
            resposta_final = (
                "Mano, ainda nao sei responder isso bem. "
                "Ja anotei e vou pesquisar mais sobre isso. "
                "Me faz essa pergunta de novo daqui a pouco!"
            )

        self._adicionar_contexto("assistant", resposta_final)
        self.memoria.salvar_historico(comando, resposta_final)
        if resposta_ia:
            self.memoria.salvar_amostra_treino(comando, resposta_ia)

        return resposta_final