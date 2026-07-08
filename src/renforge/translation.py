"""Translation tools: list languages, generate translations, report completion.

Wraps Ren'Py's built-in translation CLI commands. Completion stats come from
``translate <language> --count``, which prints the number of missing dialogue
and string translations without writing files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .project import RenpyProject
from .sdk import RenpySdk
from .util.subprocess import run_command

_TL_DIR = "tl"
_RESERVED = {"None", "none"}
_COUNT_RE = re.compile(
    r"(?P<dialogue>\d+)\s+missing\s+dialogue\s+translations.*?(?P<strings>\d+)\s+missing\s+string\s+translations",
    re.IGNORECASE | re.DOTALL,
)


def list_languages(project_path: str | Path) -> list[str]:
    """Return the translation languages present under ``game/tl/``."""
    tl = Path(project_path).expanduser().resolve() / "game" / _TL_DIR
    if not tl.is_dir():
        return []
    return sorted(
        d.name for d in tl.iterdir() if d.is_dir() and d.name not in _RESERVED
    )


def generate_translations(sdk: RenpySdk, project: RenpyProject, language: str, *, timeout: int = 180) -> dict[str, Any]:
    """Generate/update translation files for ``language`` (writes game/tl/<language>/)."""
    command = project.renpy_command(sdk, ("translate", language))
    result = run_command(command, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "language": language,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def translation_stats(sdk: RenpySdk, project: RenpyProject, language: str, *, timeout: int = 180) -> dict[str, Any]:
    """Report missing dialogue/string counts for ``language`` (no files written)."""
    command = project.renpy_command(sdk, ("translate", language, "--count"))
    result = run_command(command, timeout=timeout)
    match = _COUNT_RE.search(result.stdout) or _COUNT_RE.search(result.stderr)
    stats: dict[str, Any] = {
        "ok": result.returncode == 0,
        "language": language,
    }
    if match:
        stats["missing_dialogue"] = int(match.group("dialogue"))
        stats["missing_strings"] = int(match.group("strings"))
    else:
        stats["missing_dialogue"] = None
        stats["missing_strings"] = None
        stats["raw"] = (result.stdout or result.stderr).strip()[:500]
    return stats


def export_dialogue(sdk: RenpySdk, project: RenpyProject, language: str = "None", *, timeout: int = 180) -> dict[str, Any]:
    """Export the game's dialogue as plain text."""
    command = project.renpy_command(sdk, ("dialogue", language, "--text"))
    result = run_command(command, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "language": language,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def list_translation_strings(project_path: str | Path, language: str) -> list[dict[str, Any]]:
    """Parse real translation blocks (dialogue & strings) from game/tl/<language>/*.rpy."""
    tl_dir = Path(project_path).expanduser().resolve() / "game" / "tl" / language
    if not tl_dir.is_dir():
        return []

    strings = []
    
    # regex for dialogue translation blocks
    dialogue_block_re = re.compile(
        r'^\s*translate\s+' + re.escape(language) + r'\s+(?P<id>[a-zA-Z0-9_]+)\s*:\s*\n'
        r'(?P<lines>(?:\s+.*\n?)+)',
        re.MULTILINE
    )
    
    # regex for strings translation block
    strings_block_re = re.compile(
        r'^\s*translate\s+' + re.escape(language) + r'\s+strings\s*:\s*\n'
        r'(?P<lines>(?:\s+.*\n?)+)',
        re.MULTILINE
    )
    
    # scan for any .rpy files under the language directory
    for rpy_file in tl_dir.glob("**/*.rpy"):
        try:
            content = rpy_file.read_text(encoding="utf-8")
        except Exception:
            continue
            
        # 1. Parse dialogue blocks
        for match in dialogue_block_re.finditer(content):
            label_id = match.group("id")
            lines_str = match.group("lines")
            
            src_text = ""
            tr_text = ""
            
            for line in lines_str.split("\n"):
                line_trimmed = line.strip()
                if not line_trimmed:
                    continue
                if line_trimmed.startswith("#"):
                    quote_match = re.search(r'"(?P<text>.*?)"', line_trimmed)
                    if quote_match:
                        src_text = quote_match.group("text")
                else:
                    quote_match = re.search(r'"(?P<text>.*?)"', line_trimmed)
                    if quote_match:
                        tr_text = quote_match.group("text")
            
            if src_text or tr_text:
                status = "ok" if tr_text and tr_text != src_text else "todo"
                status_label = "OK" if status == "ok" else "À TRADUIRE"
                strings.append({
                    "id": label_id,
                    "src": src_text,
                    "tr": tr_text,
                    "status": status,
                    "statusLabel": status_label
                })
                
        # 2. Parse strings blocks
        for match in strings_block_re.finditer(content):
            lines_str = match.group("lines")
            
            old_texts = []
            new_texts = []
            
            for line in lines_str.split("\n"):
                line_trimmed = line.strip()
                if line_trimmed.startswith("old"):
                    quote_match = re.search(r'"(?P<text>.*?)"', line_trimmed)
                    if quote_match:
                        old_texts.append(quote_match.group("text"))
                elif line_trimmed.startswith("new"):
                    quote_match = re.search(r'"(?P<text>.*?)"', line_trimmed)
                    if quote_match:
                        new_texts.append(quote_match.group("text"))
            
            for i in range(min(len(old_texts), len(new_texts))):
                src = old_texts[i]
                tr = new_texts[i]
                status = "ok" if tr else "todo"
                status_label = "OK" if status == "ok" else "À TRADUIRE"
                strings.append({
                    "id": f"string_{i+1}",
                    "src": src,
                    "tr": tr,
                    "status": status,
                    "statusLabel": status_label
                })
                
    return strings


__all__ = ["list_languages", "generate_translations", "translation_stats", "export_dialogue", "list_translation_strings"]
