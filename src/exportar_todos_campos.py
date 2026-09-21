"""Utilitario: exporta os leads do OffersRequest com TODOS os campos.

Diferente de data_treatment.py (que usa o limiar configuravel por ambiente
em settings.VALOR_MINIMO_REGRA_NEGOCIO para o pipeline do Nova Vida), este
script:
  1. Busca TODOS os registros do periodo pedido.
  2. Aplica o filtro de negocio FIXO Valor Liberado >= R$4.000,00
     (VALOR_LIBERADO_MINIMO_FIXO abaixo - confirmado com a operacao em
     09/09/2026, nao e configuravel por .env/CLI de proposito).
  3. Achata TODAS as ofertas de bestOffer (nao so a primeira) - alguns
     leads vem com 2 ofertas: a primeira com valores financeiros normais,
     a segunda so com a sinalizacao de garantia via FGTS (valores nulos,
     warranty.hasWarranty=true) - ver docs/Diagnostico_403_AuctionQuery.md.
  4. Gera a planilha com ORDEM DE COLUNAS FIXA (COLUNAS_ORDEM_FIXA abaixo -
     confirmada com a operacao em 09/09/2026, nao muda de uma execucao para
     outra):
       a) Bloco principal: CPF, Nome, Matricula, Prazo, Parcela, Valor
          Liberado (ordem exata pedida pela operacao).
       b) Bloco de contexto (leilao de emprestimo consignado CLT): Telefone,
          Email, Data de Nascimento, Data de Admissao, Valido Ate, Consulta,
          Margem Disponivel, Elegivel Garantia FGTS, Quantidade de Ofertas,
          Numero da Proposta, Taxa de Juros, Valor Bruto, Valor Seguro, CNPJ
          do Empregador, PEP.
       c) Todo o restante dos campos brutos da API (auditoria completa),
          nesta ordem: qualquer coluna nao listada em (a)/(b).
     Telefone/Email ficam vazios aqui porque so existem depois da
     higienizacao no Nova Vida, que este script nao chama.

Uso:
  python -m src.exportar_todos_campos
  python -m src.exportar_todos_campos --ambiente producao --dias 1
  python -m src.exportar_todos_campos --ambiente homologacao --inicio 2026-01-01 --fim 2026-12-31
  python -m src.exportar_todos_campos --saida data/entregas/relatorio_completo.xlsx
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import settings
from src.logging_setup import get_logger
from src.uy3_api_client import buscar_offers_request, obter_token

logger = get_logger("exportar_todos_campos")

_COR_CABECALHO = "1F4E78"
_COLUNAS_TEXTO = {
    "CPF",
    "Telefone",
    "Matricula",
    "CNPJ do Empregador",
    "Numero da Proposta",
    # Copias brutas traduzidas dos mesmos dados (mantidas na cauda da
    # planilha para auditoria completa - ver colunas_restantes em
    # montar_planilha()) tambem precisam do formato texto, senao perdem
    # zero a esquerda.
    "CPF (bruto)",
    "Matricula (bruto)",
    "CNPJ Empregador (bruto)",
    "Oferta 1 - Numero da Proposta (bruto)",
    "Oferta 2 - Numero da Proposta (bruto)",
}

# Traducao dos nomes de campo em ingles que a API devolve, aplicada aos
# campos brutos que ficam na cauda da planilha (auditoria completa) - pedido
# explicito da operacao em 09/09/2026 para o codigo sempre entregar tudo
# traduzido, nao so os campos de negocio da frente. As colunas de bestOffer
# (ja renomeadas para "OfertaN" em _achatar_ofertas) sao tratadas a parte,
# em _TRADUCAO_CAMPOS_OFERTA, porque o numero N varia por extracao.
_TRADUCAO_CAMPOS_TOPO = {
    "requestId": "ID da Solicitacao",
    "registrationNumber": "CPF (bruto)",
    "employeeCode": "Matricula (bruto)",
    "employerRegistrationNumber": "CNPJ Empregador (bruto)",
    "requestValidUntil": "Validade da Solicitacao (bruto)",
    "employeeName": "Nome (bruto)",
    "birthDate": "Data de Nascimento (bruto)",
    "availableMargin": "Margem Disponivel (bruto, centavos)",
    "eligible": "Elegivel",
    "pep": "PEP (bruto)",
    "admissionDate": "Data de Admissao (bruto)",
    "alerts": "Alertas",
    "sendCTPS": "Enviar CTPS",
    "errorMessage": "Mensagem de Erro",
    "errorEligibility": "Erro de Elegibilidade",
    "errorSimulation": "Erro de Simulacao",
    "employerRegistration.code": "Tipo de Registro do Empregador (codigo)",
    "employerRegistration.description": "Tipo de Registro do Empregador (descricao)",
}

# Traducao dos campos de cada oferta (aplicada com o prefixo "Oferta N - "
# para cada N de 1 ate max_ofertas - ver _construir_traducao_ofertas()).
_TRADUCAO_CAMPOS_OFERTA = {
    "proposalNumber": "Numero da Proposta (bruto)",
    "numberOfPayments": "Numero de Parcelas",
    "interestRate": "Taxa de Juros (bruto)",
    "principalAmountInCents": "Valor Bruto (centavos)",
    "liquidValueInCents": "Valor Liquido (centavos)",
    "marginInCents": "Margem (centavos)",
    "insuranceValueInCents": "Valor Seguro (centavos)",
    "warranty.hasWarranty": "Possui Garantia",
    "warranty.fgtsBalanceInCents": "Saldo FGTS (centavos)",
    "warranty.fgtsRescissionPenaltyInCents": "Multa Rescisoria FGTS (centavos)",
    "warranty.rescissionBenefitPercentage": "Percentual Beneficio Rescisao",
}


def _construir_traducao_ofertas(max_ofertas: int) -> dict[str, str]:
    traducao = dict(_TRADUCAO_CAMPOS_TOPO)
    for i in range(1, max_ofertas + 1):
        for campo, rotulo in _TRADUCAO_CAMPOS_OFERTA.items():
            traducao[f"Oferta{i}.{campo}"] = f"Oferta {i} - {rotulo}"
    return traducao

# Regra de negocio fixa (confirmada com a operacao em 09/09/2026) - NAO e
# configuravel por .env/CLI de proposito, ao contrario de
# settings.VALOR_MINIMO_REGRA_NEGOCIO (que e do pipeline do Nova Vida, um
# consumidor diferente deste script).
VALOR_LIBERADO_MINIMO_FIXO = 4000.00

# Ordem de colunas fixa (confirmada com a operacao em 09/09/2026). Bloco
# principal primeiro (ordem exata pedida), depois o bloco de contexto do
# leilao CLT, depois todo o resto dos campos brutos da API (auditoria).
COLUNAS_PRINCIPAIS = ["CPF", "Nome", "Matricula", "Prazo", "Parcela", "Valor Liberado"]
COLUNAS_CONTEXTO_CLT = [
    "Telefone",
    "Email",
    "Data de Nascimento",
    "Data de Admissao",
    "Valido Ate",
    "Consulta",
    "Margem Disponivel (R$)",
    "Elegivel Garantia FGTS",
    "Quantidade de Ofertas",
    "Numero da Proposta",
    "Taxa de Juros",
    "Valor Bruto (R$)",
    "Valor Seguro (R$)",
    "CNPJ do Empregador",
    "PEP",
]


def _parse_ddmmaaaa(valor):
    try:
        return datetime.strptime(str(valor), "%d%m%Y")
    except (ValueError, TypeError):
        return None


def _parse_ddmmaaaahhmmss(valor):
    try:
        return datetime.strptime(str(valor), "%d%m%Y%H%M%S")
    except (ValueError, TypeError):
        return None


def _achatar_ofertas(registro: dict, max_ofertas: int) -> dict:
    """Espalha CADA oferta de bestOffer (campo em ingles vindo da API) em
    colunas proprias OfertaN.campo, em vez de descartar tudo alem da
    primeira. Ver docstring do modulo sobre o padrao de 2 ofertas (normal +
    garantia FGTS)."""
    plano = dict(registro)
    ofertas = plano.pop("bestOffer", None) or []
    plano["Quantidade de Ofertas"] = len(ofertas)

    for i in range(max_ofertas):
        oferta = dict(ofertas[i]) if i < len(ofertas) else {}
        warranty = oferta.pop("warranty", None) or {}
        prefixo = f"Oferta{i + 1}"
        for campo in (
            "proposalNumber",
            "numberOfPayments",
            "interestRate",
            "principalAmountInCents",
            "liquidValueInCents",
            "marginInCents",
            "insuranceValueInCents",
        ):
            plano[f"{prefixo}.{campo}"] = oferta.get(campo)
        for campo in (
            "hasWarranty",
            "fgtsBalanceInCents",
            "fgtsRescissionPenaltyInCents",
            "rescissionBenefitPercentage",
        ):
            plano[f"{prefixo}.warranty.{campo}"] = warranty.get(campo)

    emp_reg = plano.pop("employerRegistration", None) or {}
    plano["employerRegistration.code"] = emp_reg.get("code")
    plano["employerRegistration.description"] = emp_reg.get("description")

    return plano


def montar_planilha(registros: list[dict], momento_extracao: datetime) -> pd.DataFrame:
    if not registros:
        return pd.DataFrame(columns=COLUNAS_PRINCIPAIS + COLUNAS_CONTEXTO_CLT)

    max_ofertas = max(len(r.get("bestOffer") or []) for r in registros)
    max_ofertas = max(max_ofertas, 1)  # sempre gera ao menos Oferta1.*

    df = pd.DataFrame([_achatar_ofertas(r, max_ofertas) for r in registros])

    # --- Bloco principal (ordem exata pedida pela operacao) ---
    df["CPF"] = df["registrationNumber"]
    df["Nome"] = df["employeeName"]
    df["Matricula"] = df["employeeCode"]
    df["Prazo"] = df["Oferta1.numberOfPayments"]
    df["Parcela"] = df["availableMargin"] / 100  # nomenclatura herdada do processo original (ver consolidacao.py)
    df["Valor Liberado"] = df["Oferta1.liquidValueInCents"] / 100

    # Filtro de negocio FIXO (ver VALOR_LIBERADO_MINIMO_FIXO no topo do
    # modulo) - vectorized (>=), seguro mesmo se df ficar vazio depois.
    df = df[df["Valor Liberado"] >= VALOR_LIBERADO_MINIMO_FIXO]

    # --- Bloco de contexto (leilao de emprestimo consignado CLT) ---
    df["Telefone"] = pd.NA  # so existe apos higienizacao no Nova Vida (nao chamada aqui)
    df["Email"] = pd.NA  # so existe apos higienizacao no Nova Vida (nao chamada aqui)
    df["Data de Nascimento"] = df["birthDate"].apply(_parse_ddmmaaaa)
    df["Data de Admissao"] = df["admissionDate"].apply(_parse_ddmmaaaa)
    df["Valido Ate"] = df["requestValidUntil"].apply(_parse_ddmmaaaahhmmss)
    df["Consulta"] = momento_extracao  # sem data por registro na API - ver data_treatment.py
    df["Margem Disponivel (R$)"] = df["availableMargin"] / 100
    df["Elegivel Garantia FGTS"] = (
        df["Oferta2.warranty.hasWarranty"] if "Oferta2.warranty.hasWarranty" in df.columns else False
    )
    df["Elegivel Garantia FGTS"] = df["Elegivel Garantia FGTS"].fillna(False)
    df["Numero da Proposta"] = df["Oferta1.proposalNumber"]
    df["Taxa de Juros"] = df["Oferta1.interestRate"]
    df["Valor Bruto (R$)"] = df["Oferta1.principalAmountInCents"] / 100
    df["Valor Seguro (R$)"] = df["Oferta1.insuranceValueInCents"] / 100
    df["CNPJ do Empregador"] = df["employerRegistrationNumber"]
    df["PEP"] = df["pep"]

    # --- Demais campos brutos (auditoria completa, ordem livre) ---
    for i in range(1, max_ofertas + 1):
        pfx = f"Oferta{i}"  # chave interna (bate com _achatar_ofertas)
        rotulo = f"Oferta {i}"  # rotulo exibido (com espaco, igual _construir_traducao_ofertas)
        df[f"{rotulo} - Valor Liquido (R$)"] = df[f"{pfx}.liquidValueInCents"] / 100
        df[f"{rotulo} - Valor Bruto (R$)"] = df[f"{pfx}.principalAmountInCents"] / 100
        df[f"{rotulo} - Margem (R$)"] = df[f"{pfx}.marginInCents"] / 100
        df[f"{rotulo} - Valor Seguro (R$)"] = df[f"{pfx}.insuranceValueInCents"] / 100
        df[f"{rotulo} - Multa Rescisoria FGTS (R$)"] = df[f"{pfx}.warranty.fgtsRescissionPenaltyInCents"] / 100

    # Traduz os campos brutos restantes (nomes originais da API, em ingles)
    # para portugues - pedido explicito da operacao em 09/09/2026.
    df = df.rename(columns=_construir_traducao_ofertas(max_ofertas))

    colunas_restantes = [c for c in df.columns if c not in COLUNAS_PRINCIPAIS + COLUNAS_CONTEXTO_CLT]

    return df[COLUNAS_PRINCIPAIS + COLUNAS_CONTEXTO_CLT + colunas_restantes]


def formatar_planilha(df: pd.DataFrame, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(destino, index=False, sheet_name="Leads (todos os campos)")

    wb = load_workbook(destino)
    ws = wb.active
    total_linhas, total_colunas = ws.max_row, ws.max_column
    borda = Border(*(Side(style="thin", color="D9D9D9"),) * 4)

    for col_idx in range(1, total_colunas + 1):
        cel = ws.cell(row=1, column=col_idx)
        cel.font = Font(color="FFFFFF", bold=True, size=10)
        cel.fill = PatternFill("solid", fgColor=_COR_CABECALHO)
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cel.border = borda

        nome_col = df.columns[col_idx - 1]
        # .astype(str) sobre uma coluna com pd.NA deixa NaN como FLOAT, nao
        # como string "nan" (confirmado ao vivo em 08/09/2026, quebrava aqui
        # com "object of type 'float' has no len()" em colunas com celulas
        # vazias como Telefone/Email/OfertaN.* ausentes) - por isso o
        # str(v) explicito abaixo, em vez de confiar no dtype de amostra.
        amostra = df.iloc[:200, col_idx - 1]
        largura = max([len(str(nome_col))] + [len(str(v)) for v in amostra])
        ws.column_dimensions[get_column_letter(col_idx)].width = min(largura + 2, 30)
        if nome_col in _COLUNAS_TEXTO:
            for linha_idx in range(2, total_linhas + 1):
                ws.cell(row=linha_idx, column=col_idx).number_format = "@"

    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30
    if total_linhas >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(total_colunas)}{total_linhas}"
    wb.save(destino)


def exportar(
    data_inicio: str,
    data_fim: str,
    saida: Path,
    permitir_consulta_real: bool = False,
) -> Path:
    if not permitir_consulta_real:
        raise RuntimeError(
            "Exportacao real bloqueada por seguranca: chame exportar(permitir_consulta_real=True) "
            f"apenas apos confirmar que consultar o ambiente '{settings.UY3_AMBIENTE}' agora e intencional."
        )

    token = obter_token()
    registros = buscar_offers_request(token, data_inicio, data_fim)
    logger.info("Total de registros brutos recebidos: %d", len(registros))

    df = montar_planilha(registros, datetime.now())
    formatar_planilha(df, saida)
    logger.info("Planilha completa (todos os campos) salva em: %s (%d linhas)", saida, len(df))
    return saida


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ambiente",
        choices=["homologacao", "producao"],
        default=None,
        help="Sobrescreve UY3_AMBIENTE so para esta execucao (padrao: o que estiver no .env)",
    )
    parser.add_argument("--inicio", default=None, help="Data inicial (yyyy-MM-dd). Padrao: hoje - --dias")
    parser.add_argument("--fim", default=None, help="Data final (yyyy-MM-dd). Padrao: hoje")
    parser.add_argument("--dias", type=int, default=None, help="Janela em dias terminando hoje (ignora --inicio)")
    parser.add_argument("--saida", default=None, help="Caminho do .xlsx de saida")
    args = parser.parse_args()

    if args.ambiente:
        import os

        os.environ["UY3_AMBIENTE"] = args.ambiente
        # Forca settings a reler o ambiente escolhido nesta execucao.
        import importlib

        importlib.reload(settings)

    fim = datetime.strptime(args.fim, "%Y-%m-%d") if args.fim else datetime.now()
    if args.inicio:
        inicio = datetime.strptime(args.inicio, "%Y-%m-%d")
    else:
        dias = args.dias if args.dias is not None else settings.UY3_JANELA_DIAS_CONSULTA
        inicio = fim - timedelta(days=dias)

    saida = Path(args.saida) if args.saida else (
        settings.DATA_ENTREGAS_DIR / f"UY3_TODOS_OS_CAMPOS_{datetime.now():%d%m_%H%M}.xlsx"
    )

    exportar(inicio.strftime("%Y-%m-%d"), fim.strftime("%Y-%m-%d"), saida, permitir_consulta_real=True)


if __name__ == "__main__":
    main()
