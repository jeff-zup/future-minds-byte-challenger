from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Template
from markupsafe import escape

from config import settings
from guardrails.pii import mask_pii
from llm.bedrock import bedrock_llm


# Instrução do Agente 3: recebe apenas dados agregados (nunca textos brutos de reclamações)
# para evitar que o LLM processe PII e gerar recomendações gerenciais objetivas.
REPORT_SYSTEM = """
Você é o Agente 3 do FinGuard, responsável por recomendações gerenciais.
Receba apenas estatísticas agregadas e casos críticos já analisados.
Gere de 3 a 5 recomendações objetivas, acionáveis e estritamente baseadas nos dados fornecidos.
Retorne SOMENTE JSON válido no formato:
{"recomendacoes": ["...", "..."]}
""".strip()


def _counts(df: pd.DataFrame, col: str) -> dict[str, int]:
    """Contagem de valores de uma coluna como dict {valor: frequência}."""
    return {str(k): int(v) for k, v in df[col].fillna("Não informado").value_counts().to_dict().items()}


def _mock_recommendations(dashboard: dict, critical_count: int) -> list[str]:
    """
    Recomendações determinísticas para modo mock.
    Prioriza casos críticos e aponta categorias/produtos com maior volume.
    """
    recommendations = []
    if critical_count:
        recommendations.append(f"Priorizar revisão imediata das {critical_count} reclamações críticas e validar o cumprimento do fluxo de escalonamento.")
    if dashboard["por_categoria"]:
        top = max(dashboard["por_categoria"], key=dashboard["por_categoria"].get)
        recommendations.append(f"Investigar causa-raiz da categoria mais recorrente: {top}.")
    if dashboard["por_produto"]:
        top_product = max(dashboard["por_produto"], key=dashboard["por_produto"].get)
        recommendations.append(f"Revisar processos do produto com maior volume de reclamações: {top_product}.")
    recommendations.append("Acompanhar diariamente SLA por urgência e reincidência de reclamações.")
    return recommendations[:5]


