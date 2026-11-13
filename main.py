"""Cockpit BFR — servidor.

Serve o cockpit e a API. A ideia central: o HTML é o mesmo de sempre, mas o bloco
de dados que antes vinha cravado no arquivo agora é injetado a cada requisição a
partir do Postgres. Nenhuma linha da lógica do cockpit precisou mudar.
"""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as Request_urllib, urlopen
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)

import auth
import db
import importador

BASE = Path(__file__).parent
CHAVES_ESTADO = {
    "bfr_crm_v1", "bfr_overrides_v1", "bfr_metas_v1", "bfr_mix_v1",
    "bfr_produtos_v1", "bfr_produtos_v3_ago26", "bfr_boletas_jul26",
    "bfr_sim_v3", "bfr_atualizacao_v1", "bfr_brapi_token",
    "bfr_produtos_lixeira_v1",
}
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024

app = FastAPI(title="Cockpit BFR", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


_template_cache: str | None = None


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"não serializável: {type(o)}")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


@app.on_event("startup")
def iniciar():
    db.abrir()
    db.criar_esquema()
    semear_se_vazio()


def semear_se_vazio():
    """Na primeira subida o banco está vazio: carrega as 94 contas do arquivo.

    Roda uma vez só. Depois quem alimenta o banco é o agente de lote.
    """
    if db.buscar_um("SELECT count(*) AS n FROM carteiras")["n"] > 0:
        return
    from seed_carteiras import semear
    print("banco vazio — carregando o seed inicial")
    semear()


@app.on_event("shutdown")
def encerrar():
    db.fechar()


# ---------------------------------------------------------------- autenticação

def logado(request: Request) -> bool:
    return auth.sessao_valida(request.cookies.get(auth.COOKIE))


def exigir_login(request: Request):
    if not logado(request):
        raise HTTPException(status_code=401, detail="sessão expirada")


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request, erro: str = ""):
    if logado(request):
        return RedirectResponse("/", status_code=303)
    html = (BASE / "login.html").read_text(encoding="utf-8")
    aviso = '<p class="erro">Usuário ou senha não conferem.</p>' if erro else ""
    return HTMLResponse(html.replace("<!--ERRO-->", aviso))


@app.post("/login")
def entrar(usuario: str = Form(...), senha: str = Form(...)):
    if not auth.conferir_credenciais(usuario, senha):
        return RedirectResponse("/login?erro=1", status_code=303)
    resposta = RedirectResponse("/", status_code=303)
    resposta.set_cookie(
        auth.COOKIE, auth.criar_token(),
        max_age=auth.DIAS_SESSAO * 86400,
        httponly=True, samesite="lax",
        secure=os.environ.get("COOKIE_SEGURO", "1") == "1",
    )
    return resposta


@app.get("/logout")
def sair():
    resposta = RedirectResponse("/login", status_code=303)
    resposta.delete_cookie(auth.COOKIE)
    return resposta


# -------------------------------------------------------------------- cockpit

def carregar_template() -> str:
    global _template_cache
    if _template_cache is None or os.environ.get("DEV") == "1":
        _template_cache = (BASE / "cockpit.html").read_text(encoding="utf-8")
    return _template_cache


@app.get("/", response_class=HTMLResponse)
def cockpit(request: Request):
    if not logado(request):
        return RedirectResponse("/login", status_code=303)

    carteiras = [l["dados"] for l in db.buscar(
        "SELECT dados FROM carteiras ORDER BY patrimonio DESC NULLS LAST")]
    estado = {l["chave"]: l["valor"] for l in db.buscar("SELECT chave, valor FROM estado")}

    boot = (
        "<script>"
        f"window.__BFR_CARTEIRAS__={dumps(carteiras)};"
        f"window.__BFR_ESTADO__={dumps(estado)};"
        "</script>"
        '<script src="/static/sync.js"></script>'
        '<script src="/static/upload.js"></script>'
        '<script src="/static/gatilhos.js"></script>'
        '<script src="/static/atuacao.js"></script>'
    )
    return HTMLResponse(carregar_template().replace("<!--__BFR_BOOT__-->", boot))


# ------------------------------------------------------------------------ API

@app.get("/api/carteiras")
def api_carteiras(request: Request):
    exigir_login(request)
    linhas = db.buscar("SELECT dados FROM carteiras ORDER BY patrimonio DESC NULLS LAST")
    return JSONResponse(json.loads(dumps([l["dados"] for l in linhas])))


@app.get("/api/carteiras/{conta}")
def api_carteira(request: Request, conta: str):
    exigir_login(request)
    linha = db.buscar_um("SELECT dados FROM carteiras WHERE conta = %s", (conta,))
    if not linha:
        raise HTTPException(404, "conta não encontrada")
    return JSONResponse(json.loads(dumps(linha["dados"])))


