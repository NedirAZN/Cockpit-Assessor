"""O motor do lote: recebe arquivos, valida e grava.

Usado pelo botão de atualizar do cockpit e pelo cron. A lógica mora aqui para
que os dois caminhos tenham exatamente o mesmo comportamento — inclusive a
recusa do lote inteiro quando a leitura vai mal.

Aceita:
  .pdf   relatório XPerformance
  .zip   pacote com vários PDFs (inclusive em subpastas)
  .xlsx  planilha complementar — guardada, ainda não interpretada
  .rar   NÃO suportado; a mensagem de erro explica como converter

Sobre o .rar: descompactar RAR exige um programa externo proprietário que não
existe na imagem do servidor. ZIP é lido pelo próprio Python, sem dependência
nenhuma. No Windows é botão direito na pasta → "Compactar em arquivo ZIP".
"""
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import db
from util import brl
from parse_xp import conferir, ler_pdf

TOLERANCIA_FALHAS = 0.20      # acima disso o lote inteiro é recusado
VARIACAO_SUSPEITA = 0.15      # destaca no relatório, não bloqueia

# O PDF não informa estes campos em todo relatório; preserva o que já existe.
CAMPOS_PRESERVADOS = ["assessor", "segmento"]

EXT_ACEITAS = {".pdf", ".zip", ".xlsx", ".xls", ".csv"}


def para_data(texto):
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def ref_mensal_para_data(ref):
    """'09/2026' -> date(2026, 9, 1). Usado pelo histórico mês a mês."""
    try:
        mes, ano = ref.split("/")
        return datetime(int(ano), int(mes), 1).date()
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------- desempacotar

def desempacotar(nome, conteudo):
    """Transforma o que chegou numa lista de (nome, bytes) de PDFs e planilhas.

    ZIP é aberto aqui mesmo, recursivamente até um nível. Arquivos do macOS
    (__MACOSX, ._nome) e pastas são descartados.
    """
    ext = Path(nome).suffix.lower()

    if ext == ".pdf":
        return [(nome, conteudo)], []

    if ext in {".xlsx", ".xls", ".csv"}:
        return [(nome, conteudo)], []

    if ext == ".rar":
        return [], [(nome, "arquivos .rar não podem ser abertos no servidor — "
                           "compacte como .zip e envie de novo")]

    if ext != ".zip":
        return [], [(nome, f"extensão {ext or 'desconhecida'} não é aceita")]

    saida, erros = [], []
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                interno = info.filename
                base = Path(interno).name
                if interno.startswith("__MACOSX") or base.startswith("._"):
                    continue
                sub_ext = Path(base).suffix.lower()
                if sub_ext not in EXT_ACEITAS or sub_ext == ".zip":
                    continue
                try:
                    saida.append((base, z.read(info)))
                except Exception as e:                   # noqa: BLE001
                    erros.append((base, f"não deu para extrair: {e}"))
    except zipfile.BadZipFile:
        return [], [(nome, "o arquivo não é um .zip válido ou veio corrompido")]

    if not saida and not erros:
        erros.append((nome, "o .zip não tem nenhum PDF nem planilha dentro"))
    return saida, erros


# ----------------------------------------------------------------- gravação

def gravar_historico(con, conta, dados):
    """Grava a série mensal que vem dentro do próprio relatório.

    A página de evolução traz doze meses fechados. Em vez de esperar doze
    lotes para ter gráfico, um relatório já entrega a trajetória inteira.
    Meses que já existem são atualizados, nunca duplicados.
    """
    gravados = 0
    for mes in dados.get("historico_mensal") or []:
        data = ref_mensal_para_data(mes["ref"])
        if not data:
            continue
        con.execute(
            "INSERT INTO historico (conta, ref, patrimonio, rent_mes, ganho_mes, rent_24m) "
            "VALUES (%s, %s, %s, %s, %s, NULL) "
            "ON CONFLICT (conta, ref) DO UPDATE SET "
            "  patrimonio = EXCLUDED.patrimonio, rent_mes = EXCLUDED.rent_mes, "
            "  ganho_mes = EXCLUDED.ganho_mes",
            (conta, data, mes["patrimonio"], mes["rent"], mes["ganho"]))
        gravados += 1
    return gravados


def mesclar(novo, anterior):
    """Mantém o que o relatório não informa, em vez de apagar."""
    if not anterior:
        return novo
    for campo in CAMPOS_PRESERVADOS:
        if not novo.get(campo) and anterior.get(campo):
            novo[campo] = anterior[campo]
    return novo


# ------------------------------------------------------------------- lote

