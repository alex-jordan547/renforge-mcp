from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, List

from .project import RenpyProject
from .sdk import get_or_install_sdk
from .util.subprocess import run_command


def parse_lint_output(text: str) -> List[Dict[str, Any]]:
    """
    Transforme une sortie textuelle Ren'Py lint en liste de diagnostics:
    {file, line, severity, message}.

    Le parser est permissif: il essaie plusieurs formes courantes puis ignore
    ce qu'il ne comprend pas, sans lever d'exception.
    """

    results: List[Dict[str, Any]] = []
    if not text:
        return results

    patterns = (
        re.compile(
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<severity>warning|error|info|critical)\b\s*:?\s*(?P<message>.*?)\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*\[(?P<severity>[^\]]+)\]\s*(?P<message>.*?)\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<message>.*?)\s*$",
            re.IGNORECASE,
        ),
    )
    file_section_re = re.compile(r"^\s*(?P<file>[^:\n]+\.rpy):\s*$", re.IGNORECASE)
    section_line_re = re.compile(r"^\s*(?P<title>[^:\n][^:\n]{1,120}):\s*$")
    bullet_line_re = re.compile(r"^\s*\*\s*line\s+(?P<line>\d+)\s*(?P<message>.*?)\s*$", re.IGNORECASE)

    def _as_diagnostic(raw: str) -> Dict[str, Any] | None:
        for pat in patterns:
            m = pat.match(raw)
            if not m:
                continue

            file_name = (m.group("file") or "").strip()
            severity = (m.groupdict().get("severity") or "warning").strip().lower()
            if severity not in {"error", "warning", "info", "critical", "hint"}:
                severity = "warning"

            try:
                line_no = int(m.group("line"))
            except (TypeError, ValueError):
                line_no = 0

            message = (m.group("message") or "").strip()
            payload: Dict[str, Any] = {
                "file": file_name,
                "line": line_no,
                "severity": severity,
            }
            if message:
                payload["message"] = message
            return payload

        return None

    def _flush_pending(payload: Dict[str, Any] | None, section: str | None) -> None:
        if payload is None:
            return
        detail_message = payload.pop("_details", [])
        if detail_message:
            payload["message"] = "; ".join(detail_message).strip()
            results.append(payload)
            return
        if section:
            payload["message"] = section
            results.append(payload)

    pending: Dict[str, Any] | None = None
    current_section: str | None = None
    current_file: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            _flush_pending(pending, current_section)
            pending = None
            continue

        parsed = _as_diagnostic(line)
        if pending is not None and parsed is None and len(line) > len(stripped):
            if stripped:
                pending.setdefault("_details", []).append(stripped)
            continue

        if pending is not None:
            _flush_pending(pending, current_section)
            pending = None

        file_match = file_section_re.match(line)
        if parsed is None and file_match:
            current_file = file_match.group("file").strip()
            continue

        bullet_match = bullet_line_re.match(line)
        if parsed is None and current_file and bullet_match:
            message = bullet_match.group("message").strip()
            section = current_section or "Ren'Py lint"
            results.append(
                {
                    "file": current_file,
                    "line": int(bullet_match.group("line")),
                    "severity": "warning",
                    "message": f"{section} {message}".strip(),
                }
            )
            continue

        section_match = section_line_re.match(line)
        if parsed is None and section_match and len(line) == len(stripped):
            current_section = section_match.group("title").strip()
            current_file = None
            continue

        if parsed is None:
            # Dernier filet: lignes déjà préfixées d'un niveau de sévérité.
            lowered = line.lower()
            if "error" in lowered:
                severity = "error"
            elif "warning" in lowered:
                severity = "warning"
            elif "info" in lowered:
                severity = "info"
            else:
                continue
            results.append(
                {
                    "file": "",
                    "line": 0,
                    "severity": severity,
                    "message": line,
                }
            )
            continue

        if "message" in parsed:
            results.append(parsed)
            continue

        pending = parsed

    if pending is not None:
        _flush_pending(pending, current_section)

    return results


def run_lint(project_root: str | Path, *, version: str = "stable", timeout: int = 180) -> dict[str, Any]:
    try:
        project = RenpyProject(Path(project_root).expanduser().resolve())
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        sdk = get_or_install_sdk(version)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        command = project.lint_command(sdk)
        result = run_command(command, timeout=timeout)
        raw = result.stdout or ""
        if result.stderr:
            raw = f"{raw}\n{result.stderr}" if raw else result.stderr
        diagnostics = parse_lint_output(raw)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "diagnostics": diagnostics,
        "raw": raw,
    }


__all__ = ["parse_lint_output", "run_lint"]
