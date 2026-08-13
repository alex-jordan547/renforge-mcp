from __future__ import annotations

import json
import stat
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from renforge import dump as dump_mod


class _FakeNode:
    def __init__(self, name: object, filename: str, linenumber: int) -> None:
        self.name = name
        self.filename = filename
        self.linenumber = linenumber

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return self.name == getattr(other, "name", other)


def _install_fake_renpy(namemap: dict[object, _FakeNode]) -> dict[str, object]:
    captured: dict[str, object] = {}
    renpy = types.ModuleType("renpy")
    dump_module = types.ModuleType("renpy.dump")
    renpy.game = SimpleNamespace(script=SimpleNamespace(namemap=namemap))

    def dump_fn(error: object) -> None:
        labels: dict[str, list[object]] = {}
        for name, node in renpy.game.script.namemap.items():
            if not isinstance(name, str):
                continue
            labels[name] = [node.filename, node.linenumber]
        captured["labels"] = labels
        captured["error"] = error
        captured["namemap_during_dump"] = renpy.game.script.namemap

    dump_module.dump = dump_fn
    sys.modules["renpy"] = renpy
    sys.modules["renpy.dump"] = dump_module
    captured["renpy"] = renpy
    return captured


def _exec_adapter() -> None:
    exec(dump_mod.JSON_DUMP_ADAPTER_SOURCE, {"__file__": dump_mod.JSON_DUMP_ADAPTER_FILENAME})


@pytest.fixture
def restore_renpy_modules():
    previous = {name: sys.modules.get(name) for name in ("renpy", "renpy.dump")}
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_adapter_unwraps_node_keyed_namemap(restore_renpy_modules) -> None:
    start = _FakeNode("start", "game/script.rpy", 10)
    jump = _FakeNode(("game/script.rpy", 1, 2), "game/script.rpy", 20)
    namemap = {start: start, jump: jump}
    captured = _install_fake_renpy(namemap)

    _exec_adapter()
    sys.modules["renpy.dump"].dump(False)

    assert captured["labels"] == {"start": ["game/script.rpy", 10]}
    assert captured["error"] is False
    assert sys.modules["renpy"].game.script.namemap is namemap


def test_adapter_leaves_string_keyed_namemap_unchanged(restore_renpy_modules) -> None:
    start = _FakeNode("start", "game/script.rpy", 10)
    namemap = {"start": start}
    captured = _install_fake_renpy(namemap)

    _exec_adapter()
    sys.modules["renpy.dump"].dump(False)

    assert captured["labels"] == {"start": ["game/script.rpy", 10]}
    assert captured["namemap_during_dump"] is namemap
    assert sys.modules["renpy"].game.script.namemap is namemap


def test_adapter_still_normalizes_when_dump_already_unwraps(restore_renpy_modules) -> None:
    """Upstream dump.py may unwrap Node keys; namemap itself stays Node-keyed."""
    start = _FakeNode("start", "game/script.rpy", 10)
    namemap = {start: start}
    captured: dict[str, object] = {}
    renpy = types.ModuleType("renpy")
    dump_module = types.ModuleType("renpy.dump")
    renpy.game = SimpleNamespace(script=SimpleNamespace(namemap=namemap))

    def dump_fn(error: object) -> None:
        labels: dict[str, list[object]] = {}
        for name, node in renpy.game.script.namemap.items():
            if not isinstance(name, str):
                name = getattr(name, "name", name)
            if isinstance(name, str):
                labels[name] = [node.filename, node.linenumber]
        captured["labels"] = labels
        captured["namemap_during_dump"] = renpy.game.script.namemap
        captured["error"] = error

    dump_module.dump = dump_fn
    sys.modules["renpy"] = renpy
    sys.modules["renpy.dump"] = dump_module

    _exec_adapter()
    sys.modules["renpy.dump"].dump(False)

    assert captured["labels"] == {"start": ["game/script.rpy", 10]}
    assert captured["namemap_during_dump"] is not namemap
    assert sys.modules["renpy"].game.script.namemap is namemap


def test_adapter_follows_upstream_node_attribute_unwrap(restore_renpy_modules) -> None:
    """Match upstream dump.py: `if isinstance(name, Node): name = name.name`."""
    village = _FakeNode("village_gate", "game/script.rpy", 42)
    captured = _install_fake_renpy({village: village})

    _exec_adapter()
    sys.modules["renpy.dump"].dump(True)

    assert captured["labels"] == {"village_gate": ["game/script.rpy", 42]}
    assert captured["error"] is True


