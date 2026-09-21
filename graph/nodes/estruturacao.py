from __future__ import annotations

from pydantic import BaseModel, Field

from config import settings
from graph.logging_utils import log_node
from guardrails.pii import mask_pii
from guardrails.profanity import sanitize_profanity
from llm.bedrock import bedrock_llm


class ClassificationOutput(BaseModel):
    categoria: str = Field(pattern=r"^(Cobrança Indevida|Atendimento|Fraude/Segurança|Produto/Serviço|Cancelamento|Outros)$")
    produto: str = Field(pattern=r"^(Cartão de Crédito|Conta Corrente|Empréstimo|Investimentos|Seguros|Não Identificado)$")
    sentimento: str = Field(pattern=r"^(Positivo|Neutro|Negativo|Crítico)$")
    urgencia: str = Field(pattern=r"^(Baixa|Média|Alta|Crítica)$")
    resumo: str


SYSTEM_PROMPT = """
Você é o Agente 1 do FinGuard, especialista em recepção e estruturação de reclamações financeiras.
Sua função é classificar a reclamação de forma consistente e conservadora, usando a Política Interna fornecida como contexto.

Categorias permitidas: Cobrança Indevida, Atendimento, Fraude/Segurança, Produto/Serviço, Cancelamento, Outros.
Produtos permitidos: Cartão de Crédito, Conta Corrente, Empréstimo, Investimentos, Seguros, Não Identificado.
Sentimentos permitidos: Positivo, Neutro, Negativo, Crítico.
Urgências permitidas: Baixa, Média, Alta, Crítica.

Retorne SOMENTE JSON válido com exatamente:
{
  "categoria": "...",
  "produto": "...",
  "sentimento": "...",
  "urgencia": "...",
  "resumo": "..."
}
O resumo deve ter no máximo 3 frases e não deve inventar fatos.
""".strip()


def _mock(state: dict) -> dict:
    text = state["texto_reclamacao"].lower()
    canal = state["canal"].lower()
    produto_original = (state.get("produto_original") or "").strip()

    if any(w in text for w in ["não reconheço", "nao reconheco", "fraude", "roubaram", "compra que eu não fiz", "compra que eu nao fiz"]):
        categoria = "Fraude/Segurança"
        urgencia = "Crítica"
    elif any(w in text for w in ["cobrança", "cobranca", "cobrado", "fatura", "taxa"]):
        categoria = "Cobrança Indevida"
        urgencia = "Alta" if any(w in text for w in ["três vezes", "tres vezes", "r$ 7", "r$ 8", "r$ 9"]) else "Média"
    elif "cancel" in text:
        categoria = "Cancelamento"
        urgencia = "Média"
    elif any(w in text for w in ["atendimento", "ninguém resolve", "ninguem resolve"]):
        categoria = "Atendimento"
        urgencia = "Média"
    else:
        categoria = "Produto/Serviço"
        urgencia = "Baixa"

    if "banco central" in canal or "procon" in canal:
        urgencia = "Crítica"

    sentimento = "Crítico" if urgencia == "Crítica" else ("Negativo" if categoria != "Produto/Serviço" else "Neutro")
    produto = produto_original if produto_original in {"Cartão de Crédito", "Conta Corrente", "Empréstimo", "Investimentos", "Seguros"} else "Não Identificado"
    resumo = state["texto_reclamacao"].strip()
    if len(resumo) > 260:
        resumo = resumo[:257] + "..."

    return ClassificationOutput(
        categoria=categoria,
        produto=produto,
        sentimento=sentimento,
        urgencia=urgencia,
        resumo=resumo,
    ).model_dump()


def build_node(retriever):
    @log_node("agente_1")
    def node_estruturacao(state):
        query = (
            f"classificação de urgência e procedimento para canal {state['canal']}; "
            f"produto {state.get('produto_original')}; reclamação {state['texto_reclamacao']}"
        )
        policy_context = retriever.search(query, k=4)

        if settings.mock_llm:
            result = _mock(state)
        else:
            prompt = f"""
POLÍTICA INTERNA RELEVANTE:
{policy_context}

DADOS DA RECLAMAÇÃO:
Canal: {state['canal']}
Produto informado no CSV: {state.get('produto_original') or 'vazio'}
Texto: {state['texto_reclamacao']}
""".strip()
            raw = bedrock_llm.invoke_json(settings.classifier_model, SYSTEM_PROMPT, prompt)
            result = ClassificationOutput.model_validate(raw).model_dump()

        result["resumo"] = sanitize_profanity(mask_pii(result["resumo"]))
        return {**state, **result}

    return node_estruturacao
