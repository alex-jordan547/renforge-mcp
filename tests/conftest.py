"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    # RENFORGE_ISOLATION changes launch defaults server-wide. A developer who
    # exports it must not change what the suite tests; tests that need it set
    # it themselves.
    monkeypatch.delenv("RENFORGE_ISOLATION", raising=False)
