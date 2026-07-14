from __future__ import annotations

from renforge.state_compact import apply_serialization_limits, compact_state, normalize_state_profile


def test_normalize_state_profile_defaults_and_validates():
    assert normalize_state_profile(None) == "interaction"
    assert normalize_state_profile("FULL") == "full"
    assert normalize_state_profile("nope")["ok"] is False


def test_compact_state_interaction_drops_bulk_store():
    state = {
        "current_label": "start",
        "menu": False,
        "showing_tags": ["bg"],
        "variables": {
            "score": 1,
            "player_name": "Rin",
            "config.skipping": None,
            "huge_list": list(range(200)),
        },
    }
    result = compact_state(state, profile="interaction", include=["config.skipping"])
    assert result["current_label"] == "start"
    assert "huge_list" not in (result.get("variables") or {})
    assert result.get("variables", {}).get("config.skipping") is None
    assert result.get("config.skipping") is None


def test_compact_state_minimal_is_tiny():
    state = {
        "current_label": "show_thought",
        "menu": True,
        "showing_tags": ["bg", "eileen"],
        "variables": {f"var_{i}": i for i in range(500)},
    }
    result = compact_state(state, profile="minimal")
    assert result["current_label"] == "show_thought"
    assert "variables" not in result or len(result.get("variables", {})) == 0


def test_apply_serialization_limits_marks_truncation():
    payload = {"items": list(range(100))}
    limited = apply_serialization_limits(payload, max_items=10, max_depth=3, max_output_bytes=8192)
    assert limited["items"]["__truncated__"] is True
    assert limited["items"]["__total_items__"] == 100
