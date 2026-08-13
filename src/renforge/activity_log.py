"""Activity feed helpers for MCP tool calls."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .util import ensure_nofollow_directory


_MAX_ACTIVITY_BYTES = 8192
_MAX_STRING_CHARS = 512
_MAX_COLLECTION_ITEMS = 32
_SENSITIVE_KEYS = {
    "content",
    "contents",
    "expr",
    "password",
    "secret",
    "steps",
    "text",
    "token",
    "value",
}


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
        payload["keys"] = sorted(str(key) for key in result)[:_MAX_COLLECTION_ITEMS]
    else:
        payload["type"] = type(result).__name__
    return payload


def _coerce_payload(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[redacted]"
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str) and len(value) > _MAX_STRING_CHARS:
            return value[:_MAX_STRING_CHARS] + "...[truncated]"
        return value
    if isinstance(value, list):
        return [
            _coerce_payload(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, dict):
        return {
            str(k): _coerce_payload(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, set):
        return sorted(str(item) for item in value)[:_MAX_COLLECTION_ITEMS]
    if isinstance(value, tuple):
        return [
            _coerce_payload(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    coerced = str(value)
    return coerced[:_MAX_STRING_CHARS]


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
        "name": name,
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

    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    encoded = (payload + "\n").encode("utf-8")
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        entry["params"] = {"truncated": True}
        entry["files_touched"] = []
        encoded = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("activity log is not a regular file")
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
