"""Application-layer isolation: MCP launches must not use user saves or HOME."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from renforge.save_isolation import (
    agent_session_id,
    apply_launch_isolation_defaults,
    classify_savedir,
    configured_isolation_mode,
    default_launch_savedir,
    host_renpy_root,
    import_host_slots,
    isolation_report,
    launch_exposes_user_saves,
    list_host_slots,
    parse_save_directory,
    path_exposes_user_saves,
    resolve_launch_isolation,
    resolve_save_isolation,
)


@pytest.fixture
def host_save_root(tmp_path: Path, monkeypatch) -> Path:
    """Keep native host-save discovery inside the test's synthetic home."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    appdata = home / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    if sys.platform == "win32":
        return appdata / "RenPy"
    if sys.platform == "darwin":
        return home / "Library" / "RenPy"
    return home / ".renpy"


def test_host_renpy_root_uses_native_location(
    tmp_path: Path, host_save_root: Path
) -> None:
    home = tmp_path / "home"
    assert host_renpy_root(home=home) == host_save_root
    assert host_renpy_root() == host_save_root


def _cleanup(isolation) -> None:
    root = isolation.session_root or isolation.savedir
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


def test_classify_requires_explicit_existing_opt_out() -> None:
    assert classify_savedir(None) == "temporary"
    assert classify_savedir("existing") == "existing"
    assert classify_savedir("default") == "existing"
    assert classify_savedir(" existing ") == "existing"
    assert classify_savedir("auto") == "temporary"


def test_classify_isolates_temporary_and_blank_tokens() -> None:
    assert classify_savedir("") == "temporary"
    assert classify_savedir("temporary") == "temporary"
    assert classify_savedir(" temporary ") == "temporary"


def test_classify_treats_other_values_as_explicit_paths() -> None:
    assert classify_savedir("/tmp/renforge-profile-saves") == "path"
    assert classify_savedir("~/renforge-saves") == "path"


def test_mcp_and_dashboard_default_to_temporary_isolation() -> None:
    assert default_launch_savedir(None) == "temporary"
    assert default_launch_savedir("") == "temporary"
    assert default_launch_savedir("auto") == "temporary"
    assert default_launch_savedir("   ") == "temporary"
    assert default_launch_savedir("existing") == "existing"
    assert default_launch_savedir("/opt/saves") == "/opt/saves"


def test_apply_defaults_isolate_saves_home_and_preferences() -> None:
    resolved = apply_launch_isolation_defaults()
    assert resolved == {
        "savedir": "temporary",
        "home": "temporary",
        "persistent": "empty",
        "preferences": "empty",
    }


def test_apply_defaults_honor_server_wide_existing_config() -> None:
    resolved = apply_launch_isolation_defaults(
        environ={"RENFORGE_ISOLATION": "existing"}
    )
    assert resolved == {
        "savedir": "existing",
        "home": "existing",
        "persistent": "existing",
        "preferences": "existing",
    }
    assert configured_isolation_mode({"RENFORGE_ISOLATION": "existing"}) == "existing"
    assert configured_isolation_mode({"RENFORGE_ISOLATION": "temporary"}) == "temporary"


def test_savedir_existing_couples_home_unless_home_is_explicit() -> None:
    coupled = apply_launch_isolation_defaults(savedir="existing")
    assert coupled["savedir"] == "existing"
    assert coupled["home"] == "existing"

    isolated_home = apply_launch_isolation_defaults(
        savedir="existing", home="temporary"
    )
    assert isolated_home["home"] == "temporary"

    env_existing = apply_launch_isolation_defaults(
        savedir="temporary",
        environ={"RENFORGE_ISOLATION": "existing"},
    )
    assert env_existing["savedir"] == "temporary"
    assert env_existing["home"] == "temporary"


