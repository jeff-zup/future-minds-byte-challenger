"""
Guardrail de SAÍDA estrutural — protege quem CONSOME os artefatos do FinGuard.

Os guardrails existentes (pii, profanity) limpam o *conteúdo* do texto. Este módulo
trata o *formato*: um texto já livre de PII e de palavrões ainda pode ser hostil quando
interpretado por Excel ou por um navegador.

Dois vetores, ambos originados no `texto_reclamacao`/`canal` que vêm do CSV externo:

- CSV Formula Injection (CWE-1236): célula iniciada por `=`, `+`, `-`, `@`, TAB ou CR é
  executada como fórmula ao abrir `resultados.csv` no Excel/LibreOffice/Sheets.
- Cross-Site Scripting (CWE-79): `relatorio.html` é renderizado por Jinja2 com
  autoescape desligado (padrão de `jinja2.Template`), então HTML vindo do dado é
  interpretado pelo navegador de quem abre o relatório.
"""

from __future__ import annotations

from typing import Any

# Caracteres que fazem uma célula ser avaliada como fórmula pelas planilhas.
# Recomendação OWASP: prefixar com apóstrofo, que a planilha trata como "texto literal".
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formula(value: Any) -> Any:
    """
    Neutraliza fórmula em uma célula prefixando com apóstrofo.

    Só toca em strings que COMEÇAM com um gatilho — valores numéricos, datas e texto
    comum passam intactos. O apóstrofo é invisível na planilha: a célula exibe o
    texto original, apenas não o executa.
    """
    if not isinstance(value, str) or not value:
        return value
    if value[:1] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


def sanitize_row_for_csv(row: dict[str, Any]) -> dict[str, Any]:
    """Aplica neutralize_formula em todos os campos de uma linha antes da exportação CSV."""
    return {key: neutralize_formula(value) for key, value in row.items()}
