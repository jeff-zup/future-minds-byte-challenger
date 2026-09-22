from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Força UTF-8 no stdout do Windows para suportar caracteres como ✓ sem UnicodeEncodeError
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from config import settings
from graph.build_graph import build_item_graph
from graph.logging_utils import log_event, reset_log
from guardrails.output_safety import sanitize_row_for_csv
from guardrails.pii import mask_pii
from rag.retriever import PolicyRetriever
from report.build_report import generate_report


def normalize_optional(value):
    """Converte pandas NA e strings em branco para None de forma uniforme."""
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def make_initial_state(row: pd.Series) -> dict:
    """
    Cria o estado inicial de uma reclamação a partir de uma linha do CSV.

    Campos de análise (categoria, produto, sentimento, etc.) começam como None
    e são preenchidos pelos agentes conforme o pipeline avança.
    """
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
        "guardrail_bloqueado": False,
        "injection_flags": [],
        "revisao_humana": False,
        "current_node": "inicio",
        "node_history": [],
        "status_processamento": "ok",
        "erro_processamento": None,
    }


def sanitize_for_persistence(item: dict) -> dict:
    """
    Aplica mask_pii nos campos de texto livre antes de qualquer gravação em disco.
    Garante que CPF, número de cartão e dados bancários nunca cheguem aos arquivos de saída.
    """
    clean = dict(item)
    clean["texto_reclamacao"] = mask_pii(clean.get("texto_reclamacao"))
    clean["resumo"] = mask_pii(clean.get("resumo"))
    clean["risco_justificativa"] = mask_pii(clean.get("risco_justificativa"))
    return clean


def failure_record(state: dict, exc: BaseException) -> dict:
    """
    Converte uma exceção não recuperada em registro de falha auditável.

    Preserva os dados de entrada para que o item possa ser reprocessado depois,
    em vez de simplesmente desaparecer do resultado.
    """
    return {
        **state,
        "status_processamento": "falhou",
        "erro_processamento": f"{type(exc).__name__}: {exc}",
    }


async def process_batch(graph, states: list[dict]) -> list[dict]:
    """
    Executa o lote tolerando falha individual.

    Sem `return_exceptions=True`, uma única exceção em qualquer nó — o LLM devolvendo
    categoria fora do enum e o Pydantic rejeitando em ClassificationOutput.model_validate,
    ou LLMError após esgotar o retry de throttling — aborta o abatch inteiro e descarta
    TODAS as reclamações já processadas, sem gravar nada em disco. Com 500 itens e LLM
    real, isso é praticamente garantido.

    Duas fases:
        1. Lote completo coletando exceções em vez de propagá-las.
        2. Retry individual e serializado só dos que falharam — falha transitória
           (throttling, timeout de rede) costuma passar na segunda tentativa.

    Quem falha nas duas vezes vira registro com status_processamento="falhou".
    """
    results = await graph.abatch(
        states,
        config={"max_concurrency": settings.max_concurrency},
        return_exceptions=True,
    )

    failed = [i for i, r in enumerate(results) if isinstance(r, BaseException)]
    if failed:
        print(f"⚠ {len(failed)} item(ns) falharam na 1ª tentativa — refazendo individualmente...")
        retried = await graph.abatch(
            [states[i] for i in failed],
            config={"max_concurrency": 1},
            return_exceptions=True,
        )
        for index, result in zip(failed, retried):
            results[index] = result

    final: list[dict] = []
    for state, result in zip(states, results):
        if isinstance(result, BaseException):
            log_event({
                "reclamacao_id": state.get("id"),
                "node": state.get("current_node", "desconhecido"),
                "evento": "item_descartado",
                "error": f"{type(result).__name__}: {result}",
            })
            final.append(failure_record(state, result))
        else:
            final.append(result)
    return final


async def run() -> None:
    started = time.perf_counter()
    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    reset_log()  # Limpa o JSONL de execuções anteriores

    provider_label = {"bedrock": "AWS Bedrock", "litellm": "AI Gateway (LiteLLM)"}.get(
        settings.llm_provider, settings.llm_provider
    )
    print("FinGuard iniciado")
    print(f"Modo LLM: {'MOCK' if settings.mock_llm else provider_label}")

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

    # O retriever é construído uma única vez e compartilhado entre todos os agentes via closure
    print("Construindo índice local da Política Interna...")
    retriever = PolicyRetriever(settings.policy_path)
    graph = build_item_graph(retriever)

    # abatch processa todas as reclamações concorrentemente, tolerando falha individual
    states = [make_initial_state(row) for _, row in df.iterrows()]
    results = await process_batch(graph, states)

    # Mascara PII antes de gravar qualquer dado em disco
    clean_results = [sanitize_for_persistence(x) for x in results]
    (output_dir / "resultados.json").write_text(
        json.dumps(clean_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # risco_flags, injection_flags e node_history são listas — serializa para string
    export_rows = []
    for item in clean_results:
        row_out = dict(item)
        for col in ("risco_flags", "injection_flags", "node_history"):
            row_out[col] = json.dumps(row_out.get(col, []), ensure_ascii=False)
        # Neutraliza fórmula antes de exportar: resultados.csv é aberto no Excel
        export_rows.append(sanitize_row_for_csv(row_out))
    pd.DataFrame(export_rows).to_csv(output_dir / "resultados.csv", index=False)

    report = generate_report(clean_results, output_dir)
    elapsed = time.perf_counter() - started

    critical = len(report["reclamacoes_criticas"])
    ok = sum(1 for x in clean_results if x.get("status_processamento") == "ok")
    failures = len(clean_results) - ok
    flagged = sum(1 for x in clean_results if x.get("injection_flags"))

    print(f"✓ {ok}/{len(states)} processadas com sucesso")
    if failures:
        print(f"⚠ {failures} com falha (ver status_processamento em resultados.json)")
    if flagged:
        print(f"⚠ {flagged} com tentativa de injeção detectada — marcadas para revisão humana")
    print(f"✓ {critical} reclamações críticas")
    print("✓ output/resultados.json")
    print("✓ output/resultados.csv")
    print("✓ output/relatorio.json")
    print("✓ output/relatorio.html")
    print("✓ output/logs/execucao.jsonl")
    print(f"Processamento concluído em {elapsed:.2f}s")


if __name__ == "__main__":
    asyncio.run(run())
