"""
sirius_tempo_real.py — Hora local e clima em tempo real para o Sirius

Funcionalidades:
  - Hora e data atual do sistema (sem internet)
  - Clima atual por cidade via wttr.in (gratuito, sem API key)
  - Previsao para hoje / amanha / proximos dias
  - Deteccao automatica de cidade por IP (fallback)
  - Cache de 10min para nao fazer requests repetidos

Comandos reconhecidos pelo cerebro:
  "que horas sao"          -> hora atual
  "que dia e hoje"         -> data completa
  "qual o clima em SP"     -> clima atual em Sao Paulo
  "vai chover hoje"        -> previsao de chuva
  "temperatura agora"      -> temperatura atual
  "previsao para amanha"   -> previsao do dia seguinte
"""

import os
import re
import time
import json
import threading
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Cache simples (evita requests repetidos em menos de 10min)
# ---------------------------------------------------------------------------

_cache = {}            # chave -> (timestamp, valor)
_CACHE_TTL = 600       # 10 minutos em segundos
_lock_cache = threading.Lock()


def _cache_get(chave):
    with _lock_cache:
        entrada = _cache.get(chave)
        if entrada and (time.time() - entrada[0]) < _CACHE_TTL:
            return entrada[1]
    return None


def _cache_set(chave, valor):
    with _lock_cache:
        _cache[chave] = (time.time(), valor)


# ---------------------------------------------------------------------------
# Hora e data local (sem internet, sempre funciona)
# ---------------------------------------------------------------------------

DIAS_SEMANA = [
    "segunda-feira", "terca-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sabado", "domingo"
]

MESES = [
    "", "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"
]


def obter_hora_atual() -> str:
    """Retorna a hora atual formatada em portugues."""
    agora    = datetime.now()
    hora     = agora.strftime("%H:%M")
    dia_sem  = DIAS_SEMANA[agora.weekday()]
    dia      = agora.day
    mes      = MESES[agora.month]
    ano      = agora.year

    # Saudacao baseada no periodo do dia
    h = agora.hour
    if 5 <= h < 12:
        saudacao = "Bom dia"
    elif 12 <= h < 18:
        saudacao = "Boa tarde"
    else:
        saudacao = "Boa noite"

    return (
        "{}, chefia! Sao exatamente {} de {}, {} de {} de {}."
    ).format(saudacao, hora, dia_sem, dia, mes, ano)


def obter_data_atual() -> str:
    """Retorna a data completa de hoje."""
    agora   = datetime.now()
    dia_sem = DIAS_SEMANA[agora.weekday()]
    dia     = agora.day
    mes     = MESES[agora.month]
    ano     = agora.year
    return "Hoje e {}, {} de {} de {}.".format(dia_sem, dia, mes, ano)


def obter_data_e_hora() -> str:
    """Retorna data e hora juntos."""
    agora   = datetime.now()
    hora    = agora.strftime("%H:%M")
    dia_sem = DIAS_SEMANA[agora.weekday()]
    dia     = agora.day
    mes     = MESES[agora.month]
    ano     = agora.year
    return "{}, {} de {} de {} — {}".format(dia_sem, dia, mes, ano, hora)


# ---------------------------------------------------------------------------
# Deteccao de cidade por IP (fallback quando usuario nao especifica cidade)
# ---------------------------------------------------------------------------

_cidade_padrao = None  # cache da cidade detectada por IP