def test_temporary_isolation_is_outside_user_save_canaries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    normal_saves = home / ".renpy" / "renforge-demo"
    normal_saves.mkdir(parents=True)
    canary = normal_saves / "normal_save_canary.save"
    canary.write_text("NORMAL-SAVE-CANARY\n", encoding="utf-8")
    prefs = home / ".renpy" / "persistent"
    prefs.write_text("HOST-PERSISTENT\n", encoding="utf-8")
    project_saves = tmp_path / "project" / "game" / "saves"
    project_saves.mkdir(parents=True)
    (project_saves / "slot1.save").write_text("PROJECT-SAVE\n", encoding="utf-8")

    isolation = resolve_save_isolation("temporary")
    try:
        assert isolation.mode == "temporary"
        assert isolation.savedir_mode == "temporary"
        assert isolation.home_mode == "temporary"
        assert isolation.cleanup is True
        assert isolation.savedir is not None
        assert isolation.home is not None
        assert isolation.session_root is not None
        savedir = isolation.savedir
        assert savedir.is_dir()
        assert (savedir / "multipersistent").is_dir()
        assert savedir != normal_saves
        assert not savedir.is_relative_to(home)
        assert not savedir.is_relative_to(tmp_path / "project")
        assert isolation.home.is_dir()
        assert isolation.home.is_relative_to(isolation.session_root)
        env = isolation.environ()
        assert env["RENFORGE_SAVEDIR"] == str(savedir)
        assert env["RENPY_PATH_TO_SAVES"] == str(savedir)
        assert env["RENPY_MULTIPERSISTENT"] == str(savedir / "multipersistent")
        assert env["HOME"] == str(isolation.home)
        assert env["XDG_CONFIG_HOME"] == str(isolation.home / ".config")
        assert env["RENFORGE_SAVEDIR"] != str(normal_saves)
        assert isolation.command_args() == ["--savedir", str(savedir)]
        assert canary.read_text(encoding="utf-8") == "NORMAL-SAVE-CANARY\n"
        assert prefs.read_text(encoding="utf-8") == "HOST-PERSISTENT\n"
        assert (project_saves / "slot1.save").read_text(encoding="utf-8") == "PROJECT-SAVE\n"
    finally:
        _cleanup(isolation)


def test_existing_mode_does_not_redirect_saves_or_home() -> None:
    isolation = resolve_save_isolation("existing")
    assert isolation.mode == "existing"
    assert isolation.home_mode == "existing"
    assert isolation.savedir is None
    assert isolation.home is None
    assert isolation.session_root is None
    assert isolation.cleanup is False
    assert isolation.command_args() == []
    assert isolation.environ() == {}


def test_explicit_path_is_created_and_not_removed_on_stop(tmp_path: Path) -> None:
    target = tmp_path / "profile" / "primary-saves"
    isolation = resolve_save_isolation(str(target), cleanup_on_stop=True)
    try:
        assert isolation.mode == "path"
        assert isolation.savedir == target.resolve()
        assert isolation.home_mode == "temporary"
        assert isolation.home is not None
        assert isolation.cleanup is True
        assert (target / "multipersistent").is_dir()
        assert isolation.command_args() == ["--savedir", str(target.resolve())]
    finally:
        _cleanup(isolation)
        assert target.is_dir()
        shutil.rmtree(target, ignore_errors=True)


