"""Activity feed helpers for MCP tool calls."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .util import ensure_nofollow_directory
from .util.files import append_nofollow, ensure_private_directory, write_json_atomic


_MAX_ACTIVITY_BYTES = 8192
_MAX_ACTIVITY_SIDECARS = 200
_MAX_ACTIVITY_SIDECAR_BYTES = 50 * 1024 * 1024
_MAX_STRING_CHARS = 512
_MAX_COLLECTION_ITEMS = 32
_SIDECAR_NAME_RE = re.compile(r"^\d+-[0-9a-f]{16}\.json\Z")
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
    if isinstance(value, (set, frozenset)):
        return [str(item) for item in sorted(value, key=str) if isinstance(item, (str, Path))]
    if isinstance(value, (list, tuple)):
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
    """Describe a tool result using only explicit write-receipt fields.

    Legacy keys such as ``files`` are deliberately not treated as writes: many
    read-only tools return a ``files`` list and guessing turned those reads into
    fake activity entries.
    """
    if isinstance(result, dict):
        ok = result.get("ok", not isinstance(result.get("error"), str))
        touched = result.get("files_touched")
        deleted = result.get("files_deleted")
        return {
            "ok": bool(ok),
            "files_touched": _coerce_files_touched(touched),
            "files_deleted": _coerce_files_touched(deleted),
            "files_touched_recorded": touched is not None,
            "files_deleted_recorded": deleted is not None,
            "result": result,
        }

    if isinstance(result, (str, int, float, bool, list, type(None))):
        return {
            "ok": True,
            "files_touched": [],
            "files_deleted": [],
            "files_touched_recorded": False,
            "files_deleted_recorded": False,
            "result": result,
        }

    return {
        "ok": True,
        "files_touched": [],
        "files_deleted": [],
        "files_touched_recorded": False,
        "files_deleted_recorded": False,
        "result": str(result),
    }


def _prune_files_sidecars(directory: Path) -> None:
    """Keep the newest sidecars within the count and byte budgets.

    Sidecars are written only when a path list does not fit in the compact
    journal entry. Without a cap a long session would grow the game folder
    forever, so old receipts are removed after a successful write. The newest
    receipt is always kept, even when it alone exceeds the byte budget.
    """
    try:
        sidecars = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if not _SIDECAR_NAME_RE.fullmatch(entry.name):
                    continue
                try:
                    if entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        sidecars.append((int(entry.name.split("-", 1)[0]), entry.name, size))
                except OSError:
                    continue
    except OSError:
        return
    sidecars.sort(reverse=True)
    kept = 0
    total = 0
    for _, _, size in sidecars:
        if kept >= _MAX_ACTIVITY_SIDECARS:
            break
        if kept and total + size > _MAX_ACTIVITY_SIDECAR_BYTES:
            break
        kept += 1
        total += size
    for _, name, _ in sidecars[kept:]:
        try:
            (directory / name).unlink()
        except OSError:
            pass


def _write_files_sidecar(
    activity_dir: Path,
    project_root: Path,
    timestamp: int,
    files_touched: list[str],
    files_deleted: list[str],
) -> str | None:
    """Spill the full path lists under ``.renforge/activity-files/`` (0600).

    The sidecar is shaped ``{"files_touched": [...], "files_deleted": [...]}``.
    Returns the project-relative POSIX path, or ``None`` when the sidecar
    cannot be written safely (for example an unsafe directory, wrong DACL or
    unsupported volume). The compact journal entry is still written either way.
    """
    payload: dict[str, Any] = {
        "files_touched": files_touched,
        "files_deleted": files_deleted,
    }
    digest_source = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(digest_source).hexdigest()[:16]
    try:
        directory = ensure_private_directory(activity_dir / "activity-files")
        path = directory / f"{timestamp}-{digest}.json"
        write_json_atomic(path, payload, mode=0o600, follow_symlinks=False)
    except Exception:
        return None
    _prune_files_sidecars(directory)
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return None


def log_tool_call(
    project_root: str | Path,
    name: str,
    params: dict[str, Any],
    duration_ms: float,
    result: Any,
    policy: dict[str, Any] | None = None,
) -> None:
    summary = summarize_result(result)
    full_touched = [str(item) for item in summary["files_touched"]]
    full_deleted = [str(item) for item in summary["files_deleted"]]
    compact_touched = [_bound_text(item) for item in full_touched[:_MAX_COLLECTION_ITEMS]]
    compact_deleted = [_bound_text(item) for item in full_deleted[:_MAX_COLLECTION_ITEMS]]
    # More than 32 paths cannot live in the compact UI field, so the full list
    # has to be preserved elsewhere even when the entry itself still fits. A
    # single path longer than the compact limit is clipped the same way.
    oversized = any(
        len(item) > _MAX_STRING_CHARS for item in full_touched + full_deleted
    )
    truncated = (
        len(full_touched) > len(compact_touched)
        or len(full_deleted) > len(compact_deleted)
        or oversized
    )

    root = _coerce_project_root(project_root)
    if not root.exists() or not root.is_dir():
        return
    activity_dir = ensure_nofollow_directory(root / ".renforge")

    entry: dict[str, Any] = {
        "ts": int(time.time() * 1000),
        "name": _bound_text(str(name)),
        "params": _coerce_payload(params),
        "duration_ms": duration_ms,
        "ok": summary["ok"],
        "result": _coerce_result_payload(summary["result"]),
        "files_touched": compact_touched,
        "files_touched_recorded": summary["files_touched_recorded"],
        "files_touched_truncated": truncated,
        "files_deleted": compact_deleted,
        "files_deleted_recorded": summary["files_deleted_recorded"],
    }
    if policy:
        entry["policy"] = _coerce_payload(policy)

    sidecar_path: str | None = None
    if truncated:
        sidecar_path = _write_files_sidecar(
            activity_dir, root, entry["ts"], full_touched, full_deleted
        )
        if sidecar_path is not None:
            entry["files_touched_file"] = sidecar_path

    path = activity_dir / "activity.jsonl"
    encoded = _encode_activity_entry(entry)

    # Overflow: first discard verbose params/result detail. The write receipt
    # stays in place; it is never emptied just because the entry is large.
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        entry["params"] = {"truncated": True}
        entry["result"] = {"ok": bool(entry.get("ok")), "truncated": True}
        encoded = _encode_activity_entry(entry)

    # Still too large: the compact paths themselves are verbose. Spill the
    # full lists and keep as many compact paths as fit (but at least one when
    # the receipt was non-empty).
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        if sidecar_path is None:
            sidecar_path = _write_files_sidecar(
                activity_dir, root, entry["ts"], full_touched, full_deleted
            )
            if sidecar_path is not None:
                entry["files_touched_file"] = sidecar_path
        entry["files_touched_truncated"] = True
        while len(encoded) > _MAX_ACTIVITY_BYTES and len(entry["files_touched"]) > 1:
            entry["files_touched"] = entry["files_touched"][: max(1, len(entry["files_touched"]) // 2)]
            encoded = _encode_activity_entry(entry)
        while len(encoded) > _MAX_ACTIVITY_BYTES and entry.get("files_deleted"):
            keep = len(entry["files_deleted"]) // 2
            # Keep the key present even when its last item has to go; the
            # receipt contract always includes files_deleted.
            entry["files_deleted"] = entry["files_deleted"][:keep]
            encoded = _encode_activity_entry(entry)

    # Last resort: a minimal entry, but still carrying a bounded receipt and
    # the sidecar pointer when one was written.
    if len(encoded) > _MAX_ACTIVITY_BYTES:
        entry = {
            "ts": entry["ts"],
            "name": _bound_text(str(name)),
            "ok": bool(entry.get("ok")),
            "truncated": True,
            "files_touched": list(entry.get("files_touched") or [])[:1],
            "files_touched_recorded": summary["files_touched_recorded"],
            "files_touched_truncated": bool(entry.get("files_touched_truncated")),
            "files_deleted": [],
            "files_deleted_recorded": summary["files_deleted_recorded"],
        }
        if sidecar_path is not None:
            entry["files_touched_file"] = sidecar_path
        encoded = _encode_activity_entry(entry)

    append_nofollow(path, encoded, mode=0o600)
