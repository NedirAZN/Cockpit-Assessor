"""Agente de lote (cron): processa os PDFs que estão pendentes no banco.

    python -m agentes.lote            # processa o que estiver pendente
    python -m agentes.lote --simular  # lê e relata, sem gravar nada

A lógica de verdade mora em agentes/importador.py, compartilhada com o botão
de atualizar do cockpit — assim os dois caminhos se comportam igual.
"""
import argparse
import sys
from pathlib import Path

import db
from util import brl
import importador  # noqa: E402


def main(simular=False):
    db.abrir()
    db.criar_esquema()

    pendentes = db.buscar(
        "SELECT id, nome, conteudo FROM uploads WHERE status = 'pendente' ORDER BY id")
    if not pendentes:
        print("nada pendente.")
        db.fechar()
        return 0

    print(f"{len(pendentes)} arquivo(s) pendente(s)\n")
    arquivos = [(u["nome"], bytes(u["conteudo"])) for u in pendentes]

    lote_id = None if simular else importador.abrir_lote()

    def mostrar(i, total, nome):
        print(f"  [{i}/{total}] {nome}")

    rel = importador.processar(arquivos, lote_id=lote_id, simular=simular,
                               progresso=mostrar)

    print(f"\nstatus      {rel['status']}")
    print(f"PDFs        {rel.get('pdfs', 0)} · lidos {rel.get('lidos', 0)} · "
          f"falhas {len(rel['falhas'])}")
    if rel.get("aum"):
        print(f"AUM do lote {brl(rel['aum'])}")
    if rel.get("novas"):
        print(f"contas novas {len(rel['novas'])}: {', '.join(rel['novas'][:10])}")
    if rel.get("ausentes"):
        print(f"ausentes     {len(rel['ausentes'])}: {', '.join(rel['ausentes'][:10])}")
    if rel.get("meses_historico"):
        print(f"histórico    {rel['meses_historico']} meses gravados")
    for f in rel["falhas"]:
        print(f"  ✗ {f['arquivo']}: {f['erro']}")
    for a in rel["avisos"]:
        print(f"  ⚠ {a['arquivo']}: {a['avisos']}")
    for s in rel.get("saltos", []):
        print(f"  ! conta {s['conta']}: {s['variacao']:+.1f}%  "
              f"{brl(s['de'])} → {brl(s['para'])}")
    if rel.get("motivo"):
        print(f"\n{rel['motivo']}")

    if not simular:
        marca = "processado" if rel["status"] == "ok" else "rejeitado"
        for u in pendentes:
            db.executar("UPDATE uploads SET status = %s, lote_id = %s WHERE id = %s",
                        (marca, lote_id, u["id"]))

    db.fechar()
    return 0 if rel["status"] in ("ok", "simulado") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Processa os PDFs pendentes.")
    ap.add_argument("--simular", action="store_true", help="lê e relata sem gravar")
    sys.exit(main(simular=ap.parse_args().simular))
