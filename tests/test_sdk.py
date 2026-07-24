import io
import os
import tarfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from renforge import sdk


def _write_fake_sdk_archive(path: Path, version: str) -> Path:
    source_root = path / "staging"
    fake_sdk_root = source_root / f"renpy-{version}-sdk"
    renpy_package = fake_sdk_root / "renpy"
    renpy_package.mkdir(parents=True)
    (fake_sdk_root / "renpy.py").write_text("print('renpy')")
    (renpy_package / "vc_version.py").write_text(f"version = {version!r}\n")

    archive_path = path / "renpy-sdk.tar.bz2"
    with tarfile.open(archive_path, "w:bz2") as archive:
        archive.add(fake_sdk_root, arcname=fake_sdk_root.name)
    return archive_path


def _write_fake_sdk(root: Path, version: str, *, shell_launcher: bool = False) -> Path:
    (root / "renpy").mkdir(parents=True)
    (root / "renpy" / "vc_version.py").write_text(f"version = {version!r}\n")
    launcher = root / ("renpy.sh" if shell_launcher else "renpy.py")
    launcher.write_text("#!/bin/sh\n" if shell_launcher else "print('renpy')\n")
    if shell_launcher:
        launcher.chmod(0o755)
    return root


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
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
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
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
    monkeypatch.setenv(sdk.RENPY_SDK_BASE_URL_ENV, "https://example.invalid")

    with pytest.raises(ValueError, match="path traversal"):
        sdk.get_or_install_sdk(f"8.3.7-{uuid.uuid4().hex}")


def test_get_or_install_sdk_rejects_version_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("preserve")
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))

    with pytest.raises(ValueError, match="Invalid Ren'Py version identifier"):
        sdk.get_or_install_sdk("8.5.3/../../outside")

    assert marker.read_text() == "preserve"
    assert list(outside.iterdir()) == [marker]


def test_project_local_compatible_sdk_has_priority_without_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    local_sdk = _write_fake_sdk(project / "renpy-sdk", "8.5.4.26010101")
    cache_sdk = _write_fake_sdk(tmp_path / "cache" / "8.5.3", "8.5.3.26010101")
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_sdk.parent))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)
    monkeypatch.setattr(
        sdk,
        "_download_archive",
        lambda *_args, **_kwargs: pytest.fail("download must not be attempted"),
    )

    discovered = sdk.get_or_install_sdk("8.5.3", project_root=project)

    assert discovered.root == local_sdk


def test_project_local_sdk_is_not_patched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    local_sdk = _write_fake_sdk(project / "renpy-sdk", "8.5.3")
    dump = local_sdk / "renpy" / "dump.py"
    dump.write_text(sdk._DUMP_NAMEMAP_LOOP)
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk("8.5.3", project_root=project)

    assert discovered.root == local_sdk
    assert "renforge: unwrap Node-keyed namemap" not in dump.read_text()


def test_explicit_sdk_is_patched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit_sdk = _write_fake_sdk(tmp_path / "explicit-sdk", "8.5.3")
    dump = explicit_sdk / "renpy" / "dump.py"
    dump.write_text(sdk._DUMP_NAMEMAP_LOOP)
    monkeypatch.setenv(sdk.RENPY_SDK_ENV, str(explicit_sdk))
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(tmp_path / "cache"))

    discovered = sdk.get_or_install_sdk("8.5.3")

    assert discovered.root == explicit_sdk
    assert "renforge: unwrap Node-keyed namemap" in dump.read_text()


def test_incompatible_project_sdk_falls_back_to_compatible_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    local_sdk = _write_fake_sdk(project / "renpy-sdk", "8.4.9")
    cache_sdk = _write_fake_sdk(tmp_path / "cache" / "8.5.4", "8.5.4.26010101")
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_sdk.parent))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)
    monkeypatch.setattr(
        sdk,
        "_download_archive",
        lambda *_args, **_kwargs: pytest.fail("download must not be attempted"),
    )

    discovered = sdk.get_or_install_sdk("8.5.3", project_root=project)

    assert discovered.root == cache_sdk
    assert local_sdk.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher selection")
def test_windows_native_launcher_is_rejected_on_posix_even_when_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    local_sdk = _write_fake_sdk(project / "renpy-sdk", "8.5.3")
    (local_sdk / "renpy.py").unlink()
    native_launcher = local_sdk / "renpy.exe"
    native_launcher.write_text("native")
    native_launcher.chmod(0o755)
    cache_sdk = _write_fake_sdk(tmp_path / "cache" / "8.5.3", "8.5.3")
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_sdk.parent))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk("8.5.3", project_root=project)

    assert discovered.root == cache_sdk


