from __future__ import annotations

from renforge.tools import live


def test_game_state_forwards_optional_include_to_bridge_client(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    calls = {}

    class FakeClient:
        def get_state(self, include=None):
            calls["include"] = include
            return {"ok": True, "metrics": {"fps": 60.0}}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.game_state(str(tmp_path), include=["metrics"])

    assert result["ok"] is True
    assert result["metrics"] == {"fps": 60.0}
    assert calls == {"include": ["metrics"]}
