"""Modulo 3: Integracao Nova Vida (ver docs/Diagnostico_403_AuctionQuery.md
para o contexto da etapa anterior, que agora usa a UY3 Credit API em vez de
scraping no Astor Tech - esta etapa em si nao mudou).

STATUS: FLUXO COMPLETO VALIDADO PONTA A PONTA (herdado da automacao
original), incluindo dois jobs reais (Campanha CLT_UY3_1207, Processo NVBOOK
CEL OBG - BESTTIMETOCALL, 116 CPFs cada). O primeiro rodou com o formato de
upload errado (Saida=0, ver abaixo); apos corrigir o formato, o segundo teste
devolveu Saida=102/116 e o download funcionou de ponta a ponta - arquivo
.zip com CSV separado por ";", 24 colunas incluindo CPF, NOME, telefone
celular (DDDCEL1/CEL1), WhatsApp (FLWHATSAPPCEL1), EMAIL, endereco, score
etc.

Confirmado ao vivo:
  - URL: https://ipe.novavidati.com.br/IndicadoresGerais (redireciona para
    login automaticamente quando nao autenticado). Login funciona com os
    campos #sUsuario (name=sUsuario), #sSenha, name=sCliente.
  - Menu lateral: link com <span class="main-nav__link-text">Enriquecimentos
    </span>.
  - Botao "Adicionar" (onclick="exibirModalAdicionarEnriquecimento(this)")
    abre o modal "Novo enriquecimento" (#enriquecimentoModal, Bootstrap).
    Selecoes necessarias:
      - select#campanhaselect - usar a opcao configurada em NOVAVIDA_CAMPANHA.
      - select#processo - usar a opcao configurada em NOVAVIDA_PROCESSO.
    Esses valores ficam em config/settings.py, lidos do .env.
  - O modal TAMBEM tem select#layoutEntrada e select#layoutSaida, mas NAO
    precisam ser selecionados manualmente: o proprio JS do site filtra e
    auto-preenche esses dois campos assim que o Processo e escolhido.
  - Upload: a dropzone (.upload-file__dropzone) NAO tem um <input
    type="file"> dentro do modal - o input real do Dropzone.js
    (.dz-hidden-input, visibility:hidden) fica anexado fora do modal, direto
    na pagina. _fazer_upload() por isso busca em `page`, nao em `modal`.
  - Botao #iniciarJob dispara o job (efeito real / possivel custo).

CONFIRMADO AO VIVO (job real, 116 CPFs):
  - FORMATO DE UPLOAD ERRADO NA PRIMEIRA TENTATIVA: enviar o CSV tratado
    direto (separado por virgula, colunas CPF/Nome/Valor Liberado) faz o job
    rodar e ficar "EXPORTADO" em menos de 1 minuto, mas com Saida=0 (nenhum
    registro enriquecido). Causa raiz: o botao "Layout de entrada"
    (#btnDownloadLayoutEntrada) do modal baixa um TEMPLATE que revela o
    formato real esperado pelo layout "(PF) PADRAO NV": CSV separado por
    PIPE ("sep=|" na primeira linha, dica de separador para Excel), com uma
    UNICA coluna "CPF" - nada de Nome/Valor Liberado, nada de virgula.
    _preparar_arquivo_novavida() gera esse formato a partir do CSV tratado
    antes do upload.
  - Lista de Enriquecimentos (tabela real, colunas: ID, Arquivo, Entrada,
    Saida, Data, [acoes], Status):
      - Coluna "Arquivo" mostra o NOME ORIGINAL do arquivo enviado - usado
        para localizar a linha do job (mais confiavel que a Campanha, que se
        repete em varias linhas do historico).
      - Status fica "EXPORTADO" (nao "CONCLUIDO") quando o job termina.
      - Botao de download real: <button onclick="downloadSaida(ID, this)">
        com <span class="material-icons">download</span> dentro - NAO o
        icone generico "more_vert" (esse abre um menu com "Gerar BI" e
        "Download Alternativo", que e outra coisa).
      - Se Saida=0, o botao downloadSaida() nao dispara nenhuma requisicao
        de rede ao clicar - ou seja, nao ha nada para baixar; nao adianta
        ficar tentando.
      - O botao de download tem CSS que so o ativa de fato ao passar o
        mouse em cima da linha (classe "btn-hidden" - opacity/pointer-
        events controlados por hover). Um clique com force=True (sem hover
        previo) NAO disparava a requisicao de download, mesmo com Saida>0 -
        foi preciso chamar linha.hover() antes de clicar no botao.
        _baixar_resultado() ja faz isso.
  - SEGUNDO TESTE REAL (formato corrigido): Entrada=118, Saida=102 (87% de
    aproveitamento). Download baixado com sucesso via
    linha.hover() + clique normal no botao downloadSaida.

upload_e_higienizar() faz login, abre o modal "Novo enriquecimento", valida
que a configuracao de negocio foi preenchida, converte o CSV tratado para o
formato exigido pelo layout (pipe + CPF unico) e anexa. So clica em
"Iniciar job" (efeito real / possivel custo) se permitir_job_real=True E toda
a configuracao estiver preenchida; caso contrario cancela o modal sem
disparar nada, so para validar que o preenchimento funcionaria.

O processo tambem suporta pedir uma Campanha NOVA a cada ciclo (nome
UY3_DDMM_HHMM), via botao "+" ao lado de #campanhaselect - esse botao nunca
foi inspecionado ao vivo, por isso e controlado pelo parametro
`permitir_criar_campanha` (default False): quando False (padrao,
comportamento ja validado), continua selecionando a campanha fixa de
settings.NOVAVIDA_CAMPANHA; quando True, tenta criar a campanha nova e
levanta RuntimeError se os seletores nao baterem, em vez de arriscar clicar
no elemento errado. So ligar True numa sessao supervisionada, depois de
mapear o dialogo real do botao "+".
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

from config import settings
from src.logging_setup import get_logger

logger = get_logger("novavida_integration")


def _login(page) -> None:
    page.goto(settings.NOVAVIDA_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(800)
    page.locator("#sUsuario").fill(settings.NOVAVIDA_USER)
    page.locator("#sSenha").fill(settings.NOVAVIDA_PASS)
    page.locator("#sCliente").fill(settings.NOVAVIDA_EMPRESA)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_timeout(4000)
    if "Login" in page.url:
        raise RuntimeError(
            "Login no Nova Vida falhou ou nao pode ser confirmado (permaneceu/retornou "
            "para tela de Login). Confirmar URL e credenciais com o usuario antes de seguir."
        )
    logger.info("Login no Nova Vida OK. URL: %s", page.url)


def _ir_para_enriquecimentos(page) -> None:
    page.get_by_text("Enriquecimentos", exact=True).first.click(timeout=5000)
    page.wait_for_timeout(2000)
    logger.info("Tela de Enriquecimentos carregada. URL: %s", page.url)


def _abrir_modal_novo_enriquecimento(page):
    page.get_by_role("button", name="Adicionar").click(timeout=5000)
    page.wait_for_timeout(1000)
    modal = page.locator(".modal, [role='dialog']").filter(has_text="Novo enriquecimento").first
    modal.wait_for(timeout=5000)
    return modal


def _validar_configuracao_job(criar_campanha: bool) -> None:
    obrigatorios = {"NOVAVIDA_PROCESSO": settings.NOVAVIDA_PROCESSO}
    if not criar_campanha:
        # Se for criar campanha nova dinamicamente, NOVAVIDA_CAMPANHA (nome
        # de uma campanha ja existente) deixa de ser necessario.
        obrigatorios["NOVAVIDA_CAMPANHA"] = settings.NOVAVIDA_CAMPANHA
    faltando = [nome for nome, valor in obrigatorios.items() if not valor]
    if faltando:
        raise RuntimeError(
            "Configuracao de negocio do Nova Vida incompleta - faltam: "
            f"{', '.join(faltando)}. Defina essas variaveis no .env antes de disparar "
            "um job real."
        )


def _criar_nova_campanha(modal, nome: str) -> None:
    """Cria uma Campanha nova no Nova Vida clicando no botao "+" ao lado do
    campo Campanha, em vez de selecionar uma campanha ja existente.

    NAO VALIDADO AO VIVO - os seletores abaixo sao a melhor tentativa com
    base no padrao visual do modal (botao "+" logo ao lado do
    #campanhaselect, um input que abre para digitar o nome, e um botao de
    confirmacao tipo "Salvar"/"Criar"/"Adicionar"). Por isso
    upload_e_higienizar() so chama esta funcao quando
    permitir_criar_campanha=True, numa sessao supervisionada - qualquer
    elemento que nao for encontrado levanta RuntimeError em vez de arriscar
    clicar no elemento errado e criar uma campanha indevida no Nova Vida.
    """
    campo_campanha = modal.locator("#campanhaselect")
    linha = campo_campanha.locator("xpath=ancestor::*[self::div][1]")
    botao_criar = linha.get_by_role("button")
    if botao_criar.count() == 0:
        botao_criar = campo_campanha.locator("xpath=following-sibling::button[1]")
    if botao_criar.count() == 0:
        raise RuntimeError(
            "Nao foi possivel localizar o botao '+' de criar Campanha ao lado de "
            "#campanhaselect. Selectors nao validados ao vivo - inspecionar o DOM "
            "real do modal antes de tentar novamente."
        )
    botao_criar.first.click(timeout=5000)

    campo_nome = modal.locator("input[type='text']:visible, input:not([type]):visible").last
    if campo_nome.count() == 0:
        raise RuntimeError(
            "Botao '+' de criar Campanha foi clicado, mas nenhum campo de texto "
            "apareceu para digitar o nome da nova campanha."
        )
    campo_nome.fill(nome, timeout=5000)

    botao_confirmar = modal.get_by_role(
        "button", name=re.compile("Salvar|Criar|Adicionar|Confirmar", re.IGNORECASE)
    )
    if botao_confirmar.count() == 0:
        raise RuntimeError(
            f"Nome '{nome}' preenchido, mas nao foi encontrado um botao de "
            "confirmacao (Salvar/Criar/Adicionar/Confirmar) para criar a campanha."
        )
    botao_confirmar.first.click(timeout=5000)
    modal.locator("#campanhaselect").wait_for(timeout=5000)
    logger.info("Campanha nova criada e selecionada: %s", nome)


def _selecionar_campos(modal, criar_campanha: bool, nome_campanha: str) -> None:
    if criar_campanha:
        _criar_nova_campanha(modal, nome_campanha)
    else:
        modal.locator("#campanhaselect").select_option(label=settings.NOVAVIDA_CAMPANHA)
    modal.locator("#processo").select_option(label=settings.NOVAVIDA_PROCESSO)
    logger.info(
        "Campos selecionados no modal: Campanha=%s, Processo=%s",
        nome_campanha if criar_campanha else settings.NOVAVIDA_CAMPANHA,
        settings.NOVAVIDA_PROCESSO,
    )


def _preparar_arquivo_novavida(caminho_csv: Path) -> Path:
    """Converte a base tratada (CPF,Nome,Valor Liberado,...) para o formato
    exigido pelo Layout de entrada "(PF) PADRAO NV": CSV separado por PIPE,
    com a primeira linha "sep=|" (dica de separador do Excel) e uma UNICA
    coluna "CPF". Confirmado baixando o template real do botao "Layout de
    entrada" no modal - ver docstring do modulo.
    """
    df = pd.read_csv(caminho_csv, dtype={"CPF": str})
    destino = settings.DATA_NOVAVIDA_DIR / f"{caminho_csv.stem}_novavida.csv"
    with open(destino, "w", encoding="utf-8", newline="") as f:
        f.write("sep=|\n")
        f.write("CPF\n")
        for cpf in df["CPF"]:
            f.write(f"{cpf}\n")
    logger.info("Arquivo convertido para o layout do Nova Vida: %s (%d CPFs)", destino, len(df))
    return destino


def _fazer_upload(page, caminho_csv: Path) -> None:
    """O upload usa uma dropzone (Dropzone.js) cujo <input type="file"> real
    fica fora do DOM do modal (visibility:hidden, anexado globalmente na
    pagina). Por isso a busca e feita em `page`, nao em `modal`.
    """
    page.locator("input[type='file']").first.set_input_files(str(caminho_csv))
    page.wait_for_timeout(1000)
    logger.info("Arquivo anexado ao modal 'Novo enriquecimento': %s", caminho_csv.name)


def _baixar_resultado(
    page, nome_arquivo: str, download_dir: Path, tentativas: int = 15, espera_seg: float = 20.0
) -> Path:
    """Aguarda o job aparecer com Status "EXPORTADO" na lista de
    Enriquecimentos (localizado pelo nome do arquivo enviado, coluna
    "Arquivo") e baixa o resultado clicando no botao real de download
    (<button onclick="downloadSaida(...)">) da linha.

    Se a Saida do job ficar 0 (nenhum registro enriquecido - geralmente
    sinal de formato de entrada incompativel com o Layout escolhido), nao ha
    nada para baixar: o proprio site nao dispara nenhuma requisicao ao
    clicar o botao nesse caso. Por isso o codigo verifica a Saida antes de
    tentar o download e levanta um erro claro em vez de ficar tentando.
    """
    for tentativa in range(tentativas):
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)

        linha = page.locator(f"tr:has-text('{nome_arquivo}')").first
        if linha.count() > 0:
            status = linha.locator(".custom-pill").inner_text()
            if "EXPORTADO" in status.upper():
                saida_txt = linha.locator("td").nth(3).inner_text().strip()
                logger.info("Job '%s' EXPORTADO. Saida: %s", nome_arquivo, saida_txt)
                if saida_txt.strip("0, ") == "":
                    raise RuntimeError(
                        f"Job do Nova Vida para '{nome_arquivo}' concluido, mas Saida=0 "
                        "(nenhum registro enriquecido) - nao ha arquivo para baixar. "
                        "Verificar se o formato/layout de entrada esta correto."
                    )

                botao_download = linha.locator("button[onclick^='downloadSaida']").first
                linha.hover(timeout=5000)
                page.wait_for_timeout(300)
                with page.expect_download(timeout=60000) as download_info:
                    botao_download.click(timeout=5000)
                download = download_info.value
                extensao = Path(download.suggested_filename).suffix or ".zip"
                destino = download_dir / f"{Path(nome_arquivo).stem}_resultado{extensao}"
                download.save_as(str(destino))
                logger.info("Resultado do Nova Vida baixado em: %s", destino)
                return destino

        logger.info(
            "Job '%s' ainda nao aparece como EXPORTADO (tentativa %d/%d). Aguardando %.0fs...",
            nome_arquivo,
            tentativa + 1,
            tentativas,
            espera_seg,
        )
        page.wait_for_timeout(espera_seg * 1000)

    raise RuntimeError(
        f"Job do Nova Vida para '{nome_arquivo}' nao ficou EXPORTADO apos {tentativas} tentativas."
    )


def upload_e_higienizar(
    caminho_csv: Path,
    permitir_job_real: bool = False,
    permitir_criar_campanha: bool = False,
    headless: bool = True,
) -> Path | None:
    """`permitir_criar_campanha=True` faz o robo criar uma Campanha nova a
    cada ciclo (nome UY3_DDMM_HHMM) em vez de reaproveitar a campanha fixa em
    settings.NOVAVIDA_CAMPANHA. Default False porque essa interacao (botao
    "+") nunca foi validada ao vivo - so ligar numa sessao supervisionada
    (ver _criar_nova_campanha).
    `headless=False` abre o navegador visivel, util para acompanhar o robo
    em execucao durante testes.
    """
    _validar_configuracao_job(permitir_criar_campanha)
    arquivo_novavida = _preparar_arquivo_novavida(caminho_csv)
    nome_campanha = f"UY3_{datetime.now():%d%m_%H%M}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(accept_downloads=True)
        try:
            _login(page)
            _ir_para_enriquecimentos(page)
            modal = _abrir_modal_novo_enriquecimento(page)
            _selecionar_campos(modal, permitir_criar_campanha, nome_campanha)
            _fazer_upload(page, arquivo_novavida)

            if not permitir_job_real:
                modal.get_by_role("button", name="Cancelar").click()
                logger.warning(
                    "permitir_job_real=False: modal preenchido e validado, mas job NAO "
                    "foi disparado (sem custo). Chame com permitir_job_real=True para "
                    "efetivamente iniciar o enriquecimento."
                )
                return None

            page.locator("#iniciarJob").click(timeout=5000)
            page.wait_for_timeout(2000)
            logger.info("Job de enriquecimento iniciado no Nova Vida para %s", arquivo_novavida.name)

            return _baixar_resultado(page, arquivo_novavida.name, settings.DATA_NOVAVIDA_DIR)
        finally:
            browser.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        try:
            _login(page)
        finally:
            browser.close()
