from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile
from urllib.error import HTTPError, URLError

DEFAULT_RENPY_VERSION: Final = "8.5.3"
RENPY_SDK_ENV: Final = "RENPY_SDK_HOME"
RENPY_SDK_CACHE_ENV: Final = "RENPY_SDK_CACHE_DIR"
RENPY_SDK_STABLE_ENV: Final = "RENPY_SDK_STABLE_VERSION"
RENPY_SDK_BASE_URL_ENV: Final = "RENPY_SDK_BASE_URL"
RENPY_SDK_ARCHIVE_URL_ENV: Final = "RENPY_SDK_ARCHIVE_URL"
RENPY_SDK_BASE_URL_DEFAULT: Final = "https://www.renpy.org/dl"
_VERSION_IDENTIFIER_RE: Final = re.compile(
    r"\d+\.\d+\.\d+(?:[._+-][A-Za-z0-9]+)*\Z"
)

# Ren'Py 8.5 keyed Script.namemap by Node (Node.__hash__/__eq__ use .name) but
# left dump.py filtering with ``isinstance(name, str)``, which drops every
# label from --json-dump. Unwrap Node keys before the type check.
_DUMP_NAMEMAP_LOOP = (
    "    for name, n in renpy.game.script.namemap.items():\n"
    "        filename = n.filename\n"
    "        line = n.linenumber\n"
    "\n"
    "        if not isinstance(name, str):\n"
    "            continue\n"
)
_DUMP_NAMEMAP_LOOP_FIXED = (
    "    for name, n in renpy.game.script.namemap.items():\n"
    "        # renforge: unwrap Node-keyed namemap (Ren'Py 8.5+)\n"
    "        if not isinstance(name, str):\n"
    "            name = getattr(name, \"name\", name)\n"
    "        filename = n.filename\n"
    "        line = n.linenumber\n"
    "\n"
    "        if not isinstance(name, str):\n"
    "            continue\n"
)


