"""Activity feed helpers for MCP tool calls."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _coerce_project_root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


def _coerce_files_touched(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if isinstance(item, (str, Path))]
    return []


def _coerce_result_payload(result: Any) -> Any:
    if isinstance(result, dict) and "ok" in result:
        return result
    if isinstance(result, (str, int, float, bool, list, type(None))):
        return result
    return str(result)


def _coerce_payload(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_coerce_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _coerce_payload(v) for k, v in value.items()}
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, tuple):
        return [_coerce_payload(item) for item in value]
    return str(value)


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
        "files_touched": files_touched or summary["files_touched"],
    }

    root = _coerce_project_root(project_root)
    if not root.exists() or not root.is_dir():
        return
    path = root / ".renforge" / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(payload)
        file_obj.write("\n")
        file_obj.flush()
        os.fsync(file_obj.fileno())
