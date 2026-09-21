# Diagnostico: 403 em `/v1/DataprevEmployee/OffersRequest` (Homologacao)

> **RESOLVIDO em 08/09/2026**: a permissao `AuctionQuery` foi liberada para
> o client de homologacao. Uma chamada real via Postman (ambiente
> "UY3 Credit API - Homologacao") retornou **200 OK com 188 registros
> reais**, mesmo volume ja visto em producao. O restante deste documento fica
> como registro historico da investigacao e do schema confirmado - o
> bloqueio em si nao existe mais neste ambiente.

Registrado em 04/09/2026, a partir da investigacao feita via Postman
(collection `UY3-Credit-API.postman_collection.json`, ambiente
"UY3 Credit API - Homologacao").

## Fluxo de autenticacao (funciona)

```
POST {{token_url}}
Authorization: Basic {{basic_auth}}   <- client_id:client_secret em Base64
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
scope=credit/api_unicaii
```

Resposta (200 OK):

```json
{
  "access_token": "<JWT>",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

O token e valido, carrega o scope `credit/api_unicaii` e e enviado
corretamente no header `Authorization: Bearer ...` das chamadas seguintes.

## Endpoint alvo (bloqueado)

```
GET {{base_url}}/v1/DataprevEmployee/OffersRequest
Authorization: Bearer {{access_token}}

Query params:
  startDate=2026-05-07
  endDate=2026-08-08
  pageNumber=1
