#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


RUNTIME_IGNORE = shutil.ignore_patterns(
    ".renforge",
    "cache",
    "saves",
    "*.rpyb",
    "*.rpyc",
    "*.rpymc",
    "log.txt",
    "traceback.txt",
    "errors.txt",
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def seed_fixture(repo_root: Path, destination: Path, *, force: bool) -> Path:
    source = repo_root / "examples" / "demo_game"
    if not source.is_dir():
        raise SystemExit(f"missing demo project: {source}")

    if destination.exists():
        if not force:
            raise SystemExit(f"{destination} already exists; pass --force to replace it")
        shutil.rmtree(destination)

    shutil.copytree(source, destination, ignore=RUNTIME_IGNORE)

    renforge_dir = destination / ".renforge"
    renforge_dir.mkdir(parents=True, exist_ok=True)

    (renforge_dir / "autopilot.json").write_text(
        json.dumps(
            {
                "ok": True,
                "runs": 3,
                "steps": 18,
                "labels_total": ["start", "choice", "good", "bad"],
                "labels_covered": ["start", "choice", "good"],
                "labels_missing": ["bad"],
                "coverage": 0.75,
                "crashes": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    now = int(time.time() * 1000)
    _write_jsonl(
        renforge_dir / "activity.jsonl",
        [
            {
                "ts": now - 12_000,
                "name": "renforge_scan_project",
                "tool": "renforge_scan_project",
                "category": "static",
                "duration_ms": 42,
                "result": {"ok": True, "labels": 4, "menus": 1},
            },
            {
                "ts": now - 8_000,
                "name": "renforge_translation_stats",
                "tool": "renforge_translation_stats",
                "category": "translation",
                "duration_ms": 35,
                "params": {"language": "french"},
                "result": {"ok": True, "missing_dialogue": 6, "missing_strings": 387},
            },
            {
                "ts": now - 4_000,
                "name": "renforge_parse_lint",
                "tool": "renforge_parse_lint",
                "category": "diagnostics",
                "duration_ms": 51,
                "result": {"ok": False, "diagnostics": 2},
            },
        ],
    )

    tl_dir = destination / "game" / "tl" / "french"
    tl_dir.mkdir(parents=True, exist_ok=True)
    (tl_dir / "script.rpy").write_text(
        "\n".join(
            [
                "translate french strings:",
                "",
                "    old \"History\"",
                "    new \"Historique\"",
                "",
                "    old \"Skip\"",
                "    new \"Passer\"",
                "",
                "translate french start:",
                "",
                "    # TODO: translate this block during QA.",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a data-rich RenForge dashboard QA fixture.")
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help="Fixture destination. Defaults to .renforge/qa-demo-game under the repo root.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing destination.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    destination = args.destination or repo_root / ".renforge" / "qa-demo-game"
    destination = destination.expanduser().resolve()

    created = seed_fixture(repo_root, destination, force=args.force)
    print(created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
