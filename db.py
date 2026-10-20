"""Conexão com o Postgres. Um pool só, compartilhado pela aplicação."""
import os
from pathlib import Path

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL não definida. No Railway, crie a variável apontando para o "
        "banco: ${{Postgres.DATABASE_URL}}. Localmente, copie .env.example para .env."
    )

# O Railway entrega a URL com o esquema postgres://, que o psycopg3 não aceita.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=int(os.environ.get("DB_POOL_MAX", "5")),
    kwargs={"row_factory": dict_row},
    open=False,
)


def abrir():
    pool.open()
    pool.wait(timeout=30)


def fechar():
    pool.close()


def criar_esquema():
    """Aplica o schema.sql. Idempotente — roda a cada start sem efeito colateral."""
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with pool.connection() as con:
        con.execute(sql)


def buscar(sql, params=None):
    with pool.connection() as con:
        return con.execute(sql, params or ()).fetchall()


def buscar_um(sql, params=None):
    linhas = buscar(sql, params)
    return linhas[0] if linhas else None


def executar(sql, params=None):
    with pool.connection() as con:
        cur = con.execute(sql, params or ())
        return cur.rowcount
