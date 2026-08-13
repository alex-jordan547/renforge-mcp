from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCH = _ROOT / "scripts" / "smoke_renpy_launch.py"


def test_smoke_launch_script_resolves_demo_from_repo() -> None:
    """The committed launcher must root demo_game at the repository, not /tmp."""
    spec = importlib.util.spec_from_file_location("smoke_renpy_launch", _LAUNCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    demo = module.demo_project_path()
    assert demo == _ROOT / "examples" / "demo_game"
    assert demo.is_dir()
    assert (demo / "game" / "script.rpy").is_file()
    assert "/tmp/" not in str(demo)
