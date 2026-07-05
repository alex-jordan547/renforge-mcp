import io
import tarfile
from pathlib import Path
import uuid

import pytest

from renforge import sdk


def _write_fake_sdk_archive(path: Path, version: str) -> Path:
    source_root = path / "staging"
    fake_sdk_root = source_root / f"renpy-{version}-sdk"
    fake_sdk_root.mkdir(parents=True)
    (fake_sdk_root / "renpy.py").write_text("print('renpy')")

    archive_path = path / "renpy-sdk.tar.bz2"
    with tarfile.open(archive_path, "w:bz2") as archive:
        archive.add(fake_sdk_root / "renpy.py", arcname=f"{fake_sdk_root.name}/renpy.py")
    return archive_path


def _write_traversal_archive(path: Path) -> Path:
    archive_path = path / "renpy-sdk-traversal.tar.bz2"
    with tarfile.open(archive_path, "w:bz2") as archive:
        payload = b"hacked"
        member = tarfile.TarInfo(name="../pwn.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return archive_path


def test_get_or_install_sdk_downloads_and_install_sdk_from_local_file_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    version = f"8.3.7-{uuid.uuid4().hex}"
    cache_dir = tmp_path / "cache"
    archive_path = _write_fake_sdk_archive(tmp_path, version)

    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, f"file://{archive_path}")
    monkeypatch.setenv(sdk.RENPY_SDK_BASE_URL_ENV, "https://example.invalid")
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)
    monkeypatch.delenv(sdk.RENPY_SDK_STABLE_ENV, raising=False)

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.version == version
    assert discovered.root == cache_dir / version
    assert discovered.launcher == discovered.root / "renpy.py"


def test_get_or_install_sdk_rejects_path_traversal_in_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    archive_path = _write_traversal_archive(tmp_path)

    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, f"file://{archive_path}")
    monkeypatch.setenv(sdk.RENPY_SDK_BASE_URL_ENV, "https://example.invalid")

    with pytest.raises(ValueError, match="path traversal"):
        sdk.get_or_install_sdk(f"8.3.7-{uuid.uuid4().hex}")
