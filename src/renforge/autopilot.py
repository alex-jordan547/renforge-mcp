"""Autopilot: explore a Ren'Py visual novel and report label coverage + crashes.

Strategy — systematic branch exploration by replay. Each run launches the game
fresh, replays a fixed prefix of menu choices, then at the first *new* menu it
takes choice 0 and queues the remaining choices as future runs. Repeating until
the frontier is empty covers every branch combination, using only primitives
that work reliably (launch / advance / list_choices / select_choice /
poll_events) — no in-game save/load, which cannot be driven from the bridge's
main-thread callback.

Menus are detected by ``list_choices`` returning options. (A game with an
always-on quick menu could surface non-choice buttons here; refining detection
to the active ``choice`` screen is a future improvement.)
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Any

from .bridge.launcher import launch_with_bridge
from .project import RenpyProject
from .scanner import scan_project
from .sdk import RenpySdk


def _story_labels(project_path: str | Path) -> set[str]:
    # Labels authored in the project, excluding Ren'Py-internal (underscore) ones.
    index = scan_project(str(project_path))
    return {label["name"] for label in index.get("labels", []) if not label["name"].startswith("_")}


def autopilot(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    max_runs: int = 16,
    max_steps: int = 60,
    settle: float = 0.4,
    startup_timeout: float = 90.0,
) -> dict[str, Any]:
    """Explore the game and return a coverage/crash report."""
    total_labels = _story_labels(project.root)

    reached: set[str] = set()
    dialogue: set[str] = set()
    crashes: list[dict] = []
    choices_explored = 0

    frontier: deque[list[int]] = deque([[]])
    seen_prefixes: set[tuple[int, ...]] = set()
    runs = 0

    def _drain(client, cursor: int, run_labels: list[str]) -> int:
        events = client.poll_events(since=cursor)
        for event in events["events"]:
            kind = event.get("type")
            if kind == "label":
                name = event.get("label")
                if name and not name.startswith("_"):
                    reached.add(name)
                    run_labels.append(name)
            elif kind == "say":
                dialogue.add(event.get("what"))
            elif kind == "exception":
                crashes.append({"short": event.get("short"), "full": event.get("full")})
        return events["cursor"]

    while frontier and runs < max_runs:
        prefix = frontier.popleft()
        if tuple(prefix) in seen_prefixes:
            continue
        seen_prefixes.add(tuple(prefix))
        runs += 1

        session = launch_with_bridge(sdk, project, startup_timeout=startup_timeout)
        try:
            cursor = 0
            seq: list[int] = list(prefix)
            seq_pos = 0
            run_labels: list[str] = []

            for _step in range(max_steps):
                cursor = _drain(session.client, cursor, run_labels)

                # Loop guard: a repeated story label means the game cycled back
                # (e.g. returned to the main menu); stop this run so we don't
                # keep re-answering the same menu forever.
                if len(run_labels) != len(set(run_labels)):
                    break

                choices = session.client.list_choices()
                if choices:
                    # Let the menu's show transition finish so the choice buttons
                    # are at stable positions before we click one.
                    time.sleep(settle)
                    if seq_pos < len(seq):
                        idx = seq[seq_pos]
                    else:
                        # New branch point: take choice 0, queue the alternatives.
                        for alt in range(1, len(choices)):
                            frontier.append(seq + [alt])
                        idx = 0
                        seq.append(0)
                    seq_pos += 1
                    idx = min(idx, len(choices) - 1)
                    # Select by visible text (the reliable focus-resolution path).
                    session.client.select_choice(text=choices[idx]["text"])
                    choices_explored += 1
                    time.sleep(settle)
                    continue

                session.client.advance()
                time.sleep(settle)

            _drain(session.client, cursor, run_labels)
        finally:
            session.close()

    unreached = sorted(total_labels - reached)
    covered = sorted(total_labels & reached)
    return {
        "ok": True,
        "runs": runs,
        "labels_total": len(total_labels),
        "labels_reached": sorted(reached),
        "labels_covered": covered,
        "labels_unreached": unreached,
        "coverage": round(len(covered) / len(total_labels), 3) if total_labels else 1.0,
        "dialogue_lines": len(dialogue),
        "choices_explored": choices_explored,
        "crashes": crashes,
    }


__all__ = ["autopilot"]
