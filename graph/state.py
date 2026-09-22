from typing import Literal, Optional, TypedDict


# Estado compartilhado que flui por todos os nós do LangGraph.
# Campos são preenchidos progressivamente: CSV → Agente 1 → Agente 2 → Agente 2b.
class ComplaintState(TypedDict):
    # --- Dados de entrada (vindos do CSV) ---
    id: str
    data_reclamacao: str
    canal: str                      # SAC, Banco Central, Procon, Ouvidoria, Redes Sociais
    texto_reclamacao: str
    produto_original: Optional[str] # Produto como está no CSV, antes de normalização
    status: str                     # Aberta | Em análise

    # --- Campos preenchidos pelo Agente 1 (estruturacao) ---
    categoria: Optional[str]        # Cobrança Indevida | Atendimento | Fraude/Segurança | ...
    produto: Optional[str]          # Produto normalizado para vocabulário controlado
    sentimento: Optional[str]       # Positivo | Neutro | Negativo | Crítico
    urgencia: Optional[str]         # Baixa | Média | Alta | Crítica
    resumo: Optional[str]           # Resumo em até 3 frases, com PII mascarado

    # --- Campos preenchidos pelo Agente 2 (risco) ---
    nivel_risco: Optional[Literal["Baixo", "Médio", "Alto", "Crítico"]]
    risco_justificativa: Optional[str]  # Texto explicando o nível de risco atribuído
    risco_flags: list[str]          # Tags: "fraude", "lgpd", "orgao_regulador", ...
    escalado: bool                  # True se Agente 2b foi executado
    guardrail_bloqueado: Optional[bool]  # True se o gateway bloqueou a chamada e o fallback heurístico foi usado

    # --- Metadados de rastreamento ---
    current_node: str
    node_history: list[dict]        # Registro de tempo de execução por nó