def _patch_sdk_json_dump(root: Path) -> bool:
    """Repair label emission in a cached/downloaded Ren'Py SDK dump.py.

    Returns True when the file was modified.
    """
    dump_path = root / "renpy" / "dump.py"
    if not dump_path.is_file():
        return False
    try:
        text = dump_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "renforge: unwrap Node-keyed namemap" in text:
        return False
    if _DUMP_NAMEMAP_LOOP not in text:
        return False
    try:
        dump_path.write_text(
            text.replace(_DUMP_NAMEMAP_LOOP, _DUMP_NAMEMAP_LOOP_FIXED, 1),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True

def _resolve_version(version: str) -> str:
    if version == "stable" or not version:
        return os.environ.get(RENPY_SDK_STABLE_ENV, DEFAULT_RENPY_VERSION)
    return version


def _validate_version_identifier(version: str) -> str:
    if (
        not _VERSION_IDENTIFIER_RE.fullmatch(version)
        or ".." in version
        or "/" in version
        or "\\" in version
    ):
        raise ValueError(f"Invalid Ren'Py version identifier: {version!r}")
    return version


def _cache_dir() -> Path:
    cache_root = Path(os.environ.get(RENPY_SDK_CACHE_ENV, Path.home() / ".cache" / "renforge" / "sdks"))
    return cache_root.expanduser()


def _cache_version_dir(version: str) -> Path:
    return _cache_dir() / version


def _managed_cache_child(path: Path) -> Path:
    cache_root = _cache_dir().resolve(strict=False)
    candidate = path.expanduser().absolute()
    try:
        parent = candidate.parent.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid managed cache path: {path}") from exc
    if parent != cache_root or candidate.name in {"", ".", ".."}:
        raise ValueError(f"Managed cache path is not a direct cache child: {path}")
    return cache_root / candidate.name


def _unique_paths(candidates: Iterable[Path]) -> list[Path]:
    seen: list[Path] = []
    for path in candidates:
        try:
            normalized = path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if normalized not in seen:
            seen.append(normalized)
    return seen


def _versioned_candidates(base: Path, version: str) -> list[Path]:
    return [
        base,
        base / version,
        base / f"renpy-{version}",
        base / f"renpy-{version}-sdk",
    ]


def _project_candidate_roots(project_root: Path, version: str) -> list[Path]:
    project = project_root.expanduser().resolve()
    bases = [
        project,
        project / "renpy",
        project / "renpy-sdk",
        project / ".renpy",
        project / ".renforge" / "sdk",
    ]
    candidates = _unique_paths(
        candidate
        for base in bases
        for candidate in _versioned_candidates(base, version)
    )
    return [
        candidate
        for candidate in candidates
        if candidate.is_relative_to(project)
    ]


def _explicit_candidate_roots(version: str) -> list[Path]:
    env_root = os.environ.get(RENPY_SDK_ENV)
    if not env_root:
        return []
    root = Path(env_root)
    return _unique_paths(
        [
            *_versioned_candidates(root, version),
            *_versioned_candidates(root / "renpy", version),
        ]
    )


def _find_entrypoint(path: Path) -> Path:
    # Prefer the platform launcher scripts: they bootstrap Ren'Py's *bundled*
    # Python (which ships every dependency). `renpy.py` run with an arbitrary
    # system Python fails on missing deps, so it is only a last resort.
    # Native launchers are platform-specific; a PE file is not runnable on
    # POSIX even when its executable permission bit is set.
    scripts = ("renpy.exe",) if os.name == "nt" else ("renpy.sh",)
    options = [
        *(path / script for script in scripts),
        path / "renpy.py",
        *(path / "renpy-sdk" / script for script in scripts),
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


def _version_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def _sdk_internal_version(root: Path) -> str | None:
    version_file = root / "renpy" / "vc_version.py"
    try:
        text = version_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(
        r"(?m)^\s*version\s*=\s*(['\"])(?P<version>\d+\.\d+\.\d+[^'\"]*)\1",
        text,
    )
    return match.group("version") if match else None


def _compatible_sdk_version(installed: str, required: str) -> bool:
    installed_tuple = _version_tuple(installed)
    required_tuple = _version_tuple(required)
    if installed_tuple is None or required_tuple is None:
        return False
    return (
        installed_tuple[0] == required_tuple[0]
        and installed_tuple >= required_tuple
    )


def _validated_sdk_version(root: Path, required_version: str) -> str | None:
    try:
        launcher = _find_entrypoint(root)
        real_root = root.resolve(strict=True)
        real_launcher = launcher.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if not real_launcher.is_file() or not os.access(real_launcher, os.R_OK):
        return None
    if not real_launcher.is_relative_to(real_root):
        return None
    if real_launcher.suffix != ".py" and os.name != "nt" and not os.access(real_launcher, os.X_OK):
        return None
    installed_version = _sdk_internal_version(real_root)
    if installed_version is None or not _compatible_sdk_version(installed_version, required_version):
        return None
    return installed_version


def _first_valid_sdk(candidates: Iterable[Path], version: str) -> Path | None:
    for candidate in candidates:
        if _validated_sdk_version(candidate, version) is not None:
            return candidate.resolve()
    return None


def _cache_candidates(version: str) -> list[Path]:
    cache_root = _cache_dir()
    exact = _cache_version_dir(version)
    resolved_cache_root = cache_root.resolve(strict=False)
    compatible: list[tuple[tuple[int, int, int], Path]] = []
    try:
        children = list(cache_root.iterdir())
    except OSError:
        children = []
    for child in children:
        if child.name.startswith(".") or _version_tuple(child.name) is None:
            continue
        try:
            resolved_child = child.resolve(strict=False)
            admissible = resolved_child.is_relative_to(resolved_cache_root)
            is_directory = child.is_dir()
        except (OSError, RuntimeError):
            continue
        if not admissible or not is_directory or child == exact:
            continue
        installed = _validated_sdk_version(child, version)
        installed_tuple = _version_tuple(installed or "")
        if installed_tuple is not None:
            compatible.append((installed_tuple, child))
    compatible.sort(key=lambda item: item[0])
    candidates = [path for _, path in compatible]
    try:
        if exact.resolve(strict=False).is_relative_to(resolved_cache_root):
            candidates.insert(0, exact)
    except (OSError, RuntimeError):
        pass
    return candidates


def _directory_entry_exists(path: Path) -> bool:
    try:
        expected_name = os.path.normcase(path.name)
        return any(
            os.path.normcase(entry.name) == expected_name
            for entry in path.parent.iterdir()
        )
    except OSError:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True


@contextmanager
def _sdk_install_lock(version: str) -> Iterable[None]:
    lock_path = _cache_dir() / f".{version.replace(os.sep, '_')}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
    # Close the mkstemp handle right away: on Windows an open handle blocks
    # both re-opening for write and the unlink in the error path.
    os.close(fd)
    archive_path = Path(archive_file)
    try:
        with urllib.request.urlopen(url) as response:
            with archive_path.open("wb") as target:
                shutil.copyfileobj(response, target)
    except (HTTPError, URLError, OSError):
        archive_path.unlink(missing_ok=True)
        raise
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


def get_or_install_sdk(
    version: str = "stable",
    *,
    project_root: Path | None = None,
) -> RenpySdk:
    """
    Discover or prepare a Ren'Py SDK directory.
    """
    resolved_version = _validate_version_identifier(_resolve_version(version))
    if project_root is not None:
        local_root = _first_valid_sdk(
            _project_candidate_roots(project_root, resolved_version),
            resolved_version,
        )
        if local_root is not None:
            return RenpySdk(version=resolved_version, root=local_root)

    explicit_root = _first_valid_sdk(
        _explicit_candidate_roots(resolved_version),
        resolved_version,
    )
    if explicit_root is not None:
        _patch_sdk_json_dump(explicit_root)
        return RenpySdk(version=resolved_version, root=explicit_root)

    cached_root = _first_valid_sdk(_cache_candidates(resolved_version), resolved_version)
    if cached_root is not None:
        _patch_sdk_json_dump(cached_root)
        return RenpySdk(version=resolved_version, root=cached_root)

    with _sdk_install_lock(resolved_version):
        cached_root = _first_valid_sdk(_cache_candidates(resolved_version), resolved_version)
        if cached_root is not None:
            _patch_sdk_json_dump(cached_root)
            return RenpySdk(version=resolved_version, root=cached_root)

        cache_root = _managed_cache_child(_cache_version_dir(resolved_version))
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{cache_root.name}.install-",
            dir=cache_root.parent,
        ) as scratch:
            scratch_dir = Path(scratch)
            archive_path = _download_archive(_archive_url(resolved_version), scratch_dir)
            extraction_root = scratch_dir / "extracted"
            extraction_root.mkdir(parents=True, exist_ok=True)
            _extract_archive(archive_path, extraction_root)
            discovered_root = _find_sdk_root(extraction_root)
            if _validated_sdk_version(discovered_root, resolved_version) is None:
                raise ValueError(
                    f"Downloaded Ren'Py SDK is invalid or incompatible with {resolved_version}."
                )
            _patch_sdk_json_dump(discovered_root)

            quarantine = _managed_cache_child(
                cache_root.with_name(
                    f".{cache_root.name}.corrupt-{os.getpid()}-{uuid.uuid4().hex}"
                )
            )
            quarantined = False
            if _directory_entry_exists(cache_root):
                os.replace(cache_root, quarantine)
                quarantined = True
            try:
                os.replace(discovered_root, cache_root)
            except BaseException:
                if quarantined and not _directory_entry_exists(cache_root):
                    os.replace(quarantine, cache_root)
                raise
            else:
                if quarantined:
                    shutil.rmtree(quarantine, ignore_errors=True)

    return RenpySdk(version=resolved_version, root=cache_root)
