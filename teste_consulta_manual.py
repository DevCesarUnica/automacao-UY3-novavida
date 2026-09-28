"""Script de teste manual: consulta OffersRequest com startDate/endDate fixos
(24/09 a 26/09), fora do calculo automatico de uy3_extraction.extrair().
Nao roda o resto do pipeline (tratamento/dedup/novavida) - so a consulta.
"""
from src.uy3_api_client import buscar_offers_request, obter_token
from src.logging_setup import get_logger

logger = get_logger("teste_consulta_manual")

START_DATE = "2026-09-24"
END_DATE = "2026-09-26"

token = obter_token()
registros = buscar_offers_request(token, START_DATE, END_DATE)
print(f"\nTOTAL: {len(registros)} registro(s) para o intervalo {START_DATE} a {END_DATE}")
