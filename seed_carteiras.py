"""Carrega as 94 contas extraídas do Cockpit v6 (ref 31/08/2026) no Postgres.

Roda uma vez, na primeira subida — a aplicação chama sozinha quando o banco está
vazio. É seguro rodar de novo: atualiza as contas existentes em vez de duplicar.

    python -m scripts.seed_carteiras
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import db
from util import brl

ARQUIVO = Path(__file__).resolve().parent / "seed_carteiras.json"


def para_data(texto):
    if not texto:
        return None
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def semear():
    """Grava o seed. Assume que o pool já está aberto (é o caso no startup)."""
    carteiras = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    print(f"lendo {len(carteiras)} contas de {ARQUIVO.name}")

    inseridas = 0
    aum = 0.0
    with db.pool.connection() as con:
        for c in carteiras:
            ref = para_data(c.get("ref"))
            patrimonio = c.get("patrimonio")
            aum += patrimonio or 0
            con.execute(
                "INSERT INTO carteiras (conta, ref, assessor, segmento, patrimonio, dados) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (conta) DO UPDATE SET "
                "  ref = EXCLUDED.ref, assessor = EXCLUDED.assessor, "
                "  segmento = EXCLUDED.segmento, patrimonio = EXCLUDED.patrimonio, "
                "  dados = EXCLUDED.dados, atualizado_em = now()",
                (c["conta"], ref, c.get("assessor"), c.get("segmento"),
                 patrimonio, json.dumps(c, ensure_ascii=False)))

            if ref:
                con.execute(
                    "INSERT INTO historico (conta, ref, patrimonio, rent_mes, ganho_mes, rent_24m) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (conta, ref) DO NOTHING",
                    (c["conta"], ref, patrimonio, c.get("rent_mes"),
                     c.get("ganho_mes"), c.get("rent_24m")))
            inseridas += 1

        con.execute(
            "INSERT INTO lotes (ref, arquivos, contas, novas, ausentes, aum, status, relatorio) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (para_data(carteiras[0].get("ref")), 0, inseridas, inseridas, 0, aum, "ok",
             json.dumps({"origem": "seed do Cockpit v6"}, ensure_ascii=False)))

    print(f"gravadas {inseridas} contas · AUM {brl(aum)}")
    return inseridas


def main():
    """Uso pela linha de comando: abre e fecha o pool por conta própria."""
    db.abrir()
    db.criar_esquema()
    semear()
    db.fechar()


if __name__ == "__main__":
    main()
