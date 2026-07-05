from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

import errno
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from urllib.error import HTTPError, URLError

DEFAULT_RENPY_VERSION: Final = "8.3.7"
RENPY_SDK_ENV: Final = "RENPY_SDK_HOME"
RENPY_SDK_CACHE_ENV: Final = "RENPY_SDK_CACHE_DIR"
RENPY_SDK_STABLE_ENV: Final = "RENPY_SDK_STABLE_VERSION"
RENPY_SDK_BASE_URL_ENV: Final = "RENPY_SDK_BASE_URL"
RENPY_SDK_ARCHIVE_URL_ENV: Final = "RENPY_SDK_ARCHIVE_URL"
RENPY_SDK_BASE_URL_DEFAULT: Final = "https://www.renpy.org/dl"


def _resolve_version(version: str) -> str:
    if version == "stable" or not version:
        return os.environ.get(RENPY_SDK_STABLE_ENV, DEFAULT_RENPY_VERSION)
    return version


def _cache_dir() -> Path:
    cache_root = Path(os.environ.get(RENPY_SDK_CACHE_ENV, Path.home() / ".cache" / "renforge" / "sdks"))
    return cache_root.expanduser()


def _cache_version_dir(version: str) -> Path:
    return _cache_dir() / version


def _unique_paths(candidates: Iterable[Path]) -> list[Path]:
    seen: list[Path] = []
    for path in candidates:
        normalized = path.expanduser().resolve()
        if normalized not in seen:
            seen.append(normalized)
    return seen


def _discover_candidate_roots(version: str) -> list[Path]:
    env_root = os.environ.get(RENPY_SDK_ENV)
    bases = [
        Path(env_root) if env_root else None,
        Path.home() / ".renpy",
        Path.home() / ".cache" / "renpy",
        Path.home() / ".local" / "share" / "renpy",
        Path("/opt") / "renpy",
        Path("/usr/local") / "renpy",
    ]
    candidates: list[Path] = []
    for base in bases:
        if base is None:
            continue
        candidates.extend(
            [
                base,
                base / version,
                base / f"renpy-{version}",
                base / "renpy" / version,
                base / "renpy" / f"renpy-{version}",
            ]
        )
    return _unique_paths(candidates)


def _find_entrypoint(path: Path) -> Path:
    # Prefer the platform launcher scripts: they bootstrap Ren'Py's *bundled*
    # Python (which ships every dependency). `renpy.py` run with an arbitrary
    # system Python fails on missing deps, so it is only a last resort.
    options = [
        path / "renpy.sh",
        path / "renpy.exe",
        path / "renpy.py",
        path / "renpy-sdk" / "renpy.sh",
        path / "renpy-sdk" / "renpy.py",
        path / "lib" / "renpy" / "renpy.py",
    ]
    for option in options:
        if option.is_file():
            return option
    raise FileNotFoundError(f"No Ren'Py launcher found under SDK directory '{path}'.")


def _is_sdk_root(path: Path) -> bool:
    try:
        _find_entrypoint(path)
    except FileNotFoundError:
        return False
    return True


def _iter_existing_sdk_roots(version: str) -> Iterable[Path]:
    for candidate in [*_discover_candidate_roots(version), _cache_version_dir(version)]:
        if candidate.exists():
            try:
                _ = _find_entrypoint(candidate)
            except FileNotFoundError:
                continue
            yield candidate


def _archive_base_url() -> str:
    return os.environ.get(RENPY_SDK_BASE_URL_ENV, RENPY_SDK_BASE_URL_DEFAULT)


def _archive_url(version: str) -> str:
    override = os.environ.get(RENPY_SDK_ARCHIVE_URL_ENV)
    if override:
        return override
    return f"{_archive_base_url().rstrip('/')}/{version}/renpy-{version}-sdk.tar.bz2"


