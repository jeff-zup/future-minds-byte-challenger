import asyncio

import pytest

import graph.nodes.risco as risco_module
from config import settings
from graph.build_graph import build_item_graph
from main import make_initial_state, process_batch
from rag.retriever import PolicyRetriever


class _FakeRetriever:
    """Evita ler o PDF a cada teste — o RAG não é o alvo aqui."""

    def search(self, query: str, k: int = 4) -> str:
        return "[Política Interna, página 1]\nTrecho de política."


def _state(item_id: str, canal: str = "SAC") -> dict:
    import pandas as pd

    return make_initial_state(pd.Series({
        "id": item_id,
        "data_reclamacao": "2026-01-15",
        "canal": canal,
        "texto_reclamacao": "Cobrança indevida na fatura, quero estorno.",
        "produto": "Cartão de Crédito",
        "status": "Aberta",
    }))


@pytest.fixture
def graph():
    return build_item_graph(_FakeRetriever())


def test_item_com_falha_nao_derruba_o_lote(graph, monkeypatch):
    """
    Regressão do defeito principal: sem return_exceptions, uma exceção em um nó
    abortava o abatch inteiro e descartava todas as reclamações já processadas.
    """
    original = risco_module._mock

    def falha_no_terceiro(state):
        if state["id"] == "REC-3":
            raise ValueError("LLM devolveu categoria inválida")
        return original(state)

    monkeypatch.setattr(risco_module, "_mock", falha_no_terceiro)

    states = [_state(f"REC-{i}") for i in range(1, 6)]
    results = asyncio.run(process_batch(graph, states))

    assert len(results) == 5, "todos os itens devem voltar, inclusive o que falhou"

    ok = [r for r in results if r["status_processamento"] == "ok"]
    failed = [r for r in results if r["status_processamento"] == "falhou"]
    assert len(ok) == 4
    assert len(failed) == 1
    assert failed[0]["id"] == "REC-3"


def test_registro_de_falha_preserva_dados_de_entrada(graph, monkeypatch):
    """O item precisa poder ser reprocessado depois, não sumir."""
    monkeypatch.setattr(
        risco_module, "_mock", lambda state: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    results = asyncio.run(process_batch(graph, [_state("REC-9", canal="Procon")]))

    item = results[0]
    assert item["status_processamento"] == "falhou"
    assert "RuntimeError: boom" in item["erro_processamento"]
    assert item["canal"] == "Procon"
    assert item["texto_reclamacao"]  # entrada preservada


def test_lote_sem_falha_mantem_status_ok(graph):
    results = asyncio.run(process_batch(graph, [_state("REC-1"), _state("REC-2")]))
    assert all(r["status_processamento"] == "ok" for r in results)
    assert all(r["erro_processamento"] is None for r in results)


def test_falha_transitoria_se_recupera_no_retry(graph, monkeypatch):
    """Throttling costuma passar na segunda tentativa — o retry individual cobre isso."""
    original = risco_module._mock
    tentativas = {"REC-1": 0}

    def falha_uma_vez(state):
        if state["id"] == "REC-1":
            tentativas["REC-1"] += 1
            if tentativas["REC-1"] == 1:
                raise RuntimeError("ThrottlingException")
        return original(state)

    monkeypatch.setattr(risco_module, "_mock", falha_uma_vez)

    results = asyncio.run(process_batch(graph, [_state("REC-1")]))
    assert results[0]["status_processamento"] == "ok"
    assert tentativas["REC-1"] == 2
