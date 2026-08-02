#!/usr/bin/env python3
"""CLI for issue #43 non-focusable hit-sentinel spike."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from renforge.editor_hit_sentinel_runner import (  # noqa: E402
    run_hit_sentinel_spike,
    run_twice_for_determinism,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT / "examples" / "demo_game",
        help="Ren'Py project root (copied to a temp workdir unless --in-place)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".renforge" / "hit-sentinel-spike" / "result.json",
    )
    parser.add_argument("--display", default="auto")
    parser.add_argument(
        "--twice",
        action="store_true",
        help="Run twice and require identical capability verdicts",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Mutate the project tree directly (default: copy demo to temp)",
    )
    args = parser.parse_args()

    if args.in_place:
        project_root = args.project.resolve()
        result = (
            run_twice_for_determinism(project_root, display=args.display)
            if args.twice
            else run_hit_sentinel_spike(project_root, output=args.output, display=args.display)
        )
    else:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="renforge-hit-sentinel-") as tmp:
            dest = Path(tmp) / "project"
            shutil.copytree(
                args.project,
                dest,
                ignore=shutil.ignore_patterns("*.rpyc", "cache", "saves"),
            )
            if args.twice:
                result = run_twice_for_determinism(dest, display=args.display)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                result = run_hit_sentinel_spike(
                    dest,
                    output=args.output,
                    display=args.display,
                )

    capability = (
        result.get("run1_capability")
        if "run1_capability" in result
        else result.get("capability")
    )
    print(json.dumps({"capability": capability, "output": str(args.output)}, indent=2))
    if "deterministic" in result and not result["deterministic"]:
        return 2
    if capability == "pass":
        return 0
    if capability == "inconclusive":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
