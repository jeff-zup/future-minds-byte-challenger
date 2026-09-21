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
PII é mascarada antes da persistência nos resultados e no relatório gerencial.
