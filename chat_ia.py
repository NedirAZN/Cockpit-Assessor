"""Assistente do cockpit: Claude (API da Anthropic) com ferramentas de mercado.

O balão de chat do cockpit responde sozinho o que sabe da carteira (regras locais). O que
não sabe — cotação, perfil da empresa, risco e retorno de um fundo — vem para cá. O Claude
decide quais ferramentas chamar e escreve a resposta; as ferramentas rodam no servidor:

  cotacao_b3, ficha_b3          brapi.dev (plano free)
  historico_mais_retorno        Mais Retorno (cotas históricas; risco e retorno calculados aqui)
  exposicao_cockpit             o que a base do cockpit tem de um ativo (agregado, sem número de conta)

Variáveis de ambiente:
  ANTHROPIC_API_KEY     obrigatória para o chat de IA
  CHAT_MODEL            modelo (padrão claude-sonnet-5-5)
  CHAT_LIMITE_DIA       trava de perguntas por dia, para não gastar sem querer (padrão 300)
  MAISRETORNO_API_KEY   chave da API da Mais Retorno (sem ela essa ferramenta avisa que falta)
"""
import json
import math
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ANTHROPIC_URL = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
MODELO = os.environ.get("CHAT_MODEL", "claude-sonnet-5-5")
LIMITE_DIA = int(os.environ.get("CHAT_LIMITE_DIA", "300"))
MR_BASE = os.environ.get("MAISRETORNO_BASE", "https://data.maisretorno.com/mr-data/v4/api")
MR_TTL = 6 * 3600
MAX_VOLTAS = 6

SISTEMA = """Você é o assistente do Cockpit BFR, usado por Bruno, assessor de investimentos da BFR Investimentos (parceira da XP).

Responda em português do Brasil, direto e curto: primeiro o dado, depois uma leitura de uma ou duas frases. Use as ferramentas para qualquer número de mercado (cotação, perfil, retorno, volatilidade, drawdown) — nunca invente valor nem use memória para isso. Diga a fonte e o horário/data do dado (a brapi tem atraso de ~30 min; a Mais Retorno é D-1).

Você entrega dados e análise histórica, não recomendação: não diga para comprar, vender ou mudar a alocação de um cliente. Se perguntarem "o que fazer", mostre os números que ajudam a decidir e deixe a decisão com o Bruno.

Para ver quantos clientes têm um ativo na base do cockpit, use exposicao_cockpit (devolve só agregado). Se a ferramenta falhar ou a chave de um serviço não estiver configurada, diga isso em uma frase e siga com o que tiver.
Formato: texto simples, sem tabelas; pode usar **negrito** em poucos pontos e listas curtas com "•"."""

FERRAMENTAS = [
    {"name": "cotacao_b3",
     "description": "Cotação atual (com ~30 min de atraso) de um ticker da B3 pela brapi: preço, variação do dia, volume, máxima/mínima do dia e de 52 semanas.",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Ex.: PETR4, ABEV3, BOVA11"}}, "required": ["ticker"]}},
    {"name": "ficha_b3",
     "description": "Ficha de um ticker da B3 pela brapi: nome, setor, resumo da empresa, e comportamento do preço nos últimos 3 meses (variação em 1 e 3 meses, mínima e máxima).",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "historico_mais_retorno",
     "description": "Retorno, volatilidade anualizada e drawdown máximo de um ativo ou fundo no período, calculados a partir das cotas históricas da Mais Retorno (dado D-1). Identificador no formato ativo:mercado — petr4:b3 para ações/FIIs/ETFs, ou CNPJ só com números seguido de :fi para fundos (ex.: 12345678000190:fi). Cada chamada gasta 1 crédito do plano.",
     "input_schema": {"type": "object", "properties": {
         "identificador": {"type": "string"},
         "meses": {"type": "integer", "description": "Janela em meses, 1 a 60. Padrão 12."}}, "required": ["identificador"]}},
    {"name": "exposicao_cockpit",
     "description": "Quanto a base do cockpit tem de um ativo: nº de clientes, volume e estratégia. Busca por parte do nome (ex.: 'ABEV3', 'Tesouro Selic'). Devolve só agregado.",
     "input_schema": {"type": "object", "properties": {"ativo": {"type": "string"}}, "required": ["ativo"]}},
]

_USO = {"dia": "", "n": 0}
_TRAVA = threading.Lock()
_MR_CACHE: dict[str, tuple[float, dict]] = {}


class ChatIndisponivel(Exception):
    pass


def _conta_pergunta():
    hoje = time.strftime("%Y-%m-%d")
    with _TRAVA:
        if _USO["dia"] != hoje:
            _USO["dia"], _USO["n"] = hoje, 0
        if _USO["n"] >= LIMITE_DIA:
            raise ChatIndisponivel(f"limite de {LIMITE_DIA} perguntas por dia atingido")
        _USO["n"] += 1


# ----------------------------------------------------------- Mais Retorno

def _normaliza_id(ident: str) -> str | None:
    base, _, mercado = (ident or "").strip().lower().partition(":")
    so_num = re.sub(r"[.\-/\s]", "", base)
    if so_num.isdigit() and len(so_num) == 14:       # CNPJ → fundo
        base, mercado = so_num, mercado or "fi"
    else:
        mercado = mercado or "b3"
    ident = f"{base}:{mercado}"
    return ident if re.fullmatch(r"[a-z0-9._-]{2,30}:[a-z0-9]{1,8}", ident) else None


def metricas(quotes: list[dict], meses: int) -> dict:
    """Retorno, volatilidade anualizada e drawdown máximo a partir da série de cotas."""
    pts = [(q["d"], float(q["c"])) for q in quotes if q.get("d") and q.get("c")]
    pts.sort()
    if not pts:
        return {"erro": "sem cotas no período"}
    y, m, d = (int(x) for x in pts[-1][0].split("-"))
    total = y * 12 + (m - 1) - meses
    corte = f"{total // 12:04d}-{total % 12 + 1:02d}-{d:02d}"
    pts = [p for p in pts if p[0] >= corte]
    if len(pts) < 5:
        return {"erro": "poucas cotas no período", "pontos": len(pts)}
    rets = [pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts)) if pts[i - 1][1] > 0]
    media = sum(rets) / len(rets)
    var = sum((r - media) ** 2 for r in rets) / max(len(rets) - 1, 1)
    pico, dd, dd_data = pts[0][1], 0.0, pts[0][0]
    for dt, c in pts:
        pico = max(pico, c)
        if c / pico - 1 < dd:
            dd, dd_data = c / pico - 1, dt
    return {
        "de": pts[0][0], "ate": pts[-1][0], "pontos": len(pts),
        "retorno_periodo_pct": round((pts[-1][1] / pts[0][1] - 1) * 100, 2),
        "volatilidade_anual_pct": round(math.sqrt(var) * math.sqrt(252) * 100, 2),
        "drawdown_maximo_pct": round(dd * 100, 2), "data_do_drawdown_maximo": dd_data,
    }


