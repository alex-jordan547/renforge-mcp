from __future__ import annotations

import pytest

from renforge.launch_env import (
    LaunchError,
    detect_environment,
    resolve_audio_strategy,
    resolve_display_strategy,
)


def test_detect_environment_reports_display_flags(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    caps = detect_environment()
    assert caps.display_available is True
    assert caps.to_dict()["display"] == ":0"


def test_resolve_display_auto_falls_back_to_xvfb(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    caps = detect_environment({"PATH": "/usr/bin"})
    # Force xvfb availability independent of the host.
    caps.display_available = False
    caps.xvfb_available = True
    mode, env = resolve_display_strategy("auto", caps)
    assert mode == "xvfb"
    assert env == {}


def test_resolve_display_auto_fails_without_fallback():
    caps = detect_environment({})
    caps.display_available = False
    caps.xvfb_available = False
    with pytest.raises(LaunchError) as excinfo:
        resolve_display_strategy("auto", caps)
    assert excinfo.value.code == "DISPLAY_UNAVAILABLE"
    assert excinfo.value.phase == "detecting_environment"
    assert "suggested_fix" in excinfo.value.to_dict()


def test_resolve_audio_auto_uses_dummy_when_unavailable():
    caps = detect_environment({})
    caps.audio_available = False
    assert resolve_audio_strategy("auto", caps) == {"SDL_AUDIODRIVER": "dummy"}
