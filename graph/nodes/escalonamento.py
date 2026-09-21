from graph.logging_utils import log_node


@log_node("agente_2b")
def node_escalonamento(state):
    flags = set(state.get("risco_flags", []))
    actions = ["Compliance notificado"]
    if "fraude" in flags or "transacao_nao_autorizada" in flags:
        actions.append("Prevenção a Fraudes acionada")
    if "orgao_regulador" in flags:
        actions.append("Jurídico/Ouvidoria sinalizados")

    note = "[ESCALONADO] " + "; ".join(actions) + "."
    justification = (state.get("risco_justificativa") or "").strip()
    if justification:
        justification += " "
    justification += note

    return {**state, "escalado": True, "risco_justificativa": justification}
