from __future__ import annotations

from renforge.tools import live


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
