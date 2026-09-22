# FinGuard — Guia de Apresentação

## Papel de cada agente

### Agente 1 — Recepção e Estruturação
**Arquivo:** `graph/nodes/estruturacao.py`

Ponto de entrada do pipeline. Recebe a reclamação bruta do CSV e produz a análise estruturada inicial:

- Consulta a **Política Interna via RAG** (TF-IDF local) para contextualizar a classificação
- Classifica **categoria**, **produto**, **sentimento** e **urgência** com vocabulário controlado, validado por Pydantic — qualquer valor fora do enum é rejeitado antes de prosseguir
- Gera o **resumo** em linguagem padronizada (máximo 3 frases)
- Aplica **mascaramento de PII** (CPF, cartão, e-mail, PIX) e **remoção de palavrões** no resumo antes de qualquer persistência
- Em caso de bloqueio pelo guardrail do gateway, aciona classificação heurística determinística como fallback — a reclamação nunca é descartada

**Saída:** `categoria`, `produto`, `sentimento`, `urgencia`, `resumo`

---

### Agente 2 — Análise de Risco e Conformidade
**Arquivo:** `graph/nodes/risco.py`

Recebe a saída estruturada do Agente 1 e avalia indicadores de risco regulatório e reputacional:

- **Fraude** ou transação não autorizada
- **Violação de LGPD** ou sigilo bancário
- **Risco reputacional** — menção a imprensa, redes sociais ou órgãos reguladores (Banco Central, Procon)
- **Necessidade de escalação imediata**

Produz **nível de risco** (Baixo / Médio / Alto / Crítico) com justificativa textual e lista de flags.

**Saída:** `nivel_risco`, `risco_justificativa`, `risco_flags`

---

### Agente 2b — Escalonamento
**Arquivo:** `graph/nodes/escalonamento.py`

Ativado **condicionalmente** apenas quando o Agente 2 classifica risco como **Crítico**. Casos não-críticos encerram diretamente em `END` sem custo adicional de LLM.

- Registra o escalonamento formal no estado
- Marca `escalado=True` para rastreabilidade e auditoria
- Garante que casos críticos sejam identificados no relatório final

**Saída:** `escalado=True`

---

### Agente 3 — Relatório Gerencial
**Arquivo:** `graph/nodes/relatorio.py`

Opera sobre o **lote completo** após todos os itens serem processados individualmente. É o único agente que não processa uma reclamação por vez — consolida os 500 resultados em inteligência gerencial:

- **Dashboard** agregado: total processado, distribuição por categoria, produto, urgência e nível de risco
- **Lista de casos críticos** com parecer de risco
- **Incidentes de segurança** (tentativas de injeção detectadas)
- **Falhas de processamento** com dados preservados para reprocessamento
- **Recomendações gerenciais** geradas via LLM com base apenas em dados agregados — o LLM nunca recebe textos brutos de reclamações nesta etapa
- Gera `relatorio.json` e `relatorio.html` ao término

**Execução assíncrona:** pandas + chamada LLM rodam em thread pool (`asyncio.to_thread`); escrita dos dois arquivos acontece em paralelo (`asyncio.gather`).

---

## Fluxo do grafo

```
_start_
   │
   ▼
agente_1 (classificação + RAG)
   │
   ▼
agente_2 (risco + conformidade)
   │
   ├─── nivel_risco != "Crítico" ──▶ END
   │
   └─── nivel_risco == "Crítico" ──▶ agente_2b (escalonamento) ──▶ END

[após lote completo]
   ▼
agente_3 (relatório gerencial)
```

---

## Perguntas prováveis da banca

### Pergunta obrigatória
> **"Quais ferramentas de IA você utilizou e como elas contribuíram para a solução?"**

**Resposta:**
- **LangGraph** — orquestração do pipeline multi-agente com roteamento condicional e rastreabilidade de estado
- **LLM via AWS Bedrock / LiteLLM Gateway** — classificação de reclamações (Agente 1), análise de risco (Agente 2) e geração de recomendações gerenciais (Agente 3)
- **RAG com TF-IDF local** — consulta à Política Interna sem custo de API de embeddings, garantindo que as classificações sigam as diretrizes internas da instituição
- **Claude Code** — assistente de código para acelerar o desenvolvimento

---

### Arquitetura e Design

> **"Por que o Agente 2b só é acionado para casos Críticos?"**

Otimização de custo e throughput. Casos não-críticos não precisam de escalação formal — encerram em `END` sem chamada adicional ao LLM. O roteamento condicional é explicit no grafo e auditável no `execucao.jsonl`.

> **"Por que o Agente 3 não está dentro do grafo por-item?"**

O LangGraph processa itens unitários. O Agente 3 consolida 500 resultados — é uma operação de batch que não faz sentido dentro do grafo individual. O decorator `@log_node("agente_3")` garante que ele apareça no `execucao.jsonl` com `start`, `end` e `duration_ms`, mantendo rastreabilidade completa.

> **"O fluxo é rastreável? Como identifico em qual agente uma reclamação está?"**

Dois mecanismos: `current_node` no estado indica o nó atual em tempo real; `node_history` acumula o histórico de execução de cada nó com timestamps e duração. O `execucao.jsonl` registra eventos `start`/`end`/`error` por `reclamacao_id` e por agente.

> **"Como funciona o paralelismo?"**

O `process_batch` usa `asyncio.gather` + `Semaphore(max_concurrency)` — todas as reclamações processadas concorrentemente no event loop assíncrono, sem threads desnecessárias. O Agente 3 usa `asyncio.to_thread` para não bloquear o event loop durante operações bloqueantes (pandas + LLM).

