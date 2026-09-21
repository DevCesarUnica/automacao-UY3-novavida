"""Cliente HTTP para a UY3 Credit API (OAuth2 client_credentials via Cognito).

Endpoint alvo: GET /v1/DataprevEmployee/OffersRequest (ver
docs/Diagnostico_403_AuctionQuery.md). Continua bloqueado por 403
MISSING_RESOURCE_PERMISSION (permissionType AuctionQuery, resource
NaturalPerson) no client de homologacao configurado em
UY3_BASIC_AUTH_HOMOLOGACAO. Em producao (UY3_AMBIENTE=producao) a mesma
chamada JA FUNCIONOU e o schema real da resposta foi confirmado em
08/09/2026 (188 registros reais) - ver o mapeamento de campos em
src/data_treatment.py e o payload de exemplo em
docs/Diagnostico_403_AuctionQuery.md.

Fluxo de autenticacao confirmado ao vivo via Postman (collection
UY3-Credit-API.postman_collection.json, request "1 - Obter Token"):
  POST {token_url}
  Authorization: Basic {basic_auth}      <- client_id:client_secret em base64
  Content-Type: application/x-www-form-urlencoded
  grant_type=client_credentials&scope=credit/api_unicaii
Resposta 200: {"access_token": "<JWT>", "expires_in": 3600, "token_type": "Bearer"}
"""
from __future__ import annotations

import requests

from config import settings
from src.logging_setup import get_logger

logger = get_logger("uy3_api_client")


def obter_token() -> str:
    resp = requests.post(
        settings.UY3_TOKEN_URL,
        headers={
            "Authorization": f"Basic {settings.UY3_BASIC_AUTH}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": settings.UY3_SCOPE},
        timeout=30,
    )
    resp.raise_for_status()
    dados = resp.json()
    if "access_token" not in dados:
        raise RuntimeError(f"Resposta do token sem 'access_token': {dados}")
    logger.info(
        "Token obtido com sucesso (ambiente=%s, expira em %ss).",
        settings.UY3_AMBIENTE,
        dados.get("expires_in"),
    )
    return dados["access_token"]


def _extrair_lista(payload) -> list[dict]:
    """A resposta pode vir como lista direta ou envelopada (ex.: {"items": [...],
    "totalItems": N}, como no endpoint CreditNote da mesma API - ver request
    "2 - Consultar Leads Leilao CLT" da collection). Tenta as chaves mais
    comuns antes de desistir com um erro claro.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for chave in ("items", "data", "results", "content", "records"):
            valor = payload.get(chave)
            if isinstance(valor, list):
                return valor
    raise ValueError(
        "Formato de resposta inesperado do OffersRequest (nao e uma lista nem um "
        f"envelope reconhecido). Chaves de topo recebidas: "
        f"{list(payload) if isinstance(payload, dict) else type(payload).__name__}"
    )


def buscar_offers_request(access_token: str, start_date: str, end_date: str) -> list[dict]:
    """Busca todas as paginas de /v1/DataprevEmployee/OffersRequest no
    intervalo [start_date, end_date] (formato yyyy-MM-dd, conforme secao 3.1
    da documentacao do endpoint).

    A resposta real (confirmada em 08/09/2026) e um envelope com
    currentPage/totalPages/totalRecords/recordsPerPage e a lista de
    registros em "content". Pagina ate currentPage atingir totalPages; se o
    envelope nao trouxer totalPages por algum motivo, cai para o fallback de
    parar quando a pagina devolver menos que UY3_PAGE_SIZE registros.
    """
    url = settings.UY3_BASE_URL + settings.UY3_OFFERS_REQUEST_PATH
    headers = {"Authorization": f"Bearer {access_token}", "accept": "*/*"}

    todos: list[dict] = []
    pagina = 1
    while True:
        params = {"startDate": start_date, "endDate": end_date, "pageNumber": pagina}
        resp = requests.get(url, headers=headers, params=params, timeout=60)

        if resp.status_code == 403:
            raise PermissionError(
                "403 Forbidden em OffersRequest - permissao AuctionQuery ausente para "
                f"este client (ambiente={settings.UY3_AMBIENTE}). Corpo da resposta: "
                f"{resp.text[:500]}"
            )
        resp.raise_for_status()
        corpo = resp.json()

        lote = _extrair_lista(corpo)
        todos.extend(lote)
        total_pages = corpo.get("totalPages") if isinstance(corpo, dict) else None
        total_records = corpo.get("totalRecords") if isinstance(corpo, dict) else None
        logger.info(
            "OffersRequest pagina %d%s: %d registro(s) (total acumulado: %d%s)",
            pagina,
            f"/{total_pages}" if total_pages is not None else "",
            len(lote),
            len(todos),
            f" de {total_records}" if total_records is not None else "",
        )

        if total_pages is not None:
            if pagina >= total_pages:
                break
        elif len(lote) < settings.UY3_PAGE_SIZE:
            break
        pagina += 1

    return todos


if __name__ == "__main__":
    from datetime import datetime

    token = obter_token()
    hoje = datetime.now().strftime("%Y-%m-%d")
    registros = buscar_offers_request(token, hoje, hoje)
    print(f"{len(registros)} registro(s) recebido(s) para {hoje}.")
