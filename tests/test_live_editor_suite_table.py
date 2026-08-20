"""Keep the nightly live-editor suite table in sync with tests on disk.

The runner enumerates suites in a literal table so glob discovery cannot
silently add, drop, or rename them. That check only runs in the live job,
which is how `test_editor_say_what_live.py` stayed unlisted on main for a
week of scheduled red runs. This test is the same gate, without Ren'Py.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_live_editor_suites.sh"
TESTS = ROOT / "tests"
PAIR_RE = re.compile(r"^(test_editor_\S+_live\.py)\s+(RENFORGE_[A-Z0-9_]+)$")
GATE_RE = re.compile(r"RENFORGE_[A-Z0-9_]+")


def _table_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    in_table = False
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUITES="):
            in_table = True
            continue
        if in_table and line.strip() == '"':
            break
        if not in_table:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        match = PAIR_RE.match(stripped)
        assert match, f"malformed suite table line: {line!r}"
        pairs.append((match.group(1), match.group(2)))
    assert pairs, "empty suite table"
    return pairs


def test_live_suite_table_matches_disk_and_gates() -> None:
    pairs = _table_pairs()
    files = [name for name, _ in pairs]
    assert files == sorted(files), "suite table must stay alphabetically ordered"
    assert len(files) == len(set(files)), f"duplicate suite in table: {files}"
    gates = [gate for _, gate in pairs]
    assert len(gates) == len(set(gates)), f"duplicate gate in table: {gates}"

    on_disk = sorted(path.name for path in TESTS.glob("test_editor_*_live.py"))
    assert files == on_disk, (
        "scripts/run_live_editor_suites.sh SUITES table is out of sync with "
        f"tests/test_editor_*_live.py\n  table: {files}\n  disk: {on_disk}"
    )

    for name, gate in pairs:
        text = (TESTS / name).read_text(encoding="utf-8")
        found = GATE_RE.search(text)
        assert found is not None, f"{name} has no RENFORGE_ gate"
        assert found.group(0) == gate, (
            f"gate mismatch for {name}: table says {gate}, file says {found.group(0)}"
        )