def test_project_sdk_symlink_outside_project_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = _write_fake_sdk(tmp_path / "outside-sdk", "8.5.3")
    (project / "renpy-sdk").symlink_to(outside, target_is_directory=True)
    cache_sdk = _write_fake_sdk(tmp_path / "cache" / "8.5.3", "8.5.3")
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_sdk.parent))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk("8.5.3", project_root=project)

    assert discovered.root == cache_sdk


def test_cache_sdk_symlink_outside_cache_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "8.5.3"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    outside = _write_fake_sdk(tmp_path / "outside-sdk", version)
    (cache_dir / version).symlink_to(outside, target_is_directory=True)
    archive_path = _write_fake_sdk_archive(tmp_path, version)
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.root == cache_dir / version
    assert discovered.root.resolve() != outside.resolve()
    assert outside.exists()


def test_corrupt_cache_is_replaced_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "8.5.3"
    cache_dir = tmp_path / "cache"
    corrupt_root = cache_dir / version
    corrupt_root.mkdir(parents=True)
    (corrupt_root / "partial.txt").write_text("corrupt")
    archive_path = _write_fake_sdk_archive(tmp_path, version)
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.root == corrupt_root
    assert discovered.launcher == corrupt_root / "renpy.py"
    assert not (corrupt_root / "partial.txt").exists()
    assert list(cache_dir.glob(f".{version}.corrupt-*")) == []
    assert (cache_dir / f".{version}.lock").exists()


def test_hidden_temporary_cache_sdk_is_never_selected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "8.5.3"
    cache_dir = tmp_path / "cache"
    hidden_sdk = _write_fake_sdk(
        cache_dir / ".8.5.4.install-concurrent",
        "8.5.4",
    )
    archive_path = _write_fake_sdk_archive(tmp_path, version)
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.root == cache_dir / version
    assert discovered.root != hidden_sdk
    assert hidden_sdk.exists()


def test_corrupt_target_is_quarantined_when_exists_reports_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "8.5.3"
    cache_dir = tmp_path / "cache"
    corrupt_root = cache_dir / version
    corrupt_root.mkdir(parents=True)
    corrupt_marker = corrupt_root / "partial.txt"
    corrupt_marker.write_text("corrupt")
    archive_path = _write_fake_sdk_archive(tmp_path, version)
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.setenv(sdk.RENPY_SDK_ARCHIVE_URL_ENV, archive_path.as_uri())
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)
    original_exists = Path.exists

    def access_denied_exists(path: Path) -> bool:
        if path == corrupt_root and original_exists(corrupt_marker):
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", access_denied_exists)

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.root == corrupt_root
    assert not corrupt_marker.exists()
    assert discovered.launcher == corrupt_root / "renpy.py"


def test_sdk_install_rechecks_cache_after_acquiring_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "8.5.3"
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(sdk.RENPY_SDK_CACHE_ENV, str(cache_dir))
    monkeypatch.delenv(sdk.RENPY_SDK_ENV, raising=False)
    original_lock = sdk._sdk_install_lock

    @contextmanager
    def populate_while_waiting(requested_version: str):
        with original_lock(requested_version):
            _write_fake_sdk(cache_dir / requested_version, requested_version)
            yield

    monkeypatch.setattr(sdk, "_sdk_install_lock", populate_while_waiting)
    monkeypatch.setattr(
        sdk,
        "_download_archive",
        lambda *_args, **_kwargs: pytest.fail("download must not be attempted"),
    )

    discovered = sdk.get_or_install_sdk(version)

    assert discovered.root == cache_dir / version


def test_patch_sdk_json_dump_unwraps_node_keyed_namemap(tmp_path: Path) -> None:
    dump = tmp_path / "renpy" / "dump.py"
    dump.parent.mkdir(parents=True)
    dump.write_text(
        "def dump(error):\n"
        "    label = location[\"label\"] = {}\n"
        "\n"
        "    for name, n in renpy.game.script.namemap.items():\n"
        "        filename = n.filename\n"
        "        line = n.linenumber\n"
        "\n"
        "        if not isinstance(name, str):\n"
        "            continue\n"
        "\n"
        "        label[name] = [filename, line]\n",
        encoding="utf-8",
    )

    assert sdk._patch_sdk_json_dump(tmp_path) is True
    patched = dump.read_text(encoding="utf-8")
    assert "renforge: unwrap Node-keyed namemap" in patched
    assert "name = getattr(name, \"name\", name)" in patched
    # Idempotent.
    assert sdk._patch_sdk_json_dump(tmp_path) is False


def test_default_renpy_version_tracks_current_stable() -> None:
    assert sdk.DEFAULT_RENPY_VERSION == "8.5.3"