def historico_mais_retorno(identificador: str, meses: int = 12) -> dict:
    chave = os.environ.get("MAISRETORNO_API_KEY", "").strip()
    if not chave:
        return {"erro": "MAISRETORNO_API_KEY não está configurada no Railway"}
    ident = _normaliza_id(identificador)
    if not ident:
        return {"erro": "identificador inválido; use ticker (petr4) ou CNPJ só com números (14 dígitos)"}
    meses = max(1, min(int(meses or 12), 60))
    agora = time.time()
    hit = _MR_CACHE.get(ident)
    if hit and agora - hit[0] < MR_TTL:
        dados = hit[1]
    else:
        req = Request(f"{MR_BASE}/quotes/{ident}", headers={"X-Api-Key": chave, "Accept": "application/json",
                                                             "User-Agent": "cockpit-bfr"})
        try:
            with urlopen(req, timeout=20) as r:
                dados = json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            return {"erro": {401: "chave da Mais Retorno recusada", 403: "chave sem permissão para este ativo",
                             404: "ativo não encontrado na Mais Retorno",
                             429: "créditos do mês da Mais Retorno esgotados"}.get(e.code, f"Mais Retorno respondeu {e.code}")}
        except URLError as e:
            return {"erro": f"não deu para falar com a Mais Retorno: {e.reason}"}
        _MR_CACHE[ident] = (agora, dados)
        if len(_MR_CACHE) > 200:
            _MR_CACHE.clear()
    out = {"identificador": ident, "nome": dados.get("nicename") or dados.get("shortname"),
           "moeda": dados.get("currency"), "fonte": "Mais Retorno (D-1)"}
    out.update(metricas(dados.get("quotes") or [], meses))
    return out


# ----------------------------------------------------------- Claude

def _anthropic(payload: dict, chave: str) -> dict:
    req = Request(ANTHROPIC_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
                  headers={"content-type": "application/json", "x-api-key": chave,
                           "anthropic-version": "2023-06-01"})
    with urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def perguntar(pergunta: str, historico: list[dict], locais: dict) -> dict:
    """`locais`: ferramentas que dependem do app (cotacao_b3, ficha_b3, exposicao_cockpit)."""
    chave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not chave:
        raise ChatIndisponivel("ANTHROPIC_API_KEY não está configurada no Railway")
    _conta_pergunta()

    msgs = []
    for h in (historico or [])[-8:]:
        if h.get("role") in ("user", "assistant") and str(h.get("content", "")).strip():
            msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    msgs.append({"role": "user", "content": pergunta})

    usadas = []
    for _ in range(MAX_VOLTAS):
        try:
            r = _anthropic({"model": MODELO, "max_tokens": 1200, "system": SISTEMA,
                            "tools": FERRAMENTAS, "messages": msgs}, chave)
        except HTTPError as e:
            corpo = e.read().decode("utf-8", "replace")[:200]
            raise ChatIndisponivel(f"a API da Anthropic respondeu {e.code}: {corpo}")
        except URLError as e:
            raise ChatIndisponivel(f"não deu para falar com a Anthropic: {e.reason}")

        blocos = r.get("content") or []
        if r.get("stop_reason") != "tool_use":
            texto = "\n".join(b.get("text", "") for b in blocos if b.get("type") == "text").strip()
            return {"resposta": texto or "Não consegui montar uma resposta.", "ferramentas": usadas}

        msgs.append({"role": "assistant", "content": blocos})
        resultados = []
        for b in blocos:
            if b.get("type") != "tool_use":
                continue
            nome, args = b.get("name"), b.get("input") or {}
            usadas.append(nome)
            try:
                if nome == "historico_mais_retorno":
                    res = historico_mais_retorno(args.get("identificador", ""), args.get("meses") or 12)
                elif nome in locais:
                    res = locais[nome](**{k: v for k, v in args.items() if isinstance(k, str)})
                else:
                    res = {"erro": f"ferramenta desconhecida: {nome}"}
            except Exception as e:      # a ferramenta falhar não pode derrubar o chat
                res = {"erro": f"falha em {nome}: {type(e).__name__}"}
            resultados.append({"type": "tool_result", "tool_use_id": b.get("id"),
                               "content": json.dumps(res, ensure_ascii=False)[:6000]})
        msgs.append({"role": "user", "content": resultados})

    return {"resposta": "A consulta ficou longa demais; tente uma pergunta mais específica.", "ferramentas": usadas}
