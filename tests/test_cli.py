import os
import subprocess
import sys
from pathlib import Path

import pytest

from renforge import __version__
from renforge.cli import main


def test_cli_version_via_subprocess() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "renforge", "--version"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except OSError as exc:  # environment cannot spawn subprocesses
        pytest.skip(f"cannot spawn subprocess: {exc}")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"renforge {__version__}"


def test_cli_version_in_process() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
