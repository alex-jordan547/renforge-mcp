import os
import subprocess
import sys
from pathlib import Path

import pytest

from renforge import __version__
from renforge.cli import main


def test_cli_version_via_module_or_main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "renforge", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    if completed.returncode == 0:
        assert completed.stdout.strip() == f"renforge {__version__}"
        return

    # Fallback pour environnements où l'invocation \"python -m renforge\" est bloquée.
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
