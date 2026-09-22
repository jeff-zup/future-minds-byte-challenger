from __future__ import annotations

from pydantic import BaseModel, Field

from config import settings
from graph.logging_utils import log_node
from llm.bedrock import bedrock_llm


# Schema Pydantic que valida a saída de risco do LLM.
# risco_flags é lista aberta; nivel_risco é enum estrito.
class RiskOutput(BaseModel):
    nivel_risco: str = Field(pattern=r"^(Baixo|Médio|Alto|Crítico)$")
    risco_justificativa: str
    risco_flags: list[str]


SYSTEM_PROMPT = """
Você é o Agente 2 do FinGuard, especialista em Risco e Compliance.
Analise a saída do Agente 1 e o contexto da Política Interna e atribua nível de risco.

CRITÉRIOS POR NÍVEL DE RISCO:
- Crítico: fraude ou transação não autorizada confirmada; acionamento de órgão regulador (Banco Central, Procon, ANPD); ameaça explícita de processo judicial; vazamento confirmado de dados pessoais.
- Alto: cobrança indevida recorrente (≥2x) ou de valor elevado; exposição em redes sociais/mídia com risco de viralização; suspeita de violação LGPD sem confirmação de vazamento; urgência Alta do Agente 1 com ao menos um indicador adicional de risco.
- Médio: reclamação não resolvida após tentativas anteriores (reincidência); problema operacional sem impacto financeiro imediato; urgência Média do Agente 1 sem outros sinais de escalação.
- Baixo: reclamação pontual e isolada, sem histórico declarado, sem dano financeiro confirmado e urgência Baixa do Agente 1.

FLAGS DISPONÍVEIS — use APENAS as que se aplicam diretamente ao texto da reclamação:
- "fraude": transação não reconhecida pelo cliente
- "transacao_nao_autorizada": compra, saque ou transferência sem autorização explícita do cliente
- "lgpd": possível exposição ou uso indevido de dados pessoais sem confirmação de vazamento
- "dados_vazados": vazamento de dados mencionado explicitamente pelo cliente
- "risco_reputacional": menção a redes sociais, imprensa ou intenção clara de tornar público
- "orgao_regulador": menção a Banco Central, Procon, ANPD ou qualquer órgão regulador
- "ameaca_judicial": cliente menciona advogado, processo, ação judicial ou juizado
- "reincidencia": cliente relata que já reclamou antes e o problema não foi resolvido

Retorne SOMENTE JSON válido:
{
  "nivel_risco": "Baixo|Médio|Alto|Crítico",
  "risco_justificativa": "texto curto e objetivo, máximo 2 frases",
  "risco_flags": ["tag1", "tag2"]
}
Não invente violações ou fatos ausentes no texto.
""".strip()


def _mock(state: dict) -> dict:
    """
    Avaliação de risco determinística por palavras-chave, usada quando FINGUARD_MOCK_LLM=true.
    Espelha os critérios do SYSTEM_PROMPT para que mock e modo real produzam resultados coerentes.
    """
    text = f"{state.get('resumo', '')} {state.get('texto_reclamacao', '')}".lower()
    urgencia_agente1 = state.get("urgencia", "Baixa")
    flags: list[str] = []

    # Fraude e transação não autorizada
    if any(w in text for w in ["fraude", "não reconheço", "nao reconheco", "não fiz", "nao fiz", "clonaram", "clonado", "roubaram"]):
        flags += ["fraude", "transacao_nao_autorizada"]

    # Vazamento confirmado tem flag própria, mais grave que suspeita LGPD
    if any(w in text for w in ["vazamento", "dados vazados", "foram vazados", "dados expostos", "incidente de segurança", "meus dados foram expostos", "meus dados foram vendidos"]):
        flags += ["dados_vazados", "lgpd"]
    elif any(w in text for w in ["cpf", "dados pessoais", "lgpd", "privacidade", "sigilo"]):
        flags += ["lgpd"]

    # Risco reputacional: intenção declarada de exposição pública
    if any(w in text for w in ["imprensa", "viral", "instagram", "twitter", "x.com", "facebook", "redes sociais", "vou postar", "vou publicar"]):
        flags += ["risco_reputacional"]

    # Órgão regulador mencionado no texto (canal regulatório é tratado pelo override ao final)
    if any(w in text for w in ["banco central", "procon", "anpd", "bacen"]):
        flags += ["orgao_regulador"]

    # Ameaça judicial: advogado ou processo mencionado explicitamente
    if any(w in text for w in ["advogado", "processo", "ação judicial", "acao judicial", "juizado", "tribunal"]):
        flags += ["ameaca_judicial", "orgao_regulador"]

    # Reincidência: cliente já tentou resolver antes
    if any(w in text for w in ["já reclamei", "ja reclamei", "segunda vez", "terceira vez", "não resolveram", "nao resolveram", "voltei a reclamar", "novamente"]):
        flags += ["reincidencia"]

    # Hierarquia de risco baseada nos critérios do SYSTEM_PROMPT
    flags_set = set(flags)
    if "fraude" in flags_set or "dados_vazados" in flags_set or "ameaca_judicial" in flags_set:
        nivel = "Crítico"
    elif "orgao_regulador" in flags_set or urgencia_agente1 == "Alta":
        nivel = "Alto"
    elif "lgpd" in flags_set or "risco_reputacional" in flags_set or "reincidencia" in flags_set or urgencia_agente1 == "Média":
        nivel = "Médio"
    else:
        nivel = "Baixo"

    flags_sorted = sorted(flags_set)
    justificativa = "; ".join(flags_sorted) if flags_sorted else f"Risco compatível com urgência {urgencia_agente1}."
    return RiskOutput(nivel_risco=nivel, risco_justificativa=justificativa, risco_flags=flags_sorted).model_dump()


def build_node(retriever):
    """Constrói o nó do Agente 2 com acesso ao retriever RAG injetado."""
    @log_node("agente_2")
    def node_risco(state):
        # Query focada em compliance e risco para recuperar trechos relevantes da Política Interna
        query = (
            f"fraude LGPD compliance escalonamento risco reputacional ameaça judicial canal {state['canal']} "
            f"produto {state.get('produto')} urgência {state.get('urgencia')} categoria {state.get('categoria')}"
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
Texto original: {state.get('texto_reclamacao')}
""".strip()
            raw = bedrock_llm.invoke_json(settings.risk_model, SYSTEM_PROMPT, prompt)
            result = RiskOutput.model_validate(raw).model_dump()

        # Regra determinística da Política Interna: Banco Central e Procon exigem risco Crítico
        # independentemente da avaliação do LLM — garante conformidade regulatória sem depender de IA.
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
