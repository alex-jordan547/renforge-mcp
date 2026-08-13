"""Activity feed helpers for MCP tool calls."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .util import ensure_nofollow_directory
from .util.files import append_nofollow


_MAX_ACTIVITY_BYTES = 8192
_MAX_STRING_CHARS = 512
_MAX_COLLECTION_ITEMS = 32
_SENSITIVE_TOKENS = frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "content",
        "contents",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "expr",
        "key",
        "passwd",
        "password",
        "private",
        "secret",
        "session",
        "steps",
        "text",
        "token",
        "value",
    }
)


def _bound_text(value: str) -> str:
    if len(value) > _MAX_STRING_CHARS:
        return value[:_MAX_STRING_CHARS] + "...[truncated]"
    return value


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", separated)
    parts = [part for part in re.split(r"[^a-z0-9]+", separated.lower()) if part]
    return any(part in _SENSITIVE_TOKENS for part in parts)


def _encode_activity_entry(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _coerce_project_root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


def _coerce_files_touched(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if isinstance(item, (str, Path))]
    return []


def _coerce_result_payload(result: Any) -> dict[str, Any]:
    summary = summarize_result(result)
    payload: dict[str, Any] = {"ok": summary["ok"]}
    if isinstance(result, dict):
        payload["keys"] = sorted(_bound_text(str(key)) for key in result)[:_MAX_COLLECTION_ITEMS]
    else:
        payload["type"] = _bound_text(type(result).__name__)
    return payload


def _coerce_payload(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if key and _is_sensitive_key(key):
        return "[redacted]"
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str) and len(value) > _MAX_STRING_CHARS:
            return _bound_text(value)
        return value
    if isinstance(value, list):
        return [
            _coerce_payload(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, dict):
        return {
            _bound_text(str(k)): _coerce_payload(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, set):
        return sorted(_bound_text(str(item)) for item in value)[:_MAX_COLLECTION_ITEMS]
    if isinstance(value, tuple):
        return [
            _coerce_payload(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    coerced = str(value)
    return _bound_text(coerced)


def summarize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        ok = result.get("ok", not isinstance(result.get("error"), str))
        files_touched: list[str] = []
        for key in ("files_touched", "files", "changed_files", "changed", "file_touches"):
            candidate = result.get(key)
            if candidate:
                files_touched = _coerce_files_touched(candidate)
                break
        return {"ok": bool(ok), "files_touched": files_touched, "result": result}

    if isinstance(result, (str, int, float, bool, list, type(None))):
        return {"ok": True, "files_touched": [], "result": result}

    return {"ok": True, "files_touched": [], "result": str(result)}


def log_tool_call(
    project_root: str | Path,
    name: str,
    params: dict[str, Any],
    duration_ms: float,
    result: Any,
    files_touched: list[str] | None = None,
) -> None:
    summary = summarize_result(result)
    entry = {
        "ts": int(time.time() * 1000),
        "name": _bound_text(str(name)),
        "params": _coerce_payload(params),
        "duration_ms": duration_ms,
        "ok": summary["ok"],
        "result": _coerce_result_payload(summary["result"]),
        "files_touched": (files_touched or summary["files_touched"])[:_MAX_COLLECTION_ITEMS],
    }

    root = _coerce_project_root(project_root)
    if not root.exists() or not root.is_dir():
        return
    activity_dir = ensure_nofollow_directory(root / ".renforge")
    path = activity_dir / "activity.jsonl"

    encoded = _encode_activity_entry(entry)
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        entry["params"] = {"truncated": True}
        entry["files_touched"] = []
        entry["result"] = {"ok": bool(entry.get("ok")), "truncated": True}
        encoded = _encode_activity_entry(entry)
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        entry = {
            "ts": entry["ts"],
            "name": _bound_text(str(name)),
            "ok": bool(entry.get("ok")),
            "truncated": True,
        }
        encoded = _encode_activity_entry(entry)
    append_nofollow(path, encoded, mode=0o600)
