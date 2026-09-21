"""Utilitario standalone: inspeciona os nomes de campo de um JSON bruto salvo
por uy3_extraction.py. O schema do OffersRequest ja foi confirmado com dados
reais (ver o mapeamento hardcoded em src/data_treatment.py e o payload de
exemplo em docs/Diagnostico_403_AuctionQuery.md) - esta ferramenta continua
util para conferir rapidamente uma extracao especifica ou detectar se a API
mudou de formato (campo novo, campo sumiu, etc.) sem precisar abrir o JSON
inteiro manualmente.

Uso:
  python -m src.inspecionar_schema data/uy3_bruto/UY3_0409_1130.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


def inspecionar(caminho: Path) -> None:
    registros = json.loads(caminho.read_text(encoding="utf-8"))
    if not registros:
        print(f"{caminho}: 0 registros - nada para inspecionar.")
        return

    df = pd.json_normalize(registros)
    print(f"{caminho}: {len(registros)} registro(s), {len(df.columns)} campo(s) encontrado(s):\n")
    for coluna in sorted(df.columns):
        tem_valor = df[coluna].notna().any()
        exemplo = df[coluna].dropna().iloc[0] if tem_valor else None
        print(f"  {coluna!r:45s} exemplo: {exemplo!r}")

    print(
        "\nCompare os campos acima com o mapeamento hardcoded em "
        "src/data_treatment.py (registrationNumber, employeeName, bestOffer, "
        "availableMargin, birthDate, eligible, ...) - se algo mudou, ajuste o "
        "codigo la."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m src.inspecionar_schema <caminho_do_json>")
        sys.exit(1)
    inspecionar(Path(sys.argv[1]))
