"""Environment detection and structured launch errors for RenForge sessions."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any


class LaunchError(RuntimeError):
    """Structured failure raised while starting a Ren'Py session."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: str,
        suggested_fix: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.phase = phase
        self.suggested_fix = suggested_fix
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "code": self.code,
            "phase": self.phase,
            "message": self.message,
            "error": self.message,
        }
        if self.suggested_fix:
            payload["suggested_fix"] = self.suggested_fix
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class EnvironmentCapabilities:
    platform: str
    environment: str
    display_available: bool
    wayland_available: bool
    xvfb_available: bool
    audio_available: bool
    display: str | None = None
    wayland_display: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "environment": self.environment,
            "display_available": self.display_available,
            "wayland_available": self.wayland_available,
            "xvfb_available": self.xvfb_available,
            "audio_available": self.audio_available,
            "display": self.display,
            "wayland_display": self.wayland_display,
            "notes": list(self.notes),
        }


def _detect_environment_name(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    if source.get("WSL_DISTRO_NAME") or source.get("WSL_INTEROP"):
        return "wsl"
    if source.get("GITHUB_ACTIONS") or source.get("CI"):
        return "ci"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def _audio_available(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    # Explicit dummy driver means the process can start without real devices.
    driver = (source.get("SDL_AUDIODRIVER") or "").strip().casefold()
    if driver in {"dummy", "disk", "null"}:
        return True
    if source.get("PULSE_SERVER") or source.get("PIPEWIRE_RUNTIME_DIR"):
        return True
    # Presence of common Linux sockets is a reasonable heuristic.
    runtime = source.get("XDG_RUNTIME_DIR", "")
    if runtime:
        for name in ("pulse/native", "pipewire-0"):
            if os.path.exists(os.path.join(runtime, name)):
                return True
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return False


def detect_environment(env: dict[str, str] | None = None) -> EnvironmentCapabilities:
    """Inspect the process environment for display and audio capabilities."""
    source = dict(os.environ if env is None else env)
    display = (source.get("DISPLAY") or "").strip() or None
    wayland = (source.get("WAYLAND_DISPLAY") or "").strip() or None
    notes: list[str] = []
    xvfb = shutil.which("xvfb-run") is not None or shutil.which("Xvfb") is not None
    if not xvfb:
        notes.append("xvfb-run/Xvfb not found on PATH")
    audio = _audio_available(source)
    if not audio:
        notes.append("no audio device detected; dummy SDL driver will be used when audio=auto")
    native_desktop = sys.platform == "darwin" or sys.platform.startswith("win")

    return EnvironmentCapabilities(
        platform=sys.platform,
        environment=_detect_environment_name(source),
        display_available=bool(display or wayland or native_desktop),
        wayland_available=bool(wayland),
        xvfb_available=xvfb,
        audio_available=audio,
        display=display,
        wayland_display=wayland,
        notes=notes,
    )


def resolve_display_strategy(
    display: str,
    capabilities: EnvironmentCapabilities,
) -> tuple[str, dict[str, str]]:
    """Return ``(mode, env_updates)`` for the requested display strategy.

    ``mode`` is one of ``native``, ``xvfb``, ``external``.
    """
    strategy = (display or "auto").strip().casefold()
    if strategy not in {"auto", "native", "xvfb", "external", "none"}:
        raise LaunchError(
            "INVALID_DISPLAY_STRATEGY",
            "display must be one of: auto, native, xvfb, external",
            phase="detecting_environment",
            suggested_fix="Pass display='auto' or an explicit strategy.",
        )

    if strategy == "external":
        # Caller owns the display; do not wrap with Xvfb.
        return "external", {}

    if strategy == "native":
        if not capabilities.display_available:
            raise LaunchError(
                "DISPLAY_UNAVAILABLE",
                "display='native' requires DISPLAY or WAYLAND_DISPLAY.",
                phase="detecting_environment",
                suggested_fix="Use display='auto' or install xvfb.",
            )
        return "native", {}

    if strategy == "xvfb":
        if not capabilities.xvfb_available:
            raise LaunchError(
                "DISPLAY_START_FAILED",
                "display='xvfb' was requested but xvfb-run is not installed.",
                phase="starting_virtual_display",
                suggested_fix="Install xvfb (xvfb-run) or use a graphical session.",
            )
        return "xvfb", {}

    if strategy == "none":
        raise LaunchError(
            "DISPLAY_UNAVAILABLE",
            "display='none' is not supported; Ren'Py requires a display surface.",
            phase="detecting_environment",
            suggested_fix="Use display='auto' or display='xvfb'.",
        )

    # auto
    if capabilities.display_available:
        return "native", {}
    if capabilities.xvfb_available:
        return "xvfb", {}
    raise LaunchError(
        "DISPLAY_UNAVAILABLE",
        "Ren'Py requires a display and no virtual display could be started.",
        phase="detecting_environment",
        suggested_fix="Install xvfb or use display='external' with a prepared DISPLAY.",
        details=capabilities.to_dict(),
    )


def resolve_audio_strategy(audio: str, capabilities: EnvironmentCapabilities) -> dict[str, str]:
    """Return environment updates for the requested audio strategy."""
    strategy = (audio or "auto").strip().casefold()
    if strategy not in {"auto", "native", "dummy", "none"}:
        raise LaunchError(
            "INVALID_AUDIO_STRATEGY",
            "audio must be one of: auto, native, dummy, none",
            phase="detecting_environment",
            suggested_fix="Pass audio='auto' or audio='dummy'.",
        )
    if strategy == "dummy" or strategy == "none":
        return {"SDL_AUDIODRIVER": "dummy"}
    if strategy == "native":
        return {}
    # auto
    if capabilities.audio_available:
        return {}
    return {"SDL_AUDIODRIVER": "dummy"}


__all__ = [
    "EnvironmentCapabilities",
    "LaunchError",
    "detect_environment",
    "resolve_audio_strategy",
    "resolve_display_strategy",
]
