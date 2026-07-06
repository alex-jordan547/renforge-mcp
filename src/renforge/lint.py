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
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<severity>warning|error|info|critical)\b\s*:?\s*(?P<message>.+?)\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*\[(?P<severity>[^\]]+)\]\s*(?P<message>.+?)\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<message>.+?)\s*$",
            re.IGNORECASE,
        ),
    )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        matched = False
        for pat in patterns:
            m = pat.match(line)
            if not m:
                continue
            matched = True

            file_name = (m.group("file") or "").strip()
            severity = (m.groupdict().get("severity") or "warning").strip().lower()
            if severity not in {"error", "warning", "info", "critical", "hint"}:
                severity = "warning"

            try:
                line_no = int(m.group("line"))
            except (TypeError, ValueError):
                line_no = 0

            message = (m.group("message") or "").strip()
            if not message:
                continue

            results.append(
                {
                    "file": file_name,
                    "line": line_no,
                    "severity": severity,
                    "message": message,
                }
            )
            break

        if not matched:
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
