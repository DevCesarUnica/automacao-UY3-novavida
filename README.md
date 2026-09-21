# Automacao UY3 Credit API -> Nova Vida

> **Diferenca em relacao ao repositorio irmao [`automacao-astortech-novavida`](https://github.com/DevCesarUnica/automacao-astortech-novavida):**
> este projeto **nao faz scraping do Astor Tech**. A origem dos leads aqui e
> uma chamada direta a **API oficial da UY3 Credit** (OAuth2/Cognito,
> `GET /v1/DataprevEmployee/OffersRequest`), sem navegador nem Playwright
> nessa etapa. As etapas seguintes (Nova Vida, consolidacao, e-mail) sao as
> mesmas nos dois projetos.

Robo em Python que automatiza a extracao de leads direto da **UY3 Credit
API** (via OAuth2/Cognito, sem scraping de tela), aplica as regras de
negocio de elegibilidade, envia a base para higienizacao/enriquecimento no
**Nova Vida (Plataforma Ipe)**, consolida o resultado numa base final e a
distribui por e-mail para a operacao.

Este projeto e uma adaptacao de uma automacao irma que faz o mesmo fluxo a
partir do **Astor Tech** (scraping via Playwright) - aqui a etapa de
extracao foi substituida por uma chamada direta a API oficial da UY3, sem
navegador. As etapas de Nova Vida, consolidacao e envio por e-mail sao
identicas (mesmo codigo, mesmas validacoes ao vivo).

## Sumario

- [Visao geral do fluxo](#visão-geral-do-fluxo)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Status do projeto / bloqueio conhecido](#status-do-projeto--bloqueio-conhecido)
- [Guardas de seguranca](#guardas-de-seguranca)
- [Requisitos](#requisitos)
- [Instalacao](#instalação)
- [Configuracao](#configuração)
- [Uso](#uso)
- [Documentacao adicional](#documentação-adicional)
- [Licenca](#licença)

## Visao geral do fluxo

```
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│      UY3 Credit API     │  │     Tratamento local    │  │        Nova Vida        │  │    Consolidação final   │
│OAuth2 (Cognito)         │  │Mapeia campos da API      │  │Login                    │  │Cruza base tratada       │
│GET /v1/DataprevEmployee/│  │Valida CPF (dv)          │  │Enriquecimentos          │  │+ telefone/e-mail        │
│OffersRequest (paginado) │  │Filtra > R$4.000         │  │(Novo enriquecimento)    │  │do Nova Vida             │
│                         │  │Filtra janela de horas   │  │Campanha/Processo/       │  │.xlsx formatado          │
│                         │  │Dedup (24h)              │  │Layouts                  │  │E-mail p/ operação       │
│                         │  │                         │  │                         │  │(Outlook Web)            │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘

  extrai (JSON bruto) ──────▶   trata + filtra + dedup   ──────▶   upload + higieniza (CSV)   ──────▶   consolida + envia por e-mail
```

O orquestrador (`src/orchestrator.py`) executa esse ciclo sob demanda ou em
intervalos programados, com trava de concorrencia, deduplicacao contra um
historico de CPFs ja processados nas ultimas 24h e um resumo legivel por
ciclo (`logs/resumo_ciclos.txt`).

## Estrutura do projeto

```
.
├── config/
│   └── settings.py            # Configuração central (ambiente UY3, mapeamento de campos, Nova Vida, e-mail)
├── src/
│   ├── uy3_api_client.py      # OAuth2 client_credentials + chamada paginada ao OffersRequest
│   ├── uy3_extraction.py      # Orquestra a extração via API e salva o JSON bruto
│   ├── inspecionar_schema.py  # Utilitário: lista os campos reais de um JSON bruto salvo
│   ├── data_treatment.py      # Mapeamento de campos, validação de CPF, regras de elegibilidade
│   ├── novavida_integration.py# Login + upload/higienização no Nova Vida (Plataforma Ipê) - Playwright
│   ├── consolidacao.py        # Cruza base tratada com resultado do Nova Vida, gera .xlsx formatado
│   ├── notificacao.py         # Envio da base final por e-mail via Outlook Web - Playwright
│   ├── relatorio.py           # Resumo legível por humano de cada ciclo (logs/resumo_ciclos.txt)
│   ├── orchestrator.py        # Orquestração, lock, deduplicação, agendamento
│   ├── cpf_utils.py           # Validação/normalização de CPF
│   └── logging_setup.py       # Logging estruturado com rotação de arquivo
├── docs/
│   └── Diagnostico_403_AuctionQuery.md  # Investigação do bloqueio atual de permissão
├── data/{uy3_bruto,treated,final,novavida,entregas}/  # Saída de cada etapa do pipeline (git-ignorado)
├── logs/                               # Logs de execução e resumo por ciclo (git-ignorado)
├── .env.example                        # Modelo de variáveis de ambiente
└── requirements.txt
```

## Status do projeto

**O bloqueio de permissão foi resolvido em 08/09/2026** - a permissão
`AuctionQuery` foi liberada tanto para o client de produção quanto para o de
homologação. O endpoint `/v1/DataprevEmployee/OffersRequest` responde 200 OK
com dados reais nos dois ambientes agora. Histórico completo da investigação
(incluindo o `correlationId` do erro original) em
[`docs/Diagnostico_403_AuctionQuery.md`](docs/Diagnostico_403_AuctionQuery.md).

O **schema real da resposta foi confirmado** com dados reais (188
registros) - o mapeamento de campos em `src/data_treatment.py` (CPF, Nome,
Valor Liberado, Margem Disponível, etc.) reflete esse payload real, não é
mais uma estimativa. O payload completo de exemplo e a tabela de
mapeamento estão em
[`docs/Diagnostico_403_AuctionQuery.md`](docs/Diagnostico_403_AuctionQuery.md).

Um ponto ainda pendente:

- **O limiar de elegibilidade (Valor Liberado > X) é configurado por
  ambiente**: `VALOR_MINIMO_REGRA_NEGOCIO_PRODUCAO` (padrão R$4.000,00,
  herdado do processo Astor Tech) e `VALOR_MINIMO_REGRA_NEGOCIO_HOMOLOGACAO`
  (padrão R$0,00 - sem filtro, só para testar o pipeline, a pedido do
  usuário). Os valores reais de "Valor Liberado" observados ficaram entre
  ~R$175 e ~R$2.100 - com o padrão de produção, o filtro tende a zerar
  quase toda extração real. `data_treatment.py` avisa no log se isso
  acontecer, mas o limiar de produção **precisa ser revisto com o negócio**
  antes de depender dele de fato.

As etapas de Nova Vida, consolidação e e-mail são herdadas sem alteração
funcional da automação original (já validadas ao vivo ponta a ponta lá).

## Guardas de seguranca

| Trava | Onde | Comportamento padrao |
|---|---|---|
| `permitir_consulta_real` | `uy3_extraction.extrair()` | `False` - recusa chamar a UY3 Credit API (traz dados reais de pessoas físicas) até ser chamado explicitamente com `True` |
| `permitir_job_real` | `novavida_integration.upload_e_higienizar()` | `False` - preenche e valida o formulário de upload no Nova Vida, mas cancela antes de clicar em "Iniciar job" |
| `permitir_criar_campanha` | `novavida_integration.upload_e_higienizar()` | `False` - reaproveita a campanha fixa em `NOVAVIDA_CAMPANHA`; criar campanha nova a cada ciclo via botão "+" nunca foi validado ao vivo, só usar em sessão supervisionada |
| Validação de configuração | `novavida_integration._validar_configuracao_job()` | Bloqueia com erro claro se Processo/Campanha (quando aplicável) não estiverem definidos no `.env` |
| 403 da UY3 Credit API | `uy3_api_client.buscar_offers_request()` | Levanta `PermissionError` explícito (em vez de um erro genérico), capturado por `orchestrator.py` para terminar o ciclo de forma limpa |
| Falha no envio de e-mail | `orchestrator.run_ciclo()` | Não derruba o ciclo - a base final já foi gerada e salva antes do e-mail; a falha fica registrada no log e no resumo do ciclo |

O orquestrador expõe as mesmas travas via CLI: `--permitir-consulta-real`,
`--permitir-novavida-job-real` e `--permitir-criar-campanha`.

## Requisitos

- Python 3.11+
- Google Chrome/Chromium (instalado automaticamente pelo Playwright - usado
  apenas nas etapas de Nova Vida e e-mail, não na extração)

## Instalação

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
```

## Configuração

Copie `.env.example` para `.env` e preencha com as credenciais reais (o
arquivo `.env` nunca deve ser versionado - já está no `.gitignore`):

```bash
cp .env.example .env
```

| Variável | Descrição |
|---|---|
| `UY3_AMBIENTE` | `homologacao` ou `producao` - escolhe qual par de credenciais/URLs abaixo usar (equivalente a trocar o environment ativo no Postman) |
| `UY3_TOKEN_URL_*`, `UY3_BASE_URL_*`, `UY3_BASIC_AUTH_*` | URLs e credencial (client_id:client_secret em Base64) de cada ambiente |
| `UY3_SCOPE` | Scope do OAuth2 (`credit/api_unicaii`) |
| `UY3_JANELA_DIAS_CONSULTA` | Quantos dias para trás consultar a cada ciclo (o endpoint filtra só por dia, não por hora) |
| `NOVAVIDA_URL`, `NOVAVIDA_USER`, `NOVAVIDA_PASS`, `NOVAVIDA_EMPRESA` | Credenciais de acesso ao Nova Vida (Plataforma Ipê) |
| `NOVAVIDA_CAMPANHA`, `NOVAVIDA_PROCESSO` | Seleções do modal "Novo enriquecimento" no Nova Vida |
| `VALOR_MIN`, `VALOR_MAX` | Faixa de referência do "Valor Liberado" (informativa) |
| `VALOR_MINIMO_REGRA_NEGOCIO_PRODUCAO` | Filtro de elegibilidade em produção (`Valor Liberado > este valor`). **Padrão R$4.000 provavelmente incompatível com esta API** - ver [Status do projeto](#status-do-projeto--bloqueio-conhecido) |
| `VALOR_MINIMO_REGRA_NEGOCIO_HOMOLOGACAO` | Mesmo filtro, mas em homologação (padrão `0.00` - sem filtro, só para testar o pipeline) |
| `INTERVALO_HORAS` | Intervalo entre ciclos no modo loop contínuo |
| `EMAIL_REMETENTE`, `EMAIL_SENHA` | Conta usada para logar no Outlook Web e enviar a base final. Se a conta tiver MFA ativo, o login automático falha - ver docstring de `src/notificacao.py` |
| `EMAIL_DESTINATARIO` | Destinatário da base final higienizada a cada ciclo |

> **Nota sobre e-mail:** o envio é feito via automação do Outlook Web
> (Playwright), não via SMTP - tenants Microsoft 365 com "Authenticated
> SMTP" desativado bloqueiam esse caminho. Detalhes em `src/notificacao.py`.

## Uso

```bash
# Ciclo único (recomendado para agendar via Windows Task Scheduler / cron)
python -m src.orchestrator --once

# Loop contínuo (dispara um ciclo a cada INTERVALO_HORAS)
python -m src.orchestrator

# Autorizando explicitamente etapas com efeito real
python -m src.orchestrator --once --permitir-consulta-real --permitir-novavida-job-real

# Truncando a base final para as N primeiras linhas antes do envio ao Nova Vida
python -m src.orchestrator --once --permitir-consulta-real --permitir-novavida-job-real --limite-leads 50

# Navegador visível (não-headless) nas etapas de Nova Vida/e-mail, útil para acompanhar em testes
python -m src.orchestrator --once --headed

# Testando só a extração via API, sem rodar o resto do pipeline
python -m src.uy3_extraction

# Inspecionando os campos reais de um JSON bruto já extraído
python -m src.inspecionar_schema data/uy3_bruto/UY3_0409_1130.json
```

Cada ciclo grava um resumo legível em `logs/resumo_ciclos.txt` (uma etapa
por linha, `OK`/`FALHOU`/`PULADO`) além do log técnico completo em
`logs/automacao.log`.

## Documentação adicional

- [`docs/Diagnostico_403_AuctionQuery.md`](docs/Diagnostico_403_AuctionQuery.md) - investigação completa do bloqueio de permissão atual, com o payload de erro real e o próximo passo de escalação.

## Licença

Distribuído sob a licença MIT.
