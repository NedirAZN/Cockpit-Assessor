"""Leitor do XPerformance (Relatório de Investimentos da XP).

Calibrado contra o relatório real de 13 páginas, ref. 30/09/2026.

MAPA DO RELATÓRIO — o que vem de onde:

  pág. 1   capa          conta, segmento, assessor, data de referência
  pág. 2   evolução      patrimônio bruto, rent./ganho do mês e 24M,
                         tabela Referências (Portfólio, CDI, Ibovespa, IPCA,
                         Dólar × mês/ano/12M/24M), resumo por período,
                         estatística histórica (meses +/-, volatilidade)
  pág. 3   histórica     rentabilidade mês a mês dos últimos 3 anos
  pág. 4   período       12 meses de patrimônio inicial/final, movimentações,
                         IR, IOF, ganho, rentabilidade e %CDI
  pág. 5   composição    saldo e rentabilidade por estratégia
  pág. 6   performance   rentabilidade por classe e benchmarks
  pág. 7+  posições      ativo a ativo: saldo, quantidade, alocação,
                         rentabilidade e %CDI em três janelas
  pág. 10+ movimentações data, histórico, valor e saldo

AS TRÊS ARMADILHAS, agora com o diagnóstico certo:

  1. QUANTIDADE NÃO USA VÍRGULA. O relatório escreve dinheiro como
     "R$ 7.527,71" (ponto de milhar, vírgula decimal) e quantidade como
     "3722.2" (ponto decimal, sem milhar), além de aceitar negativo ("-200").
     São duas gramáticas numéricas diferentes na mesma linha. Ler as duas com
     a mesma regra foi o que trocou saldo por quantidade. Tratado em
     `dinheiro()` e `quantidade()`, separadas de propósito.

  2. O GRÁFICO INVADE A TABELA. Na pág. 2 os rótulos do eixo entram no meio
     das linhas: "30,00% Portfólio 1,89% 8,47% ...". Na pág. 5 o "99,66%" e o
     "Total investido" do donut viram prefixo das linhas de estratégia. A
     defesa é ancorar cada linha no seu rótulo conhecido e ignorar o que vier
     antes, em vez de contar colunas da esquerda.

  3. NOME LONGO QUEBRA EM TRÊS LINHAS. O padrão real é nome na linha de cima,
     números na linha do meio, resto do nome na linha de baixo:

         Riza Renda Imobiliária Feeder FII RL - Subclasse
         R$ 25.163,97 248 8,02% 2,25% 207,63% 3,15% 151,14% 3,15% 151,14%
         I

     Por isso a linha de números é reconhecida por começar com "R$", e o nome
     é remontado olhando para trás e para frente.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

from util import brl

MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}

DIN = r"-?R\$\s?-?[\d.]+,\d{2}"          # dinheiro: R$ 1.234,56 / -R$ 70,00
PCT = r"-?[\d.]+,\d{2}%"                 # percentual: 1,89% / -152,27%
PCT_OU_TRACO = rf"(?:{PCT}|-)"
QTD = r"-?\d+(?:\.\d+)?"                 # quantidade: 3371 / 3722.2 / -200

RUIDO = ("XPerformance", "POSIÇÃO DETALHADA", "PRECIFICAÇÃO", "Estratégias Saldo",
         "MÊS ATUAL", "Legenda", "Os dados dos ativos", "MOVIMENTAÇÕES",
         "A rentabilidade", "O gráfico")


def dinheiro(texto):
    """'R$ 1.234,56' -> 1234.56. Ponto é milhar, vírgula é decimal."""
    if texto is None:
        return None
    t = str(texto).replace("R$", "").replace(" ", "").strip()
    if not t or t == "-":
        return None
    negativo = t.startswith("-")
    t = t.lstrip("-")
    if "," not in t:
        return None
    try:
        return (-1 if negativo else 1) * float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def percentual(texto):
    """'1,89%' -> 1.89. Mesma gramática do dinheiro, sem o R$."""
    if texto is None:
        return None
    t = str(texto).replace("%", "").replace(" ", "").strip()
    if not t or t == "-":
        return None
    negativo = t.startswith("-")
    t = t.lstrip("-")
    try:
        return (-1 if negativo else 1) * float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def quantidade(texto):
    """ARMADILHA 1: '3722.2' -> 3722.2. Ponto é DECIMAL aqui, não milhar."""
    if texto is None:
        return None
    t = str(texto).strip()
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def limpar(texto):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", texto or "")).strip()


def ruido(linha):
    return not linha or linha.startswith(RUIDO)


# ------------------------------------------------------------------ cabeçalho

RE_CAPA = re.compile(r"^(\d{4,9})\s+(.+?)\s+(\d{2}/\d{2}/\d{4})$")
RE_SEG = re.compile(r"^(Signature|Exclusive|Digital|Private|Corporate)$", re.I)


def ler_capa(linhas, nome_arquivo):
    dados = {"conta": None, "assessor": None, "ref": None, "segmento": None}
    for i, l in enumerate(linhas[:25]):
        m = RE_CAPA.match(l.strip())
        if m:
            dados["conta"], dados["assessor"], dados["ref"] = m.groups()
            for prox in linhas[i + 1:i + 4]:
                if RE_SEG.match(prox.strip()):
                    dados["segmento"] = prox.strip().capitalize()
                    break
            break
    if not dados["conta"]:
        m = re.search(r"(\d{6,9})", nome_arquivo)
        if m:
            dados["conta"] = m.group(1)
    return dados


# ----------------------------------------------------- página 2: consolidado

RE_TOPO = re.compile(rf"^({DIN})\s+({PCT})\s+({DIN})\s+({PCT})\s+({DIN})$")
RE_REFERENCIA = re.compile(
    rf"(Portf[óo]lio|CDI|Ibovespa|IPCA|D[óo]lar)\s+({PCT})\s+({PCT})\s+({PCT})\s+({PCT})\s*$")
RE_RESUMO = re.compile(rf"^(M[ÊE]S|ANO|12M|24M)\s+({DIN})\s+({PCT})\s+({PCT})\s+({DIN})")

ZERADO = {"mes": 0.0, "ano": 0.0, "m12": 0.0, "m24": 0.0}


def ler_consolidado(linhas):
    """Patrimônio, referências e resumo por período.

    ARMADILHA 2: as linhas de referência vêm com o rótulo do eixo do gráfico
    grudado na frente ("30,00% Portfólio 1,89% ..."). A âncora é o nome da
    referência no meio da linha, não o começo dela.
    """
    out = {
        "patrimonio": None, "rent_mes": None, "ganho_mes": None,
        "rent_24m": None, "ganho_24m": None,
        "portfolio": dict(ZERADO), "cdi": dict(ZERADO), "ibov": dict(ZERADO),
        "ipca": dict(ZERADO), "dolar": dict(ZERADO), "resumo": {},
    }
    destino = {"portfólio": "portfolio", "portfolio": "portfolio", "cdi": "cdi",
               "ibovespa": "ibov", "ipca": "ipca", "dólar": "dolar", "dolar": "dolar"}

    for l in linhas:
        m = RE_TOPO.match(l.strip())
        if m and out["patrimonio"] is None:
            out["patrimonio"] = dinheiro(m.group(1))
            out["rent_mes"] = percentual(m.group(2))
            out["ganho_mes"] = dinheiro(m.group(3))
            out["rent_24m"] = percentual(m.group(4))
            out["ganho_24m"] = dinheiro(m.group(5))
            continue

        m = RE_REFERENCIA.search(l)
        if m:
            chave = destino.get(m.group(1).lower())
            if chave:
                out[chave] = {
                    "mes": percentual(m.group(2)), "ano": percentual(m.group(3)),
                    "m12": percentual(m.group(4)), "m24": percentual(m.group(5)),
                }
            continue

        m = RE_RESUMO.match(l.strip())
        if m:
            periodo = m.group(1).replace("MES", "MÊS")
            out["resumo"][periodo] = {
                "ganho": dinheiro(m.group(2)), "rent": percentual(m.group(3)),
                "pcdi": percentual(m.group(4)), "mov": dinheiro(m.group(5)),
            }
    return out


RE_PAR_PCT = re.compile(rf"^({PCT})\s+({PCT})$")
RE_PAR_INT = re.compile(r"(\d{1,3})\s+(\d{1,3})\s*$")


def ler_estatisticas(linhas):
    """Meses positivos/negativos, retorno máximo/mínimo e volatilidade.

    São pares na linha seguinte ao rótulo. Best-effort: se o layout mudar,
    volta vazio em vez de derrubar o lote.
    """
    est = {}
    for i, l in enumerate(linhas):
        prox = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
        if "Meses Positivos" in l:
            m = RE_PAR_INT.match(prox)
            if m:
                est["meses_positivos"], est["meses_negativos"] = int(m.group(1)), int(m.group(2))
        elif "Retorno Mensal" in l:
            m = RE_PAR_PCT.match(prox)
            if m:
                est["retorno_max"] = percentual(m.group(1))
                est["retorno_min"] = percentual(m.group(2))
        elif "Volatilidade" in l:
            m = RE_PAR_PCT.match(prox)
            if m:
                est["vol_12m"], est["vol_24m"] = percentual(m.group(1)), percentual(m.group(2))
        elif "Meses Acima do CDI" in l:
            m = RE_PAR_INT.search(prox)
            if m:
                est["meses_acima_cdi"] = int(m.group(1))
                est["meses_abaixo_cdi"] = int(m.group(2))
    return est


# ------------------------------------------- página 3: rentabilidade por ano

RE_LINHA_ANUAL = re.compile(rf"^(Portf[óo]lio|%CDI)\s+((?:{PCT_OU_TRACO}\s+){{13}}{PCT})\s*$")
RE_ANO = re.compile(r"^(20\d{2})$")


def ler_historico_anual(linhas):
    """Rentabilidade mês a mês dos últimos três anos.

    O ano aparece numa linha sozinha ENTRE a linha do Portfólio e a do %CDI —
    artefato de como a tabela é desenhada.
    """
    anos, pendente, ultimo = {}, None, None
    for l in linhas:
        m = RE_LINHA_ANUAL.match(l.strip())
        if m:
            valores = [percentual(x) for x in re.findall(PCT_OU_TRACO, m.group(2))]
            if m.group(1).lower().startswith("portf"):
                pendente = valores
            elif ultimo is not None:
                anos[ultimo]["pcdi"] = valores[:12]
            continue
        m = RE_ANO.match(l.strip())
        if m and pendente is not None:
            ultimo = int(m.group(1))
            anos[ultimo] = {"meses": pendente[:12], "ano": pendente[12] if len(pendente) > 12 else None,
                            "pcdi": []}
            pendente = None
    return anos


# -------------------------------------- página 4: evolução mês a mês (12M)

RE_LINHA_MENSAL = re.compile(
    rf"^([a-z]{{3}})\.?/(\d{{2}})\s+({DIN})\s+({DIN})\s+({DIN})\s+({DIN})\s+"
    rf"({DIN})\s+({DIN})\s+({PCT})\s+({PCT})\s*$", re.I)


def ler_historico_mensal(linhas):
    """Os 12 meses com patrimônio inicial e final, movimentações, IR e ganho.

    É daqui que sai a série histórica do cockpit: um único relatório já traz
    doze pontos, então o gráfico de trajetória nasce preenchido.
    """
    meses = []
    for l in linhas:
        m = RE_LINHA_MENSAL.match(l.strip())
        if not m:
            continue
        mes = MESES.get(m.group(1).lower())
        if not mes:
            continue
        meses.append({
            "ref": f"{mes:02d}/20{m.group(2)}",
            "patrimonio_inicial": dinheiro(m.group(3)),
            "movimentacoes": dinheiro(m.group(4)),
            "ir": dinheiro(m.group(5)),
            "iof": dinheiro(m.group(6)),
            "patrimonio": dinheiro(m.group(7)),
            "ganho": dinheiro(m.group(8)),
            "rent": percentual(m.group(9)),
            "pcdi": percentual(m.group(10)),
        })
    return sorted(meses, key=lambda x: (x["ref"][3:], x["ref"][:2]))


# ------------------------------------------------ página 5: composição

RE_COMPOSICAO = re.compile(
    rf"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s/\.]+?)\s+\((\d+,\d+)%\)\s+({DIN})\s+"
    rf"({PCT_OU_TRACO})\s+({PCT_OU_TRACO})\s+({PCT_OU_TRACO})\s+({PCT_OU_TRACO})\s*$")


def ler_composicao(linhas):
    """Estratégias com saldo e rentabilidade.

    ARMADILHA 2 de novo: o "99,66%" e o "Total investido" do donut entram como
    prefixo, então a busca é `search` e o nome é limpo depois.
    """
    estrategias = []
    for l in linhas:
        m = RE_COMPOSICAO.search(l)
        if not m:
            continue
        nome = re.sub(r"^(?:\d+,\d+%|Total investido)\s*", "", limpar(m.group(1))).strip()
        if not nome:
            continue
        estrategias.append({
            "nome": nome,
            "saldo": dinheiro(m.group(3)),
            "aloc": percentual(m.group(2) + "%"),
            "rent_mes": percentual(m.group(4)),
            "pcdi_mes": 0.0,
            "rent_ano": percentual(m.group(5)),
            "pcdi_ano": 0.0,
            "rent_12m": percentual(m.group(6)),
            "rent_24m": percentual(m.group(7)),
        })
    return estrategias


# --------------------------------------------- páginas 7+: posição detalhada

RE_POSICAO = re.compile(
    rf"^(?:(?P<nome>.*?)\s+)?(?P<saldo>{DIN})\s+(?P<qtd>{QTD}|-)\s+(?P<aloc>{PCT})\s+"
    rf"(?P<r1>{PCT_OU_TRACO})\s+(?P<c1>{PCT_OU_TRACO})\s+"
    rf"(?P<r2>{PCT_OU_TRACO})\s+(?P<c2>{PCT_OU_TRACO})\s+"
    rf"(?P<r3>{PCT_OU_TRACO})\s+(?P<c3>{PCT_OU_TRACO})\s*$")


def ler_posicoes(linhas):
    """Ativo a ativo, com a estratégia de cada um.

    ARMADILHA 3: a linha de números começa com "R$" quando o nome quebrou.
    O nome é então a linha anterior, e o complemento pode estar na seguinte.
    Linhas cuja quantidade é "-" são o total da estratégia, não um ativo.
    """
    posicoes = []
    estrategia_atual = None
    consumidas = set()

    for i, l in enumerate(linhas):
        if i in consumidas:
            continue
        m = RE_POSICAO.match(l.strip())
        if not m:
            continue

        nome = limpar(m.group("nome") or "")

        # Nome vazio = a linha começa em "R$", ou seja, o nome quebrou. SÓ
        # nesse caso vale remontar com as linhas vizinhas. Fazer isso também
        # em linha de nome inteiro rouba o nome do ativo seguinte.
        if not nome:
            if i >= 1 and (i - 1) not in consumidas:
                anterior = limpar(linhas[i - 1])
                if anterior and not RE_POSICAO.match(anterior) and not ruido(anterior):
                    nome = anterior
                    consumidas.add(i - 1)

            if i + 1 < len(linhas):
                seguinte = limpar(linhas[i + 1])
                if (seguinte and len(seguinte) <= 60 and not ruido(seguinte)
                        and not re.search(r"R\$|%", seguinte)
                        and not RE_POSICAO.match(seguinte)):
                    nome = f"{nome} {seguinte}".strip()
                    consumidas.add(i + 1)

        if not nome or ruido(nome):
            continue

        if m.group("qtd") == "-":            # total da estratégia, não um ativo
            estrategia_atual = nome
            continue

        posicoes.append({
            "estrategia": estrategia_atual,
            "ativo": nome,
            "saldo": dinheiro(m.group("saldo")),
            "qtd": quantidade(m.group("qtd")),       # ARMADILHA 1
            "aloc": percentual(m.group("aloc")),
            "rent_mes": percentual(m.group("r1")),
            "pcdi_mes": percentual(m.group("c1")),
            "rent_ano": percentual(m.group("r2")),
            "pcdi_ano": percentual(m.group("c2")),
            "rent_24m": percentual(m.group("r3")),
            "pcdi_24m": percentual(m.group("c3")),
        })
    return posicoes


# ----------------------------------------------- páginas 10+: movimentações

RE_MOV = re.compile(
    rf"^(\d{{2}}/\d{{2}}/\d{{4}})\s+(\d{{2}}/\d{{2}}/\d{{4}})\s+(.+?)\s+({DIN})\s+({DIN})\s*$")


def ler_movimentacoes(linhas):
    movs = []
    for l in linhas:
        m = RE_MOV.match(l.strip())
        if not m:
            continue
        movs.append({
            "data": m.group(1),
            "liquidacao": m.group(2),
            "historico": limpar(m.group(3)),
            "valor": dinheiro(m.group(4)),
            "saldo": dinheiro(m.group(5)),
        })
    return movs


# ------------------------------------------------------------------ principal

def ler_pdf(caminho):
    caminho = Path(caminho)
    linhas = []
    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            for l in (pagina.extract_text() or "").split("\n"):
                if l.strip():
                    linhas.append(l.rstrip())

    capa = ler_capa(linhas, caminho.name)
    cons = ler_consolidado(linhas)
    posicoes = ler_posicoes(linhas)

    patrimonio = cons["patrimonio"]
    if patrimonio is None and posicoes:
        patrimonio = round(sum(p["saldo"] or 0 for p in posicoes), 2)

    return {
        "arquivo": caminho.name,
        "conta": capa["conta"],
        "assessor": capa["assessor"],
        "ref": capa["ref"],
        "segmento": capa["segmento"],
        "patrimonio": patrimonio,
        "rent_mes": cons["rent_mes"],
        "ganho_mes": cons["ganho_mes"],
        "rent_24m": cons["rent_24m"],
        "ganho_24m": cons["ganho_24m"],
        "portfolio": cons["portfolio"],
        "cdi": cons["cdi"],
        "ibov": cons["ibov"],
        "ipca": cons["ipca"],
        "dolar": cons["dolar"],
        "resumo": cons["resumo"],
        "estatisticas": ler_estatisticas(linhas),
        "estrategias": ler_composicao(linhas),
        "posicoes": posicoes,
        "movimentacoes": ler_movimentacoes(linhas),
        "historico_mensal": ler_historico_mensal(linhas),
        "historico_anual": ler_historico_anual(linhas),
    }


def conferir(dados):
    """As checagens que impedem um número errado de chegar ao cockpit."""
    avisos = []
    if not dados["conta"]:
        avisos.append("conta não identificada")
    if not dados["ref"]:
        avisos.append("data de referência não identificada")
    if not dados["patrimonio"]:
        avisos.append("patrimônio não identificado")
    if not dados["posicoes"]:
        avisos.append("nenhuma posição extraída")

    soma = sum(p["saldo"] or 0 for p in dados["posicoes"])
    if dados["patrimonio"] and soma:
        dif = abs(soma - dados["patrimonio"]) / dados["patrimonio"]
        if dif > 0.02:
            avisos.append(f"soma das posições ({brl(soma)}) difere do patrimônio "
                          f"({brl(dados['patrimonio'])}) em {dif:.1%}")

    soma_estr = sum(e["saldo"] or 0 for e in dados["estrategias"])
    if dados["patrimonio"] and soma_estr:
        dif = abs(soma_estr - dados["patrimonio"]) / dados["patrimonio"]
        if dif > 0.02:
            avisos.append(f"soma das estratégias ({brl(soma_estr)}) difere do "
                          f"patrimônio em {dif:.1%}")

    # ARMADILHA 1: quantidade perdida indica coluna lida errado.
    sem_qtd = [p for p in dados["posicoes"] if p["qtd"] is None and (p["saldo"] or 0) > 0]
    if len(sem_qtd) > len(dados["posicoes"]) * 0.3:
        avisos.append(f"{len(sem_qtd)} posições com saldo mas sem quantidade")

    # ARMADILHA 3: nome picado.
    picados = [p["ativo"] for p in dados["posicoes"] if len(p["ativo"]) < 4]
    if picados:
        avisos.append(f"nomes curtos demais, possível quebra de linha: {picados[:3]}")

    if not dados["historico_mensal"]:
        avisos.append("sem histórico mensal — a página de evolução mudou de formato")

    return avisos


def main():
    ap = argparse.ArgumentParser(description="Lê um XPerformance em PDF.")
    ap.add_argument("pdf")
    ap.add_argument("--conferir", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    d = ler_pdf(args.pdf)

    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    print(f"conta       {d['conta']}  ({d['segmento']})")
    print(f"assessor    {d['assessor']}")
    print(f"ref         {d['ref']}")
    print(f"patrimônio  {brl(d['patrimonio'])}")
    print(f"mês         {d['rent_mes']}%   ganho {brl(d['ganho_mes'])}")
    print(f"24M         {d['rent_24m']}%  ganho {brl(d['ganho_24m'])}")
    print(f"CDI         {d['cdi']}")
    print(f"IBOV        {d['ibov']}")
    print(f"resumo      {list(d['resumo'])}")
    print(f"estatíst.   {d['estatisticas']}")
    print(f"hist mensal {len(d['historico_mensal'])} meses")
    print(f"hist anual  {sorted(d['historico_anual'])}")

    print(f"\nestratégias ({len(d['estrategias'])}):")
    for e in d["estrategias"]:
        print(f"  {e['nome']:<26} {brl(e['saldo']):>16}  {e['aloc']}%  mês {e['rent_mes']}")
    print(f"\nposições ({len(d['posicoes'])}):")
    for p in d["posicoes"]:
        print(f"  {(p['estrategia'] or '—')[:20]:<22} {p['ativo'][:46]:<48} "
              f"{brl(p['saldo']):>16}  qtd {p['qtd']}")
    print(f"\nmovimentações: {len(d['movimentacoes'])}")

    if args.conferir:
        avisos = conferir(d)
        print("\nconferência:", "sem avisos" if not avisos else "")
        for a in avisos:
            print("  ⚠", a)


if __name__ == "__main__":
    main()
