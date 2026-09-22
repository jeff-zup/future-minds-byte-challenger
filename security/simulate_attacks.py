"""
Simulação de ataques contra o pipeline do FinGuard.

Roda o pipeline REAL (mesmo grafo, mesmos nós, mesmo relatório) sobre um lote de
reclamações maliciosas e verifica, para cada vetor, se o artefato final ficou seguro.
Para XSS e CSV Formula Injection também renderiza a versão SEM o guardrail, para
mostrar lado a lado o que acontecia antes.

Uso:
    python -m security.simulate_attacks

Saída:
    output/security/simulacao.json   — resultado por payload, auditável
    output/security/relatorio_ataque.html — relatório gerado A PARTIR dos payloads
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
from jinja2 import Template

from config import settings
from graph.build_graph import build_item_graph
from guardrails.injection import detect_injection
from guardrails.output_safety import sanitize_row_for_csv
from main import make_initial_state, process_batch, sanitize_for_persistence
from rag.retriever import PolicyRetriever
from report.build_report import generate_report
from security.payloads import PAYLOADS, Payload

OUT_DIR = Path("output/security")

# Aberturas de tag que não podem aparecer CRUAS no HTML final.
# O critério é a abertura de tag, não a substring do atributo: uma vez que `<`
# vira `&lt;`, o payload é texto inerte mesmo que "onerror=" continue legível.
_XSS_MARKERS = ("<script", "<img", "<iframe", "<svg", "<object", "javascript:")
# Gatilhos de fórmula que não podem iniciar uma célula do CSV.
_FORMULA_TRIGGERS = ("=", "+", "-", "@")


def _row(payload: Payload) -> pd.Series:
    return pd.Series({
        "id": payload.id,
        "data_reclamacao": "2026-01-15",
        "canal": payload.canal,
        "texto_reclamacao": payload.texto,
        "produto": payload.produto,
        "status": "Aberta",
    })


def _check_prompt_injection(payload: Payload, item: dict) -> tuple[bool, str]:
    """O guardrail de entrada precisa ter sinalizado e marcado para revisão humana."""
    detected = set(item.get("injection_flags") or [])
    expected = set(payload.regras_esperadas)
    missing = expected - detected
    if missing:
        return False, f"regras esperadas não disparadas: {sorted(missing)}"
    if not item.get("revisao_humana"):
        return False, "item não foi marcado para revisão humana"
    return True, f"detectado {sorted(detected)}; marcado para revisão humana"


def _check_no_downgrade(item: dict) -> tuple[bool, str]:
    """
    A regra determinística tem de sobreviver à injeção.

    Canal Banco Central/Procon força urgência Crítica e risco Crítico fora do LLM
    (ADR #3). Este check prova que a injeção não consegue rebaixar a classificação
    mesmo que o modelo obedeça ao atacante.
    """
    if item.get("urgencia") != "Crítica" or item.get("nivel_risco") != "Crítico":
        return False, f"rebaixado para urgencia={item.get('urgencia')} risco={item.get('nivel_risco')}"
    if not item.get("escalado"):
        return False, "escalonamento não ocorreu"
    return True, "urgência/risco Crítico preservados e escalonamento executado"


def _check_xss(html: str) -> tuple[bool, str]:
    lowered = html.lower()
    found = [m for m in _XSS_MARKERS if m in lowered]
    if found:
        return False, f"marcador executável presente no HTML: {found}"
    return True, "HTML escapado (&lt;script&gt;), sem marcador executável"


def _check_csv(csv_text: str) -> tuple[bool, str]:
    reader = pd.read_csv(pd.io.common.StringIO(csv_text), dtype=str).fillna("")
    offenders = []
    for _, row in reader.iterrows():
        for col, value in row.items():
            if isinstance(value, str) and value[:1] in _FORMULA_TRIGGERS:
                offenders.append(f"{col}={value[:30]!r}")
    if offenders:
        return False, f"célula iniciando com gatilho de fórmula: {offenders[:3]}"
    return True, "nenhuma célula inicia com =, +, - ou @"


def _check_pii(item: dict) -> tuple[bool, str]:
    text = json.dumps(item, ensure_ascii=False)
    leaks = []
    for raw, nome in [
        ("123.456.789-00", "CPF"),
        ("4111 1111 1111 1111", "cartão"),
        ("(11) 98765-4321", "telefone"),
        ("joao.silva@email.com", "e-mail"),
    ]:
        if raw in text:
            leaks.append(nome)
    if leaks:
        return False, f"PII persistida em claro: {leaks}"
    return True, "CPF, cartão, telefone e e-mail mascarados no registro persistido"


def _vulnerable_html(report: dict) -> str:
    """Reproduz o render ANTERIOR (jinja2.Template com autoescape desligado)."""
    return Template(
        "{% for x in report.reclamacoes_criticas %}<td>{{x.canal}}</td>"
        "<td>{{x.risco_justificativa}}</td>{% endfor %}"
    ).render(report=report)


def _vulnerable_csv(rows: list[dict]) -> str:
    """Reproduz a exportação ANTERIOR, sem neutralização de fórmula."""
    return pd.DataFrame(rows).to_csv(index=False)


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print("SIMULAÇÃO DE ATAQUES — FinGuard")
    print(f"Modo LLM: {'MOCK' if settings.mock_llm else 'AWS Bedrock'} | {len(PAYLOADS)} payloads")
    print("=" * 78)

    graph = build_item_graph(PolicyRetriever(settings.policy_path))
    states = [make_initial_state(_row(p)) for p in PAYLOADS]
    results = await process_batch(graph, states)
    clean = [sanitize_for_persistence(x) for x in results]
    by_id = {item["id"]: item for item in clean}

    # Artefatos reais, produzidos pelo mesmo código de produção
    report = generate_report(clean, OUT_DIR)
    html = (OUT_DIR / "relatorio.html").read_text(encoding="utf-8")
    # replace (não rename): no Windows, rename falha se o destino já existir
    (OUT_DIR / "relatorio.html").replace(OUT_DIR / "relatorio_ataque.html")

    export_rows = []
    for item in clean:
        row_out = dict(item)
        for col in ("risco_flags", "injection_flags", "node_history"):
            row_out[col] = json.dumps(row_out.get(col, []), ensure_ascii=False)
        export_rows.append(sanitize_row_for_csv(row_out))
    csv_text = pd.DataFrame(export_rows).to_csv(index=False)

    # Checagens globais (um artefato, vale para todos os payloads daquele vetor)
    xss_ok, xss_msg = _check_xss(html)
    csv_ok, csv_msg = _check_csv(csv_text)

    findings = []
    for payload in PAYLOADS:
        item = by_id[payload.id]
        checks: list[tuple[str, bool, str]] = []

        if payload.regras_esperadas:
            checks.append(("guardrail de entrada", *_check_prompt_injection(payload, item)))
        if payload.canal.lower().startswith(("banco central", "procon")):
            checks.append(("regra determinística", *_check_no_downgrade(item)))
        if payload.vetor == "XSS Armazenado":
            checks.append(("render HTML", xss_ok, xss_msg))
        if payload.vetor == "CSV Formula Injection":
            checks.append(("exportação CSV", csv_ok, csv_msg))
        if payload.vetor == "Vazamento de PII":
            checks.append(("máscara de PII", *_check_pii(item)))
        checks.append((
            "item não perdido",
            item.get("status_processamento") == "ok",
            f"status={item.get('status_processamento')}",
        ))

        mitigated = all(ok for _, ok, _ in checks)
        findings.append({
            "id": payload.id,
            "vetor": payload.vetor,
            "cwe": payload.cwe,
            "descricao": payload.descricao,
            "mitigado": mitigated,
            "checks": [{"nome": n, "ok": ok, "detalhe": d} for n, ok, d in checks],
        })

        status = "MITIGADO" if mitigated else "VULNERÁVEL"
        print(f"\n[{status}] {payload.id}  ({payload.vetor} / {payload.cwe})")
        print(f"   alvo: {payload.descricao}")
        for nome, ok, detalhe in checks:
            print(f"   {'✓' if ok else '✗'} {nome}: {detalhe}")

    # Comparativo antes/depois, para provar que a correção importa
    print("\n" + "=" * 78)
    print("LINHA DE BASE — mesmos dados, guardrails de saída DESLIGADOS")
    print("=" * 78)
    vuln_html = _vulnerable_html(report)
    vuln_csv = _vulnerable_csv([
        {k: v for k, v in r.items() if k in ("id", "texto_reclamacao")} for r in clean
    ])
    base_xss_ok, base_xss_msg = _check_xss(vuln_html)
    base_csv_ok, base_csv_msg = _check_csv(vuln_csv)
    print(f"   {'✓' if base_xss_ok else '✗'} render sem autoescape: {base_xss_msg}")
    print(f"   {'✓' if base_csv_ok else '✗'} CSV sem neutralização: {base_csv_msg}")

    total = len(findings)
    mitigated_count = sum(1 for f in findings if f["mitigado"])
    (OUT_DIR / "simulacao.json").write_text(
        json.dumps(
            {
                "total": total,
                "mitigados": mitigated_count,
                "vulneraveis": total - mitigated_count,
                "linha_de_base": {
                    "html_sem_autoescape_seguro": base_xss_ok,
                    "csv_sem_neutralizacao_seguro": base_csv_ok,
                },
                "resultados": findings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 78)
    print(f"RESULTADO: {mitigated_count}/{total} vetores mitigados")
    print(f"Detalhe: {OUT_DIR / 'simulacao.json'}")
    print(f"Relatório gerado sob ataque: {OUT_DIR / 'relatorio_ataque.html'}")
    print("=" * 78)
    return 0 if mitigated_count == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
