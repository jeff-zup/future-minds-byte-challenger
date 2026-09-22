# ADR — Decisões principais do FinGuard

## 1. LangGraph para orquestração
Escolhido para tornar explícito o estado, a separação de nós e o roteamento condicional.

## 2. Um grafo por reclamação + agregação posterior
O grafo processa cada reclamação independentemente. O lote usa `abatch` com concorrência controlada. O relatório roda somente após todos os itens terminarem.

## 3. Regras críticas determinísticas
Banco Central/Procon e o nó de escalonamento não dependem exclusivamente do LLM. Isso reduz risco de descumprimento de regra de negócio.

## 4. RAG local
A Política Interna é indexada localmente. A versão entregue usa TF-IDF para simplicidade e previsibilidade. Pode ser substituída por FAISS/embeddings preservando a interface.

## 5. LLM somente onde agrega valor
Pandas calcula estatísticas. O LLM é usado para classificação, parecer de risco e recomendações, evitando alucinação numérica e reduzindo custo.

## 6. Privacidade
PII é mascarada antes da persistência nos resultados e no relatório gerencial, **e também
antes de qualquer envio ao LLM** — o texto cru com CPF ou número de cartão não trafega até
a AWS. Cobertura: CPF, CNPJ, cartão, e-mail, telefone, CEP, chave PIX e conta/agência.

## 7. Guardrail de entrada, não só de saída
Os guardrails originais tratavam apenas o conteúdo do texto na saída. O `texto_reclamacao`
vem de canal público e é entrada não confiável: passa por `guardrails/injection.py` antes de
chegar ao LLM, é isolado em bloco delimitado e o system prompt é endurecido contra o bloco.

Item com suspeita **nunca é descartado** — só marcado com `revisao_humana=True`. Um falso
positivo não pode custar uma reclamação regulatória legítima. A garantia real não vem da
detecção, e sim da decisão #3: as regras críticas são determinísticas e fora do alcance do LLM.

## 8. Segurança de formato na saída
Os artefatos são consumidos por interpretadores (Excel, navegador), então o formato do dado
importa tanto quanto o conteúdo. `relatorio.html` renderiza com `autoescape=True`
(`jinja2.Template` vem com autoescape DESLIGADO por padrão) e `resultados.csv` neutraliza
gatilho de fórmula. Ver `SECURITY.md`.

## 9. Falha isolada por item
O lote usa `return_exceptions=True` com retry individual. Sem isso, uma única resposta
malformada do LLM abortava o `abatch` inteiro e descartava todas as reclamações já
processadas, sem gravar nada. Item que falha nas duas tentativas vira registro auditável
com `status_processamento="falhou"`, preservando a entrada para reprocessamento.
