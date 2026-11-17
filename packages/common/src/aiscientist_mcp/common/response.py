"""Bound both MCP text and structured copies while retaining complete disk evidence."""

import json
from pathlib import Path

# MCP emits a pretty text JSON copy plus structuredContent. Measure both, including
# escaping of the text copy, and leave room for protocol metadata below 24 KB.
MAX_STRUCTURED_BYTES = 10000
MAX_WIRE_PREVIEW_BYTES = 23000
_ESSENTIAL = {"status", "state", "analysis_id", "task_id", "job_id", "mode", "tool", "error", "note", "artifact_root"}


def _fits(value):
    if len(json.dumps(value).encode()) > MAX_STRUCTURED_BYTES:
        return False
    wire = {"structuredContent": value, "content": [{"type": "text", "text": json.dumps(value, indent=2)}]}
    return len(json.dumps(wire).encode()) <= MAX_WIRE_PREVIEW_BYTES


def bounded_result(result: dict, *, full_result: str | Path) -> dict:
    """Preserve status/identity/counts; large arrays/strings become marked previews.

    ``full_result`` must be an already persisted result file admitted by the caller.
    This helper never writes or mutates the scientific result. Clients can retrieve
    complete rows/citations/variants from that evidence instead of model context.
    """
    if _fits(result):
        return result

    def bounded(value, width, depth=0):
        if isinstance(value, str):
            return value if len(value) <= 600 else value[:600] + " [preview truncated]"
        if isinstance(value, list):
            return [bounded(item, width, depth + 1) for item in value[:width]] if depth < 4 else []
        if isinstance(value, dict):
            return {key: bounded(item, width, depth + 1) for key, item in value.items()} if depth < 4 else {}
        return value

    def envelope(out):
        # Routing and failure states are never dropped, including at the deepest
        # fallback. Complete long diagnostic text remains in the evidence file.
        out.update({key: bounded(value, 1) for key, value in result.items() if key in _ESSENTIAL})
        out.update(response_omitted=True, full_result=str(full_result))
        return out

    for width in (5, 2, 1):
        out = envelope(bounded(result, width))
        if _fits(out):
            return out
    out = {
        key: bounded(value, 1)
        for key, value in result.items()
        if key in _ESSENTIAL or (key.startswith("n_") and isinstance(value, (int, float)))
    }
    return envelope(out)
