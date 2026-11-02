from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from renforge.activity_log import log_tool_call, summarize_result


def _read_activity(project: Path) -> bytes:
    return (project / ".renforge" / "activity.jsonl").read_bytes()


def _read_entry(project: Path) -> dict:
    return json.loads(_read_activity(project))


def test_summarize_result_reads_only_explicit_write_receipts() -> None:
    legacy = summarize_result(
        {"ok": True, "files": ["a.rpy"], "changed_files": ["b.rpy"], "changed": ["c"]}
    )
    assert legacy["files_touched"] == []
    assert legacy["files_deleted"] == []
    assert legacy["files_touched_recorded"] is False
    assert legacy["files_deleted_recorded"] is False

    explicit = summarize_result(
        {
            "ok": True,
            "files_touched": ["game/a.rpy"],
            "files_deleted": ["game/b.rpy"],
        }
    )
    assert explicit["files_touched"] == ["game/a.rpy"]
    assert explicit["files_deleted"] == ["game/b.rpy"]
    assert explicit["files_touched_recorded"] is True
    assert explicit["files_deleted_recorded"] is True

    explicit_nulls = summarize_result({"ok": True, "files_touched": None, "files_deleted": None})
    assert explicit_nulls["files_touched"] == []
    assert explicit_nulls["files_deleted"] == []
    assert explicit_nulls["files_touched_recorded"] is False
    assert explicit_nulls["files_deleted_recorded"] is False


def test_activity_log_does_not_treat_legacy_files_as_writes(tmp_path: Path) -> None:
    log_tool_call(
        tmp_path,
        "renforge_scan_project",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files": ["game/read_only.rpy"], "changed_files": ["nope.rpy"]},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched"] == []
    assert entry["files_touched_recorded"] is False
    assert entry["files_touched_truncated"] is False
    assert entry["files_deleted"] == []
    assert entry["files_deleted_recorded"] is False
    assert "files_touched_file" not in entry


def test_activity_log_marks_a_null_receipt_as_not_recorded(tmp_path: Path) -> None:
    log_tool_call(
        tmp_path,
        "renforge_export_dialogue",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": False, "files_touched": None},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched"] == []
    assert entry["files_touched_recorded"] is False
    assert entry["files_touched_truncated"] is False


def test_activity_log_always_includes_files_deleted_for_symmetry(tmp_path: Path) -> None:
    log_tool_call(
        tmp_path,
        "renforge_capture_screenshot",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files_touched": [".renforge/captures/frame.png"]},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_deleted"] == []
    assert entry["files_deleted_recorded"] is False