def _safe_member_path(root: Path, name: str) -> Path:
    if Path(name).is_absolute():
        raise ValueError(f"Archive entry has absolute path: {name}")
    destination = (root / name).resolve()
    root_path = root.resolve()
    if os.path.commonpath([str(destination), str(root_path)]) != str(root_path):
        raise ValueError(f"Archive path traversal attempt detected: {name}")
    return destination


def _extract_tar_archive(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            _safe_member_path(destination, member.name)
        for member in archive.getmembers():
            if member.isdir():
                continue
            target = _safe_member_path(destination, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source:
                with target.open("wb") as destination_file:
                    shutil.copyfileobj(source, destination_file)
            # Preserve the executable bit so launchers (renpy.sh) stay runnable.
            if member.mode & 0o111:
                os.chmod(target, target.stat().st_mode | 0o111)


def _extract_zip_archive(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            _safe_member_path(destination, member)
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = _safe_member_path(destination, member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination_file:
                shutil.copyfileobj(source, destination_file)
            # Preserve the executable bit encoded in the zip's unix attributes.
            unix_mode = member.external_attr >> 16
            if unix_mode & 0o111:
                os.chmod(target, target.stat().st_mode | 0o111)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    if tarfile.is_tarfile(archive_path):
        _extract_tar_archive(archive_path, destination)
        return
    if zipfile.is_zipfile(archive_path):
        _extract_zip_archive(archive_path, destination)
        return
    raise ValueError(f"Unsupported archive format: {archive_path}")


def _find_sdk_root(directory: Path) -> Path:
    for current, _, __ in os.walk(directory):
        candidate = Path(current)
        if _is_sdk_root(candidate):
            return candidate
    raise FileNotFoundError(f"No SDK launcher found inside archive extraction directory '{directory}'.")


def _download_archive(url: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    fd, archive_file = tempfile.mkstemp(prefix="renpy-sdk-", suffix=".archive", dir=destination)
    archive_path = Path(archive_file)
    try:
        with urllib.request.urlopen(url) as response:
            with archive_path.open("wb") as target:
                shutil.copyfileobj(response, target)
    except (HTTPError, URLError, OSError):
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    return archive_path


@dataclass(frozen=True)
class RenpySdk:
    """Represents a discovered Ren'Py SDK directory."""

    version: str
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if not self.root.exists():
            raise FileNotFoundError(f"SDK root does not exist: {self.root}")
        if not self.root.is_dir():
            raise NotADirectoryError(f"SDK root is not a directory: {self.root}")

    @property
    def launcher(self) -> Path:
        return _find_entrypoint(self.root)

    @property
    def launcher_command(self) -> list[str]:
        if self.launcher.suffix == ".py":
            return [sys.executable, str(self.launcher)]
        return [str(self.launcher)]

    def command(self, *args: str) -> list[str]:
        return [*self.launcher_command, *args]

    def launch_command(self, project_root: Path, *args: str) -> list[str]:
        # Ren'Py CLI form is: <launcher> <basedir> <command> [args].
        # The project base directory must come first.
        return self.command(str(project_root), *args)


def get_or_install_sdk(version: str = "stable") -> RenpySdk:
    """
    Discover or prepare a Ren'Py SDK directory.
    """
    resolved_version = _resolve_version(version)
    for sdk_root in _iter_existing_sdk_roots(resolved_version):
        return RenpySdk(version=resolved_version, root=sdk_root)

    cache_root = _cache_version_dir(resolved_version)
    cache_root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=_cache_dir()) as scratch:
        scratch_dir = Path(scratch)
        archive_path = _download_archive(_archive_url(resolved_version), scratch_dir)
        extraction_root = scratch_dir / "extracted"
        extraction_root.mkdir(parents=True, exist_ok=True)
        _extract_archive(archive_path, extraction_root)
        discovered_root = _find_sdk_root(extraction_root)
        try:
            os.replace(discovered_root, cache_root)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if not _is_sdk_root(cache_root):
                raise

    return RenpySdk(version=resolved_version, root=cache_root)