def generate_report(results: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """
    Agente 3: gera relatório gerencial consolidado a partir dos resultados do pipeline.

    Fluxo:
        1. Pandas agrega contagens por categoria/produto/urgência/risco
        2. Filtra casos críticos (urgência ou risco == Crítico)
        3. Aplica mask_pii na risco_justificativa dos críticos antes de enviar ao LLM
        4. LLM (ou mock) produz recomendações gerenciais
        5. Escreve relatorio.json e relatorio.html

    O LLM só recebe o dashboard agregado e até 50 casos críticos — nunca textos brutos.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    full_df = pd.DataFrame(results)

    # Itens que falharam não entram nas estatísticas — categoria/risco vazios
    # contaminariam o dashboard com uma faixa "Não informado" enganosa. Eles são
    # reportados à parte, no painel de falhas, para não sumirem silenciosamente.
    if "status_processamento" in full_df.columns:
        failed_df = full_df[full_df["status_processamento"] == "falhou"]
        df = full_df[full_df["status_processamento"] != "falhou"].copy()
    else:
        failed_df = full_df.iloc[0:0]
        df = full_df.copy()

    failures = [
        {"id": r.get("id"), "canal": r.get("canal"), "erro": r.get("erro_processamento"),
         "ultimo_no": r.get("current_node")}
        for r in failed_df.to_dict(orient="records")
    ]

    dashboard = {
        "total_recebido": int(len(full_df)),
        "total_processado": int(len(df)),
        "total_falhas": int(len(failed_df)),
        "por_categoria": _counts(df, "categoria"),
        "por_produto": _counts(df, "produto"),
        "por_urgencia": _counts(df, "urgencia"),
        "por_nivel_risco": _counts(df, "nivel_risco"),
    }

    # Inclui na lista crítica qualquer item com urgência OU risco Crítico
    critical_df = df[(df["urgencia"] == "Crítica") | (df["nivel_risco"] == "Crítico")].copy()
    critical_cols = ["id", "canal", "categoria", "produto", "urgencia", "nivel_risco", "risco_justificativa", "escalado"]
    critical_items = critical_df[critical_cols].to_dict(orient="records")
    # Mascara PII na justificativa antes de qualquer envio ao LLM ou persistência
    for item in critical_items:
        item["risco_justificativa"] = mask_pii(item.get("risco_justificativa"))

    if settings.mock_llm:
        recommendations = _mock_recommendations(dashboard, len(critical_items))
    else:
        payload = {
            "dashboard": dashboard,
            "criticos": critical_items[:50],  # Limita para não exceder context window do LLM
        }
        raw = bedrock_llm.invoke_json(
            settings.report_model,
            REPORT_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        recommendations = [str(x) for x in raw.get("recomendacoes", [])][:5]

    # Painel de segurança: itens em que o guardrail de entrada detectou tentativa de
    # injeção. Ficam visíveis para a triagem manual em vez de só existirem no JSONL.
    incidents = []
    if "injection_flags" in df.columns:
        for record in df.to_dict(orient="records"):
            if record.get("injection_flags"):
                incidents.append({
                    "id": record.get("id"),
                    "canal": record.get("canal"),
                    "regras": list(record["injection_flags"]),
                    "revisao_humana": bool(record.get("revisao_humana")),
                })

    report = {
        "dashboard": dashboard,
        "reclamacoes_criticas": critical_items,
        "incidentes_seguranca": incidents,
        "falhas_processamento": failures,
        "recomendacoes": recommendations,
    }

    (output_dir / "relatorio.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "relatorio.html").write_text(_render_html(report), encoding="utf-8")
    return report


def _bars(data: dict[str, int]) -> str:
    """
    Gera linhas de barras HTML proporcionais ao valor máximo do dicionário.

    O retorno é inserido no template com `|safe`, então o escape precisa acontecer AQUI:
    `label` vem de colunas do CSV (ex.: produto) e não é confiável. `width` e `value`
    são derivados de inteiros, por isso não podem carregar payload.
    """
    if not data:
        return "<p>Sem dados.</p>"
    max_value = max(data.values()) or 1
    parts = []
    for label, value in data.items():
        width = max(3, round((value / max_value) * 100))
        parts.append(f'<div class="bar-row"><div class="bar-label">{escape(label)}</div><div class="bar"><span style="width:{width}%"></span></div><div class="bar-value">{int(value)}</div></div>')
    return "\n".join(parts)


def _render_html(report: dict[str, Any]) -> str:
    """
    Renderiza o relatório como página HTML auto-contida via Jinja2.

    autoescape=True é OBRIGATÓRIO aqui: `jinja2.Template` vem com autoescape DESLIGADO
    por padrão, e campos como `canal` e `id` chegam crus do CSV externo até
    `{{x.canal}}`/`{{x.id}}`. Sem isso, uma reclamação com `<script>` no canal vira
    XSS armazenado no relatório aberto pelo time de Compliance.
    """
    template = Template(autoescape=True, source="""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>FinGuard — Relatório Gerencial</title>
<style>
body{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;color:#172033}.wrap{max-width:1180px;margin:0 auto;padding:32px}
h1{margin-bottom:4px}.sub{color:#667085;margin-top:0}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}
.card{background:white;border-radius:12px;padding:18px;box-shadow:0 1px 4px #0001}.card strong{display:block;font-size:28px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{background:white;border-radius:12px;padding:20px;margin-bottom:18px;box-shadow:0 1px 4px #0001}
.bar-row{display:grid;grid-template-columns:180px 1fr 48px;gap:10px;align-items:center;margin:8px 0}.bar{height:12px;background:#edf0f5;border-radius:8px;overflow:hidden}.bar span{display:block;height:100%;background:#4f63d8}.bar-value{text-align:right}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px;border-bottom:1px solid #e6e8ef;text-align:left;vertical-align:top}th{background:#f8f9fc}.badge{font-weight:bold}.critical{color:#b42318}
li{margin-bottom:8px}@media(max-width:800px){.cards,.grid{grid-template-columns:1fr}.bar-row{grid-template-columns:120px 1fr 36px}}
</style>
</head>
<body><div class="wrap">
<h1>FinGuard — Relatório Gerencial</h1><p class="sub">Consolidação automática das reclamações analisadas pelo fluxo multiagente.</p>
<div class="cards">
<div class="card"><span>Total processado</span><strong>{{ d.total_processado }}</strong></div>
<div class="card"><span>Críticas</span><strong>{{ critical_count }}</strong></div>
<div class="card"><span>Incidentes de segurança</span><strong class="{{ 'critical' if report.incidentes_seguranca else '' }}">{{ report.incidentes_seguranca|length }}</strong></div>
<div class="card"><span>Falhas de processamento</span><strong class="{{ 'critical' if report.falhas_processamento else '' }}">{{ report.falhas_processamento|length }}</strong></div>
</div>
<div class="grid">
<div class="panel"><h2>Por categoria</h2>{{ category_bars|safe }}</div>
<div class="panel"><h2>Por urgência</h2>{{ urgency_bars|safe }}</div>
<div class="panel"><h2>Por produto</h2>{{ product_bars|safe }}</div>
<div class="panel"><h2>Por nível de risco</h2>{{ risk_bars|safe }}</div>
</div>
<div class="panel"><h2>Recomendações</h2><ul>{% for r in report.recomendacoes %}<li>{{ r }}</li>{% endfor %}</ul></div>
<div class="panel"><h2>Reclamações críticas</h2>
{% if report.reclamacoes_criticas %}
<table><thead><tr><th>ID</th><th>Canal</th><th>Categoria</th><th>Produto</th><th>Urgência</th><th>Risco</th><th>Parecer</th><th>Escalado</th></tr></thead><tbody>
{% for x in report.reclamacoes_criticas %}<tr><td>{{x.id}}</td><td>{{x.canal}}</td><td>{{x.categoria}}</td><td>{{x.produto}}</td><td class="critical">{{x.urgencia}}</td><td class="critical">{{x.nivel_risco}}</td><td>{{x.risco_justificativa}}</td><td>{{'Sim' if x.escalado else 'Não'}}</td></tr>{% endfor %}
</tbody></table>{% else %}<p>Nenhuma reclamação crítica.</p>{% endif %}
</div>
<div class="panel"><h2>Incidentes de segurança (guardrail de entrada)</h2>
{% if report.incidentes_seguranca %}
<p>Reclamações em que o texto do cliente disparou regras de injeção. O item seguiu no pipeline e foi marcado para triagem manual — nenhuma reclamação é descartada por suspeita.</p>
<table><thead><tr><th>ID</th><th>Canal</th><th>Regras disparadas</th><th>Revisão humana</th></tr></thead><tbody>
{% for i in report.incidentes_seguranca %}<tr><td>{{i.id}}</td><td>{{i.canal}}</td><td class="critical">{{ i.regras|join(', ') }}</td><td>{{'Sim' if i.revisao_humana else 'Não'}}</td></tr>{% endfor %}
</tbody></table>{% else %}<p>Nenhuma tentativa de injeção detectada.</p>{% endif %}
</div>
<div class="panel"><h2>Falhas de processamento</h2>
{% if report.falhas_processamento %}
<p>Itens que falharam mesmo após retry individual. Os dados de entrada foram preservados em <code>resultados.json</code> para reprocessamento.</p>
<table><thead><tr><th>ID</th><th>Canal</th><th>Último nó</th><th>Erro</th></tr></thead><tbody>
{% for f in report.falhas_processamento %}<tr><td>{{f.id}}</td><td>{{f.canal}}</td><td>{{f.ultimo_no}}</td><td class="critical">{{f.erro}}</td></tr>{% endfor %}
</tbody></table>{% else %}<p>Todos os {{ d.total_recebido }} itens foram processados sem falha.</p>{% endif %}
</div>
</div></body></html>
""")
    d = report["dashboard"]
    return template.render(
        report=report,
        d=d,
        critical_count=len(report["reclamacoes_criticas"]),
        category_bars=_bars(d["por_categoria"]),
        urgency_bars=_bars(d["por_urgencia"]),
        product_bars=_bars(d["por_produto"]),
        risk_bars=_bars(d["por_nivel_risco"]),
    )
