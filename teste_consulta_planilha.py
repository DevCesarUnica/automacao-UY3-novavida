"""Gera a planilha completa (todos os campos) para o mesmo teste manual
(startDate=2026-09-24, endDate=2026-09-26), usando o mesmo caminho de
exportar_todos_campos.py (filtro fixo Valor Liberado >= R$4.000).
"""
from pathlib import Path

from src.exportar_todos_campos import exportar

START_DATE = "2026-09-24"
END_DATE = "2026-09-26"

destino = Path("data/entregas/UY3_TESTE_2409_2609_TODOS_OS_CAMPOS.xlsx")
exportar(START_DATE, END_DATE, destino, permitir_consulta_real=True)
