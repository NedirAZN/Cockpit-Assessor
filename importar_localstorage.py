"""Versão de linha de comando da página /importar.

Traz para o banco o que está preso no localStorage do navegador antigo.

Como gerar o arquivo:
  1. abra o cockpit antigo, logado
  2. console do navegador (F12 → Console)
  3. rode:  copy(JSON.stringify(localStorage))
  4. cole num arquivo chamado localstorage.json

Depois:
  python -m scripts.importar_localstorage localstorage.json
"""
import json
import sys
from pathlib import Path

import db
from main import CHAVES_ESTADO


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    bruto = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    dados = bruto.get("dados", bruto)  # aceita o despejo cru e o backup do cockpit

    conhecidas = {k: v for k, v in dados.items() if k in CHAVES_ESTADO}
    ignoradas = sorted(set(dados) - set(conhecidas))

    if not conhecidas:
        print("nenhuma chave bfr_* reconhecida no arquivo. Nada foi gravado.")
        sys.exit(1)

    db.abrir()
    db.criar_esquema()
    with db.pool.connection() as con:
        for chave, valor in conhecidas.items():
            con.execute(
                "INSERT INTO estado (chave, valor, atualizado_em) VALUES (%s, %s, now()) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, atualizado_em = now()",
                (chave, str(valor)))
            print(f"  gravado {chave} ({len(str(valor))} caracteres)")
    db.fechar()

    print(f"\n{len(conhecidas)} conjunto(s) no banco.")
    if ignoradas:
        print(f"ignoradas (não são do cockpit): {', '.join(ignoradas)}")


if __name__ == "__main__":
    main()
