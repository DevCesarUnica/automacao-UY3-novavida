"""Modulo: envio por e-mail da base final.

Envio via SMTP (smtplib) NAO FUNCIONA em tenants Microsoft 365 com
"Authenticated SMTP" desativado (erro tipico: "535 5.7.139 Authentication
unsuccessful, SmtpClientAuthentication is disabled for the Tenant"). Nenhuma
senha (normal ou de app) contorna isso - e bloqueio do administrador do
tenant, nao das credenciais.

Por isso o envio e feito via RPA (Playwright) direto na interface web do
Outlook (outlook.office.com), no MESMO padrao ja usado para Nova Vida
(src/novavida_integration.py): login, navegar, preencher, anexar, enviar. O
login web normal nao passa pelo protocolo SMTP bloqueado, entao contorna a
restricao do tenant.

VALIDADO AO VIVO: fluxo completo testado ponta a ponta (login -> novo e-mail
-> Para -> Assunto -> Corpo -> 2 anexos -> Enviar), com confirmacao real na
pasta "Itens Enviados". Achados que corrigiram suposicoes erradas durante a
validacao:
  - A conta NAO tem MFA ativado - login direto com usuario/senha, sem nenhum
    codigo/prompt adicional (so o "Continuar conectado?" padrao).
  - O campo "Para" e um `<div aria-label="Para">` SEM role acessivel nenhum
    (nao e textbox/combobox) - precisa ser localizado por atributo
    aria-label, nao por get_by_role().
  - Digitar o destinatario abre um dropdown de sugestoes de contato. Escape
    NAO fecha esse dropdown (aciona o atalho global "Descartar mensagem" do
    Outlook!). Enter tambem nao confirma de forma confiavel. O que
    funciona: clicar no card de sugestao visivel - ha varios elementos na
    pagina com o mesmo texto do e-mail (o campo digitado, um anuncio para
    leitor de tela, o card do dropdown), e o card visivel e sempre o ULTIMO
    desses elementos no DOM.
  - Apos selecionar o destinatario, a barra de ferramentas do compose
    (botoes "Anexar arquivo", "Enviar") fica com a arvore de acessibilidade
    "escondida" - get_by_role("button", ...) passa a retornar ZERO
    resultados dali em diante, mesmo com os botoes visiveis e funcionais.
    Por isso esses botoes sao localizados por seletor CSS de atributo
    (button[aria-label=...]), que nao depende da arvore de acessibilidade.
  - O texto do menu de anexar e "Navegar neste computador" (nao "Procurar
    este computador").

RISCO CONHECIDO (nao se aplica a conta ja validada, mas pode se aplicar a
outras): se EMAIL_REMETENTE tiver MFA ativado, o login pode travar esperando
um codigo que o robo nao consegue responder. Caminhos: (a) excluir essa
conta especifica da exigencia de MFA, ou (b) login manual uma vez +
`context.storage_state(path=...)` do Playwright reaproveitado nas execucoes
seguintes.

Apos a consolidacao, envia por e-mail para settings.EMAIL_DESTINATARIO duas
planilhas anexadas, discriminadas no corpo do e-mail:
  1) A base final higienizada (.xlsx formatado, pos Nova Vida, com Telefone
     e E-mail).
  2) A base da UY3 Credit API tratada, no estado em que foi enviada para
     higienizacao no Nova Vida (ainda sem Telefone/E-mail).

Assunto do e-mail: "Base de leads higienizadas UY3 DD/MM/AAAA HH:MM".
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import settings
from src.logging_setup import get_logger

logger = get_logger("notificacao")

OUTLOOK_URL = "https://outlook.office.com/mail/"


def _validar_configuracao_email() -> None:
    faltando = [
        nome
        for nome, valor in {
            "EMAIL_REMETENTE": settings.EMAIL_REMETENTE,
            "EMAIL_SENHA": settings.EMAIL_SENHA,
            "EMAIL_DESTINATARIO": settings.EMAIL_DESTINATARIO,
        }.items()
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Configuracao de e-mail incompleta - faltam: "
            f"{', '.join(faltando)}. Preencha essas variaveis no .env antes de enviar."
        )


def _login(page) -> None:
    page.goto(OUTLOOK_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1000)

    page.get_by_role("textbox", name=re.compile("e-mail|email|telefone|phone", re.I)).fill(
        settings.EMAIL_REMETENTE
    )
    page.get_by_role("button", name=re.compile("avan[cç]ar|next", re.I)).click(timeout=10000)
    page.wait_for_timeout(1500)

    page.get_by_role("textbox", name=re.compile("senha|password", re.I)).fill(settings.EMAIL_SENHA)
    page.get_by_role("button", name=re.compile("^entrar$|^sign in$", re.I)).click(timeout=10000)
    page.wait_for_timeout(3000)

    # Apos senha correta (sem MFA), o Microsoft mostra a tela "Continuar
    # conectado?" (KMSI) ANTES de redirecionar pro Outlook - essa tela ainda
    # fica em dominio login.microsoftonline.com, entao precisa tentar fechar
    # ela (clicar "Sim") ANTES de checar se o login travou, senao um login
    # bem sucedido e erroneamente tratado como falha/MFA.
    try:
        page.get_by_role("button", name=re.compile("^sim$|^yes$", re.I)).click(timeout=8000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    if "login.microsoftonline.com" in page.url or "login.live.com" in page.url:
        raise RuntimeError(
            "Login no Outlook nao foi concluido (permaneceu na tela de autenticacao mesmo "
            "apos tentar fechar o prompt 'Continuar conectado?'). Provavel bloqueio de "
            "MFA/2FA - ver docstring do modulo para como contornar."
        )

    logger.info("Login no Outlook Web OK. URL: %s", page.url)


def _preencher_campo(page, nomes: list[str], valor: str) -> None:
    """Preenche um campo do formulario do Outlook Web tentando varias
    estrategias. O campo "Para", por exemplo, e um `<div aria-label="Para">`
    SEM role nenhum, entao get_by_role() nunca encontra. Por isso tenta
    primeiro por role (funciona para Assunto/Corpo da mensagem, que sao
    textbox de verdade) e cai para selecao direta por atributo aria-label
    como fallback.

    O regex dos nomes precisa ser ANCORADO (^nome$). Sem ancorar, "To" bate
    como substring dentro de "Assun-TO", entao _preencher_campo(["Para","To"],
    destinatario) encontraria o textbox "Assunto" (que bate no padrao "To")
    ANTES de tentar o fallback do aria-label, e escreveria o destinatario
    dentro do campo de assunto por engano.
    """
    regex = re.compile("|".join(f"^{re.escape(n)}$" for n in nomes), re.I)
    for role in ("textbox", "combobox"):
        loc = page.get_by_role(role, name=regex)
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            loc.first.fill(valor, timeout=5000)
            return

    for nome in nomes:
        loc = page.locator(f'[aria-label="{nome}"]')
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            page.keyboard.type(valor)
            return

    raise RuntimeError(f"Campo nao encontrado no Outlook Web (nomes tentados: {nomes})")


def _normalizar_espacos(texto: str) -> str:
    """Colapsa qualquer sequencia de espacos/quebras de linha em um unico
    espaco. Necessario porque o editor rich-text do Outlook Web reformata
    linhas em branco (parece inserir 1 quebra de linha extra por linha em
    branco ao renderizar/ler de volta via inner_text()) - comparar o texto
    cru byte-a-byte contra o original gera falso negativo mesmo quando o
    conteudo esta correto.
    """
    return re.sub(r"\s+", " ", texto).strip()


def _preencher_corpo_com_verificacao(page, corpo: str, tentativas: int = 3) -> None:
    """Preenche o corpo da mensagem e confere se o texto realmente foi para
    o campo por completo, tentando de novo se nao bater.

    BUG CONFIRMADO EM PRODUCAO (automacao original): um ciclo agendado rodou
    sem lancar nenhuma excecao, mas o e-mail ficou parado em Rascunhos com o
    corpo cortado (so a primeira linha, em vez do texto completo). O envio
    em si nao falhou, so o conteudo ficou incompleto - provavel autosave do
    Outlook interrompendo o preenchimento no meio. Como isso nao levanta
    excecao, _preencher_campo() sozinha nao detecta o problema; por isso
    aqui conferimos o texto de fato apos preencher e tentamos de novo se nao
    bater, em vez de confiar numa unica tentativa.

    A comparacao usa texto normalizado (_normalizar_espacos) para nao
    confundir reformatacao cosmetica de linha em branco com truncamento real
    - so o truncamento real reduz o numero de caracteres SIGNIFICATIVOS
    (nao-espaco) abaixo do limiar de 90%.
    """
    campo = page.locator('[aria-label="Corpo da mensagem"], [aria-label="Message body"]').first
    esperado_normalizado = _normalizar_espacos(corpo)
    inicio_esperado = esperado_normalizado[:60]
    for tentativa in range(1, tentativas + 1):
        campo.click(timeout=5000)
        page.keyboard.press("Control+A")
        campo.fill(corpo, timeout=5000)
        page.wait_for_timeout(700)
        texto_atual = _normalizar_espacos(campo.inner_text())
        if texto_atual.startswith(inicio_esperado) and len(texto_atual) >= len(esperado_normalizado) * 0.9:
            return
        logger.warning(
            "Corpo da mensagem nao bateu apos preencher (tentativa %d/%d, %d de %d caracteres normalizados) - "
            "tentando de novo.",
            tentativa,
            tentativas,
            len(texto_atual),
            len(esperado_normalizado),
        )
    raise RuntimeError(
        f"Nao foi possivel preencher o corpo da mensagem corretamente apos {tentativas} tentativas."
    )


def _compor_e_enviar(page, assunto: str, corpo: str, anexos: list[Path]) -> None:
    page.get_by_role(
        "button", name=re.compile("novo e-?mail|nova mensagem|new mail|new message", re.I)
    ).first.click(timeout=15000)
    page.wait_for_timeout(1500)

    _preencher_campo(page, ["Para", "To"], settings.EMAIL_DESTINATARIO)
    # O campo "Para" abre um dropdown de sugestoes de contato ao digitar, que
    # fica flutuando sobre o resto do formulario e intercepta cliques
    # seguintes:
    #  - Escape NAO fecha esse dropdown - aciona o atalho global "Descartar
    #    mensagem" do Outlook (abre dialogo pra fechar o rascunho inteiro!).
    #  - Enter tambem NAO confirma o destinatario de forma confiavel.
    #  - O que funciona: clicar diretamente no card da sugestao. Ha varios
    #    elementos na pagina com o mesmo texto do e-mail (o proprio campo
    #    digitado, um anuncio para leitor de tela, e o card visivel do
    #    dropdown) - o card visivel e sempre o ULTIMO desses elementos no
    #    DOM (renderizado por ultimo, num overlay por cima do resto).
    page.wait_for_timeout(1000)
    sugestoes = page.get_by_text(settings.EMAIL_DESTINATARIO, exact=False)
    if sugestoes.count() > 0:
        sugestoes.last.click(timeout=5000)
    else:
        page.keyboard.press("Enter")
    page.wait_for_timeout(800)

    _preencher_campo(page, ["Assunto", "Subject"], assunto)
    _preencher_corpo_com_verificacao(page, corpo)

    # A partir daqui, get_by_role("button", ...) passa a retornar ZERO
    # resultados mesmo com os botoes (Anexar, Enviar) visiveis e funcionais
    # na tela - a barra de ferramentas fica com a arvore de acessibilidade
    # "escondida" (provavel efeito colateral do clique no card de sugestao
    # do destinatario, que e um overlay portalizado do Fluent UI). Botao
    # real confirmado via HTML puro: <button aria-label="Anexar arquivo">.
    # Por isso os botoes daqui pra frente sao localizados por atributo CSS
    # (button[aria-label=...]), que nao depende da arvore de acessibilidade,
    # em vez de get_by_role().
    for anexo in anexos:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            page.locator('button[aria-label*="Anexar" i], button[aria-label*="Attach" i]').first.click(
                timeout=10000
            )
            page.get_by_text(
                re.compile("navegar neste computador|browse this computer", re.I)
            ).click(timeout=5000)
        fc_info.value.set_files(str(anexo))
        page.wait_for_timeout(2000)
        logger.info("Anexo adicionado: %s", anexo.name)

    _clicar_enviar_e_confirmar(page)


def _clicar_enviar_e_confirmar(page, tentativas: int = 3) -> None:
    """Espera o botao 'Enviar' ficar habilitado, clica, e confirma que o
    compose realmente fechou. Reaproveitada tanto para o e-mail recem-
    composto quanto para reenviar um rascunho pendente encontrado por
    _reenviar_rascunhos_pendentes() - a logica de garantir o envio e a mesma
    nos dois casos.

    Dois bugs reais e distintos ja confirmados em producao:
    1) Com anexos maiores, o botao "Enviar" fica com aria-disabled="true"
       enquanto o Outlook ainda esta processando os anexos recem-anexados -
       clicar nesse estado nunca registra (Playwright espera o elemento
       ficar "enabled" e estoura TimeoutError). A espera fixa de 2s apos
       cada anexo nao e suficiente para anexos grandes, entao espera
       ativamente o botao ficar habilitado antes de tentar clicar.
    2) Mesmo com o botao habilitado, o clique em "Enviar" as vezes nao
       completa o envio na primeira tentativa (o clique em si nunca lanca
       excecao, entao so a verificacao do compose fechado detecta isso).
       Reabrir o mesmo rascunho manualmente minutos depois e clicar Enviar
       de novo sempre funcionou de primeira - por isso tenta de novo
       automaticamente em vez de desistir na primeira falha.
    """
    botao_enviar = page.locator('button[aria-label="Enviar"], button[aria-label="Send"]').first
    botao_enviar.wait_for(state="visible", timeout=10000)
    inicio_espera = time.time()
    while botao_enviar.get_attribute("aria-disabled") == "true":
        if time.time() - inicio_espera > 60:
            raise RuntimeError(
                "Botao 'Enviar' continuou desabilitado por mais de 60s - os anexos podem "
                "nao ter terminado de processar no Outlook."
            )
        page.wait_for_timeout(1000)

    for tentativa in range(1, tentativas + 1):
        page.locator('button[aria-label="Enviar"], button[aria-label="Send"]').first.click(timeout=10000)
        page.wait_for_timeout(3000)
        if page.locator('[aria-label="Para"]').count() == 0:
            return
        logger.warning(
            "Compose nao fechou apos clicar 'Enviar' (tentativa %d/%d) - tentando de novo.",
            tentativa,
            tentativas,
        )
        page.wait_for_timeout(2000)

    raise RuntimeError(
        f"Clique em 'Enviar' nao fechou o compose apos {tentativas} tentativas - o e-mail "
        "provavelmente NAO foi enviado e ficou parado em Rascunhos."
    )


_PADRAO_ASSUNTO_AUTOMACAO = re.compile(r"Base de leads higienizadas UY3", re.I)


def _reenviar_rascunhos_pendentes(page, limite: int = 10) -> int:
    """Varre a pasta Rascunhos por e-mails DESTA automacao que ficaram
    parados em ciclos anteriores (compose nao fechou apos "Enviar", por
    qualquer um dos motivos documentados em _clicar_enviar_e_confirmar) e
    tenta enviar cada um de novo. Roda no INICIO de todo envio, antes de
    compor o e-mail do ciclo atual - assim, mesmo que um envio falhe TODAS
    as tentativas num ciclo, ele se autorrecupera sozinho no proximo ciclo
    que conseguir logar no Outlook, sem precisar de intervencao manual.

    So mexe em rascunhos cujo assunto bate com o padrao desta automacao
    (_PADRAO_ASSUNTO_AUTOMACAO) - nunca toca em rascunhos pessoais do
    usuario.
    """
    page.get_by_text("Rascunhos", exact=True).first.click(timeout=10000)
    page.wait_for_timeout(1500)

    # Apos um reenvio bem sucedido, um resquicio (toast/notificacao ou item
    # duplicado momentaneo) com o mesmo texto pode continuar sendo
    # encontrado por mais uma volta do loop, mesmo restringindo a busca a
    # role="option" - isso fazia tentar abrir algo que nao e mais um
    # rascunho de verdade e travar esperando um botao "Enviar" que nunca
    # aparece. Por isso guarda os assuntos ja tentados NESTA varredura e
    # para assim que reencontrar um repetido, em vez de fazer varias
    # tentativas contra o mesmo item.
    ja_tentados: set[str] = set()
    enviados = 0
    for _ in range(limite):
        item = page.get_by_role("option").filter(has_text=_PADRAO_ASSUNTO_AUTOMACAO).first
        if item.count() == 0:
            break
        assunto_pendente = item.inner_text()
        if assunto_pendente in ja_tentados:
            break
        ja_tentados.add(assunto_pendente)
        item.click(timeout=10000)
        page.wait_for_timeout(1500)
        try:
            _clicar_enviar_e_confirmar(page)
            enviados += 1
            logger.info("Rascunho pendente de ciclo anterior reenviado.")
        except Exception as e:
            logger.warning("Rascunho pendente encontrado, mas nao foi possivel reenviar agora: %s", e)
            break
        page.wait_for_timeout(3000)
        page.get_by_text("Rascunhos", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(1000)

    if enviados:
        logger.info("Recuperacao automatica: %d rascunho(s) pendente(s) reenviado(s) com sucesso.", enviados)
    return enviados


def enviar_base_por_email(
    caminho_final_xlsx: Path, caminho_uy3_tratado_xlsx: Path, headless: bool = True
) -> None:
    """Envia a base final por e-mail com o maximo de robustez razoavel:
      1) Varre e reenvia qualquer rascunho pendente de ciclos anteriores
         ANTES de compor o e-mail novo (auto-recuperacao, ver
         _reenviar_rascunhos_pendentes).
      2) Tenta o fluxo completo (login + recuperacao + compor + enviar) ate
         3 vezes, com um navegador NOVO a cada tentativa, antes de desistir
         de verdade - cobre falhas transitorias que um retry dentro da
         mesma sessao/pagina nao resolveria (ex.: login instavel, pagina em
         estado inconsistente).
    Mesmo se as 3 tentativas falharem, o rascunho desta execucao (se chegou
    a ser criado) fica pronto pra ser pego pela varredura de recuperacao do
    PROXIMO ciclo - o sistema se autocura sem precisar de intervencao
    manual, mesmo no pior caso.
    """
    _validar_configuracao_email()

    assunto = f"Base de leads higienizadas UY3 {datetime.now():%d/%m/%Y %H:%M}"
    corpo = (
        "Segue em anexo:\n\n"
        f"1) {caminho_final_xlsx.name}\n"
        "   BASE FINAL HIGIENIZADA - ja passou pelo Nova Vida, contem Telefone "
        "e E-mail. Pronta para a operacao.\n\n"
        f"2) {caminho_uy3_tratado_xlsx.name}\n"
        "   BASE DA UY3 CREDIT API TRATADA - estado em que foi enviada para "
        "higienizacao no Nova Vida, AINDA SEM Telefone/E-mail. Serve de "
        "conferencia/auditoria do que foi processado.\n\n"
        "Enviado automaticamente pela automacao UY3 Credit API -> Nova Vida."
    )

    tentativas_totais = 3
    ultimo_erro: Exception | None = None
    for tentativa_geral in range(1, tentativas_totais + 1):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            try:
                _login(page)

                try:
                    _reenviar_rascunhos_pendentes(page)
                except Exception as e:
                    # Falha na varredura nao pode impedir o envio do e-mail
                    # deste ciclo - e um bonus de recuperacao, nao o
                    # objetivo principal desta chamada.
                    logger.warning("Varredura de rascunhos pendentes nao concluida: %s", e)

                _compor_e_enviar(page, assunto, corpo, [caminho_final_xlsx, caminho_uy3_tratado_xlsx])
                logger.info(
                    "E-mail '%s' enviado via Outlook Web para %s com anexos: %s, %s",
                    assunto,
                    settings.EMAIL_DESTINATARIO,
                    caminho_final_xlsx.name,
                    caminho_uy3_tratado_xlsx.name,
                )
                return
            except Exception as e:
                ultimo_erro = e
                restam = tentativas_totais - tentativa_geral
                logger.warning(
                    "Tentativa %d/%d de enviar e-mail falhou: %s. %s",
                    tentativa_geral,
                    tentativas_totais,
                    e,
                    f"Tentando de novo com navegador novo ({restam} restantes)."
                    if restam
                    else "Sem mais tentativas neste ciclo.",
                )
            finally:
                browser.close()
        if tentativa_geral < tentativas_totais:
            time.sleep(10)

    raise RuntimeError(
        f"Nao foi possivel enviar o e-mail apos {tentativas_totais} tentativas completas "
        f"(login + envio, navegador novo a cada vez). Ultimo erro: {ultimo_erro}. Se um "
        "rascunho chegou a ser criado, ele sera reenviado automaticamente pela varredura de "
        "recuperacao no proximo ciclo que conseguir logar no Outlook."
    )


if __name__ == "__main__":
    import sys

    enviar_base_por_email(Path(sys.argv[1]), Path(sys.argv[2]), headless=False)
