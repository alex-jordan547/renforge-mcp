from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "run_ui_server":
        from .server import run_ui_server

        return run_ui_server
    raise AttributeError(name)


__all__ = ["run_ui_server"]