---

### Segurança e Governança

> **"Como vocês protegem dados sensíveis (PII)?"**

Mascaramento via regex em `guardrails/pii.py` aplicado em três momentos: no resumo gerado pelo Agente 1, nos campos de texto antes de qualquer gravação em disco (`sanitize_for_persistence`), e nas justificativas de risco antes de enviar ao LLM no Agente 3. CPF, CNPJ, número de cartão, e-mail, telefone e chave PIX nunca chegam aos arquivos de saída nem ao LLM de relatório.

> **"O que acontece se alguém tentar injetar um prompt malicioso na reclamação?"**

`guardrails/injection.py` detecta 15 padrões — SQL tautologia, turno falso, template injection, entre outros. O item **não é descartado**: segue no pipeline com `injection_flags` preenchido e `revisao_humana=True`, ficando visível no painel de incidentes do relatório HTML para triagem manual. Nenhuma reclamação se perde silenciosamente.

> **"Como protegem o relatório HTML contra XSS?"**

Jinja2 com `autoescape=True` obrigatório. Campos vindos do CSV (como `canal` e `id`) são escapados automaticamente. Sem isso, uma reclamação com `<script>` no campo canal viraria XSS armazenado no relatório aberto pelo time de Compliance.

> **"E o CSV? Pode ter fórmulas maliciosas?"**

`guardrails/output_safety.py` neutraliza CSV formula injection (CWE-1236) — qualquer célula que comece com `=`, `+`, `-` ou `@` recebe um prefixo que impede execução automática no Excel.

---

### Custo

> **"Como pensaram em otimização de custo?"**

- **Modelos diferentes por complexidade:** modelo menor para classificação (Agente 1), modelo mais capaz para recomendações gerenciais (Agente 3)
- **Roteamento condicional:** Agente 2b só consome tokens para casos Críticos
- **RAG local com TF-IDF:** zero custo de embeddings — busca semântica suficiente para o corpus da Política Interna
- **Mock determinístico:** desenvolvimento e testes sem consumir tokens reais
- **Agente 3 recebe apenas dados agregados:** o LLM de relatório nunca processa os 500 textos brutos — só estatísticas e até 50 casos críticos resumidos

---

### Tratamento de casos não-críticos

> **"Por que casos com risco Baixo, Médio e Alto não passam pelo Agente 2b?"**

**Resposta:**

Casos não-críticos **são tratados** — eles passam pelo Agente 1 (classificação completa) e pelo Agente 2 (avaliação de risco com justificativa e flags). O que não acontece é **escalação imediata**, e isso é uma decisão arquitetural intencional, não uma lacuna.

O modelo segue a lógica de **resposta proporcional ao risco**, que é como instituições financeiras reais operam:

- **Crítico** → requer intervenção humana imediata → `agente_2b` garante o escalonamento formal e rastreável antes de qualquer outra ação
- **Alto, Médio, Baixo** → requerem acompanhamento gerencial, não escalação de emergência → são consolidados pelo `agente_3`, que os agrupa por categoria e produto, identifica padrões recorrentes e gera recomendações acionáveis para a equipe de gestão

O relatório gerencial **é o tratamento** dos casos não-críticos. Em vez de gerar uma ação individual para cada uma das centenas de reclamações de baixo risco, o sistema produz inteligência agregada: quais categorias concentram mais volume, quais produtos acumulam mais insatisfação, quais SLAs precisam de atenção. Isso é mais útil para a gestão do que um ticket individual por reclamação.

Além disso, escalar todos os casos individualmente — independentemente do risco — geraria ruído operacional e eliminaria o propósito da triagem automatizada. A separação entre escalação emergencial (agente_2b) e tratamento gerencial (agente_3) é exatamente a **separação de responsabilidades** que o desafio avalia.

---

### Resiliência

> **"O que acontece se o LLM falhar no meio de 500 reclamações?"**

`process_batch` opera em duas fases: primeiro processa o lote completo coletando exceções sem propagar (`return_exceptions=True`); depois faz retry individual e serializado apenas dos itens que falharam. Itens que falham nas duas tentativas viram registros com `status_processamento="falhou"` — os dados de entrada são preservados em `resultados.json` para reprocessamento posterior. Nenhuma reclamação desaparece.

> **"O que acontece se o guardrail do gateway bloquear uma chamada?"**

`GuardrailBlockedError` é capturado em cada agente. O nó cai para classificação heurística determinística como fallback e sinaliza `guardrail_bloqueado=True` no estado para auditoria. A reclamação segue no pipeline normalmente.

---

## Resumo dos critérios de avaliação

| Critério | Peso | Como está coberto |
|---|---|---|
| **Funcionalidade** | 30% | Pipeline completo, mock + LLM real, todas as saídas geradas |
| **Uso de Ferramentas de IA** | 20% | LangGraph, Bedrock/LiteLLM, RAG TF-IDF, Pydantic structured output |
| **Arquitetura e Design** | 20% | 4 agentes com responsabilidades distintas, roteamento condicional, grafo rastreável |
| **Segurança e Governança** | 15% | PII, profanity, injection, XSS, CSV formula, fallback gracioso |
| **Apresentação e Justificativa** | 15% | Este documento + ADR.md + pitch de 5 minutos |

**Critério bônus:** otimização de custo demonstrada — modelos por complexidade, roteamento condicional, RAG local, mock para dev.
