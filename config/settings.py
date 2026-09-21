import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# UY3 Credit API (OAuth2 client_credentials via Cognito)
# ---------------------------------------------------------------------------
# UY3_AMBIENTE escolhe qual par de credenciais/URLs usar, no mesmo espirito
# dos dois environments ja criados no Postman ("UY3 Credit API - Homologacao"
# e "UY3 Credit API - Producao"): troque so essa variavel no .env para alternar,
# sem precisar editar mais nada.
UY3_AMBIENTE = os.environ.get("UY3_AMBIENTE", "homologacao").strip().lower()

if UY3_AMBIENTE == "producao":
    UY3_TOKEN_URL = os.environ["UY3_TOKEN_URL_PRODUCAO"]
    UY3_BASE_URL = os.environ["UY3_BASE_URL_PRODUCAO"]
    UY3_BASIC_AUTH = os.environ["UY3_BASIC_AUTH_PRODUCAO"]
elif UY3_AMBIENTE == "homologacao":
    UY3_TOKEN_URL = os.environ["UY3_TOKEN_URL_HOMOLOGACAO"]
    UY3_BASE_URL = os.environ["UY3_BASE_URL_HOMOLOGACAO"]
    UY3_BASIC_AUTH = os.environ["UY3_BASIC_AUTH_HOMOLOGACAO"]
else:
    raise ValueError(
        f"UY3_AMBIENTE invalido: '{UY3_AMBIENTE}' - use 'homologacao' ou 'producao'."
    )

UY3_SCOPE = os.environ.get("UY3_SCOPE", "credit/api_unicaii")
UY3_OFFERS_REQUEST_PATH = "/v1/DataprevEmployee/OffersRequest"
UY3_PAGE_SIZE = 250  # fixo, conforme documentacao do endpoint (secao 3.1)

# Janela de datas consultada a cada ciclo (o endpoint filtra so por DIA,
# formato yyyy-MM-dd - nao aceita hora).
UY3_JANELA_DIAS_CONSULTA = int(os.environ.get("UY3_JANELA_DIAS_CONSULTA", "1"))

# Schema da resposta do OffersRequest CONFIRMADO em 08/09/2026 com dados reais
# de producao (188 registros) - ver docs/Diagnostico_403_AuctionQuery.md. Os
# nomes de campo estao hardcoded em src/data_treatment.py (nao sao mais
# configuraveis por .env, ja que o schema e conhecido). Resumo:
#   registrationNumber (CPF), employeeName (Nome), birthDate (DDMMAAAA),
#   availableMargin (Margem Disponivel, em CENTAVOS), eligible (bool),
#   bestOffer[0].liquidValueInCents (Valor Liberado, em CENTAVOS),
#   bestOffer[0].numberOfPayments (Numero de Parcelas).
# O endpoint NAO devolve uma data de consulta por registro (so o intervalo
# startDateTime/endDateTime da PAGINA inteira, que e o proprio periodo
# pedido) - por isso data_treatment.py usa o horario da propria extracao
# como "Data da Consulta" (so para ordenacao na base final).

# ---------------------------------------------------------------------------
# Nova Vida (Plataforma Ipe) - inalterado em relacao ao processo original
# ---------------------------------------------------------------------------
NOVAVIDA_USER = os.environ["NOVAVIDA_USER"]
NOVAVIDA_PASS = os.environ["NOVAVIDA_PASS"]
NOVAVIDA_EMPRESA = os.environ["NOVAVIDA_EMPRESA"]
NOVAVIDA_URL = os.environ["NOVAVIDA_URL"]

NOVAVIDA_CAMPANHA = os.environ.get("NOVAVIDA_CAMPANHA", "")
NOVAVIDA_PROCESSO = os.environ.get("NOVAVIDA_PROCESSO", "")

# ---------------------------------------------------------------------------
# Regras de negocio / filtros
# ---------------------------------------------------------------------------
VALOR_MIN = float(os.environ.get("VALOR_MIN", "4001.00"))
VALOR_MAX = float(os.environ.get("VALOR_MAX", "8000.99"))

# Limiar de elegibilidade (Valor Liberado > este valor), configurado POR
# AMBIENTE (pedido explicito do usuario em 08/09/2026): em producao mantem o
# limiar de R$4.000,00 herdado do processo Astor Tech; em homologacao fica
# em R$0,00 (sem filtro de valor - so serve para teste/validacao do
# pipeline, ja que homologacao nunca teve dados reais de negocio mesmo).
# ATENCAO sobre o valor de producao: nos primeiros 188 registros reais do
# OffersRequest observados em 08/09/2026, o "Valor Liberado"
# (bestOffer.liquidValueInCents) tipico ficou entre ~R$175 e ~R$2.100 - ou
# seja, o limiar de R$4.000 tende a zerar quase toda extracao real. Mantido
# assim a pedido do usuario, mas reveja com o negocio antes de depender
# deste numero de fato (ver docs/Diagnostico_403_AuctionQuery.md).
if UY3_AMBIENTE == "producao":
    VALOR_MINIMO_REGRA_NEGOCIO = float(
        os.environ.get("VALOR_MINIMO_REGRA_NEGOCIO_PRODUCAO", "4000.00")
    )
else:
    VALOR_MINIMO_REGRA_NEGOCIO = float(
        os.environ.get("VALOR_MINIMO_REGRA_NEGOCIO_HOMOLOGACAO", "0.00")
    )

INTERVALO_HORAS = float(os.environ.get("INTERVALO_HORAS", "1"))

# ---------------------------------------------------------------------------
# E-mail (envio via Outlook Web / Playwright - SMTP desativado no tenant)
# ---------------------------------------------------------------------------
EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_REMETENTE = os.environ.get("EMAIL_REMETENTE", "")
EMAIL_SENHA = os.environ.get("EMAIL_SENHA", "")
EMAIL_DESTINATARIO = os.environ.get("EMAIL_DESTINATARIO", "cs@unicapromotora.com.br")

# ---------------------------------------------------------------------------
# Diretorios
# ---------------------------------------------------------------------------
DATA_RAW_DIR = BASE_DIR / "data" / "uy3_bruto"  # JSON bruto devolvido pela API, sem tratamento
DATA_TREATED_DIR = BASE_DIR / "data" / "treated"
DATA_FINAL_DIR = BASE_DIR / "data" / "final"
DATA_NOVAVIDA_DIR = BASE_DIR / "data" / "novavida"
DATA_ENTREGAS_DIR = BASE_DIR / "data" / "entregas"
LOGS_DIR = BASE_DIR / "logs"

for _dir in (DATA_RAW_DIR, DATA_TREATED_DIR, DATA_FINAL_DIR, DATA_NOVAVIDA_DIR, DATA_ENTREGAS_DIR, LOGS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
