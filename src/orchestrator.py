"""Orquestracao, agendamento, trava de concorrencia, dedup e logs.

Uso:
  - Ciclo unico:      python -m src.orchestrator --once
  - Loop continuo:    python -m src.orchestrator
    (dispara um ciclo a cada INTERVALO_HORAS; para producao, prefira agendar
    "python -m src.orchestrator --once" via Windows Task Scheduler em vez de
    manter este processo rodando)
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from config import settings
from src.logging_setup import get_logger

logger = get_logger("orchestrator")

HISTORICO_CPF = settings.DATA_FINAL_DIR / "cpf_historico.csv"


def _lock_path() -> Path:
    from config.settings import BASE_DIR

    return BASE_DIR / "orchestrator.lock"


def _adquirir_lock() -> bool:
    lock = _lock_path()
    if lock.exists():
        idade = time.time() - lock.stat().st_mtime
        if idade < 2 * 3600:
            logger.warning("Ciclo anterior ainda em execucao (lock com %.0f min). Pulando ciclo.", idade / 60)
            return False
        logger.warning("Lock antigo (%.0f min) considerado travado (stale). Removendo.", idade / 60)
        lock.unlink()

    lock.write_text(f"pid={os.getpid()} inicio={datetime.now().isoformat()}", encoding="utf-8")
    return True


def _liberar_lock() -> None:
    lock = _lock_path()
    if lock.exists():
        lock.unlink()


def _deduplicar_contra_historico(csv_tratado: Path) -> Path:
    df = pd.read_csv(csv_tratado, dtype={"CPF": str})

    agora = datetime.now()
    janela = agora - timedelta(hours=24)

    if HISTORICO_CPF.exists():
        historico = pd.read_csv(HISTORICO_CPF, dtype={"CPF": str}, parse_dates=["data_hora"])
        historico = historico[historico["data_hora"] >= janela]
    else:
        historico = pd.DataFrame(columns=["CPF", "data_hora"])

    cpfs_recentes = set(historico["CPF"])
    total_antes = len(df)
    df_dedup = df[~df["CPF"].isin(cpfs_recentes)].copy()
    logger.info(
        "Deduplicacao (janela 24h): %d linhas -> %d novas (%d ja processadas recentemente)",
        total_antes,
        len(df_dedup),
        total_antes - len(df_dedup),
    )

    novo_historico = pd.concat(
        [historico, pd.DataFrame({"CPF": df_dedup["CPF"], "data_hora": agora})],
        ignore_index=True,
    )
    novo_historico.to_csv(HISTORICO_CPF, index=False)

    destino = settings.DATA_FINAL_DIR / f"{csv_tratado.stem}_dedup.csv"
    df_dedup.to_csv(destino, index=False, encoding="utf-8-sig")
    return destino


def _limitar_leads(csv_final: Path, limite: int) -> Path:
    df = pd.read_csv(csv_final, dtype={"CPF": str})
    df_limitado = df.head(limite)
    destino = settings.DATA_FINAL_DIR / f"{csv_final.stem}_limite{limite}.csv"
    df_limitado.to_csv(destino, index=False, encoding="utf-8-sig")
    logger.info("Base limitada a %d leads (de %d disponiveis): %s", len(df_limitado), len(df), destino)
    return destino


def _recuperar_bases_pendentes(
    resumo,
    permitir_novavida_job_real: bool,
    permitir_criar_campanha: bool,
    headless: bool,
) -> None:
    """Varre data/final/ por bases ja tratadas e deduplicadas que nunca
    chegaram a virar uma entrega em data/entregas/ (Nova Vida, consolidacao
    ou e-mail falharam num ciclo anterior DEPOIS da deduplicacao ja ter
    marcado os CPFs como processados no historico). Roda no INICIO de todo
    ciclo, antes da extracao nova - mesmo espirito da recuperacao de
    rascunhos de e-mail (ver _reenviar_rascunhos_pendentes em
    src/notificacao.py).

    So tenta arquivos com menos de 48h (evita ficar tentando pra sempre algo
    que falha de forma persistente, ex.: CPFs que o Nova Vida nunca vai
    encontrar) e pula arquivos vazios (nada para processar).
    """
    limite_idade = datetime.now() - timedelta(hours=48)
    for csv_dedup in sorted(settings.DATA_FINAL_DIR.glob("*_dedup*.csv")):
        modificado = datetime.fromtimestamp(csv_dedup.stat().st_mtime)
        if modificado < limite_idade:
            continue

        consolidado = settings.DATA_ENTREGAS_DIR / f"{csv_dedup.stem}_consolidado.csv"
        if consolidado.exists():
            continue

        try:
            df = pd.read_csv(csv_dedup, dtype={"CPF": str})
        except Exception:
            continue
        if len(df) == 0:
            continue

        logger.warning(
            "Base pendente encontrada (nunca chegou a ser entregue): %s (%d leads). "
            "Tentando concluir o processamento agora.",
            csv_dedup.name,
            len(df),
        )
        try:
            from src import consolidacao, notificacao, novavida_integration

            resultado_novavida = novavida_integration.upload_e_higienizar(
                csv_dedup,
                permitir_job_real=permitir_novavida_job_real,
                permitir_criar_campanha=permitir_criar_campanha,
                headless=headless,
            )
            if resultado_novavida is None:
                continue

            resultado_final = consolidacao.consolidar(csv_dedup, resultado_novavida)
            uy3_tratado_xlsx = consolidacao.gerar_copia_formatada(csv_dedup)
            final_xlsx = resultado_final.with_suffix(".xlsx")
            notificacao.enviar_base_por_email(final_xlsx, uy3_tratado_xlsx, headless=headless)

            resumo.ok(
                f"Recuperacao automatica: {csv_dedup.name} ({len(df)} leads) "
                "processado e entregue com sucesso"
            )
            logger.info("Base pendente %s recuperada e entregue com sucesso.", csv_dedup.name)
        except Exception as e:
            resumo.falhou(f"Recuperacao automatica de {csv_dedup.name} ainda nao concluida: {e}")
            logger.warning("Base pendente %s continua sem conseguir ser entregue: %s", csv_dedup.name, e)


def run_ciclo(
    permitir_consulta_real: bool = False,
    permitir_novavida_job_real: bool = False,
    permitir_criar_campanha: bool = False,
    limite_leads: int | None = None,
    headless: bool = True,
    pular_novavida: bool = False,
) -> None:
    if not _adquirir_lock():
        return

    from src import relatorio

    resumo = relatorio.ResumoCiclo()
    inicio = time.time()
    try:
        logger.info("=== Iniciando ciclo (ambiente UY3=%s) ===", settings.UY3_AMBIENTE)

        if permitir_novavida_job_real:
            try:
                _recuperar_bases_pendentes(resumo, permitir_novavida_job_real, permitir_criar_campanha, headless)
            except Exception as e:
                logger.warning("Varredura de bases pendentes nao concluida: %s", e)

        from src import data_treatment, uy3_extraction

        bruto = uy3_extraction.extrair(permitir_consulta_real=permitir_consulta_real, headless=headless)
        resumo.ok(f"Extracao UY3 Credit API ({settings.UY3_AMBIENTE}): {bruto.name}")

        try:
            import json

            from src import exportar_todos_campos

            registros_brutos = json.loads(bruto.read_text(encoding="utf-8"))
            df_completo = exportar_todos_campos.montar_planilha(registros_brutos, datetime.now())
            destino_completo = settings.DATA_ENTREGAS_DIR / f"{bruto.stem}_TODOS_OS_CAMPOS.xlsx"
            exportar_todos_campos.formatar_planilha(df_completo, destino_completo)
            resumo.ok(
                f"Planilha completa (todos os campos, traduzida): {destino_completo.name} "
                f"({len(df_completo)} leads)"
            )
        except Exception as e:
            # Nao deve derrubar o ciclo - e um produto adicional (auditoria/
            # relatorio completo), o pipeline do Nova Vida nao depende dela.
            logger.warning("Planilha completa (todos os campos) nao gerada: %s", e)
            resumo.falhou(f"Planilha completa (todos os campos): {e}")

        tratado = data_treatment.tratar(bruto)
        linhas_tratadas = len(pd.read_csv(tratado, dtype={"CPF": str}))
        resumo.ok(f"Tratamento: {linhas_tratadas} leads elegiveis (valor + janela de horas)")

        final_sem_higienizar = _deduplicar_contra_historico(tratado)
        linhas_dedup = len(pd.read_csv(final_sem_higienizar, dtype={"CPF": str}))
        resumo.ok(f"Deduplicacao: {linhas_dedup} leads novos (nao processados nas ultimas 24h)")

        if limite_leads is not None:
            final_sem_higienizar = _limitar_leads(final_sem_higienizar, limite_leads)
            linhas_dedup = len(pd.read_csv(final_sem_higienizar, dtype={"CPF": str}))
            resumo.ok(f"Limite aplicado: {linhas_dedup} leads (--limite-leads {limite_leads})")

        logger.info("Base pronta para envio ao Nova Vida: %s", final_sem_higienizar)

        if pular_novavida:
            resumo.pulado("Nova Vida: etapa pulada (--pular-novavida)")
            resumo.pulado("Consolidacao: nao executada (etapa Nova Vida pulada)")
            resumo.pulado("E-mail: nao enviado (etapa Nova Vida pulada)")
            logger.info("=== Ciclo concluido em %.1fs (Nova Vida pulada) ===", time.time() - inicio)
            return

        try:
            from src import novavida_integration

            resultado_novavida = novavida_integration.upload_e_higienizar(
                final_sem_higienizar,
                permitir_job_real=permitir_novavida_job_real,
                permitir_criar_campanha=permitir_criar_campanha,
                headless=headless,
            )
            if resultado_novavida is None:
                resumo.pulado("Nova Vida: job nao disparado (permitir_novavida_job_real=False)")
                resumo.pulado("E-mail: nao enviado (job Nova Vida nao foi disparado)")
            else:
                resumo.ok(f"Nova Vida: resultado baixado ({resultado_novavida.name})")

                from src import consolidacao

                resultado_final = consolidacao.consolidar(final_sem_higienizar, resultado_novavida)
                df_final = pd.read_csv(resultado_final, dtype={"CPF": str})
                sem_contato = df_final["Telefone"].isna().sum()
                resumo.ok(
                    f"Consolidacao: {len(df_final)} leads na base final "
                    f"({len(df_final) - sem_contato} com telefone, {sem_contato} sem)"
                )

                try:
                    from src import notificacao

                    uy3_tratado_xlsx = consolidacao.gerar_copia_formatada(final_sem_higienizar)
                    final_xlsx = resultado_final.with_suffix(".xlsx")
                    notificacao.enviar_base_por_email(final_xlsx, uy3_tratado_xlsx, headless=headless)
                    resumo.ok(f"E-mail: enviado para {settings.EMAIL_DESTINATARIO}")
                except Exception as e:
                    # Falha no envio (config incompleta, Outlook fora do ar,
                    # etc.) nao deve derrubar o ciclo - a base ja foi gerada
                    # com sucesso.
                    logger.warning("Envio de e-mail nao realizado: %s", e)
                    resumo.falhou(f"E-mail: nao enviado - {e}")
        except (NotImplementedError, RuntimeError) as e:
            logger.warning("Etapa Nova Vida pendente: %s", e)
            resumo.falhou(f"Nova Vida: {e}")
            resumo.pulado("E-mail: nao enviado (etapa Nova Vida nao concluida)")

        logger.info("=== Ciclo concluido em %.1fs ===", time.time() - inicio)
    except PermissionError as e:
        # 403 da UY3 Credit API (falta de permissao AuctionQuery) - ver
        # docs/Diagnostico_403_AuctionQuery.md. Nao adianta tentar de novo
        # sem a permissao ser concedida no backend/IAM, mas o ciclo termina
        # de forma limpa (lock liberado, resumo salvo) em vez de travar.
        logger.error("Ciclo interrompido por falta de permissao na UY3 Credit API: %s", e)
        resumo.erro(e)
    except Exception as e:
        logger.exception("Ciclo falhou com erro")
        resumo.erro(e)
    finally:
        resumo.salvar()
        _liberar_lock()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Executa um unico ciclo e sai")
    parser.add_argument(
        "--permitir-consulta-real",
        action="store_true",
        help=(
            "Autoriza a chamada real a UY3 Credit API (traz dados reais de pessoas "
            "fisicas do ambiente configurado em UY3_AMBIENTE)"
        ),
    )
    parser.add_argument(
        "--permitir-novavida-job-real",
        action="store_true",
        help="Autoriza disparar um job real (com possivel custo) no Nova Vida",
    )
    parser.add_argument(
        "--permitir-criar-campanha",
        action="store_true",
        help=(
            "Autoriza criar uma Campanha nova a cada ciclo no Nova Vida (botao '+', "
            "NAO validado ao vivo - so usar em sessao supervisionada). Sem esta flag, "
            "reaproveita a campanha fixa de NOVAVIDA_CAMPANHA (comportamento validado)."
        ),
    )
    parser.add_argument(
        "--limite-leads",
        type=int,
        default=None,
        help="Trunca a base final para as N primeiras linhas antes do envio ao Nova Vida",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Abre o navegador visivel (nao-headless), util para acompanhar o robo em testes",
    )
    parser.add_argument(
        "--pular-novavida",
        action="store_true",
        help=(
            "Pula a etapa Nova Vida por completo (nenhum login/upload no Ipe), assim como "
            "a consolidacao e o envio de e-mail que dependem dela. Roda so extracao UY3 + "
            "tratamento + deduplicacao + planilha completa (todos os campos)."
        ),
    )
    args = parser.parse_args()

    if args.once:
        run_ciclo(
            permitir_consulta_real=args.permitir_consulta_real,
            permitir_novavida_job_real=args.permitir_novavida_job_real,
            permitir_criar_campanha=args.permitir_criar_campanha,
            limite_leads=args.limite_leads,
            headless=not args.headed,
            pular_novavida=args.pular_novavida,
        )
        return

    intervalo_seg = settings.INTERVALO_HORAS * 3600
    logger.info("Loop continuo iniciado (intervalo=%.0fs)", intervalo_seg)
    while True:
        run_ciclo(
            permitir_consulta_real=args.permitir_consulta_real,
            permitir_novavida_job_real=args.permitir_novavida_job_real,
            permitir_criar_campanha=args.permitir_criar_campanha,
            limite_leads=args.limite_leads,
            headless=not args.headed,
            pular_novavida=args.pular_novavida,
        )
        time.sleep(intervalo_seg)


if __name__ == "__main__":
    main()
