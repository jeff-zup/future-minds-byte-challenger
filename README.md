# FinGuard — Orquestrador Multiagente em Python

Projeto de referência para o **Future Minds 3 — Nível 2: Orquestrador de Análise**.

## Arquitetura

```text
CSV
 └─> Agente 1: Estruturação
      └─> Agente 2: Risco/Compliance
           ├─ risco crítico -> Agente 2b: Escalonamento
           └─ demais -> fim do fluxo por item

Todos os itens concluídos
 └─> Agente 3: Relatório Gerencial
```

O projeto inclui:

- LangGraph para orquestração;
- AWS Bedrock via `boto3`;
- modo `mock` para desenvolvimento sem custo/AWS;
- RAG local da Política Interna usando TF-IDF;
- guardrails de PII e palavras impróprias;
- guardrail de entrada contra prompt/SQL/template injection;
- proteção contra XSS no relatório e CSV formula injection na exportação;
- tolerância a falha por item: uma reclamação quebrada não derruba o lote;
- simulação de ataque automatizada (`python -m security.simulate_attacks`);
- validação de schema com Pydantic;
- regra determinística para Banco Central/Procon;
- fluxo condicional para casos críticos;
- processamento concorrente;
- logs JSONL por agente;
- resultados JSON/CSV;
- relatório HTML gerencial.

> O RAG foi implementado localmente com TF-IDF para reduzir dependências e permitir execução imediata. A interface `PolicyRetriever` pode ser trocada por FAISS/embeddings sem alterar os agentes.

## 1. Criar ambiente

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Instale:

```bash
pip install -r requirements.txt
```

## 2. Configurar ambiente

Copie:

```bash
copy .env.example .env
```

ou Linux/macOS:

```bash
cp .env.example .env
```

### Rodar sem AWS

No `.env`:

```env
FINGUARD_MOCK_LLM=true
```

Então:

```bash
python main.py
```

### Rodar com AWS Bedrock

Defina:

```env
FINGUARD_MOCK_LLM=false
AWS_REGION=us-east-1
BEDROCK_MODEL_CLASSIFIER=<model-id-disponivel-na-sua-conta>
BEDROCK_MODEL_RISK=<model-id-disponivel-na-sua-conta>
BEDROCK_MODEL_REPORT=<model-id-disponivel-na-sua-conta>
```

A autenticação usa a cadeia padrão do boto3. Você pode usar `aws configure`, variáveis de ambiente ou `AWS_PROFILE`.

### Rodar com AI Gateway (LiteLLM)

