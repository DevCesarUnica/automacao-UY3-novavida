"""Modulo 1: extracao de leads via UY3 Credit API (GET
/v1/DataprevEmployee/OffersRequest), substituindo o scraping via Playwright
que a automacao original fazia no Astor Tech.

STATUS: endpoint bloqueado por 403 em homologacao (falta a permissao
AuctionQuery/NaturalPerson no client - ver
docs/Diagnostico_403_AuctionQuery.md). Em producao (UY3_AMBIENTE=producao) a
chamada ja funcionou e o schema real da resposta foi confirmado com dados
reais (ver mapeamento de campos em src/data_treatment.py e o payload de
exemplo em docs/Diagnostico_403_AuctionQuery.md). Se a API mudar no futuro,
rode:
  python -m src.inspecionar_schema data/uy3_bruto/<arquivo>.json
para conferir os campos que estao vindo e comparar com o que
data_treatment.py espera.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from src.logging_setup import get_logger
from src.uy3_api_client import buscar_offers_request, obter_token

logger = get_logger("uy3_extraction")


def extrair(permitir_consulta_real: bool = False, headless: bool = True) -> Path:
    """Busca leads via API na janela de UY3_JANELA_DIAS_CONSULTA dias
    (terminando hoje) e salva o JSON bruto em data/uy3_bruto/.

    `permitir_consulta_real` e uma trava de seguranca proposital, no mesmo
    espirito do antigo astor_extraction.extrair(): a chamada traz dados reais
    de pessoas fisicas (CPF, nome, etc.) do ambiente configurado em
    UY3_AMBIENTE - so passe True apos confirmar que rodar contra esse
    ambiente (especialmente producao) agora e intencional.

    `headless` nao tem efeito aqui (nao ha navegador nesta etapa - e uma
    chamada HTTP direta) - o parametro so existe para preservar a mesma
    assinatura de astor_extraction.extrair() usada por orchestrator.py.
    """
    if not permitir_consulta_real:
        raise RuntimeError(
            "Extracao real bloqueada por seguranca: chame extrair(permitir_consulta_real=True) "
            "apenas apos confirmar que consultar o ambiente "
            f"'{settings.UY3_AMBIENTE}' agora e intencional."
        )

    # fim = datetime.now()
    # inicio = fim - timedelta(days=settings.UY3_JANELA_DIAS_CONSULTA)
    # start_date = inicio.strftime("%Y-%m-%d")
    # end_date = fim.strftime("%Y-%m-%d")
    start_date = "2026-09-28"
    end_date = "2026-09-29"
    
    #Regra atual hoje até amanhã
    # cd automacao-uy3-novavida   
    # python -m src.orchestrator --once --permitir-consulta-real --pular-novavida

    token = obter_token()
    registros = buscar_offers_request(token, start_date, end_date)

    nome_arquivo = f"UY3_{datetime.now():%d%m_%H%M}.json"
    destino = settings.DATA_RAW_DIR / nome_arquivo
    destino.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Extracao via API concluida: %d registro(s) (%s a %s, ambiente=%s) salvos em %s",
        len(registros),
        start_date,
        end_date,
        settings.UY3_AMBIENTE,
        destino,
    )
    return destino


if __name__ == "__main__":
    extrair(permitir_consulta_real=False)
