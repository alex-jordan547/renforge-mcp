from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_reload_committed(
    status: Mapping[str, Any],
    *,
    generation: int | None = None,
    minimum_generation: int | None = None,
) -> bool:
    if status.get("save_in_progress") or status.get("status_code") != "reload_committed":
        return False
    if generation is None and minimum_generation is None:
        return True
    try:
        actual_generation = int(status.get("script_generation"))
    except (TypeError, ValueError):
        return False
    if generation is not None and actual_generation != generation:
        return False
    return minimum_generation is None or actual_generation >= minimum_generation
