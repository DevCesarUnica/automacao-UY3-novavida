"""Modulo 2: Tratamento de dados.

Le o JSON bruto salvo por uy3_extraction.extrair() (lista de registros do
schema "content[]" do OffersRequest - ver docs/Diagnostico_403_AuctionQuery.md
para o payload de exemplo confirmado em 08/09/2026 com dados reais de
producao), aplica os filtros de elegibilidade da propria API, extrai a
melhor oferta de cada registro e as regras de negocio (Valor Liberado acima
do limiar configurado, CPF valido, dedup) e gera o CSV tratado para o Nova
Vida - mesmo contrato de saida usado por consolidacao.py e
novavida_integration.py.

SCHEMA CONFIRMADO (campos usados, o resto do payload e ignorado):
  registrationNumber      -> CPF (pode vir sem mascara, so digitos)
  employeeName             -> Nome
  eligible (bool)          -> so mantem registros com eligible=true
  errorMessage/errorEligibility/errorSimulation -> descarta se algum vier preenchido
  bestOffer (lista)        -> usa o PRIMEIRO item; descarta registro sem oferta
    .liquidValueInCents    -> Valor Liberado (dividido por 100 -> reais)
    .numberOfPayments      -> Numero de Parcelas
  availableMargin          -> Margem Disponivel (em CENTAVOS, dividido por 100 -> reais)
  birthDate ("DDMMAAAA")   -> Data de Nascimento

O endpoint NAO devolve uma data de consulta por registro (so o intervalo
startDateTime/endDateTime da pagina inteira, que e o proprio periodo
pedido) - por isso "Data da Consulta" aqui e o horario de MODIFICACAO do
arquivo bruto (ou seja, o momento em que este projeto fez a extracao), usado
so para ordenar a base final em consolidacao.py, sem equivalente ao filtro
de "janela de horas" que existia no processo Astor Tech original.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import settings
from src.cpf_utils import cpf_valido, limpar_cpf
from src.logging_setup import get_logger

logger = get_logger("data_treatment")

_COLUNAS_SAIDA = [
    "CPF",
    "Nome",
    "Valor Liberado",
    "Numero de Parcelas",
    "Data da Consulta",
    "Data de Nascimento",
    "Margem Disponivel",
]


def _parse_data_ddmmaaaa(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor), "%d%m%Y")
    except ValueError:
        return None


def _melhor_oferta(registro: dict) -> dict | None:
    ofertas = registro.get("bestOffer") or []
    return ofertas[0] if ofertas else None


def _ler_arquivo_bruto(caminho: Path) -> pd.DataFrame:
    registros = json.loads(caminho.read_text(encoding="utf-8"))
    if not registros:
        return pd.DataFrame(columns=_COLUNAS_SAIDA)

    # Momento da extracao (usado como "Data da Consulta" - ver docstring do
    # modulo sobre a ausencia de data por registro na resposta da API).
    momento_extracao = datetime.fromtimestamp(caminho.stat().st_mtime)

    linhas = []
    nao_elegiveis = 0
    com_erro = 0
    sem_oferta = 0
    for registro in registros:
        if not registro.get("eligible", False):
            nao_elegiveis += 1
            continue
        if registro.get("errorMessage") or registro.get("errorEligibility") or registro.get("errorSimulation"):
            com_erro += 1
            continue
        oferta = _melhor_oferta(registro)
        if oferta is None:
            sem_oferta += 1
            continue

        linhas.append(
            {
                "CPF": registro.get("registrationNumber"),
                "Nome": registro.get("employeeName"),
                "Valor Liberado": (oferta.get("liquidValueInCents") or 0) / 100,
                "Numero de Parcelas": oferta.get("numberOfPayments"),
                "Data da Consulta": momento_extracao,
                "Data de Nascimento": _parse_data_ddmmaaaa(registro.get("birthDate")),
                "Margem Disponivel": (registro.get("availableMargin") or 0) / 100,
            }
        )

    logger.info(
        "Filtros da propria API: %d registro(s) brutos -> %d nao elegiveis descartados -> "
        "%d com erro descartados -> %d sem oferta descartados -> %d candidatos",
        len(registros),
        nao_elegiveis,
        com_erro,
        sem_oferta,
        len(linhas),
    )
    return pd.DataFrame(linhas, columns=_COLUNAS_SAIDA)


def tratar(caminho_bruto: Path) -> Path:
    saida = _ler_arquivo_bruto(caminho_bruto)
    total_candidatos = len(saida)

    if saida.empty:
        # Guarda necessaria: filtrar um DataFrame de 0 linhas com uma mascara
        # booleana vazia (saida["CPF"].apply(...) sobre uma Series vazia)
        # derruba TODAS as colunas no pandas (confirmado ao vivo em
        # 08/09/2026, extracao real de homologacao sem leads na janela do
        # dia) - as linhas de filtro abaixo quebrariam com KeyError em vez
        # de simplesmente produzir um CSV tratado vazio.
        logger.info("Nenhum registro elegivel no arquivo bruto - nada para tratar.")
        destino = settings.DATA_TREATED_DIR / f"{caminho_bruto.stem}_tratado.csv"
        saida.to_csv(destino, index=False, sep=",", encoding="utf-8-sig")
        return destino

    saida["CPF"] = saida["CPF"].apply(limpar_cpf)
    saida = saida[saida["CPF"].apply(cpf_valido)]
    descartados_cpf_invalido = total_candidatos - len(saida)

    antes_filtro_valor = saida.copy()
    # >= (nao >): consistencia pedida pela operacao em 11/09/2026 com o
    # mesmo filtro fixo usado em exportar_todos_campos.py.
    saida = saida[saida["Valor Liberado"] >= settings.VALOR_MINIMO_REGRA_NEGOCIO]
    descartados_valor_baixo = len(antes_filtro_valor) - len(saida)

    if len(saida) == 0 and len(antes_filtro_valor) > 0:
        logger.warning(
            "0 registros passaram do filtro 'Valor Liberado >= R$%.2f' (de %d candidatos com "
            "CPF valido). Valores observados nesta extracao: min=R$%.2f, max=R$%.2f. Esse "
            "limiar foi herdado do processo Astor Tech (produto diferente) - reavaliar com o "
            "negocio se faz sentido para esta API (ver docs/Diagnostico_403_AuctionQuery.md) "
            "antes de assumir que 0 leads e o resultado esperado.",
            settings.VALOR_MINIMO_REGRA_NEGOCIO,
            len(antes_filtro_valor),
            antes_filtro_valor["Valor Liberado"].min(),
            antes_filtro_valor["Valor Liberado"].max(),
        )

    saida = saida.drop_duplicates(subset="CPF", keep="first")

    logger.info(
        "Tratamento concluido: %d candidato(s) -> %d CPFs invalidos descartados -> "
        "%d abaixo do limiar de Valor Liberado (R$%.2f) -> %d linha(s) finais",
        total_candidatos,
        descartados_cpf_invalido,
        descartados_valor_baixo,
        settings.VALOR_MINIMO_REGRA_NEGOCIO,
        len(saida),
    )

    destino = settings.DATA_TREATED_DIR / f"{caminho_bruto.stem}_tratado.csv"
    saida.to_csv(destino, index=False, sep=",", encoding="utf-8-sig")
    logger.info("CSV tratado salvo em: %s", destino)
    return destino


if __name__ == "__main__":
    import sys

    tratar(Path(sys.argv[1]))
