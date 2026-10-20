"""Backup diário: retrato de carteiras + estado dentro do próprio banco.

O Railway já faz backup do Postgres, mas isso te protege de perda de máquina,
não de erro seu — um lote importado torto, uma anotação apagada sem querer.
Este snapshot é o que te deixa voltar um dia atrás sem abrir chamado.

Guarda os últimos 30. Roda como cron no Railway:  python -m agentes.backup
"""
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import db
from util import brl

RETENCAO = 30


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(str(type(o)))


def main():
    db.abrir()
    db.criar_esquema()

    carteiras = [l["dados"] for l in db.buscar("SELECT dados FROM carteiras ORDER BY conta")]
    estado = {l["chave"]: l["valor"] for l in db.buscar("SELECT chave, valor FROM estado")}

    conteudo = {
        "gerado": datetime.now().isoformat(timespec="seconds"),
        "contas": len(carteiras),
        "aum": sum(c.get("patrimonio") or 0 for c in carteiras),
        "carteiras": carteiras,
        "estado": estado,
    }

    db.executar("INSERT INTO backups (conteudo, contas) VALUES (%s, %s)",
                (json.dumps(conteudo, ensure_ascii=False, default=_default), len(carteiras)))

    removidos = db.executar(
        "DELETE FROM backups WHERE id NOT IN "
        "(SELECT id FROM backups ORDER BY criado_em DESC LIMIT %s)", (RETENCAO,))

    print(f"backup: {len(carteiras)} contas · AUM {brl(conteudo['aum'])} · "
          f"{len(estado)} chaves de estado · {removidos} antigo(s) removido(s)")
    db.fechar()


if __name__ == "__main__":
    main()