@app.get("/api/historico/{conta}")
def api_historico(request: Request, conta: str):
    exigir_login(request)
    linhas = db.buscar(
        "SELECT ref, patrimonio, rent_mes, ganho_mes, rent_24m "
        "FROM historico WHERE conta = %s ORDER BY ref", (conta,))
    return JSONResponse(json.loads(dumps(linhas)))


@app.get("/api/estado")
def api_estado_ler(request: Request):
    exigir_login(request)
    return {l["chave"]: l["valor"] for l in db.buscar("SELECT chave, valor FROM estado")}


@app.post("/api/estado")
async def api_estado_gravar(request: Request):
    """Recebe o lote de chaves alteradas que o sync.js acumulou."""
    exigir_login(request)
    corpo = await request.json()
    if not isinstance(corpo, dict):
        raise HTTPException(400, "esperado um objeto {chave: valor}")

    gravadas = 0
    with db.pool.connection() as con:
        for chave, valor in corpo.items():
            if chave not in CHAVES_ESTADO:
                continue  # ignora chave desconhecida em vez de encher o banco
            if valor is None:
                con.execute("DELETE FROM estado WHERE chave = %s", (chave,))
            else:
                con.execute(
                    "INSERT INTO estado (chave, valor, atualizado_em) "
                    "VALUES (%s, %s, now()) "
                    "ON CONFLICT (chave) DO UPDATE "
                    "SET valor = EXCLUDED.valor, atualizado_em = now()",
                    (chave, str(valor)))
            gravadas += 1
    return {"gravadas": gravadas}


@app.get("/api/lotes")
def api_lotes(request: Request):
    exigir_login(request)
    linhas = db.buscar(
        "SELECT id, ref, arquivos, contas, novas, ausentes, aum, status, criado_em "
        "FROM lotes ORDER BY criado_em DESC LIMIT 50")
    return JSONResponse(json.loads(dumps(linhas)))


