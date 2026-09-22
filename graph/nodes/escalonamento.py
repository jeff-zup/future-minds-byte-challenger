from graph.logging_utils import log_node


@log_node("agente_2b")
def node_escalonamento(state):
    """
    Agente 2b — Escalonamento automático.

    Executado apenas quando nivel_risco == "Crítico" (roteamento condicional no build_graph).
    Não faz chamada ao LLM: decisões de escalonamento são sempre determinísticas para garantir
    conformidade — o LLM não pode decidir se um caso vai para Compliance, DPO ou Jurídico.

    Ações possíveis com base nos risco_flags:
        - "Compliance notificado"              → sempre (baseline obrigatório)
        - "Prevenção a Fraudes acionada"       → se "fraude" ou "transacao_nao_autorizada"
        - "DPO notificado (LGPD)"              → se "dados_vazados" ou "lgpd" (exigência legal LGPD/ANPD)
        - "Jurídico/Ouvidoria sinalizados"     → se "orgao_regulador"
        - "Jurídico acionado com prioridade"   → se "ameaca_judicial" (cliente mencionou processo)
        - "Gestão de Crise alertada"           → se "risco_reputacional" (exposição em mídia/redes)
    """
    flags = set(state.get("risco_flags", []))
    actions = ["Compliance notificado"]

    if "fraude" in flags or "transacao_nao_autorizada" in flags:
        actions.append("Prevenção a Fraudes acionada")

    # LGPD exige notificação ao DPO e eventualmente à ANPD em até 72h (Art. 48 LGPD)
    if "dados_vazados" in flags or "lgpd" in flags:
        actions.append("DPO notificado (LGPD)")

    if "orgao_regulador" in flags:
        actions.append("Jurídico/Ouvidoria sinalizados")

    # Ameaça judicial requer resposta jurídica prioritária, além do sinalização padrão
    if "ameaca_judicial" in flags and "Jurídico/Ouvidoria sinalizados" not in actions:
        actions.append("Jurídico acionado com prioridade")
    elif "ameaca_judicial" in flags:
        # Substitui sinalização padrão por acionamento prioritário quando há ameaça judicial explícita
        actions = [a.replace("Jurídico/Ouvidoria sinalizados", "Jurídico acionado com prioridade") for a in actions]

    if "risco_reputacional" in flags:
        actions.append("Gestão de Crise alertada")

    # Appenda a nota de escalonamento à justificativa existente do Agente 2
    note = "[ESCALONADO] " + "; ".join(actions) + "."
    justification = (state.get("risco_justificativa") or "").strip()
    if justification:
        justification += " "
    justification += note

    return {**state, "escalado": True, "risco_justificativa": justification}
