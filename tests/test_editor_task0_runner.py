from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from renforge.bridge.client import BridgeProtocolError
from renforge.editor_demo_runner import _ed_do_save
from renforge.editor_runner_status import is_reload_committed

from renforge.editor_task0_runner import (
    EDITOR_RESOURCE,
    FIXTURE_RESOURCE,
    _purple_border_visible,
    _save_has_started,
    inject_editor_task0_resources,
)


def test_task0_runner_resources_are_real_files() -> None:
    assert EDITOR_RESOURCE.is_file(), EDITOR_RESOURCE
    assert FIXTURE_RESOURCE.is_file(), FIXTURE_RESOURCE


def test_save_has_started_uses_structured_state_before_transaction_assignment() -> None:
    status = {
        "save_in_progress": True,
        "save_requested": True,
        "status_code": "saving",
        "status_text": "Enregistrement",
        "save_button_state": "saving",
        "pending_transaction_id": None,
    }

    assert _save_has_started(status)
    assert not _save_has_started({**status, "save_in_progress": False})
    assert not _save_has_started({**status, "save_requested": False})
    assert not _save_has_started({**status, "status_code": "reload_committed"})
    assert not _save_has_started({**status, "save_button_state": "idle"})


def test_purple_border_detection_scales_logical_coordinates() -> None:
    image = Image.new("RGB", (2560, 1440), "#101010")
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 200, 598, 398), outline="#a78bfa", width=4)

    assert _purple_border_visible(image, [100, 100, 200, 100])


def test_purple_border_detection_rejects_plain_perimeter() -> None:
    image = Image.new("RGB", (1280, 720), "#101010")

    assert not _purple_border_visible(image, [100, 100, 200, 100])


def test_task0_runner_injects_editor_and_fixture(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / "game").mkdir(parents=True)

    copied = inject_editor_task0_resources(project)

    editor = Path(copied["editor"])
    fixture = Path(copied["fixture"])
    stress = Path(copied["stress"])
    assert editor.is_file()
    assert fixture.is_file()
    assert stress.is_file()
    assert editor.read_text(encoding="utf-8")
    assert fixture.read_text(encoding="utf-8")
    assert stress.read_text(encoding="utf-8")


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
                "status_code": "reload_committed", "status_text": "Reload committed",
            }

        def click_element(self, **kwargs) -> dict:
            assert kwargs == {"id": "rf_save", "screen": "_renforge_editor_overlay"}
            return {"ok": True}

    reply = _ed_do_save(ReloadingClient(), timeout=1.0)

    assert is_reload_committed(reply)
    assert reply.get("status_code") == "reload_committed"


def test_reload_committed_status_supports_exact_and_minimum_generations() -> None:
    status = {
        "save_in_progress": False,
        "status_code": "reload_committed",
        "script_generation": 4,
    }

    assert is_reload_committed(status)
    assert is_reload_committed(status, generation=4)
    assert not is_reload_committed(status, generation=5)
    assert is_reload_committed(status, minimum_generation=3)
    assert not is_reload_committed(status, minimum_generation=5)