def test_isolated_env_rewrites_home_and_preserves_xauthority(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "real-home"
    home.mkdir()
    xauth = home / ".Xauthority"
    xauth.write_bytes(b"host-xauth")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XAUTHORITY", raising=False)
    isolation = resolve_launch_isolation(
        "temporary", persistent="empty", preferences="empty", host_env=os.environ
    )
    try:
        env = isolation.environ(host_env=os.environ)
        assert env["HOME"] == str(isolation.home)
        assert isolation.home is not None
        assert Path(env["HOME"]) != home
        assert env["XAUTHORITY"] == str(xauth.resolve())
        assert os.environ["HOME"] == str(home)
        assert isolation.savedir is not None
        assert Path(env["RENFORGE_SAVEDIR"]) != home / ".renpy"
        assert isolation.persistent_mode == "empty"
        assert isolation.preferences_mode == "empty"
    finally:
        _cleanup(isolation)


def test_explicit_home_existing_keeps_host_home_with_isolated_saves() -> None:
    isolation = resolve_launch_isolation("temporary", home="existing")
    try:
        assert isolation.savedir_mode == "temporary"
        assert isolation.home_mode == "existing"
        assert isolation.savedir is not None
        assert isolation.home is None
        assert "HOME" not in isolation.environ()
    finally:
        _cleanup(isolation)


def test_session_init_payload_installs_storage_before_persistent_load() -> None:
    import textwrap

    from renforge.bridge.artifacts import session_init_payload

    text = session_init_payload().decode("utf-8")
    assert text.startswith("python early:\n")
    compile(textwrap.dedent(text.split("\n", 1)[1]), "session_init", "exec")
    assert "_renforge_engine.savelocation.init = _renforge_init_save_location" in text
    assert "unlink" not in text


def test_low_level_defaults_match_mcp_defaults(monkeypatch) -> None:
    monkeypatch.delenv("RENFORGE_ISOLATION", raising=False)
    isolation = resolve_launch_isolation()
    try:
        assert isolation.savedir_mode == "temporary"
        assert isolation.home_mode == "temporary"
        assert isolation.persistent_mode == "empty"
        assert isolation.preferences_mode == "empty"
    finally:
        _cleanup(isolation)


def test_existing_opt_out_keeps_all_state() -> None:
    assert apply_launch_isolation_defaults(savedir="existing") == {
        "savedir": "existing", "home": "existing",
        "persistent": "existing", "preferences": "existing",
    }


@pytest.mark.parametrize("field", ["persistent", "preferences"])
def test_empty_state_never_deletes_reused_persistent(tmp_path: Path, field: str) -> None:
    persistent = tmp_path / "persistent"
    persistent.write_bytes(b"KEEP-STATE")
    with pytest.raises(ValueError, match="requires savedir='temporary'"):
        resolve_launch_isolation(str(tmp_path), **{field: "empty"})
    assert persistent.read_bytes() == b"KEEP-STATE"


def test_isolation_report_and_session_id(tmp_path: Path) -> None:
    isolation = resolve_launch_isolation("temporary")
    try:
        report = isolation_report(isolation, session_id="sess_abc")
        assert report["session_id"] == "sess_abc"
        payload = report["isolation"]
        assert payload["savedir_mode"] == "temporary"
        assert payload["home_mode"] == "temporary"
        assert payload["exposes_user_saves"] is False
        assert payload["savedir"] == str(isolation.savedir)
        assert payload["home"] == str(isolation.home)
        assert agent_session_id("deadbeef") == "sess_deadbeef"
        assert agent_session_id("sess_deadbeef") == "sess_deadbeef"
    finally:
        _cleanup(isolation)

    existing = resolve_launch_isolation("existing")
    assert existing.exposes_user_saves is True
    assert isolation_report(existing, session_id="sess_x")["isolation"]["exposes_user_saves"] is True


def test_launch_exposes_user_saves_classifies_existing_and_host_paths(
    tmp_path: Path, monkeypatch, host_save_root: Path
) -> None:
    monkeypatch.delenv("RENFORGE_ISOLATION", raising=False)
    assert launch_exposes_user_saves() is False
    assert launch_exposes_user_saves(savedir="temporary") is False
    assert launch_exposes_user_saves(savedir="existing") is True
    assert launch_exposes_user_saves(savedir="auto", home="existing") is True
    host = tmp_path / "home"
    monkeypatch.setenv("HOME", str(host))
    user_saves = host_save_root / "renforge-demo"
    user_saves.mkdir(parents=True)
    assert path_exposes_user_saves(user_saves, home=host) is True
    assert launch_exposes_user_saves(savedir=str(user_saves), home="temporary") is True
    other = tmp_path / "scratch-saves"
    other.mkdir()
    assert launch_exposes_user_saves(savedir=str(other), home="temporary") is False


def test_parse_save_directory_and_list_host_slots(
    tmp_path: Path, monkeypatch, host_save_root: Path
) -> None:
    project = tmp_path / "game-root"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    (game / "options.rpy").write_text(
        'define config.save_directory = "renforge-demo"\n', encoding="utf-8"
    )
    host = tmp_path / "home"
    host_saves = host_save_root / "renforge-demo"
    host_saves.mkdir(parents=True)
    (host_saves / "1-1-LT1.save").write_bytes(b"SLOT-1-1")
    (host_saves / "1-1-LT1.save.json").write_text(
        '{"_save_name": "after menu"}', encoding="utf-8"
    )
    (host_saves / "branch-a-LT1.save").write_bytes(b"SLOT-BRANCH")
    project_saves = game / "saves"
    project_saves.mkdir()
    (project_saves / "local-LT1.save").write_bytes(b"LOCAL")
    monkeypatch.setenv("HOME", str(host))

    assert parse_save_directory(project) == "renforge-demo"
    listed = list_host_slots(project, home=host)
    assert listed["ok"] is True
    names = [item["name"] for item in listed["slots"]]
    assert names == ["1-1", "branch-a", "local"]
    by_name = {item["name"]: item for item in listed["slots"]}
    assert by_name["1-1"]["extra_info"] == "after menu"
    assert str(host_saves) in listed["directories"]

    filtered = list_host_slots(project, regexp="branch", home=host)
    assert [item["name"] for item in filtered["slots"]] == ["branch-a"]


def test_import_copies_selected_slots_and_refuses_user_tree(
    tmp_path: Path, monkeypatch, host_save_root: Path
) -> None:
    project = tmp_path / "game-root"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    (game / "options.rpy").write_text(
        'define config.save_directory = "renforge-demo"\n', encoding="utf-8"
    )
    host = tmp_path / "home"
    host_saves = host_save_root / "renforge-demo"
    host_saves.mkdir(parents=True)
    (host_saves / "1-1-LT1.save").write_bytes(b"SLOT-1-1")
    (host_saves / "1-1-LT1.save.json").write_text(
        '{"_save_name": "after menu"}', encoding="utf-8"
    )
    (host_saves / "branch-a-LT1.save").write_bytes(b"SLOT-BRANCH")
    dest = tmp_path / "session" / "saves"
    dest.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(host))

    copied = import_host_slots(project, dest, slots=["1-1"], home=host)
    assert copied["ok"] is True
    assert (dest / "1-1-LT1.save").read_bytes() == b"SLOT-1-1"
    assert (dest / "1-1-LT1.save.json").is_file()
    assert not (dest / "branch-a-LT1.save").exists()
    assert (host_saves / "1-1-LT1.save").read_bytes() == b"SLOT-1-1"

    refused = import_host_slots(project, host_saves, slot="1-1", home=host)
    assert refused["ok"] is False
    assert refused["code"] == "SAVE_IMPORT_NOT_ISOLATED"

    missing = import_host_slots(project, dest, slot="nope", home=host)
    assert missing["ok"] is False
    assert "no matching" in missing["error"]


