from __future__ import annotations

from pathlib import Path

from renforge.editor_rotation_runner import _run_manual_rotate_roundtrip


def test_manual_rotate_roundtrip_handles_replacement_length_change(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.rpy"
    baseline = (
        b'screen example():\n'
        b'    button id "rotation_target":\n'
        b'        add Transform(Solid("#fff"), rotate=9)\n'
    )
    fixture.write_bytes(baseline)

    report = _run_manual_rotate_roundtrip(fixture)

    assert report["rotate"] == {"before": 9, "patched": 10}
    assert report["outside_bytes_equal"] is True
    assert report["patch"]["patched_end"] == report["patch"]["original_end"] + 1
    assert report["matches_baseline"] is True
    assert fixture.read_bytes() == baseline