def _detectar_cidade_por_ip() -> str:
    """Detecta a cidade aproximada pelo IP do usuario. Usa ip-api.com (gratuito)."""
    global _cidade_padrao
    if _cidade_padrao:
        return _cidade_padrao

    chave = "cidade_ip"
    cached = _cache_get(chave)
    if cached:
        _cidade_padrao = cached
        return cached

    try:
        import urllib.request
        url  = "http://ip-api.com/json/?fields=city,regionName,country&lang=pt-BR"
        req  = urllib.request.Request(url, headers={"User-Agent": "Sirius/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            dados = json.loads(r.read().decode())
        cidade = dados.get("city", "")
        if cidade:
            _cache_set(chave, cidade)
            _cidade_padrao = cidade
            print("[TEMPO REAL]: Cidade detectada por IP: {}".format(cidade))
            return cidade
    except Exception as e:
        print("[TEMPO REAL]: Falha ao detectar cidade por IP: {}".format(e))

    return "Sao Paulo"  # fallback final


# ---------------------------------------------------------------------------
# Clima via wttr.in (gratuito, sem API key, suporta portugues)
# ---------------------------------------------------------------------------

# Condicoes meteorologicas em portugues
_CONDICOES_PT = {
    "Clear":              "ceu limpo",
    "Sunny":              "ensolarado",
    "Partly cloudy":      "parcialmente nublado",
    "Cloudy":             "nublado",
    "Overcast":           "encoberto",
    "Mist":               "neblina",
    "Fog":                "neblina densa",
    "Light rain":         "chuva fraca",
    "Moderate rain":      "chuva moderada",
    "Heavy rain":         "chuva forte",
    "Light drizzle":      "garoa",
    "Drizzle":            "garoa",
    "Thundery outbreaks": "trovoadas",
    "Thunderstorm":       "tempestade",
    "Blizzard":           "nevasca",
    "Light snow":         "neve leve",
    "Snow":               "neve",
    "Sleet":              "chuva com neve",
    "Patchy rain":        "chuva irregular",
    "Patchy snow":        "neve irregular",
    "Freezing drizzle":   "garoa gelada",
    "Ice pellets":        "granizo",
    "Blowing snow":       "neve com vento",
}


def _traduzir_condicao(condicao_en: str) -> str:
    """Traduz condicao meteorologica para portugues."""
    for en, pt in _CONDICOES_PT.items():
        if en.lower() in condicao_en.lower():
            return pt
    return condicao_en  # retorna original se nao achar


def _buscar_clima_wttr(cidade: str) -> dict | None:
    """
    Busca clima atual e previsao via wttr.in em formato JSON.
    Retorna dict com dados ou None se falhar.
    """
    chave  = "clima_{}".format(cidade.lower().replace(" ", "_"))
    cached = _cache_get(chave)
    if cached:
        return cached

    try:
        import urllib.request
        import urllib.parse
        cidade_enc = urllib.parse.quote(cidade)
        url = "https://wttr.in/{}?format=j1&lang=pt".format(cidade_enc)
        req = urllib.request.Request(url, headers={"User-Agent": "Sirius/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            dados = json.loads(r.read().decode("utf-8"))
        _cache_set(chave, dados)
        return dados
    except Exception as e:
        print("[TEMPO REAL]: Falha ao buscar clima para '{}': {}".format(cidade, e))
        return None


def _extrair_cidade_do_texto(texto: str) -> str | None:
    """
    Extrai o nome da cidade do texto do usuario.
    Cobre: "clima em SP", "tempo em Sao Paulo", "temperatura no Rio"
    """
    t = texto.lower()

    # Preposicoes que antecedem cidade
    padroes = [
        r"(?:em|no|na|de|para|sobre)\s+([a-zA-ZÀ-ú][a-zA-ZÀ-ú\s]{1,30}?)(?:\s*$|\s*(?:agora|hoje|amanha|essa semana|proximos))",
        r"(?:em|no|na)\s+([a-zA-ZÀ-ú][a-zA-ZÀ-ú\s]{1,30})",
    ]
    for p in padroes:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            cidade = m.group(1).strip()
            # Filtra palavras que nao sao cidades
            if cidade.lower() not in {"hoje", "amanha", "agora", "semana", "mes"}:
                return cidade

    # Siglas de estados brasileiros
    siglas = {
        "sp": "Sao Paulo", "rj": "Rio de Janeiro", "mg": "Belo Horizonte",
        "ba": "Salvador", "rs": "Porto Alegre", "pr": "Curitiba",
        "pe": "Recife", "ce": "Fortaleza", "go": "Goiania",
        "am": "Manaus", "pa": "Belem", "sc": "Florianopolis",
        "es": "Vitoria", "mt": "Cuiaba", "ms": "Campo Grande",
        "df": "Brasilia", "al": "Maceio", "rn": "Natal",
        "pb": "Joao Pessoa", "se": "Aracaju", "pi": "Teresina",
        "ma": "Sao Luis", "to": "Palmas", "ro": "Porto Velho",
        "rr": "Boa Vista", "ac": "Rio Branco", "ap": "Macapa",
    }
    for sigla, cidade in siglas.items():
        if re.search(r'\b' + sigla + r'\b', t):
            return cidade

    return None


# ---------------------------------------------------------------------------
# Funcoes publicas de clima
# ---------------------------------------------------------------------------

def obter_clima_atual(cidade: str = None) -> str:
    """
    Retorna o clima atual da cidade especificada.
    Se cidade=None, detecta pelo IP.
    """
    if not cidade:
        cidade = _detectar_cidade_por_ip()

    dados = _buscar_clima_wttr(cidade)
    if not dados:
        return "Nao consegui acessar o servico de clima agora. Tenta de novo daqui a pouco."

    try:
        atual    = dados["current_condition"][0]
        temp_c   = atual["temp_C"]
        sensacao = atual["FeelsLikeC"]
        umidade  = atual["humidity"]
        vento    = atual["windspeedKmph"]
        condicao = _traduzir_condicao(atual["weatherDesc"][0]["value"])

        # Chance de chuva (pega do primeiro periodo do dia)
        chuva_chance = "?"
        try:
            chuva_chance = dados["weather"][0]["hourly"][0]["chanceofrain"]
        except Exception:
            pass

        return (
            "Em {}: {} e {}\u00b0C (sensacao de {}\u00b0C). "
            "Umidade {}%, vento {} km/h. "
            "Chance de chuva: {}%."
        ).format(cidade, condicao, temp_c, sensacao, umidade, vento, chuva_chance)

    except (KeyError, IndexError) as e:
        return "Recebi dados do clima mas nao consegui interpretar. Erro: {}".format(e)


def obter_previsao(cidade: str = None, dias: int = 1) -> str:
    """
    Retorna a previsao para os proximos N dias.
    dias=0 = hoje, dias=1 = amanha, dias=2/3 = proximos dias.
    """
    if not cidade:
        cidade = _detectar_cidade_por_ip()

    dados = _buscar_clima_wttr(cidade)
    if not dados:
        return "Nao consegui acessar a previsao do tempo agora."

    try:
        weather = dados["weather"]
        if dias >= len(weather):
            dias = len(weather) - 1

        dia_dados = weather[dias]
        data_str  = dia_dados["date"]           # YYYY-MM-DD
        max_temp  = dia_dados["maxtempC"]
        min_temp  = dia_dados["mintempC"]

        # Condicao predominante (pega do periodo do meio do dia)
        periodo   = dia_dados["hourly"][len(dia_dados["hourly"]) // 2]
        condicao  = _traduzir_condicao(periodo["weatherDesc"][0]["value"])
        chuva     = periodo["chanceofrain"]
        vento     = periodo["windspeedKmph"]
        umidade   = periodo["humidity"]

        # Formata a data
        try:
            dt      = datetime.strptime(data_str, "%Y-%m-%d")
            dia_sem = DIAS_SEMANA[dt.weekday()]
            dia_num = dt.day
            mes     = MESES[dt.month]
            data_fmt = "{}, {} de {}".format(dia_sem, dia_num, mes)
        except Exception:
            data_fmt = data_str

        if dias == 0:
            prefixo = "Hoje"
        elif dias == 1:
            prefixo = "Amanha"
        else:
            prefixo = data_fmt

        return (
            "{} em {}: {} com maxima de {}\u00b0C e minima de {}\u00b0C. "
            "Chance de chuva: {}%, vento {} km/h, umidade {}%."
        ).format(prefixo, cidade, condicao, max_temp, min_temp, chuva, vento, umidade)

    except (KeyError, IndexError) as e:
        return "Nao consegui interpretar a previsao. Erro: {}".format(e)


def vai_chover(cidade: str = None) -> str:
    """Responde diretamente se vai chover hoje/amanha."""
    if not cidade:
        cidade = _detectar_cidade_por_ip()

    dados = _buscar_clima_wttr(cidade)
    if not dados:
        return "Nao consigo verificar a previsao de chuva agora."

    try:
        # Pega chance de chuva para hoje e amanha
        hoje   = dados["weather"][0]
        amanha = dados["weather"][1] if len(dados["weather"]) > 1 else None

        # Maior chance de chuva durante o dia de hoje
        chances_hoje  = [int(h["chanceofrain"]) for h in hoje["hourly"]]
        max_hoje      = max(chances_hoje)
        condicao_hoje = _traduzir_condicao(hoje["hourly"][len(hoje["hourly"]) // 2]["weatherDesc"][0]["value"])

        if max_hoje >= 70:
            resp_hoje = "Sim, tem boa chance de chover em {} hoje ({}% de probabilidade, {}).".format(
                cidade, max_hoje, condicao_hoje)
        elif max_hoje >= 40:
            resp_hoje = "Pode chover em {} hoje ({}% de chance), fique de olho.".format(cidade, max_hoje)
        else:
            resp_hoje = "Nao deve chover em {} hoje (apenas {}% de chance, {}).".format(
                cidade, max_hoje, condicao_hoje)

        if amanha:
            chances_amanha = [int(h["chanceofrain"]) for h in amanha["hourly"]]
            max_amanha     = max(chances_amanha)
            if max_amanha >= 70:
                resp_hoje += " Amanha tem previsao de chuva forte ({}%).".format(max_amanha)
            elif max_amanha >= 40:
                resp_hoje += " Amanha tambem pode chover ({}%).".format(max_amanha)

        return resp_hoje

    except (KeyError, IndexError) as e:
        return "Nao consegui verificar a previsao de chuva. Erro: {}".format(e)


def obter_temperatura(cidade: str = None) -> str:
    """Retorna apenas a temperatura atual de forma direta."""
    if not cidade:
        cidade = _detectar_cidade_por_ip()

    dados = _buscar_clima_wttr(cidade)
    if not dados:
        return "Nao consegui a temperatura agora."

    try:
        atual    = dados["current_condition"][0]
        temp_c   = atual["temp_C"]
        sensacao = atual["FeelsLikeC"]
        condicao = _traduzir_condicao(atual["weatherDesc"][0]["value"])
        return "Em {} esta {}\u00b0C (sensacao de {}\u00b0C), {}.".format(
            cidade, temp_c, sensacao, condicao)
    except Exception as e:
        return "Erro ao obter temperatura: {}".format(e)


# ---------------------------------------------------------------------------
# Parser principal — detecta intencao e chama a funcao certa
# ---------------------------------------------------------------------------

# Palavras-chave que ativam esta funcao
TRIGGERS_HORA = {
    "que horas", "que hora", "horas sao", "hora e", "hora sao",
    "que horas e", "me diz a hora", "me fala a hora", "hora atual",
    "horas agora"
}

TRIGGERS_DATA = {
    "que dia", "qual dia", "que data", "qual data", "dia e hoje",
    "dia de hoje", "data de hoje", "data atual", "que mes",
    "qual mes", "que ano", "qual ano"
}

TRIGGERS_CLIMA = {
    "clima", "tempo", "temperatura", "chuva", "vai chover",
    "chover hoje", "chover amanha", "previsao", "previsão",
    "como esta o tempo", "como ta o tempo", "faz frio",
    "faz calor", "esta frio", "esta quente", "ta frio", "ta quente",
    "graus", "celsius", "umidade", "vento", "tempestade",
    "nublado", "sol hoje", "sol amanha"
}


def _e_pergunta_tempo_real(texto: str) -> bool:
    """Retorna True se o texto parece ser uma pergunta de hora/clima."""
    t = texto.lower()
    return (
        any(kw in t for kw in TRIGGERS_HORA) or
        any(kw in t for kw in TRIGGERS_DATA) or
        any(kw in t for kw in TRIGGERS_CLIMA)
    )


def processar_tempo_real(texto: str) -> str | None:
    """
    Ponto de entrada principal.
    Recebe o comando do usuario e retorna a resposta ou None se nao for relevante.
    """
    t = texto.lower().strip()

    # --- HORA ---
    if any(kw in t for kw in TRIGGERS_HORA):
        return obter_hora_atual()

    # --- DATA ---
    if any(kw in t for kw in TRIGGERS_DATA):
        # Se tambem pedir hora junto
        if any(kw in t for kw in TRIGGERS_HORA):
            return obter_data_e_hora()
        return obter_data_atual()

    # --- CLIMA ---
    if any(kw in t for kw in TRIGGERS_CLIMA):
        cidade = _extrair_cidade_do_texto(t)

        # "vai chover" / "vai chover hoje/amanha"
        if any(p in t for p in ["vai chover", "chover hoje", "vai ter chuva",
                                  "chance de chuva", "previsao de chuva"]):
            return vai_chover(cidade)

        # Previsao amanha
        if any(p in t for p in ["amanha", "amanhã", "proximo dia", "proximo dias"]):
            dias = 1
            if any(p in t for p in ["proximos 3", "3 dias"]):
                # Retorna os 3 proximos dias
                if not cidade:
                    cidade = _detectar_cidade_por_ip()
                partes = []
                for d in range(3):
                    partes.append(obter_previsao(cidade, d))
                return "\n".join(partes)
            return obter_previsao(cidade, dias=1)

        # Previsao hoje
        if any(p in t for p in ["hoje", "agora", "atual"]):
            return obter_previsao(cidade, dias=0)

        # So temperatura
        if any(p in t for p in ["temperatura", "graus", "celsius",
                                  "faz frio", "faz calor", "ta frio",
                                  "ta quente", "esta frio", "esta quente"]):
            return obter_temperatura(cidade)

        # Clima geral (default)
        return obter_clima_atual(cidade)

    return None


# ---------------------------------------------------------------------------
# Standalone — teste direto
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== TESTE SIRIUS TEMPO REAL ===\n")

    testes = [
        "que horas sao",
        "que dia e hoje",
        "qual o clima em Sao Paulo",
        "vai chover hoje no Rio",
        "previsao para amanha em Curitiba",
        "temperatura agora em Manaus",
        "como ta o tempo em BH",
    ]

    for cmd in testes:
        print(">> {}".format(cmd))
        resp = processar_tempo_real(cmd)
        print("   {}".format(resp))
        print()