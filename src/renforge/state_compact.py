"""Compact live-state profiles and serialization limits for agent-facing tools.

Profiles keep default MCP responses small while still allowing an explicit
full store when an agent opts in. Truncation is always marked so callers can
tell when data was dropped.
"""

from __future__ import annotations

import json
from typing import Any

STATE_PROFILES = ("minimal", "interaction", "debug", "full")

# Fields always useful for interaction-level automation.
_INTERACTION_FIELDS = (
    "current_label",
    "showing_tags",
    "menu",
    "dialogue",
    "screens",
    "skipping",
    "auto",
)

# Store keys commonly needed when validating Skip/Auto/quick-menu behaviour.
_INTERACTION_VARIABLE_HINTS = (
    "config.skipping",
    "_preferences.skip_after_choices",
    "_preferences.skip_unseen",
    "_preferences.afm_enable",
    "skip_delay",
)

_DEFAULT_MAX_DEPTH = 3
_DEFAULT_MAX_ITEMS = 50
_DEFAULT_MAX_OUTPUT_BYTES = 8192


def normalize_state_profile(profile: str | None, *, default: str = "interaction") -> str | dict:
    """Return a valid profile name, or an error payload when the value is bad."""
    if profile is None or profile == "":
        return default
    if not isinstance(profile, str):
        return {"ok": False, "error": "state_profile must be a string"}
    value = profile.strip().casefold()
    if value not in STATE_PROFILES:
        return {
            "ok": False,
            "error": "state_profile must be one of: %s" % ", ".join(STATE_PROFILES),
        }
    return value


def _limit_value(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    max_items: int,
    truncated: list[bool],
) -> Any:
    if depth >= max_depth:
        if isinstance(value, (dict, list, tuple)):
            truncated[0] = True
            return {"__truncated__": True, "__reason__": "max_depth"}
        return value

    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > max_items:
            truncated[0] = True
            limited = {
                str(key): _limit_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    truncated=truncated,
                )
                for key, item in items[:max_items]
            }
            limited["__truncated__"] = True
            limited["__total_items__"] = len(items)
            return limited
        return {
            str(key): _limit_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                truncated=truncated,
            )
            for key, item in items
        }

    if isinstance(value, (list, tuple)):
        sequence = list(value)
        if len(sequence) > max_items:
            truncated[0] = True
            limited = [
                _limit_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    truncated=truncated,
                )
                for item in sequence[:max_items]
            ]
            return {
                "__truncated__": True,
                "__total_items__": len(sequence),
                "items": limited,
            }
        return [
            _limit_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                truncated=truncated,
            )
            for item in sequence
        ]

    if isinstance(value, str) and len(value) > 500:
        truncated[0] = True
        return value[:500] + "…"
    return value


