from __future__ import annotations

from pathlib import Path

from renforge.bridge.client import BridgeProtocolError
from renforge.editor_demo_runner import _ed_do_save

from renforge.editor_task0_runner import (
    EDITOR_RESOURCE,
    FIXTURE_RESOURCE,
    inject_editor_task0_resources,
)


def test_task0_runner_resources_are_real_files() -> None:
    assert EDITOR_RESOURCE.is_file(), EDITOR_RESOURCE
    assert FIXTURE_RESOURCE.is_file(), FIXTURE_RESOURCE


def test_task0_runner_injects_editor_and_fixture(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / "game").mkdir(parents=True)

    copied = inject_editor_task0_resources(project)

    editor = Path(copied["editor"])
    fixture = Path(copied["fixture"])
    assert editor.is_file()
    assert fixture.is_file()
    assert editor.read_text(encoding="utf-8")
    assert fixture.read_text(encoding="utf-8")


def test_task0_runner_accepts_custom_fixture_path(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / "game").mkdir(parents=True)
    custom_fixture = tmp_path / "renforge_editor_task0_custom_fixture.rpy"
    custom_fixture.write_text(
        "screen renforge_editor_task0_fixture():\n    text \"custom\"\n",
        encoding="utf-8",
    )

    copied = inject_editor_task0_resources(project, fixture_path=custom_fixture)
    assert Path(copied["fixture"]).is_file()


def test_demo_save_tolerates_bridge_reload_disconnect() -> None:
    class ReloadingClient:
        def __init__(self) -> None:
            self.status_calls = 0

        def request(self, command: str) -> dict:
            assert command == "editor_task0_status"
            self.status_calls += 1
            if self.status_calls == 1:
                return {"ok": True, "save_enabled": True}
            if self.status_calls == 2:
                raise BridgeProtocolError("bridge response was empty")
            return {
                "ok": True,
                "save_in_progress": False,
                "status_text": "Reload committed",
            }

        def click_element(self, **kwargs) -> dict:
            assert kwargs == {"id": "rf_save", "screen": "_renforge_editor_overlay"}
            return {"ok": True}

    reply = _ed_do_save(ReloadingClient(), timeout=1.0)

    assert reply["status_text"] == "Reload committed"
