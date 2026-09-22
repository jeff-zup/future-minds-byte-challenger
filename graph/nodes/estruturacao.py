from __future__ import annotations

from pydantic import BaseModel, Field

from config import settings
from graph.logging_utils import log_node
from guardrails.injection import SYSTEM_HARDENING, detect_injection, wrap_untrusted
from guardrails.pii import mask_pii
from guardrails.profanity import sanitize_profanity
from llm.factory import llm_client
from llm.utils import GuardrailBlockedError


# Schema Pydantic que valida e rejeita qualquer resposta do LLM fora dos valores permitidos.
# Garante que os enums nunca cheguem corrompidos ao restante do pipeline.
class ClassificationOutput(BaseModel):
    categoria: str = Field(pattern=r"^(Cobrança Indevida|Atendimento|Fraude/Segurança|Produto/Serviço|Cancelamento|Outros)$")
    produto: str = Field(pattern=r"^(Cartão de Crédito|Conta Corrente|Empréstimo|Investimentos|Seguros|Não Identificado)$")
    sentimento: str = Field(pattern=r"^(Positivo|Neutro|Negativo|Crítico)$")
    urgencia: str = Field(pattern=r"^(Baixa|Média|Alta|Crítica)$")
    resumo: str


# Instrução de sistema enviada ao LLM em cada chamada real.
# Define vocabulário controlado, critérios de urgência e exige retorno estritamente em JSON.
SYSTEM_PROMPT = """
Você é o Agente 1 do FinGuard, especialista em recepção e estruturação de reclamações financeiras.
Sua função é classificar a reclamação de forma consistente e conservadora, usando a Política Interna fornecida como contexto.

Categorias permitidas: Cobrança Indevida, Atendimento, Fraude/Segurança, Produto/Serviço, Cancelamento, Outros.
Produtos permitidos: Cartão de Crédito, Conta Corrente, Empréstimo, Investimentos, Seguros, Não Identificado.
Sentimentos permitidos: Positivo, Neutro, Negativo, Crítico.
Urgências permitidas: Baixa, Média, Alta, Crítica.

CRITÉRIOS DE URGÊNCIA:
- Crítica: fraude ou transação não autorizada; canais Banco Central ou Procon; risco imediato ao patrimônio do cliente.
- Alta: cobrança indevida de valor relevante ou recorrente; canal Ouvidoria; problema impedindo totalmente o uso do produto.
- Média: atendimento insatisfatório; cancelamento não processado; problema operacional sem bloqueio total.
- Baixa: dúvida, consulta ou reclamação pontual sem dano financeiro confirmado.

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
# A instrução sobre o bloco de texto do cliente vive em guardrails.injection.SYSTEM_HARDENING,
# que é concatenado a este prompt e nomeia os delimitadores realmente usados.


def _mock(state: dict) -> dict:
    """
    Classificação determinística por palavras-chave, usada quando FINGUARD_MOCK_LLM=true.
    Permite rodar e testar o pipeline sem credenciais AWS.
    Espelha os critérios de urgência do SYSTEM_PROMPT.
    """
    text = state["texto_reclamacao"].lower()
    canal = state["canal"].lower()
    produto_original = (state.get("produto_original") or "").strip()

    # Detecta indicadores de fraude primeiro (maior prioridade)
    if any(w in text for w in ["não reconheço", "nao reconheco", "fraude", "roubaram", "clonaram", "compra que eu não fiz", "compra que eu nao fiz", "transação não autorizada", "transacao nao autorizada"]):
        categoria = "Fraude/Segurança"
        urgencia = "Crítica"
    elif any(w in text for w in ["cobrança", "cobranca", "cobrado", "fatura", "taxa", "desconto indevido"]):
        categoria = "Cobrança Indevida"
        # Cobranças repetidas ou de alto valor elevam urgência para Alta
        urgencia = "Alta" if any(w in text for w in ["três vezes", "tres vezes", "duas vezes", "duas cobranças", "r$ 7", "r$ 8", "r$ 9", "novamente"]) else "Média"
    elif "cancel" in text:
        categoria = "Cancelamento"
        urgencia = "Média"
    elif any(w in text for w in ["atendimento", "ninguém resolve", "ninguem resolve", "não fui atendido", "nao fui atendido"]):
        categoria = "Atendimento"
        urgencia = "Média"
    else:
        categoria = "Produto/Serviço"
        urgencia = "Baixa"

    # Canais regulatórios forçam urgência máxima independentemente do conteúdo
    if "banco central" in canal or "procon" in canal:
        urgencia = "Crítica"
    # Ouvidoria é canal regulado (Resolução BCB): eleva urgência para Alta no mínimo
    elif "ouvidoria" in canal and urgencia not in ("Crítica",):
        urgencia = "Alta"

    sentimento = "Crítico" if urgencia == "Crítica" else ("Negativo" if categoria != "Produto/Serviço" else "Neutro")
    # Mantém o produto original se já está no vocabulário controlado; senão marca como não identificado
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
    """
    Constrói o nó do Agente 1 com acesso ao retriever RAG injetado.
    O closure captura o retriever para evitar estado global.
    """
    @log_node("agente_1")
    def node_estruturacao(state):
        # GUARDRAIL DE ENTRADA: o texto vem de canal externo não confiável.
        # A reclamação NUNCA é descartada — apenas sinalizada para revisão humana,
        # porque um falso positivo não pode custar uma reclamação regulatória legítima.
        injection_flags = detect_injection(state.get("texto_reclamacao"))

        # Monta query combinando canal, produto e texto para recuperar trechos relevantes da Política Interna
        query = (
            f"classificação de urgência e procedimento para canal {state['canal']}; "
            f"produto {state.get('produto_original')}; reclamação {state['texto_reclamacao']}"
        )
        policy_context = retriever.search(query, k=4)

        if settings.mock_llm:
            result = _mock(state)
        else:
            # PII é mascarada ANTES de sair da máquina: o texto cru com CPF/cartão
            # não deve trafegar até a AWS. Os agentes só precisam do padrão, não do dado.
            safe_text = mask_pii(state["texto_reclamacao"]) or ""
            prompt = f"""
POLÍTICA INTERNA RELEVANTE:
{policy_context}

DADOS DA RECLAMAÇÃO:
Canal: {state['canal']}
Produto informado no CSV: {state.get('produto_original') or 'vazio'}

{wrap_untrusted(safe_text)}
""".strip()
            try:
                raw = llm_client.invoke_json(
                    settings.model_classifier, SYSTEM_PROMPT + SYSTEM_HARDENING, prompt
                )
                result = ClassificationOutput.model_validate(raw).model_dump()
            except GuardrailBlockedError:
                # O gateway bloqueou a requisição por um guardrail de segurança
                # (ex.: falso positivo de prompt injection). Em vez de derrubar
                # a reclamação inteira, aplicamos a classificação heurística
                # determinística como fallback e sinalizamos isso no estado
                # para auditoria/revisão posterior.
                result = _mock(state)
                result["guardrail_bloqueado"] = True

        result["injection_flags"] = injection_flags

        # Guardrails aplicados no resumo antes de persistir: remove PII e palavrões
        result["resumo"] = sanitize_profanity(mask_pii(result["resumo"]))
        return {**state, **result}

    return node_estruturacao
