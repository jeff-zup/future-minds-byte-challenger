"""
Regressão do vazamento de PII por mensagem de erro.

Mensagens de exceção não passavam por mask_pii, então o mesmo registro que
gravava `texto_reclamacao` como "CPF [CPF]" gravava o CPF em claro no campo de
erro — em resultados.json, resultados.csv, relatorio.json, relatorio.html e
execucao.jsonl.
"""

import asyncio
import json

import pandas as pd
import pytest

import graph.nodes.risco as risco_module
from graph.build_graph import build_item_graph
from graph.logging_utils import LOG_FILE, reset_log
from guardrails.pii import safe_error_message
from main import make_initial_state, process_batch, sanitize_for_persistence

PII_CRUA = {
    "CPF": "123.456.789-00",
    "cartão": "4111 1111 1111 1111",
    "telefone": "(11) 98765-4321",
    "e-mail": "joao.silva@email.com",
}

# Formato real de llm.utils.extract_json, que embute a resposta crua do modelo.
MENSAGEM_HOSTIL = (
    "Resposta do LLM não contém JSON válido. Texto original recebido: "
    "'cliente João, CPF 123.456.789-00, cartão 4111 1111 1111 1111, "
    "tel (11) 98765-4321, email joao.silva@email.com'"
)


class _FakeRetriever:
    def search(self, query: str, k: int = 4) -> str:
        return "[Política Interna, página 1]\nTrecho."


@pytest.mark.parametrize("rotulo,bruto", PII_CRUA.items())
def test_safe_error_message_mascara_pii(rotulo, bruto):
    msg = safe_error_message(ValueError(MENSAGEM_HOSTIL))
    assert bruto not in msg, f"{rotulo} vazou na mensagem de erro"


def test_safe_error_message_preserva_diagnostico():
    """Mascarar não pode cegar o diagnóstico: tipo e causa continuam legíveis."""
    msg = safe_error_message(ValueError(MENSAGEM_HOSTIL))
    assert msg.startswith("ValueError:")
    assert "não contém JSON válido" in msg
    assert "[CPF]" in msg


def test_safe_error_message_trunca_resposta_gigante():
    """extract_json embute até 2000 chars da resposta crua do modelo."""
    msg = safe_error_message(ValueError("x" * 5000))
    assert len(msg) <= 300


def test_pii_nao_vaza_em_resultado_nem_log(monkeypatch, tmp_path):
    monkeypatch.setattr(
        risco_module,
        "_mock",
        lambda state: (_ for _ in ()).throw(ValueError(MENSAGEM_HOSTIL)),
    )
    reset_log()

    graph = build_item_graph(_FakeRetriever())
    row = pd.Series({
        "id": "REC-PII", "data_reclamacao": "2026-01-01", "canal": "SAC",
        "texto_reclamacao": "Houve cobrança indevida.",
        "produto": "Cartão de Crédito", "status": "Aberta",
    })
    results = asyncio.run(process_batch(graph, [make_initial_state(row)]))
    persistido = json.dumps(sanitize_for_persistence(results[0]), ensure_ascii=False)
    log = LOG_FILE.read_text(encoding="utf-8")

    for rotulo, bruto in PII_CRUA.items():
        assert bruto not in persistido, f"{rotulo} vazou em resultados.json"
        assert bruto not in log, f"{rotulo} vazou em execucao.jsonl"
