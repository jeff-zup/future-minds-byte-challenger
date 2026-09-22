from __future__ import annotations

import asyncio
import json

from graph.logging_utils import log_node
from report.build_report import _build_data, _render_html


def _write_bytes(path, data: bytes) -> None:
    path.write_bytes(data)


@log_node("agente_3")
async def node_relatorio(state: dict) -> dict:
    """
    Agente 3: consolida os resultados do pipeline e gera o relatório gerencial.

    Execução em duas etapas assíncronas:
        1. _build_data roda em thread pool — pandas + chamada LLM são bloqueantes
           e não devem ocupar o event loop principal.
        2. Escrita de relatorio.json e relatorio.html em paralelo via asyncio.gather,
           aproveitando que as duas operações são independentes entre si.
    """
    output_dir = state["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    report = await asyncio.to_thread(_build_data, state["results"])

    json_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    html_bytes = _render_html(report).encode("utf-8")

    await asyncio.gather(
        asyncio.to_thread(_write_bytes, output_dir / "relatorio.json", json_bytes),
        asyncio.to_thread(_write_bytes, output_dir / "relatorio.html", html_bytes),
    )

    return {**state, "report": report}
