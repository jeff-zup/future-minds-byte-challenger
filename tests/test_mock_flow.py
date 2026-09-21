from graph.nodes.escalonamento import node_escalonamento


def test_escalation_marks_state():
    state = {
        "id": "X",
        "risco_flags": ["fraude"],
        "risco_justificativa": "Indício de fraude.",
        "node_history": [],
        "current_node": "agente_2",
        "escalado": False,
    }
    result = node_escalonamento(state)
    assert result["escalado"] is True
    assert "Compliance" in result["risco_justificativa"]
