import pytest
from jinja2 import Template

from guardrails.output_safety import neutralize_formula, sanitize_row_for_csv
from report.build_report import _bars, _render_html


@pytest.mark.parametrize(
    "payload",
    [
        '=HYPERLINK("https://evil.tld","clique")',
        "+1+1",
        "-1+1",
        "@SUM(1+1)*cmd|' /C calc'!A0",
        "\tcomando",
        "\rcomando",
    ],
)
def test_neutraliza_gatilho_de_formula(payload):
    resultado = neutralize_formula(payload)
    assert resultado.startswith("'")
    # O texto original é preservado — a planilha exibe, mas não executa
    assert resultado[1:] == payload


@pytest.mark.parametrize(
    "valor",
    ["Cobrança indevida", "R$ 2.000", "2026-01-15", "Cartão de Crédito", ""],
)
def test_nao_altera_valor_inofensivo(valor):
    assert neutralize_formula(valor) == valor


def test_nao_altera_tipos_nao_string():
    assert neutralize_formula(42) == 42
    assert neutralize_formula(None) is None
    assert neutralize_formula(True) is True


def test_sanitize_row_cobre_todos_os_campos():
    row = {"id": "X", "texto": "=1+1", "canal": "SAC", "resumo": "@cmd"}
    limpa = sanitize_row_for_csv(row)
    assert limpa["texto"] == "'=1+1"
    assert limpa["resumo"] == "'@cmd"
    assert limpa["canal"] == "SAC"


def _report_com(canal: str, justificativa: str = "ok") -> dict:
    return {
        "dashboard": {
            "total_recebido": 1, "total_processado": 1, "total_falhas": 0,
            "por_categoria": {}, "por_produto": {}, "por_urgencia": {}, "por_nivel_risco": {},
        },
        "reclamacoes_criticas": [{
            "id": "REC-1", "canal": canal, "categoria": "Fraude/Segurança",
            "produto": "Cartão de Crédito", "urgencia": "Crítica", "nivel_risco": "Crítico",
            "risco_justificativa": justificativa, "escalado": True,
        }],
        "incidentes_seguranca": [],
        "falhas_processamento": [],
        "recomendacoes": [],
    }


def test_html_escapa_script_no_canal():
    """O canal vem cru do CSV externo e é renderizado no relatório."""
    html = _render_html(_report_com('SAC<script>alert(1)</script>'))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escapa_justificativa_do_llm():
    """
    Em modo Bedrock, risco_justificativa é texto livre do modelo.

    O que torna o payload inerte é a abertura de tag ficar escapada: `&lt;img`
    nunca é interpretado como elemento. A substring "onerror=" pode continuar
    visível como texto puro sem representar risco.
    """
    html = _render_html(_report_com("SAC", '<img src=x onerror="alert(1)">'))
    assert "<img" not in html
    assert "&lt;img" in html
    assert "&#34;" in html  # as aspas do atributo também foram escapadas


def test_bars_escapa_rotulo():
    """_bars é inserido com |safe, então precisa escapar por conta própria."""
    saida = _bars({"<script>alert(1)</script>": 3})
    assert "<script>" not in saida
    assert "&lt;script&gt;" in saida


def test_template_sem_autoescape_era_vulneravel():
    """Documenta o comportamento padrão do jinja2.Template que causava o XSS."""
    assert Template("{{ x }}").render(x="<script>") == "<script>"
    assert Template("{{ x }}", autoescape=True).render(x="<script>") == "&lt;script&gt;"
