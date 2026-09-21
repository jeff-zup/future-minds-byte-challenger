from langgraph.graph import END, StateGraph

from graph.nodes.escalonamento import node_escalonamento
from graph.nodes.estruturacao import build_node as build_estruturacao
from graph.nodes.risco import build_node as build_risco
from graph.state import ComplaintState


def build_item_graph(retriever):
    graph = StateGraph(ComplaintState)

    graph.add_node("agente_1", build_estruturacao(retriever))
    graph.add_node("agente_2", build_risco(retriever))
    graph.add_node("agente_2b", node_escalonamento)

    graph.set_entry_point("agente_1")
    graph.add_edge("agente_1", "agente_2")

    def route_after_risk(state: ComplaintState):
        return "agente_2b" if state["nivel_risco"] == "Crítico" else END

    graph.add_conditional_edges(
        "agente_2",
        route_after_risk,
        {"agente_2b": "agente_2b", END: END},
    )
    graph.add_edge("agente_2b", END)
    return graph.compile()
