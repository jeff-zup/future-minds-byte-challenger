from __future__ import annotations

import json
import re
from typing import Any


class LLMError(RuntimeError):
    """Exceção unificada para falhas de comunicação com o provedor de LLM (Bedrock ou LiteLLM Gateway)."""
    pass


class GuardrailBlockedError(LLMError):
    """
    Levantada quando o AI Gateway/modelo bloqueia a requisição por um guardrail
    de segurança (ex.: detecção de prompt injection), em vez de retornar o
    conteúdo esperado. Permite que os nós do grafo façam fallback gracioso
    (ex.: classificação heurística) em vez de derrubar todo o pipeline.
    """
    pass


def extract_json(text: str) -> dict[str, Any]:
    """
    Extrai JSON da resposta do LLM com tolerância a formatação extra.

    Estratégia em dois passos:
        1. Remove blocos de código markdown (```json ... ```) e tenta json.loads direto.
        2. Se falhar, localiza o primeiro { e o último } e tenta parsear só esse trecho.
           Cobre casos onde o LLM adiciona texto antes/depois do JSON.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValueError(
            "Resposta do LLM ficou vazia após remover marcações markdown "
            f"(texto original recebido: {text!r}). Provável corte por max_tokens "
            "ou resposta não-JSON do modelo."
        )
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc2:
                raise ValueError(
                    f"Falha ao parsear JSON extraído do LLM: {exc2}. "
                    f"Texto original recebido (repr, até 2000 chars): {text[:2000]!r}"
                ) from exc2
        raise ValueError(
            f"Resposta do LLM não contém JSON válido nem chaves '{{' '}}': {exc}. "
            f"Texto original recebido (repr, até 2000 chars): {text[:2000]!r}"
        ) from exc
