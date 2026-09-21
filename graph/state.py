from typing import Literal, Optional, TypedDict


class ComplaintState(TypedDict):
    id: str
    data_reclamacao: str
    canal: str
    texto_reclamacao: str
    produto_original: Optional[str]
    status: str

    categoria: Optional[str]
    produto: Optional[str]
    sentimento: Optional[str]
    urgencia: Optional[str]
    resumo: Optional[str]

    nivel_risco: Optional[Literal["Baixo", "Médio", "Alto", "Crítico"]]
    risco_justificativa: Optional[str]
    risco_flags: list[str]
    escalado: bool

    current_node: str
    node_history: list[dict]