```

Resposta atual (403 Forbidden):

```json
{
  "code": "MISSING_RESOURCE_PERMISSION",
  "message": "Acesso negado. Voce nao possui o nivel de permissao necessario para acessar este recurso",
  "details": {
    "permissionType": "AuctionQuery",
    "resource": "NaturalPerson"
  },
  "correlationId": "#98948723e46e176c"
}
```

## Diagnostico

- A autenticacao OAuth2 funciona corretamente - token gerado com sucesso via
  Cognito STG (client_id `j46qqu18ar2h32g1fpaiutqg7`).
- O erro NAO e de autenticacao (401), e de autorizacao (403): o client nao
  possui a permissao `AuctionQuery` sobre o recurso `NaturalPerson` no
  ambiente de homologacao.
- Isso precisa ser concedido no backend/IAM da UY3 - nao e resolvivel via
  Postman, via este projeto, ou por qualquer ajuste de request.
- Segundo relato do usuario, a mesma chamada em **producao**
  (`UY3_AMBIENTE=producao`) retornou com sucesso - ou seja, o client de
  producao ja tem essa permissao (ou o endpoint nao exige a mesma trava
  la). Isso nunca foi capturado/documentado neste projeto.

## Impacto neste projeto

`src/uy3_extraction.py` chama exatamente este endpoint. Enquanto a
permissao nao for liberada para o client de homologacao:

- Rodar com `UY3_AMBIENTE=homologacao` sempre vai falhar com
  `PermissionError` (capturado explicitamente em `src/uy3_api_client.py` e
  tratado em `src/orchestrator.py`).
- Rodar com `UY3_AMBIENTE=producao` deveria funcionar, mas **o schema real
  da resposta (nomes de campo: CPF, Nome, Valor Liberado, etc.) nunca foi
  confirmado** - o mapeamento em `config/settings.py` (`CAMPO_CPF`,
  `CAMPO_NOME`, ...) e uma estimativa. Assim que uma extracao real em
  producao rodar, use `python -m src.inspecionar_schema
  data/uy3_bruto/<arquivo>.json` para ver os campos reais e ajustar o
  `.env` (nao precisa mexer em codigo).

## Proximo passo (permissao)

Escalar ao time responsavel pela API/IAM da UY3 o pedido de liberacao da
permissao `AuctionQuery` (recurso `NaturalPerson`) para o client de
homologacao, citando o `correlationId` acima como referencia.

## Atualizacao 08/09/2026: schema confirmado em producao

Em producao (`UY3_AMBIENTE=producao`) o mesmo endpoint respondeu 200 OK com
188 registros reais. Exemplo de payload (CPF mascarado pelo usuario ao
compartilhar; a API real devolve o CPF completo):

```json
{
  "currentPage": 1,
  "totalPages": 1,
  "totalRecords": 188,
  "recordsPerPage": 250,
  "currentPageRecords": 188,
  "startDateTime": "07052026000000",
  "endDateTime": "08082026000000",
  "content": [
    {
      "requestId": 31143852,
      "registrationNumber": "87500000227**",
      "employeeCode": "VX_SOC87574122784",
      "employerRegistration": { "code": 1, "description": "CNPJ" },
      "employerRegistrationNumber": "2IT4BKK7TW2E28",
      "requestValidUntil": "23072026202539",
      "employeeName": "Tatiane Figueiredo Toledo",
      "birthDate": "22071951",
      "availableMargin": 77041,
      "eligible": true,
      "pep": false,
      "admissionDate": "27102025",
      "alerts": null,
      "sendCTPS": true,
      "bestOffer": [
        {
          "proposalNumber": "dd7d57cf86794f508a50",
          "numberOfPayments": 6,
          "interestRate": 0.0452,
          "principalAmountInCents": 204947,
          "liquidValueInCents": 200000,
          "marginInCents": 43757,
          "insuranceValueInCents": 0,
          "warranty": {
            "hasWarranty": false,
            "fgtsBalanceInCents": null,
            "fgtsRescissionPenaltyInCents": null,
            "rescissionBenefitPercentage": null
          }
        }
      ],
      "errorMessage": null,
      "errorEligibility": null,
      "errorSimulation": null
    }
  ]
}
```

### Mapeamento usado em `src/data_treatment.py`

| Campo da API | Tipo | Observacao |
|---|---|---|
| `registrationNumber` | string | CPF (11 digitos, sem mascara na API real) |
| `employeeName` | string | Nome completo -> "Nome" |
| `eligible` | boolean | So mantemos registros com `eligible=true` |
| `errorMessage`/`errorEligibility`/`errorSimulation` | null\|string | Se qualquer um vier preenchido, o registro e descartado |
| `birthDate` | string `DDMMAAAA` | -> "Data de Nascimento" |
| `availableMargin` | integer, **centavos** | /100 -> "Margem Disponivel" em reais |
| `bestOffer[0].liquidValueInCents` | integer, **centavos** | /100 -> "Valor Liberado" em reais (usamos so a primeira/melhor oferta) |
| `bestOffer[0].numberOfPayments` | integer | -> "Numero de Parcelas" |

Campos recebidos mas **nao usados** no pipeline (disponiveis se precisar no
futuro): `requestId`, `employeeCode`, `employerRegistration*`,
`requestValidUntil`, `pep`, `admissionDate`, `sendCTPS`,
`bestOffer[].proposalNumber/interestRate/principalAmountInCents/marginInCents/insuranceValueInCents/warranty`.

### Sem data de consulta por registro

O envelope so traz `startDateTime`/`endDateTime` da **pagina inteira**
(igual ao periodo pedido via `startDate`/`endDate`) - nao ha um campo de
data por registro individual. Por isso `data_treatment.py` usa o horario de
modificacao do proprio arquivo JSON bruto (ou seja, quando ESTE projeto
extraiu os dados) como "Data da Consulta", so para ordenar a base final -
nao existe mais o filtro de "janela de horas" que o processo Astor Tech
original aplicava sobre uma data de consulta real da fonte.

### ATENCAO: limiar de "Valor Liberado > R$4.000" provavelmente esta errado para esta API

Nos 188 registros de exemplo, o "Valor Liberado" (`bestOffer.liquidValueInCents`)
tipico observado ficou entre **~R$175 e ~R$2.100** (excluindo um registro
com `availableMargin: 42000000000` = R$420.000.000,00, que tem cara de
dado de teste/mock, nao um caso real). O limiar de negocio de R$4.000,00 foi
herdado do processo Astor Tech (produto de credito diferente) - aplicado
sem ajuste, ele **zera praticamente toda extracao real desta API**.

Por isso o limiar agora e configuravel POR AMBIENTE via `.env`
(`VALOR_MINIMO_REGRA_NEGOCIO_PRODUCAO`, mantido em 4000.00, e
`VALOR_MINIMO_REGRA_NEGOCIO_HOMOLOGACAO`, definido em 0.00 - sem filtro, a
pedido do usuario em 08/09/2026, ja que homologacao serve so para testar o
pipeline). `data_treatment.py` tambem registra um aviso no log se 0
registros sobreviverem a esse filtro (mostrando o min/max observado), para
essa situacao nao passar despercebida como um "bug silencioso" (mesmo
padrao do `totalItems: 0` que ja confundiu uma vez em homologacao).
**Reveja o limiar de producao com o negocio antes de depender dele de
fato.**

### Pendente

- ~~`UY3_BASIC_AUTH_PRODUCAO` ainda nao foi preenchido no `.env`~~ - **resolvido
  em 08/09/2026**, chave de producao ja configurada.
- Limiar de negocio (`VALOR_MINIMO_REGRA_NEGOCIO_PRODUCAO`) ainda precisa
  ser revisto com o negocio antes de depender dele em producao de verdade
  (ver secao acima).
