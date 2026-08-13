from __future__ import annotations

import json
from pathlib import Path

import pytest

from renforge.activity_log import log_tool_call


def _read_activity(project: Path) -> bytes:
    return (project / ".renforge" / "activity.jsonl").read_bytes()


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
