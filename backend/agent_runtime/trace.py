"""Append-only Agent trace log helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


TRACE_DIR = Path(__file__).resolve().parents[1] / "data" / "agent_traces"
_SECRET_KEYS = {"api_key", "openai_api_key", "claude_api_key", "tts_api_key", "subtitle_api_key", "authorization"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in _SECRET_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def append_trace(
    task_id: str,
    event_type: str,
    *,
    source: str = "agent_runtime",
    payload: Optional[Dict[str, Any]] = None,
) -> Path:
    """Append one JSONL trace event for a task and return the trace path."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{task_id}.jsonl"
    event = {
        "timestamp": datetime.now().isoformat(),
        "task_id": task_id,
        "event_type": event_type,
        "source": source,
        "payload": _redact(payload or {}),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return path

