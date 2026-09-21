from __future__ import annotations

from pydantic import BaseModel, Field

from config import settings
from graph.logging_utils import log_node
from llm.bedrock import bedrock_llm


class RiskOutput(BaseModel):
    nivel_risco: str = Field(pattern=r"^(Baixo|Médio|Alto|Crítico)$")
    risco_justificativa: str
    risco_flags: list[str]


SYSTEM_PROMPT = """
Você é o Agente 2 do FinGuard, especialista em Risco e Compliance.
Analise a saída estruturada do Agente 1 e o contexto da Política Interna.
Verifique: fraude/transação não autorizada; LGPD/sigilo bancário; risco reputacional; menção a imprensa/redes sociais/órgãos reguladores; necessidade de escalação imediata.

Retorne SOMENTE JSON válido:
{
  "nivel_risco": "Baixo|Médio|Alto|Crítico",
  "risco_justificativa": "texto curto e objetivo",
  "risco_flags": ["tag1", "tag2"]
}
Não invente violações ou fatos ausentes.
""".strip()


def _mock(state: dict) -> dict:
    text = f"{state.get('resumo','')} {state.get('texto_reclamacao','')}".lower()
    canal = state.get("canal", "").lower()
    flags: list[str] = []

    if any(w in text for w in ["fraude", "não reconheço", "nao reconheco", "não fiz", "nao fiz"]):
        flags += ["fraude", "transacao_nao_autorizada"]
    if any(w in text for w in ["cpf", "dados pessoais", "vazamento", "lgpd"]):
        flags += ["lgpd"]
    if any(w in text for w in ["imprensa", "viral", "instagram", "twitter", "x.com", "facebook"]):
        flags += ["risco_reputacional"]
    if "banco central" in canal or "procon" in canal or any(w in text for w in ["banco central", "procon", "justiça", "justica"]):
        flags += ["orgao_regulador"]

    if "fraude" in flags or "orgao_regulador" in flags or "lgpd" in flags:
        nivel = "Crítico"
    elif state.get("urgencia") == "Alta":
        nivel = "Alto"
    elif state.get("urgencia") == "Média":
        nivel = "Médio"
    else:
        nivel = "Baixo"

    justificativa = "; ".join(flags) if flags else f"Risco compatível com urgência {state.get('urgencia', 'não informada')}."
    return RiskOutput(nivel_risco=nivel, risco_justificativa=justificativa, risco_flags=sorted(set(flags))).model_dump()


def build_node(retriever):
    @log_node("agente_2")
    def node_risco(state):
        query = (
            f"fraude LGPD compliance escalonamento risco reputacional canal {state['canal']} "
            f"produto {state.get('produto')} urgência {state.get('urgencia')}"
        )
        policy_context = retriever.search(query, k=4)

        if settings.mock_llm:
            result = _mock(state)
        else:
            prompt = f"""
POLÍTICA INTERNA RELEVANTE:
{policy_context}

SAÍDA DO AGENTE 1:
Categoria: {state.get('categoria')}
Produto: {state.get('produto')}
Sentimento: {state.get('sentimento')}
Urgência: {state.get('urgencia')}
Resumo: {state.get('resumo')}
Canal: {state.get('canal')}
""".strip()
            raw = bedrock_llm.invoke_json(settings.risk_model, SYSTEM_PROMPT, prompt)
            result = RiskOutput.model_validate(raw).model_dump()

        # Regra crítica da Política Interna: Banco Central / Procon => urgência automaticamente crítica.
        canal_norm = state.get("canal", "").strip().lower()
        if canal_norm in {"banco central", "procon"}:
            state["urgencia"] = "Crítica"
            result["nivel_risco"] = "Crítico"
            if "urgencia_corrigida_por_canal" not in result["risco_flags"]:
                result["risco_flags"].append("urgencia_corrigida_por_canal")
            if "orgao_regulador" not in result["risco_flags"]:
                result["risco_flags"].append("orgao_regulador")

        return {**state, **result}

    return node_risco
