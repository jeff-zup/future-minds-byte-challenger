from langgraph.graph import END, StateGraph

from graph.nodes.escalonamento import node_escalonamento
from graph.nodes.estruturacao import build_node as build_estruturacao
from graph.nodes.risco import build_node as build_risco
from graph.state import ComplaintState


def build_item_graph(retriever):
    """
    Monta o grafo LangGraph para processar uma reclamação individual.

    Fluxo linear com desvio condicional:
        agente_1 (classificação) → agente_2 (risco) → [agente_2b se Crítico] → END

    O mesmo grafo compilado é reutilizado para todas as reclamações via abatch().
    """
    graph = StateGraph(ComplaintState)

    # Registra os três nós do pipeline
    graph.add_node("agente_1", build_estruturacao(retriever))
    graph.add_node("agente_2", build_risco(retriever))
    graph.add_node("agente_2b", node_escalonamento)

    graph.set_entry_point("agente_1")
    graph.add_edge("agente_1", "agente_2")

    # Agente 2b (escalonamento) só é acionado quando o risco é Crítico.
    # Casos não-críticos encerram diretamente em END sem custo adicional.
    def route_after_risk(state: ComplaintState):
        return "agente_2b" if state["nivel_risco"] == "Crítico" else END

    graph.add_conditional_edges(
        "agente_2",
        route_after_risk,
        {"agente_2b": "agente_2b", END: END},
    )
    graph.add_edge("agente_2b", END)
    return graph.compile()
