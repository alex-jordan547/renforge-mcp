from __future__ import annotations

from renforge.tools import live


def test_save_slot_receipt_uses_the_bridge_savedir(tmp_path):
    class FakeClient:
        def save_slot(self, slot, extra_info=""):
            return {
                "ok": True,
                "slot": slot,
                "savedir": str(tmp_path / "saves"),
            }

    reply = live._save_slot_with_receipt(str(tmp_path), "slot1", "", FakeClient())

    assert reply["files_touched"] == [str(tmp_path / "saves")]
    assert reply["savedir"] == str(tmp_path / "saves")


def test_save_slot_failure_declares_no_writes(tmp_path):
    class FakeClient:
        def save_slot(self, slot, extra_info=""):
            return {"ok": False, "error": "save failed"}

    reply = live._save_slot_with_receipt(str(tmp_path), "slot1", "", FakeClient())

    assert reply["files_touched"] == []


def test_inspect_screen_forwards_name_to_bridge_client(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    calls = {}

    class FakeClient:
        def inspect_screen(self, name):
            calls["name"] = name
            return {"ok": True, "active": False, "name": name}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.inspect_screen(str(tmp_path), "custom")

    assert result == {"ok": True, "active": False, "name": "custom"}
    assert calls == {"name": "custom"}
