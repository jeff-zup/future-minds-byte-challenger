from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pandas as pd

from config import settings
from graph.build_graph import build_item_graph
from graph.logging_utils import reset_log
from guardrails.pii import mask_pii
from rag.retriever import PolicyRetriever
from report.build_report import generate_report


def normalize_optional(value):
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def make_initial_state(row: pd.Series) -> dict:
    return {
        "id": str(row["id"]),
        "data_reclamacao": str(row["data_reclamacao"]),
        "canal": str(row["canal"]).strip(),
        "texto_reclamacao": str(row["texto_reclamacao"]),
        "produto_original": normalize_optional(row.get("produto")),
        "status": str(row.get("status", "")),
        "categoria": None,
        "produto": None,
        "sentimento": None,
        "urgencia": None,
        "resumo": None,
        "nivel_risco": None,
        "risco_justificativa": None,
        "risco_flags": [],
        "escalado": False,
        "current_node": "inicio",
        "node_history": [],
    }


def sanitize_for_persistence(item: dict) -> dict:
    clean = dict(item)
    clean["texto_reclamacao"] = mask_pii(clean.get("texto_reclamacao"))
    clean["resumo"] = mask_pii(clean.get("resumo"))
    clean["risco_justificativa"] = mask_pii(clean.get("risco_justificativa"))
    return clean


async def run() -> None:
    started = time.perf_counter()
    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    reset_log()

    print("FinGuard iniciado")
    print(f"Modo LLM: {'MOCK' if settings.mock_llm else 'AWS Bedrock'}")

    if not settings.dataset_path.exists():
        raise FileNotFoundError(f"Dataset não encontrado: {settings.dataset_path}")
    if not settings.policy_path.exists():
        raise FileNotFoundError(f"Política interna não encontrada: {settings.policy_path}")

    df = pd.read_csv(settings.dataset_path)
    required = {"id", "data_reclamacao", "canal", "texto_reclamacao", "produto", "status"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes no CSV: {sorted(missing)}")

    print(f"✓ {len(df)} reclamações carregadas")
    print("Construindo índice local da Política Interna...")
    retriever = PolicyRetriever(settings.policy_path)
    graph = build_item_graph(retriever)

    states = [make_initial_state(row) for _, row in df.iterrows()]
    results = await graph.abatch(
        states,
        config={"max_concurrency": settings.max_concurrency},
    )

    clean_results = [sanitize_for_persistence(x) for x in results]
    (output_dir / "resultados.json").write_text(
        json.dumps(clean_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    export_df = pd.DataFrame(clean_results)
    for col in ("risco_flags", "node_history"):
        export_df[col] = export_df[col].apply(lambda x: json.dumps(x, ensure_ascii=False))
    export_df.to_csv(output_dir / "resultados.csv", index=False)

    report = generate_report(clean_results, output_dir)
    elapsed = time.perf_counter() - started

    critical = len(report["reclamacoes_criticas"])
    print(f"✓ {len(clean_results)}/{len(states)} processadas")
    print(f"✓ {critical} reclamações críticas")
    print("✓ output/resultados.json")
    print("✓ output/resultados.csv")
    print("✓ output/relatorio.json")
    print("✓ output/relatorio.html")
    print("✓ output/logs/execucao.jsonl")
    print(f"Processamento concluído em {elapsed:.2f}s")


if __name__ == "__main__":
    asyncio.run(run())
