"""Consulta de teste: startDate=2026-09-26, endDate=2026-09-27, ambiente de
producao, gerando a planilha completa (todos os campos) via
exportar_todos_campos.exportar().
"""
from pathlib import Path

from src.exportar_todos_campos import exportar

START_DATE = "2026-09-26"
END_DATE = "2026-09-27"

destino = Path("data/entregas/UY3_TESTE_2609_2709_TODOS_OS_CAMPOS.xlsx")
exportar(START_DATE, END_DATE, destino, permitir_consulta_real=True)