def test_activity_log_marks_explicit_receipts_recorded(tmp_path: Path) -> None:
    log_tool_call(
        tmp_path,
        "renforge_scene_tree",
        {"project_path": str(tmp_path)},
        2.0,
        {
            "ok": True,
            "files_touched": ["game/.renforge/scenes/before.json"],
            "files_deleted": ["game/old.rpy"],
        },
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched"] == ["game/.renforge/scenes/before.json"]
    assert entry["files_deleted"] == ["game/old.rpy"]
    assert entry["files_touched_recorded"] is True
    assert entry["files_touched_truncated"] is False
    assert entry["files_deleted_recorded"] is True


def test_activity_log_overflow_keeps_the_compact_receipt(tmp_path: Path) -> None:
    bloated_params = {f"field_{index}": "x" * 500 for index in range(30)}
    log_tool_call(
        tmp_path,
        "renforge_set_var",
        {"project_path": str(tmp_path), **bloated_params},
        1.0,
        {"ok": True, "files_touched": ["game/screens.rpy"]},
    )

    raw = _read_activity(tmp_path)
    assert len(raw) <= 8192
    entry = json.loads(raw)
    assert entry["params"] == {"truncated": True}
    assert entry["files_touched"] == ["game/screens.rpy"]
    assert entry["files_touched_recorded"] is True
    assert entry["files_touched_truncated"] is False
    assert entry["files_deleted"] == []
    assert entry["files_deleted_recorded"] is False
    assert "files_touched_file" not in entry


def test_activity_log_marks_one_oversized_path_as_truncated_and_spills_it(
    tmp_path: Path,
) -> None:
    long_path = "game/" + ("d" * 595)
    assert len(long_path) == 600

    log_tool_call(
        tmp_path,
        "renforge_generate_translations",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files_touched": [long_path]},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched_truncated"] is True
    assert len(entry["files_touched"]) == 1
    assert entry["files_touched"][0].startswith("game/" + ("d" * 100))
    assert entry["files_touched"][0].endswith("...[truncated]")
    sidecar = json.loads(
        (tmp_path / entry["files_touched_file"]).read_text(encoding="utf-8")
    )
    assert sidecar["files_touched"] == [long_path]
    assert sidecar["files_deleted"] == []


def test_activity_log_keeps_the_journal_entry_when_the_sidecar_fails(
    tmp_path: Path,
) -> None:
    renforge = tmp_path / ".renforge"
    renforge.mkdir()
    # A regular file where the sidecar directory belongs makes
    # ensure_private_directory raise PrivatePathError.
    (renforge / "activity-files").write_text("not a directory", encoding="utf-8")
    paths = [f"game/file_{index}.rpy" for index in range(33)]

    log_tool_call(
        tmp_path,
        "renforge_generate_translations",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files_touched": paths},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched_truncated"] is True
    assert entry["files_touched"] == paths[:32]
    assert "files_touched_file" not in entry
    assert (renforge / "activity-files").is_file()


def test_activity_log_overflow_spills_full_path_list_to_private_sidecar(
    tmp_path: Path,
) -> None:
    paths = ["game/" + ("d" * 400) + f"/file_{index}.rpy" for index in range(60)]

    log_tool_call(
        tmp_path,
        "renforge_generate_translations",
        {"project_path": str(tmp_path)},
        3.0,
        {"ok": True, "files_touched": paths},
    )

    raw = _read_activity(tmp_path)
    assert len(raw) <= 8192
    entry = json.loads(raw)
    assert entry["files_touched_truncated"] is True
    assert entry["files_touched"], "overflow must not empty files_touched"
    assert len(entry["files_touched"]) <= 32
    assert entry["files_touched_file"].startswith(".renforge/activity-files/")
    assert not entry["files_touched_file"].startswith("/")

    sidecar_path = tmp_path / entry["files_touched_file"]
    assert sidecar_path.is_file()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["files_touched"] == paths
    if os.name == "posix":
        assert stat.S_IMODE(sidecar_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(sidecar_path.parent.stat().st_mode) == 0o700


def test_activity_log_spills_an_oversized_deleted_list_too(tmp_path: Path) -> None:
    deleted = [f"game/deleted_{index}.rpy" for index in range(40)]

    log_tool_call(
        tmp_path,
        "renforge_stop",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files_touched": [], "files_deleted": deleted},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched_truncated"] is True
    assert len(entry["files_deleted"]) == 32
    assert entry["files_deleted_recorded"] is True
    sidecar = json.loads(
        (tmp_path / entry["files_touched_file"]).read_text(encoding="utf-8")
    )
    assert sidecar["files_deleted"] == deleted


def test_activity_log_sidecar_is_only_written_when_the_list_is_truncated(
    tmp_path: Path,
) -> None:
    paths = [f"game/file_{index}.rpy" for index in range(33)]
    log_tool_call(
        tmp_path,
        "renforge_web_build",
        {"project_path": str(tmp_path)},
        4.0,
        {"ok": True, "files_touched": paths},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched_truncated"] is True
    assert len(entry["files_touched"]) == 32
    sidecar_path = tmp_path / entry["files_touched_file"]
    assert sidecar_path.is_file()
    assert json.loads(sidecar_path.read_text(encoding="utf-8"))["files_touched"] == paths

    # A receipt that fits in 32 paths needs no sidecar at all.
    (tmp_path / ".renforge" / "activity.jsonl").unlink()
    sidecar_path.unlink()
    fits = [f"game/kept_{index}.rpy" for index in range(32)]
    log_tool_call(
        tmp_path,
        "renforge_web_build",
        {"project_path": str(tmp_path)},
        4.0,
        {"ok": True, "files_touched": fits},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched"] == fits
    assert "files_touched_file" not in entry
    assert entry["files_touched_truncated"] is False
    assert not any((tmp_path / ".renforge" / "activity-files").glob("*.json"))


def test_read_only_wrapper_is_not_journaled_even_when_the_result_has_writes(
    tmp_path: Path,
) -> None:
    from renforge.tool_registration.wrappers import _log_tool_call

    result = _log_tool_call(
        name="renforge_scan_project",
        params={"project_path": str(tmp_path)},
        project_root=str(tmp_path),
        fn=lambda: {"ok": True, "files_touched": ["game/read_only.rpy"]},
        args=(),
        kwargs={},
    )

    assert result["files_touched"] == ["game/read_only.rpy"]
    assert not (tmp_path / ".renforge" / "activity.jsonl").exists()


def test_mutating_wrapper_journals_the_explicit_receipt(tmp_path: Path) -> None:
    from renforge.tool_registration.wrappers import _log_tool_call

    result = _log_tool_call(
        name="renforge_capture_screenshot",
        params={"project_path": str(tmp_path), "name": "frame"},
        project_root=str(tmp_path),
        fn=lambda: {
            "ok": True,
            "files_touched": [".renforge/captures/frame.png"],
        },
        args=(),
        kwargs={},
    )

    entry = _read_entry(tmp_path)
    assert result["files_touched"] == [".renforge/captures/frame.png"]
    assert entry["files_touched"] == [".renforge/captures/frame.png"]
    assert entry["files_touched_recorded"] is True


def test_activity_log_redacts_compound_secret_keys(tmp_path: Path) -> None:
    log_tool_call(
        tmp_path,
        "renforge_wait_until",
        {
            "project_path": str(tmp_path),
            "expected_state": {
                "api_key": "SECRET-API",
                "authorization": "Bearer SECRET-AUTH",
                "access_token": "SECRET-TOKEN",
                "cookie": "SECRET-COOKIE",
                "apiKey": "SECRET-CAMEL-API",
                "accessToken": "SECRET-CAMEL-TOKEN",
                "privateKey": "SECRET-CAMEL-PRIVATE",
            },
        },
        1.25,
        {"ok": True},
    )

    raw = _read_activity(tmp_path).decode("utf-8")
    assert "SECRET-API" not in raw
    assert "SECRET-AUTH" not in raw
    assert "SECRET-TOKEN" not in raw
    assert "SECRET-COOKIE" not in raw
    assert "SECRET-CAMEL-API" not in raw
    assert "SECRET-CAMEL-TOKEN" not in raw
    assert "SECRET-CAMEL-PRIVATE" not in raw
    entry = json.loads(raw)
    redacted = entry["params"]["expected_state"]
    assert redacted["api_key"] == "[redacted]"
    assert redacted["authorization"] == "[redacted]"
    assert redacted["access_token"] == "[redacted]"
    assert redacted["cookie"] == "[redacted]"
    assert redacted["apiKey"] == "[redacted]"
    assert redacted["accessToken"] == "[redacted]"
    assert redacted["privateKey"] == "[redacted]"


def test_activity_log_bounds_result_keys_and_final_entry_size(tmp_path: Path) -> None:
    long_key = "k" * 10_000
    log_tool_call(
        tmp_path,
        "renforge_set_var",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, long_key: "value"},
    )

    raw = _read_activity(tmp_path)
    assert len(raw) <= 8192
    entry = json.loads(raw)
    logged_keys = entry["result"]["keys"]
    assert long_key not in logged_keys
    assert all(len(key) <= 512 + len("...[truncated]") for key in logged_keys)


def test_activity_log_does_not_follow_dir_swapped_after_validation(
    monkeypatch, tmp_path: Path
) -> None:
    from renforge import activity_log as logmod

    outside = tmp_path / "outside"
    outside.mkdir()
    real_ensure = logmod.ensure_nofollow_directory

    def swap_after_validation(path: Path) -> Path:
        result = real_ensure(path)
        swapped = Path(result)
        for child in swapped.iterdir():
            child.unlink()
        swapped.rmdir()
        swapped.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(logmod, "ensure_nofollow_directory", swap_after_validation)

    with pytest.raises((OSError, ValueError)):
        log_tool_call(tmp_path, "renforge_set_var", {"name": "x"}, 1.0, {"ok": True})

    assert list(outside.iterdir()) == []
    assert not (tmp_path / ".renforge" / "activity.jsonl").is_file()


def test_activity_log_does_not_follow_project_swapped_after_validation(
    monkeypatch, tmp_path: Path
) -> None:
    from renforge import activity_log as logmod

    project = tmp_path / "game"
    project.mkdir()
    outside = tmp_path / "outside"
    (outside / ".renforge").mkdir(parents=True)
    real_ensure = logmod.ensure_nofollow_directory

    def swap_project_after_validation(path: Path) -> Path:
        result = real_ensure(path)
        project.rename(tmp_path / "game-held")
        project.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(logmod, "ensure_nofollow_directory", swap_project_after_validation)

    with pytest.raises((OSError, ValueError)):
        log_tool_call(project, "renforge_set_var", {"name": "x"}, 1.0, {"ok": True})

    assert not (outside / ".renforge" / "activity.jsonl").exists()


def test_activity_log_prunes_old_sidecars(monkeypatch, tmp_path: Path) -> None:
    from renforge import activity_log as logmod
    from renforge.util.files import ensure_private_directory

    sidecar_dir = ensure_private_directory(tmp_path / ".renforge" / "activity-files")
    for stamp in (1, 2, 3):
        (sidecar_dir / f"{stamp}-{'a' * 16}.json").write_text("{}", encoding="utf-8")
    unrelated = sidecar_dir / "keep-me.txt"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(logmod, "_MAX_ACTIVITY_SIDECARS", 2)

    paths = [f"game/file_{index}.rpy" for index in range(33)]
    log_tool_call(
        tmp_path,
        "renforge_write",
        {"project_path": str(tmp_path)},
        1.0,
        {"ok": True, "files_touched": paths},
    )

    entry = _read_entry(tmp_path)
    assert entry["files_touched_truncated"] is True
    assert (tmp_path / entry["files_touched_file"]).is_file()

    remaining = sorted(path.name for path in sidecar_dir.iterdir())
    assert remaining == sorted(
        [f"3-{'a' * 16}.json", entry["files_touched_file"].rsplit("/", 1)[-1], unrelated.name]
    )


def test_activity_log_prunes_sidecars_over_the_byte_budget(monkeypatch, tmp_path: Path) -> None:
    from renforge import activity_log as logmod
    from renforge.util.files import ensure_private_directory

    sidecar_dir = ensure_private_directory(tmp_path / ".renforge" / "activity-files")
    for stamp in (1, 2, 3):
        (sidecar_dir / f"{stamp}-{'b' * 16}.json").write_bytes(b"x" * 10)
    monkeypatch.setattr(logmod, "_MAX_ACTIVITY_SIDECARS", 10)
    monkeypatch.setattr(logmod, "_MAX_ACTIVITY_SIDECAR_BYTES", 25)

    logmod._prune_files_sidecars(sidecar_dir)
    assert sorted(path.name for path in sidecar_dir.iterdir()) == [
        f"2-{'b' * 16}.json",
        f"3-{'b' * 16}.json",
    ]

    monkeypatch.setattr(logmod, "_MAX_ACTIVITY_SIDECAR_BYTES", 1)
    logmod._prune_files_sidecars(sidecar_dir)
    assert [path.name for path in sidecar_dir.iterdir()] == [f"3-{'b' * 16}.json"]