@app.post("/api/upload")
async def api_upload(request: Request, arquivo: UploadFile = File(...)):
    """Recebe PDF do XPerformance ou imagem. O agente de lote processa depois."""
    exigir_login(request)
    conteudo = await arquivo.read()
    if len(conteudo) > MAX_UPLOAD:
        raise HTTPException(413, f"arquivo acima de {MAX_UPLOAD // 1024 // 1024} MB")
    linha = db.buscar_um(
        "INSERT INTO uploads (nome, tipo, tamanho, conteudo) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (arquivo.filename, arquivo.content_type, len(conteudo), conteudo))
    return {"id": linha["id"], "nome": arquivo.filename, "status": "pendente"}


@app.get("/api/uploads")
def api_uploads(request: Request):
    exigir_login(request)
    linhas = db.buscar(
        "SELECT id, nome, tipo, tamanho, status, erro, criado_em "
        "FROM uploads ORDER BY criado_em DESC LIMIT 100")
    return JSONResponse(json.loads(dumps(linhas)))


@app.post("/api/lote")
async def api_lote_enviar(
    request: Request,
    background: BackgroundTasks,
    arquivos: list[UploadFile] = File(...),
):
    """Recebe o pacote de relatórios e começa a processar em segundo plano.

    Devolve o id do lote na hora; o cockpit acompanha o andamento por
    /api/lote/{id} e recarrega a página quando termina.
    """
    exigir_login(request)

    recebidos = []
    for arquivo in arquivos:
        conteudo = await arquivo.read()
        if len(conteudo) > MAX_UPLOAD:
            raise HTTPException(
                413, f"{arquivo.filename} passa de {MAX_UPLOAD // 1024 // 1024} MB")
        recebidos.append((arquivo.filename, conteudo))
        db.executar(
            "INSERT INTO uploads (nome, tipo, tamanho, conteudo, status) "
            "VALUES (%s, %s, %s, %s, 'processando')",
            (arquivo.filename, arquivo.content_type, len(conteudo), conteudo))

    if not recebidos:
        raise HTTPException(400, "nenhum arquivo recebido")

    lote_id = importador.abrir_lote()
    background.add_task(_processar_lote, lote_id, recebidos)
    return {"lote_id": lote_id, "arquivos": len(recebidos)}


def _processar_lote(lote_id: int, arquivos):
    """Roda fora da requisição. Vai anotando o andamento na linha do lote."""
    def progresso(i, total, nome):
        db.executar(
            "UPDATE lotes SET relatorio = jsonb_build_object("
            "  'fase', 'lendo', 'atual', %s::int, 'total', %s::int, 'arquivo', %s::text) "
            "WHERE id = %s", (i, total, nome, lote_id))

    try:
        importador.processar(arquivos, lote_id=lote_id, progresso=progresso)
    except Exception as e:                                   # noqa: BLE001
        importador.finalizar(
            lote_id, "rejeitado",
            {"status": "rejeitado", "motivo": f"{type(e).__name__}: {e}",
             "falhas": [], "avisos": []}, len(arquivos), 0, 0, 0, None)
    finally:
        db.executar(
            "UPDATE uploads SET status = CASE WHEN %s IN "
            "  (SELECT id FROM lotes WHERE status = 'ok') THEN 'processado' "
            "  ELSE 'rejeitado' END, lote_id = %s "
            "WHERE status = 'processando'", (lote_id, lote_id))


@app.get("/api/lote/{lote_id}")
def api_lote_status(request: Request, lote_id: int):
    exigir_login(request)
    linha = db.buscar_um(
        "SELECT id, status, contas, novas, ausentes, aum, relatorio, criado_em "
        "FROM lotes WHERE id = %s", (lote_id,))
    if not linha:
        raise HTTPException(404, "lote não encontrado")
    return JSONResponse(json.loads(dumps(linha)))


@app.get("/importar", response_class=HTMLResponse)
def tela_importar(request: Request):
    """Traz para o banco o que está preso no localStorage do navegador antigo."""
    if not logado(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse((BASE / "importar.html").read_text(encoding="utf-8"))


@app.post("/api/importar-estado")
async def api_importar_estado(request: Request, arquivo: UploadFile = File(...)):
    exigir_login(request)
    try:
        bruto = json.loads((await arquivo.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "o arquivo não é um JSON válido")

    dados = bruto.get("dados", bruto)
    if not isinstance(dados, dict):
        raise HTTPException(400, "formato não reconhecido")

    conhecidas = {k: v for k, v in dados.items() if k in CHAVES_ESTADO}
    if not conhecidas:
        raise HTTPException(400, "nenhuma chave do cockpit encontrada no arquivo")

    with db.pool.connection() as con:
        for chave, valor in conhecidas.items():
            con.execute(
                "INSERT INTO estado (chave, valor, atualizado_em) VALUES (%s, %s, now()) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, atualizado_em = now()",
                (chave, str(valor)))

    return {"importadas": sorted(conhecidas), "ignoradas": sorted(set(dados) - set(conhecidas))}


SCRIPTS = {"sync.js", "upload.js", "gatilhos.js", "atuacao.js"}


@app.get("/static/{nome}")
def estatico(nome: str):
    """Serve apenas os dois scripts do cockpit.

    No layout plano o código-fonte mora na mesma pasta, então publicar o
    diretório inteiro exporia main.py e auth.py. Esta rota entrega só o que
    o navegador precisa.
    """
    if nome not in SCRIPTS:
        raise HTTPException(404, "não encontrado")
    return Response((BASE / nome).read_text(encoding="utf-8"),
                    media_type="application/javascript")


# ------------------------------------------------------------------ cotações

_COTACOES_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_SEGUNDOS = int(os.environ.get("COTACOES_CACHE_S", "60"))


@app.get("/api/cotacoes")
def api_cotacoes(request: Request, tickers: str, token: str = ""):
    """Busca cotações na brapi.dev pelo servidor, não pelo navegador.

    Três motivos para não deixar o navegador chamar a brapi direto:
    o navegador bloqueia chamadas entre domínios, o token apareceria no
    código-fonte da página para qualquer um que abrisse o cockpit, e sem
    cache cada clique consome a cota mensal de 15 mil requisições.

    O token sai de BRAPI_TOKEN no ambiente. Se não houver, aceita o que o
    cockpit mandar — assim continua funcionando com o token salvo no
    navegador enquanto a variável não for criada.
    """
    exigir_login(request)

    lista = ",".join(t.strip() for t in tickers.split(",") if t.strip())[:400]
    if not lista:
        raise HTTPException(400, "nenhum ticker informado")

    agora = time.time()
    em_cache = _COTACOES_CACHE.get(lista)
    if em_cache and agora - em_cache[0] < CACHE_SEGUNDOS:
        return JSONResponse(em_cache[1])

    chave = os.environ.get("BRAPI_TOKEN", "").strip() or token.strip()
    url = f"https://brapi.dev/api/quote/{quote(lista, safe=',^')}"
    if chave:
        url += f"?token={quote(chave)}"

    try:
        req = Request_urllib(url, headers={"User-Agent": "cockpit-bfr"})
        with urlopen(req, timeout=12) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")[:200]
        raise HTTPException(502, f"brapi respondeu {e.code}: {corpo}")
    except URLError as e:
        raise HTTPException(502, f"não deu para falar com a brapi: {e.reason}")

    _COTACOES_CACHE[lista] = (agora, dados)
    if len(_COTACOES_CACHE) > 300:
        _COTACOES_CACHE.clear()
    return JSONResponse(dados)


# ------------------------------------------------------------------ gatilhos

@app.get("/api/gatilhos")
def api_gatilhos(request: Request):
    exigir_login(request)
    linhas = db.buscar(
        "SELECT id, nome, tipo, origem, tag, ativo, cor, texto, condicao, aplica, "
        "       produtos, ordem FROM gatilhos ORDER BY ordem, id")
    return JSONResponse(json.loads(dumps(linhas)))


@app.post("/api/gatilhos")
async def api_gatilho_gravar(request: Request):
    """Cria ou atualiza uma regra. Com `id`, atualiza; sem, cria.

    Para regra de fábrica o identificador é a `tag`: a linha guarda só o que
    você mudou nela, e apagar a linha devolve o comportamento original.
    """
    exigir_login(request)
    g = await request.json()

    campos = (g.get("nome"), g.get("tipo", "carteira"), g.get("origem", "propria"),
              g.get("tag"), bool(g.get("ativo", True)), g.get("cor", "gold"),
              g.get("texto"), dumps(g.get("condicao") or {}),
              dumps(g.get("aplica") or {"escopo": "todos"}),
              dumps(g.get("produtos") or []), int(g.get("ordem", 100)))

    if not g.get("nome"):
        raise HTTPException(400, "a regra precisa de um nome")

    if g.get("id"):
        linha = db.buscar_um(
            "UPDATE gatilhos SET nome=%s, tipo=%s, origem=%s, tag=%s, ativo=%s, cor=%s, "
            "texto=%s, condicao=%s, aplica=%s, produtos=%s, ordem=%s, alterado_em=now() "
            "WHERE id=%s RETURNING id", campos + (g["id"],))
        if not linha:
            raise HTTPException(404, "gatilho não encontrado")
    elif g.get("tag"):
        linha = db.buscar_um(
            "INSERT INTO gatilhos (nome, tipo, origem, tag, ativo, cor, texto, condicao, "
            "aplica, produtos, ordem) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (tag) WHERE tag IS NOT NULL DO UPDATE SET nome=EXCLUDED.nome, "
            "  ativo=EXCLUDED.ativo, "
            "  cor=EXCLUDED.cor, texto=EXCLUDED.texto, condicao=EXCLUDED.condicao, "
            "  aplica=EXCLUDED.aplica, produtos=EXCLUDED.produtos, alterado_em=now() "
            "RETURNING id", campos)
    else:
        linha = db.buscar_um(
            "INSERT INTO gatilhos (nome, tipo, origem, tag, ativo, cor, texto, condicao, "
            "aplica, produtos, ordem) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING id", campos)
    return {"id": linha["id"]}


@app.delete("/api/gatilhos/{gatilho_id}")
def api_gatilho_apagar(request: Request, gatilho_id: int):
    """Apagar uma regra de fábrica não a remove: devolve o texto original."""
    exigir_login(request)
    db.executar("DELETE FROM gatilhos WHERE id = %s", (gatilho_id,))
    return {"ok": True}


@app.get("/api/avisos")
def api_avisos(request: Request, situacao: str = "aberto"):
    exigir_login(request)
    linhas = db.buscar(
        "SELECT a.id, a.conta, a.titulo, a.detalhe, a.valor, a.situacao, a.adiado_ate, "
        "       a.criado_em, g.nome AS gatilho FROM avisos a "
        "LEFT JOIN gatilhos g ON g.id = a.gatilho_id "
        "WHERE a.situacao = %s ORDER BY a.criado_em DESC LIMIT 200", (situacao,))
    return JSONResponse(json.loads(dumps(linhas)))


# ----------------------------------------------------- consultas salvas

@app.get("/api/consultas")
def api_consultas(request: Request):
    exigir_login(request)
    linhas = db.buscar("SELECT id, nome, condicoes, acao FROM consultas ORDER BY nome")
    return JSONResponse(json.loads(dumps(linhas)))


@app.post("/api/consultas")
async def api_consulta_gravar(request: Request):
    exigir_login(request)
    c = await request.json()
    if not c.get("nome"):
        raise HTTPException(400, "a consulta precisa de um nome")
    linha = db.buscar_um(
        "INSERT INTO consultas (nome, condicoes, acao) VALUES (%s, %s, %s) RETURNING id",
        (c["nome"], dumps(c.get("condicoes") or []), dumps(c.get("acao") or {})))
    return {"id": linha["id"]}


@app.delete("/api/consultas/{consulta_id}")
def api_consulta_apagar(request: Request, consulta_id: int):
    exigir_login(request)
    db.executar("DELETE FROM consultas WHERE id = %s", (consulta_id,))
    return {"ok": True}


@app.get("/healthz")
def saude():
    """Usado pelo healthcheck do Railway. Não exige login de propósito."""
    try:
        db.buscar_um("SELECT 1 AS ok")
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=503)