Como alternativa ao AWS Bedrock, o FinGuard pode chamar os modelos através de um
AI Gateway compatível com [LiteLLM Proxy](https://docs.litellm.ai/). A troca de
provedor é feita apenas via configuração — nenhum código dos agentes precisa mudar.

No `.env`:

```env
FINGUARD_MOCK_LLM=false
FINGUARD_LLM_PROVIDER=litellm

LITELLM_API_BASE=https://seu-ai-gateway.exemplo.com
LITELLM_TOKEN=<sua-key-do-gateway>
LITELLM_KEY_ALIAS=future-minds-013
LITELLM_BUDGET_USD=6.54

# Aliases de modelo configurados no gateway (Claude via Bedrock por trás):
LITELLM_MODEL_CLASSIFIER=bedrock-anthropic-claude-haiku-4-5
LITELLM_MODEL_RISK=bedrock-anthropic-claude-sonnet-4-5
LITELLM_MODEL_REPORT=bedrock-anthropic-claude-sonnet-4-5
```

#### Validação de certificado TLS (CA interno/corporativo)

Se o AI Gateway usa um certificado emitido por uma CA interna/corporativa (não
presente na cadeia de confiança padrão do sistema), a validação TLS vai falhar
com erro do tipo `SSLCertVerificationError` / `CERTIFICATE_VERIFY_FAILED`.

Para resolver, exporte o certificado da CA (ou a cadeia completa) em formato
PEM e aponte para ele via `LITELLM_CA_BUNDLE`:

```env
LITELLM_CA_BUNDLE=/caminho/para/ca-corporativa.pem
```

Dica para obter o certificado do gateway via OpenSSL:

```bash
openssl s_client -connect dx-ai-gateway.platform.sbox.zupcloud.corp:443 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ca-corporativa.pem
```

Se o gateway responder com a cadeia completa (root + intermediários), talvez
seja necessário extrair todos os blocos `-----BEGIN CERTIFICATE-----` e
concatená-los em um único arquivo PEM.

Como último recurso (apenas debug local, **nunca em produção**), é possível
desabilitar a verificação TLS:

```env
LITELLM_VERIFY_SSL=false
```

Quando `LITELLM_CA_BUNDLE` está definido, ele tem prioridade sobre
`LITELLM_VERIFY_SSL` — o cliente sempre tentará validar contra o bundle
informado.

Então:

```bash
python main.py
```

> Instale a dependência `openai` (já incluída em `requirements.txt`) com
> `pip install -r requirements.txt`. O gateway LiteLLM Proxy expõe uma API
> 100% compatível com o formato OpenAI, por isso usamos o SDK oficial `openai`
> (mais leve e estável) em vez do SDK `litellm` no lado cliente.
> `LITELLM_BUDGET_USD` é apenas informativo neste projeto — o controle de
> orçamento em si é feito pelo gateway/chave.

## 3. Executar

```bash
python main.py
```

Saídas:

```text
output/resultados.json
output/resultados.csv
output/relatorio.json
output/relatorio.html
output/logs/execucao.jsonl
```

## 4. Executar testes

```bash
pytest -q
```

## 5. Simulação de segurança

Roda o pipeline real sobre 11 reclamações maliciosas (prompt injection, SQL injection,
XSS, CSV formula injection, vazamento de PII) e verifica cada vetor no artefato final:

```bash
python -m security.simulate_attacks
```

Sai com código 0 somente se todos os vetores estiverem mitigados — utilizável em CI.
Também imprime a linha de base (guardrails desligados) para comparação. Análise completa
em [`SECURITY.md`](SECURITY.md).

Saídas:

```text
output/security/simulacao.json
output/security/relatorio_ataque.html
```

## Estrutura

```text
finguard/
├── main.py
├── config.py
├── requirements.txt
├── data/
├── graph/
│   ├── state.py
│   ├── build_graph.py
│   ├── logging_utils.py
│   └── nodes/
├── llm/
├── rag/
├── guardrails/
│   ├── pii.py             # máscara de PII (entrada e saída)
│   ├── profanity.py
│   ├── injection.py       # guardrail de entrada: prompt/SQL/template injection
│   └── output_safety.py   # neutralização de fórmula para o CSV
├── report/
├── security/              # catálogo de payloads + simulação de ataque
├── tests/
└── output/
```

## Decisões de design

1. **Agente 1** usa política recuperada por RAG para classificar e resumir.
2. **Agente 2** recebe a análise estruturada, consulta trechos focados em risco/conformidade e devolve parecer.
3. **Banco Central/Procon** é regra de negócio determinística: urgência e risco críticos, independentemente da resposta do LLM.
4. **Agente 2b** não usa LLM: apenas registra o escalonamento de forma determinística.
5. **Agente 3** calcula estatísticas com pandas e usa LLM apenas para recomendações textuais.
6. O relatório aplica máscara de PII antes de persistir dados gerenciais.
7. **Entrada é não confiável**: o texto do cliente é verificado e isolado antes de chegar ao LLM,
   e nenhuma reclamação é descartada por suspeita — apenas marcada para revisão humana.
8. **Falha é isolada por item**: uma reclamação quebrada vira registro auditável em vez de
   abortar o lote inteiro.

## Demo sugerida

Use primeiro o dataset de exemplo com `FINGUARD_MOCK_LLM=true`. Durante a apresentação, mostre:

- terminal processando os itens;
- `output/logs/execucao.jsonl`;
- um caso crítico passando pelo `agente_2b`;
- `output/relatorio.html`;
- `python -m security.simulate_attacks` com os 11 vetores mitigados e a linha de base
  mostrando o comportamento anterior;
- `output/security/relatorio_ataque.html`, onde o `<script>` injetado aparece como
  texto inerte no painel de incidentes.