def processar(arquivos, lote_id=None, simular=False, progresso=None):
    """Processa uma lista de (nome, bytes). Devolve o relatório do lote.

    Ou o lote inteiro entra, ou nada entra. Meia importação é pior que
    nenhuma, porque você olha para um número errado sem desconfiar.
    """
    brutos, erros_pacote = [], []
    for nome, conteudo in arquivos:
        itens, erros = desempacotar(nome, conteudo)
        brutos.extend(itens)
        erros_pacote.extend(erros)

    pdfs = [(n, c) for n, c in brutos if n.lower().endswith(".pdf")]
    planilhas = [n for n, _ in brutos if not n.lower().endswith(".pdf")]
    total = len(pdfs)

    relatorio = {
        "arquivos_recebidos": len(arquivos),
        "pdfs": total,
        "planilhas": planilhas,
        "erros_pacote": [{"arquivo": n, "erro": e} for n, e in erros_pacote],
        "falhas": [], "avisos": [], "novas": [], "ausentes": [], "saltos": [],
    }

    if not pdfs:
        relatorio["status"] = "rejeitado"
        relatorio["motivo"] = "nenhum PDF encontrado no que foi enviado"
        if lote_id and not simular:
            finalizar(lote_id, "rejeitado", relatorio, 0, 0, 0, 0, None)
        return relatorio

    lidos, falhas = [], []
    with TemporaryDirectory() as tmp:
        for i, (nome, conteudo) in enumerate(pdfs, 1):
            caminho = Path(tmp) / nome
            caminho.write_bytes(conteudo)
            try:
                dados = ler_pdf(caminho)
                avisos = conferir(dados)
                graves = [a for a in avisos
                          if "não identificad" in a or "nenhuma posição" in a]
                if graves:
                    falhas.append((nome, "; ".join(graves)))
                else:
                    lidos.append((nome, dados, avisos))
            except Exception as e:                       # noqa: BLE001
                falhas.append((nome, f"{type(e).__name__}: {e}"))
            if progresso:
                progresso(i, total, nome)

    relatorio["falhas"] = [{"arquivo": n, "erro": e} for n, e in falhas]
    relatorio["avisos"] = [{"arquivo": n, "avisos": a} for n, _, a in lidos if a]
    relatorio["lidos"] = len(lidos)

    taxa = len(falhas) / total
    if taxa > TOLERANCIA_FALHAS:
        relatorio["status"] = "rejeitado"
        relatorio["motivo"] = (
            f"{len(falhas)} de {total} relatórios falharam ({taxa:.0%}), acima do "
            f"limite de {TOLERANCIA_FALHAS:.0%}. Nada foi gravado. É sinal de que o "
            f"layout do XPerformance mudou.")
        if lote_id and not simular:
            finalizar(lote_id, "rejeitado", relatorio, total, 0, 0, 0, None)
        return relatorio

    # ------------------------------------------------------- comparação
    atuais = {l["conta"]: l for l in db.buscar(
        "SELECT conta, patrimonio, dados FROM carteiras")}
    contas_lote = {d["conta"] for _, d, _ in lidos}

    relatorio["novas"] = sorted(contas_lote - set(atuais))
    relatorio["ausentes"] = sorted(set(atuais) - contas_lote)

    for _, d, _ in lidos:
        antes = atuais.get(d["conta"])
        if antes and antes["patrimonio"] and d["patrimonio"]:
            variacao = (d["patrimonio"] - float(antes["patrimonio"])) / float(antes["patrimonio"])
            if abs(variacao) > VARIACAO_SUSPEITA:
                relatorio["saltos"].append({
                    "conta": d["conta"], "variacao": round(variacao * 100, 1),
                    "de": float(antes["patrimonio"]), "para": d["patrimonio"]})

    aum = sum(d["patrimonio"] or 0 for _, d, _ in lidos)
    refs = {d["ref"] for _, d, _ in lidos if d["ref"]}
    relatorio["aum"] = aum
    relatorio["aum_formatado"] = brl(aum)
    relatorio["refs"] = sorted(refs)
    if len(refs) > 1:
        relatorio["avisos"].append(
            {"arquivo": "(lote)", "avisos": [f"datas de referência misturadas: {sorted(refs)}"]})

    if simular:
        relatorio["status"] = "simulado"
        return relatorio

    # --------------------------------------------------------- gravação
    ref_lote = para_data(sorted(refs)[-1]) if refs else None
    meses_gravados = 0

    with db.pool.connection() as con:          # transação única: tudo ou nada
        for _, d, _ in lidos:
            d = mesclar(d, atuais.get(d["conta"], {}).get("dados"))
            con.execute(
                "INSERT INTO carteiras (conta, ref, assessor, segmento, patrimonio, dados) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (conta) DO UPDATE SET ref = EXCLUDED.ref, "
                "  assessor = EXCLUDED.assessor, segmento = EXCLUDED.segmento, "
                "  patrimonio = EXCLUDED.patrimonio, dados = EXCLUDED.dados, "
                "  atualizado_em = now()",
                (d["conta"], para_data(d["ref"]), d.get("assessor"), d.get("segmento"),
                 d["patrimonio"], json.dumps(d, ensure_ascii=False)))
            meses_gravados += gravar_historico(con, d["conta"], d)

        relatorio["status"] = "ok"
        relatorio["meses_historico"] = meses_gravados
        if lote_id:
            con.execute(
                "UPDATE lotes SET ref = %s, arquivos = %s, contas = %s, novas = %s, "
                "ausentes = %s, aum = %s, status = 'ok', relatorio = %s WHERE id = %s",
                (ref_lote, total, len(lidos), len(relatorio["novas"]),
                 len(relatorio["ausentes"]), aum,
                 json.dumps(relatorio, ensure_ascii=False), lote_id))

    return relatorio


def finalizar(lote_id, status, relatorio, arquivos, contas, novas, ausentes, aum):
    db.executar(
        "UPDATE lotes SET status = %s, relatorio = %s, arquivos = %s, contas = %s, "
        "novas = %s, ausentes = %s, aum = %s WHERE id = %s",
        (status, json.dumps(relatorio, ensure_ascii=False), arquivos, contas,
         novas, ausentes, aum, lote_id))


def abrir_lote():
    linha = db.buscar_um(
        "INSERT INTO lotes (status, relatorio) VALUES ('processando', '{}'::jsonb) "
        "RETURNING id")
    return linha["id"]