def test_adapter_install_is_idempotent(restore_renpy_modules) -> None:
    start = _FakeNode("start", "game/script.rpy", 1)
    captured = _install_fake_renpy({start: start})

    _exec_adapter()
    first = sys.modules["renpy.dump"].dump
    _exec_adapter()
    second = sys.modules["renpy.dump"].dump

    assert first is second
    second(False)
    assert captured["labels"] == {"start": ["game/script.rpy", 1]}


def _fake_project() -> SimpleNamespace:
    return SimpleNamespace(
        renpy_command=lambda _sdk, args: ["renpy", "/tmp/project", *args],
    )


def test_run_native_dump_injects_adapter_without_touching_sdk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sdk_root = tmp_path / "sdk"
    dump_path = sdk_root / "renpy" / "dump.py"
    dump_path.parent.mkdir(parents=True)
    original = "original dump.py\n"
    dump_path.write_text(original, encoding="utf-8")
    dump_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    sdk = SimpleNamespace(root=sdk_root)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        env = kwargs["env"]
        searchpath = env["RENPY_SEARCHPATH"]
        adapter_dir = Path(searchpath.split("::", 1)[0])
        adapter = adapter_dir / dump_mod.JSON_DUMP_ADAPTER_FILENAME
        seen["adapter"] = adapter
        seen["adapter_dir"] = adapter_dir
        seen["searchpath"] = searchpath
        assert adapter.is_file()
        text = adapter.read_text(encoding="utf-8")
        assert "renforge json-dump adapter" in text
        assert "unwrap" in text
        out_path = Path(command[command.index("--json-dump") + 1])
        payload = {"location": {"label": {"start": ["game/script.rpy", 1]}}}
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0, timed_out=False, stdout="", stderr="")

    monkeypatch.setattr(dump_mod, "run_command", fake_run)

    raw = dump_mod.run_native_dump(sdk, _fake_project())

    assert raw["location"]["label"]["start"] == ["game/script.rpy", 1]
    assert dump_path.read_text(encoding="utf-8") == original
    assert not seen["adapter_dir"].exists()


def test_run_native_dump_prepends_adapter_to_existing_searchpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENPY_SEARCHPATH", "/already/there")
    seen: dict[str, str] = {}

    def fake_run(command, **kwargs):
        searchpath = kwargs["env"]["RENPY_SEARCHPATH"]
        first, rest = searchpath.split("::", 1)
        seen["first"] = first
        seen["rest"] = rest
        assert (Path(first) / dump_mod.JSON_DUMP_ADAPTER_FILENAME).is_file()
        out_path = Path(command[command.index("--json-dump") + 1])
        out_path.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, timed_out=False, stdout="", stderr="")

    monkeypatch.setattr(dump_mod, "run_command", fake_run)

    dump_mod.run_native_dump(SimpleNamespace(), _fake_project())

    assert seen["rest"] == "/already/there"
    assert "renforge-dump-adapter-" in Path(seen["first"]).name


def test_run_native_dump_is_isolated_across_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_dirs: list[Path] = []

    def fake_run(command, **kwargs):
        adapter_dir = Path(kwargs["env"]["RENPY_SEARCHPATH"].split("::", 1)[0])
        adapter_dirs.append(adapter_dir)
        out_path = Path(command[command.index("--json-dump") + 1])
        out_path.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, timed_out=False, stdout="", stderr="")

    monkeypatch.setattr(dump_mod, "run_command", fake_run)
    dump_mod.run_native_dump(SimpleNamespace(), _fake_project())
    dump_mod.run_native_dump(SimpleNamespace(), _fake_project())

    assert len(adapter_dirs) == 2
    assert adapter_dirs[0] != adapter_dirs[1]


def test_normalize_definitions_flattens_label_locations() -> None:
    definitions = dump_mod.normalize_definitions(
        {
            "location": {
                "label": {"start": ["game/script.rpy", 12]},
                "screen": {"hud": ["game/screens.rpy", 4]},
            }
        }
    )
    assert definitions == [
        {"name": "start", "kind": "label", "file": "game/script.rpy", "line": 12},
        {"name": "hud", "kind": "screen", "file": "game/screens.rpy", "line": 4},
    ]
