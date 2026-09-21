from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

LOG_FILE = Path("output/logs/execucao.jsonl")
_LOCK = threading.Lock()


def reset_log() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_node(node_name: str):
    def decorator(func):
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
    parts = []
    for key in ("categoria", "urgencia", "nivel_risco", "escalado"):
        value = state.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "; ".join(parts)