def test_live_saves_list_user_and_import(
    tmp_path: Path, monkeypatch, host_save_root: Path
) -> None:
    from types import SimpleNamespace

    from renforge.tools import live

    project = tmp_path / "game-root"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    (game / "options.rpy").write_text(
        'define config.save_directory = "renforge-demo"\n', encoding="utf-8"
    )
    host = tmp_path / "home"
    host_saves = host_save_root / "renforge-demo"
    host_saves.mkdir(parents=True)
    (host_saves / "1-1-LT1.save").write_bytes(b"SLOT-1-1")
    dest = tmp_path / "session" / "saves"
    dest.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(host))

    listed = live.saves(str(project), "list_user")
    assert listed["ok"] is True
    assert [item["name"] for item in listed["slots"]] == ["1-1"]

    refused = live.saves(str(project), "import", slot="1-1")
    assert refused["ok"] is False
    assert refused["code"] == "NO_ISOLATED_SESSION"

    isolation = SimpleNamespace(savedir_mode="temporary", home_mode="temporary")
    session = SimpleNamespace(temporary_savedir=dest, isolation=isolation)
    live._SESSIONS[live._key(project)] = session  # type: ignore[assignment]
    try:
        imported = live.saves(str(project), "import", slot="1-1")
        assert imported["ok"] is True
        assert (dest / "1-1-LT1.save").read_bytes() == b"SLOT-1-1"
        existing = SimpleNamespace(
            temporary_savedir=host_saves,
            isolation=SimpleNamespace(savedir_mode="existing"),
        )
        live._SESSIONS[live._key(project)] = existing  # type: ignore[assignment]
        blocked = live.saves(str(project), "import", slot="1-1")
        assert blocked["ok"] is False
        assert blocked["code"] == "SAVE_IMPORT_NOT_ISOLATED"
    finally:
        live._SESSIONS.pop(live._key(project), None)