def apply_serialization_limits(
    value: Any,
    *,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_items: int = _DEFAULT_MAX_ITEMS,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> Any:
    """Bound nested structures and overall JSON size; mark truncations."""
    truncated = [False]
    limited = _limit_value(
        value,
        depth=0,
        max_depth=max(0, int(max_depth)),
        max_items=max(1, int(max_items)),
        truncated=truncated,
    )

    try:
        encoded = json.dumps(limited, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"__truncated__": True, "__reason__": "not_serializable"}

    budget = max(64, int(max_output_bytes))
    if len(encoded.encode("utf-8")) <= budget:
        return limited

    # Last resort: drop large nested payloads while keeping top-level keys.
    if isinstance(limited, dict):
        compact: dict[str, Any] = {"__truncated__": True, "__reason__": "max_output_bytes"}
        for key, item in limited.items():
            if key.startswith("__"):
                continue
            if isinstance(item, (dict, list)):
                compact[key] = {"__truncated__": True, "__reason__": "max_output_bytes"}
            else:
                compact[key] = item
            probe = json.dumps(compact, ensure_ascii=False, default=str)
            if len(probe.encode("utf-8")) > budget:
                compact.pop(key, None)
                break
        return compact

    return {"__truncated__": True, "__reason__": "max_output_bytes", "__total_items__": 1}


def _dig_variable(variables: dict[str, Any], path: str) -> Any:
    """Resolve ``name`` or ``obj.attr`` from a flat/nested variables map."""
    if path in variables:
        return variables[path]
    # Prefer exact dotted keys first, then nested walk.
    parts = path.split(".")
    current: Any = variables
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _select_variables(
    variables: dict[str, Any] | None,
    *,
    profile: str,
    include: list[str] | None,
) -> dict[str, Any] | None:
    if not isinstance(variables, dict):
        return None
    if profile == "full":
        return dict(variables)

    selected: dict[str, Any] = {}
    wanted: list[str] = []
    if profile in ("interaction", "debug"):
        wanted.extend(_INTERACTION_VARIABLE_HINTS)
    if include:
        for name in include:
            if not isinstance(name, str) or not name.strip():
                continue
            # Skip structural field names already handled at the state root.
            if name in _INTERACTION_FIELDS or name in ("metrics", "audio", "variables"):
                continue
            wanted.append(name.strip())

    for path in wanted:
        if path in selected:
            continue
        if path in variables:
            selected[path] = variables[path]
            continue
        # Nested walk for dotted paths when the store is nested.
        value = _dig_variable(variables, path)
        if value is not None or path in variables:
            selected[path] = value

    if profile == "debug" and not selected and variables:
        # Debug without explicit include still returns a bounded sample.
        for index, (key, value) in enumerate(variables.items()):
            if index >= 20:
                selected["__truncated__"] = True
                selected["__total_items__"] = len(variables)
                break
            selected[key] = value
    return selected


def compact_state(
    state: dict[str, Any] | None,
    *,
    profile: str = "interaction",
    include: list[str] | tuple[str, ...] | None = None,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_items: int = _DEFAULT_MAX_ITEMS,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Return a profile-filtered, size-limited copy of a bridge state object."""
    if not isinstance(state, dict):
        return {}

    include_list = list(include) if include else []
    profile = profile if profile in STATE_PROFILES else "interaction"

    if profile == "full" and not include_list:
        limited = apply_serialization_limits(
            state,
            max_depth=max_depth,
            max_items=max_items,
            max_output_bytes=max_output_bytes,
        )
        return limited if isinstance(limited, dict) else {"value": limited}

    result: dict[str, Any] = {}
    for key in ("current_label", "menu", "showing_tags", "dialogue", "screens", "skipping", "auto"):
        if key in state:
            result[key] = state[key]

    # Always honour explicit structural includes from the original state.
    for name in include_list:
        if name in state and name not in result:
            result[name] = state[name]

    variables = state.get("variables")
    if profile == "minimal":
        # Minimal keeps only the interaction skeleton plus explicitly included vars.
        selected = _select_variables(variables if isinstance(variables, dict) else None, profile="minimal", include=include_list)
        if selected:
            result["variables"] = selected
    elif profile in ("interaction", "debug"):
        selected = _select_variables(
            variables if isinstance(variables, dict) else None,
            profile=profile,
            include=include_list,
        )
        if selected:
            result["variables"] = selected
        # Promote a few interaction hints to the root when present.
        if isinstance(selected, dict):
            for key in ("config.skipping", "_preferences.afm_enable"):
                if key in selected and key not in result:
                    result[key] = selected[key]
    else:  # full
        if isinstance(variables, dict):
            result["variables"] = dict(variables)
        for key, value in state.items():
            if key not in result:
                result[key] = value

    # Optional sections requested via include.
    for section in ("metrics", "audio"):
        if section in include_list and section in state:
            result[section] = state[section]

    limited = apply_serialization_limits(
        result,
        max_depth=max_depth,
        max_items=max_items,
        max_output_bytes=max_output_bytes,
    )
    return limited if isinstance(limited, dict) else {"value": limited}


def validate_limit_args(
    *,
    max_depth: Any = _DEFAULT_MAX_DEPTH,
    max_items: Any = _DEFAULT_MAX_ITEMS,
    max_output_bytes: Any = _DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[int, int, int] | dict[str, Any]:
    """Validate serialization limit arguments; return limits or an error dict."""
    for name, value, minimum in (
        ("max_depth", max_depth, 0),
        ("max_items", max_items, 1),
        ("max_output_bytes", max_output_bytes, 64),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"ok": False, "error": "%s must be a positive number" % name}
        if not float(value).is_integer() if isinstance(value, float) else False:
            # floats that are whole numbers are accepted after int()
            if float(value) != int(value):
                return {"ok": False, "error": "%s must be an integer" % name}
        number = int(value)
        if number < minimum:
            return {"ok": False, "error": "%s must be >= %s" % (name, minimum)}
        if name == "max_depth" and number > 20:
            return {"ok": False, "error": "max_depth must be <= 20"}
        if name == "max_items" and number > 10_000:
            return {"ok": False, "error": "max_items must be <= 10000"}
        if name == "max_output_bytes" and number > 2_000_000:
            return {"ok": False, "error": "max_output_bytes must be <= 2000000"}

    return int(max_depth), int(max_items), int(max_output_bytes)


__all__ = [
    "STATE_PROFILES",
    "apply_serialization_limits",
    "compact_state",
    "normalize_state_profile",
    "validate_limit_args",
]
