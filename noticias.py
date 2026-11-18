"""Notícia principal de um ativo da B3, com as fontes.

Busca nos portais liberados e deixa a IA escrever um resumo curto SÓ com o que as manchetes dizem:
  - Google Notícias (RSS de busca), restrito a InfoMoney, Money Times e Yahoo Finance
  - Yahoo Finance (RSS do ticker)
O Google Finance não tem feed de notícias; o Google Notícias cobre esse papel.

Sem nenhuma matéria dos últimos 14 dias que cite o papel, devolve vazio e a tela não mostra nada.
Resultado fica em cache (NOTICIAS_CACHE_S, padrão 3 h). Sem ANTHROPIC_API_KEY, o "resumo" é a
manchete mais recente.

Variáveis: NOTICIAS_CACHE_S, NOTICIAS_RESUMOS_DIA (padrão 150), NOTICIAS_JANELA_DIAS (padrão 14).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

import chat_ia

CACHE_S = int(os.environ.get("NOTICIAS_CACHE_S", "10800"))
RESUMOS_DIA = int(os.environ.get("NOTICIAS_RESUMOS_DIA", "150"))
JANELA_DIAS = int(os.environ.get("NOTICIAS_JANELA_DIAS", "14"))
UA = "Mozilla/5.0 (compatible; CockpitBFR/1.0)"
MAX_BYTES = 1_500_000

_CACHE: dict[str, tuple[float, dict]] = {}
_TRAVA = threading.Lock()
_USO = {"dia": "", "n": 0}

# portal -> nome de exibição (compara pelo domínio ou pelo nome da fonte que o feed traz)
PORTAIS = [("infomoney", "InfoMoney"), ("moneytimes", "Money Times"), ("money times", "Money Times"),
           ("yahoo", "Yahoo Finance")]
SITES_GOOGLE = "(site:infomoney.com.br OR site:moneytimes.com.br OR site:finance.yahoo.com OR site:br.financas.yahoo.com)"


def _baixa(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"})
    with urlopen(req, timeout=8) as r:
        return r.read(MAX_BYTES)


def _portal(nome_fonte: str, url: str) -> str | None:
    alvo = f"{nome_fonte} {url}".lower()
    for chave, exib in PORTAIS:
        if chave in alvo:
            return exib
    return None


def _itens_rss(xml: bytes) -> list[dict]:
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        fonte = (it.findtext("source") or "").strip()
        try:
            data = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            data = None
        if titulo and link.startswith("http") and data:
            out.append({"titulo": titulo, "url": link, "data": data, "fonte": fonte})
    return out


def _google(ticker: str) -> list[dict]:
    q = quote(f"{ticker} {SITES_GOOGLE} when:{JANELA_DIAS}d")
    return _itens_rss(_baixa(f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"))


def _yahoo(ticker: str) -> list[dict]:
    return _itens_rss(_baixa(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}.SA&region=BR&lang=pt-BR"))


def coletar(ticker: str) -> list[dict]:
    """Matérias dos portais liberados dos últimos dias que citam o papel, da mais nova para a mais velha."""
    brutas: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        for fut in [ex.submit(_google, ticker), ex.submit(_yahoo, ticker)]:
            try:
                brutas += fut.result()
            except Exception:
                pass            # um portal fora do ar não derruba o resto
    limite = datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)
    cita = re.compile(rf"\b{re.escape(ticker[:4])}\d{{1,2}}\b")      # "VALE3", não "vale a pena"
    vistos, itens = set(), []
    for x in sorted(brutas, key=lambda i: i["data"], reverse=True):
        if x["data"] < limite or not cita.search(x["titulo"].upper()):
            continue
        # o título do Google vem como "Manchete - Portal": separa o portal do texto
        titulo, _, sufixo = x["titulo"].rpartition(" - ")
        portal = _portal(x["fonte"] or sufixo, x["url"])
        if not portal:
            continue
        titulo = titulo if titulo else x["titulo"]
        chave = re.sub(r"\W+", "", titulo.lower())[:60]
        if chave in vistos:
            continue
        vistos.add(chave)
        itens.append({"titulo": titulo.strip(), "portal": portal, "url": x["url"],
                      "data": x["data"].astimezone().strftime("%d/%m"), "iso": x["data"].isoformat()})
    return itens[:6]


def _conta_resumo() -> bool:
    hoje = time.strftime("%Y-%m-%d")
    with _TRAVA:
        if _USO["dia"] != hoje:
            _USO["dia"], _USO["n"] = hoje, 0
        if _USO["n"] >= RESUMOS_DIA:
            return False
        _USO["n"] += 1
        return True


SISTEMA_RESUMO = (
    "Você escreve para um assessor de investimentos brasileiro. Com base SOMENTE nas manchetes dadas, "
    "escreva a notícia principal sobre o papel em 2 a 3 frases curtas, em português simples, para o assessor "
    "repassar ao cliente. Não invente números, datas, causas ou fatos que não estejam nas manchetes. "
    "Não recomende comprar, vender ou manter. Não use emojis nem markdown. Se as manchetes forem vagas, "
    "diga só o que elas permitem afirmar. Responda apenas com o texto."
)


def resumir(ticker: str, itens: list[dict]) -> str:
    chave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not itens:
        return ""
    if not chave or not _conta_resumo():
        return itens[0]["titulo"]                   # sem IA: a manchete mais recente
    manchetes = "\n".join(f"- {i['data']} ({i['portal']}): {i['titulo']}" for i in itens[:5])
    try:
        r = chat_ia._anthropic({"model": chat_ia.MODELO, "max_tokens": 300, "system": SISTEMA_RESUMO,
                                "messages": [{"role": "user", "content": f"Papel: {ticker}\nManchetes:\n{manchetes}"}]}, chave)
        txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
        return txt or itens[0]["titulo"]
    except Exception:
        return itens[0]["titulo"]


def noticia(ticker: str) -> dict:
    agora = time.time()
    hit = _CACHE.get(ticker)
    if hit and agora - hit[0] < CACHE_S:
        return hit[1]
    itens = coletar(ticker)
    res = {"symbol": ticker, "resumo": resumir(ticker, itens), "data": itens[0]["data"] if itens else "",
           "fontes": [{"portal": i["portal"], "data": i["data"], "url": i["url"], "titulo": i["titulo"]} for i in itens[:3]]}
    # resultado vazio (ou portais fora do ar) só fica 15 min em cache; com matéria, CACHE_S
    _CACHE[ticker] = (agora - (CACHE_S - 900 if not res["fontes"] else 0), res)
    if len(_CACHE) > 500:
        _CACHE.clear()
    return res
