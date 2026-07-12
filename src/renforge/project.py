from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable, Sequence

from .sdk import RenpySdk


RENPY_GAME_DIR: Final = "game"
RENFORGE_CACHE_DIR: Final = ".renforge"

# A real project's game/ dir holds scripts or archives; requiring one avoids
# misdetecting an unrelated directory that merely contains a "game" folder.
_PROJECT_CONTENT_GLOBS: Final = ("*.rpy", "*.rpyc", "*.rpa")
_PROJECT_CONTENT_SUFFIXES: Final = (".rpy", ".rpyc", ".rpa")


def _has_renpy_content(game_dir: Path) -> bool:
    # Fast path: virtually every project keeps a script or archive at the top
    # level of game/. Fall back to one recursive walk for projects that nest
    # everything in subfolders (e.g. game/chapters/script.rpy).
    for pattern in _PROJECT_CONTENT_GLOBS:
        if next(game_dir.glob(pattern), None) is not None:
            return True
    return (
        next(
            (p for p in game_dir.rglob("*") if p.suffix in _PROJECT_CONTENT_SUFFIXES),
            None,
        )
        is not None
    )


def discover_project_from(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to the nearest Ren'Py project root."""
    # This runs inside the first tool an agent calls (renforge_info), so a
    # deleted cwd or an unreadable directory must degrade to "not found"
    # rather than crash the call.
    try:
        current = (start or Path.cwd()).resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        game_dir = candidate / RENPY_GAME_DIR
        try:
            if game_dir.is_dir() and _has_renpy_content(game_dir):
                return candidate
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class RenpyProject:
    """Represents a Ren'Py project directory."""

    root: Path
    game_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        if not self.root.exists():
            raise FileNotFoundError(f"Project root does not exist: {self.root}")

        if not self.root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {self.root}")

        game_dir = self.root / RENPY_GAME_DIR
        if not game_dir.is_dir():
            raise FileNotFoundError(f"Invalid Ren'Py project: missing '{RENPY_GAME_DIR}/' in {self.root}")

        cache_dir = self.root / RENFORGE_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "game_dir", game_dir)
        object.__setattr__(self, "cache_dir", cache_dir)

    @property
    def abs_root(self) -> Path:
        return self.root.resolve()

    def has_config(self, filename: str) -> bool:
        return (self.root / filename).is_file()

    def files(self, names: Iterable[str]) -> list[Path]:
        return [self.root / name for name in names]

    def renpy_command(self, sdk: RenpySdk, args: Sequence[str] | tuple[str, ...] | None = None) -> list[str]:
        command_args = tuple(args) if args else ()
        # Pass the absolute project root so the command is cwd-independent.
        return sdk.launch_command(self.abs_root, *command_args)

    def lint_command(self, sdk: RenpySdk) -> list[str]:
        return self.renpy_command(sdk, ("lint",))
