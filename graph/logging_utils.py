from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

LOG_FILE = Path("output/logs/execucao.jsonl")
# Lock garante escrita segura sob concorrência do abatch (múltiplas reclamações processadas em paralelo)
_LOCK = threading.Lock()


def reset_log() -> None:
    """Apaga o log no início de cada execução para não acumular dados de runs anteriores."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event: dict) -> None:
    """Appenda um evento JSON ao JSONL de forma thread-safe."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_node(node_name: str):
    """
    Decorator que envolve qualquer nó do LangGraph com logging automático.

    Registra três eventos no JSONL por chamada:
        - "start": quando o nó começa
        - "end": quando termina, com duration_ms e resumo do estado
        - "error": se lançar exceção, com duration_ms e mensagem

    Também acumula node_history no estado para rastreabilidade individual por reclamação.
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(state, *args, **kwargs):
                started_at = _now()
                started = time.perf_counter()
                state["current_node"] = node_name
                log_event({
                    "reclamacao_id": state.get("id"),
                    "node": node_name,
                    "evento": "start",
                    "timestamp": started_at,
                })
                try:
                    result = await func(state, *args, **kwargs)
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - started) * 1000, 2)
                    log_event({
                        "reclamacao_id": state.get("id"),
                        "node": node_name,
                        "evento": "error",
                        "timestamp": _now(),
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    })
                    raise
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                history = list(result.get("node_history", state.get("node_history", [])))
                history.append({
                    "node": node_name,
                    "started_at": started_at,
                    "finished_at": _now(),
                    "duration_ms": duration_ms,
                })
                result["node_history"] = history
                result["current_node"] = node_name
                log_event({
                    "reclamacao_id": state.get("id"),
                    "node": node_name,
                    "evento": "end",
                    "timestamp": _now(),
                    "duration_ms": duration_ms,
                    "output_resumo": _summary(result),
                })
                return result
            return async_wrapper

        @wraps(func)
        def wrapper(state, *args, **kwargs):
            started_at = _now()
            started = time.perf_counter()
            state["current_node"] = node_name
            log_event({
                "reclamacao_id": state.get("id"),
                "node": node_name,
                "evento": "start",
                "timestamp": started_at,
            })

            try:
                result = func(state, *args, **kwargs)
            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event({
                    "reclamacao_id": state.get("id"),
                    "node": node_name,
                    "evento": "error",
                    "timestamp": _now(),
                    "duration_ms": duration_ms,
                    "error": str(exc),
                })
                raise

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # Preserva o histórico acumulado de nós anteriores antes de adicionar este
            history = list(result.get("node_history", state.get("node_history", [])))
            history.append({
                "node": node_name,
                "started_at": started_at,
                "finished_at": _now(),
                "duration_ms": duration_ms,
            })
            result["node_history"] = history
            result["current_node"] = node_name

            log_event({
                "reclamacao_id": state.get("id"),
                "node": node_name,
                "evento": "end",
                "timestamp": _now(),
                "duration_ms": duration_ms,
                "output_resumo": _summary(result),
            })
            return result
        return wrapper
    return decorator


def _summary(state: dict) -> str:
    """Gera string compacta dos campos-chave do estado para o log de fim de nó."""
    parts = []
    for key in ("categoria", "urgencia", "nivel_risco", "escalado"):
        value = state.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "; ".join(parts)
