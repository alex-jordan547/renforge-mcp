"""Schema-3 session artifact ownership under ``.renforge/control/artifacts.json``."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from renforge.bridge.client import BridgeProtocolError
from renforge.bridge.control import (
    ensure_control_dir,
    read_bridge_info,
)
from renforge.launch_env import LaunchError
from renforge.project import RenpyProject
from renforge.util.files import (
    PrivatePathError,
    atomic_write_private_json,
    fsync_directory,
    hash_file_nofollow,
    read_regular_file_nofollow,
    sha256_bytes,
    write_exclusive_bytes,
)

_ARTIFACTS_NAME: Final[str] = "artifacts.json"
_ARTIFACTS_SCHEMA: Final[int] = 3
_ARTIFACTS_MAX_BYTES: Final[int] = 1 * 1024 * 1024
_BRIDGE_INFO_BASENAME: Final[str] = "bridge.json"
_SESSION_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_ROLE_BRIDGE: Final[str] = "bridge"
_ROLE_SESSION_INIT: Final[str] = "session_init"
_ROLE_EDITOR: Final[str] = "editor"
_ALLOWED_ROLES: Final[frozenset[str]] = frozenset(
    {_ROLE_BRIDGE, _ROLE_SESSION_INIT, _ROLE_EDITOR}
)
_MANIFEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "session_id",
        "project_root",
        "bridge_info",
        "sources",
        "asset_tree",
    }
)
_SOURCE_KEYS: Final[frozenset[str]] = frozenset(
    {"role", "basename", "sha256", "generated_siblings"}
)
_ASSET_TREE_KEYS: Final[frozenset[str]] = frozenset({"dirname", "files"})
_ASSET_FILE_KEYS: Final[frozenset[str]] = frozenset({"path", "sha256"})
_MAX_ALLOCATION_ATTEMPTS: Final[int] = 32


class ArtifactOwnershipError(Exception):
    """Proven ownership conflict; leave the tree and project lock untouched."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class MaterializedArtifacts:
    session_id: str
    manifest: dict[str, Any]
    editor_assets_dirname: str | None
    editor_font_relative: str


def artifacts_path(project_root: Path) -> Path:
    return Path(project_root) / ".renforge" / "control" / _ARTIFACTS_NAME


def bridge_basename(session_id: str) -> str:
    return f"zzrenforge_bridge_{session_id}.rpy"


def session_init_basename(session_id: str) -> str:
    return f"00renforge_session_{session_id}.rpy"


def editor_basename(session_id: str) -> str:
    return f"zzrenforge_editor_{session_id}.rpy"


def editor_assets_dirname(session_id: str) -> str:
    return f"zzrenforge_editor_{session_id}"


def _generated_siblings(basename: str) -> list[str]:
    return [basename + "c", basename + "c.bak"]


def _is_safe_basename(name: str) -> bool:
    return (
        isinstance(name, str)
        and name == Path(name).name
        and "/" not in name
        and "\\" not in name
        and name.endswith(".rpy")
        and ".." not in name
    )


def _is_safe_relative_posix(path: str) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    parts = path.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def _path_absent_and_not_link(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _is_symlink_or_nonfile(path: Path) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode)


def session_init_payload() -> bytes:
    return "\n".join(
        [
            "init -1500 python:",
            "    import os",
            "    _renforge_savedir = os.environ.get('RENFORGE_SAVEDIR')",
            "    if _renforge_savedir:",
            "        config.savedir = _renforge_savedir",
            "    _renforge_persistent = os.environ.get('RENFORGE_PERSISTENT_MODE')",
            "    if _renforge_persistent == 'empty':",
            "        # Keep persistent empty for isolated agent sessions.",
            "        try:",
            "            renpy.loadsave.location.unlink('persistent')",
            "        except Exception:",
            "            pass",
            "",
        ]
    ).encode("utf-8")


def _source_entry(*, role: str, basename: str, digest: str) -> dict[str, Any]:
    return {
        "role": role,
        "basename": basename,
        "sha256": digest,
        "generated_siblings": _generated_siblings(basename),
    }


def build_manifest(
    *,
    project_root: Path,
    session_id: str,
    sources: list[dict[str, Any]],
    asset_tree: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": _ARTIFACTS_SCHEMA,
        "session_id": session_id,
        "project_root": str(Path(project_root).expanduser().resolve(strict=True)),
        "bridge_info": _BRIDGE_INFO_BASENAME,
        "sources": sources,
        "asset_tree": asset_tree,
    }


def validate_artifacts_manifest(
    payload: object,
    *,
    expected_project_root: Path,
    expected_session_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest is not a JSON object",
        )
    if set(payload.keys()) != _MANIFEST_KEYS:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest keys are invalid",
        )
    if payload.get("schema_version") != _ARTIFACTS_SCHEMA:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest schema_version is invalid",
        )
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest session_id is invalid",
        )
    if expected_session_id is not None and session_id != expected_session_id:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest session_id does not match the expected owner",
        )

    try:
        canonical = Path(expected_project_root).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "project root is missing for artifacts validation",
        ) from exc
    if payload.get("project_root") != str(canonical):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest project_root does not match the canonical root",
        )
    if payload.get("bridge_info") != _BRIDGE_INFO_BASENAME:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest bridge_info is invalid",
        )

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest sources are invalid",
        )

    seen_roles: list[str] = []
    for entry in sources:
        if not isinstance(entry, dict) or set(entry.keys()) != _SOURCE_KEYS:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts source entry keys are invalid",
            )
        role = entry.get("role")
        basename = entry.get("basename")
        digest = entry.get("sha256")
        siblings = entry.get("generated_siblings")
        if role not in _ALLOWED_ROLES or role in seen_roles:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts source roles are invalid",
            )
        seen_roles.append(str(role))
        if not _is_safe_basename(str(basename)):
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts source basename is unsafe",
            )
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts source digest is invalid",
            )
        expected_siblings = _generated_siblings(str(basename))
        if siblings != expected_siblings:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts generated_siblings are invalid",
            )

    if seen_roles[0] != _ROLE_BRIDGE:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts sources must start with the bridge role",
        )
    # Ordered: bridge, optional session_init, optional editor.
    allowed_orders = (
        [_ROLE_BRIDGE],
        [_ROLE_BRIDGE, _ROLE_SESSION_INIT],
        [_ROLE_BRIDGE, _ROLE_EDITOR],
        [_ROLE_BRIDGE, _ROLE_SESSION_INIT, _ROLE_EDITOR],
    )
    if seen_roles not in allowed_orders:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts source order/cardinality is invalid",
        )

    asset_tree = payload.get("asset_tree")
    has_editor = _ROLE_EDITOR in seen_roles
    if has_editor:
        if not isinstance(asset_tree, dict) or set(asset_tree.keys()) != _ASSET_TREE_KEYS:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts asset_tree is invalid for editor ownership",
            )
        dirname = asset_tree.get("dirname")
        files = asset_tree.get("files")
        editor_entry = next(entry for entry in sources if entry["role"] == _ROLE_EDITOR)
        editor_stem = str(editor_entry["basename"])[: -len(".rpy")]
        if dirname != editor_stem or not isinstance(dirname, str) or "/" in dirname:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts asset_tree dirname does not match the editor stem",
            )
        if not isinstance(files, list):
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                "artifacts asset_tree files are invalid",
            )
        seen_paths: set[str] = set()
        previous_path = ""
        for file_entry in files:
            if not isinstance(file_entry, dict) or set(file_entry.keys()) != _ASSET_FILE_KEYS:
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    "artifacts asset file entry is invalid",
                )
            rel = file_entry.get("path")
            digest = file_entry.get("sha256")
            # Paths are relative *within* the asset tree (not prefixed by dirname).
            if not _is_safe_relative_posix(str(rel)):
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    "artifacts asset path is unsafe",
                )
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    "artifacts asset digest is invalid",
                )
            if str(rel) in seen_paths:
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    "artifacts asset paths are not unique",
                )
            if previous_path and str(rel) < previous_path:
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    "artifacts asset paths are not sorted",
                )
            seen_paths.add(str(rel))
            previous_path = str(rel)
    elif asset_tree is not None:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts asset_tree must be null without an editor source",
        )

    return payload


def _read_manifest_bytes(project_root: Path) -> bytes | None:
    path = artifacts_path(project_root)
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest became a symlink",
        )
    if not stat.S_ISREG(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest is not a regular file",
        )
    try:
        return read_regular_file_nofollow(path, max_bytes=_ARTIFACTS_MAX_BYTES)
    except PrivatePathError as exc:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            f"artifacts manifest is unsafe: {exc.message}",
        ) from exc


def load_validated_manifest(
    project_root: Path,
    *,
    expected_session_id: str | None = None,
) -> dict[str, Any] | None:
    raw = _read_manifest_bytes(project_root)
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest is not valid JSON",
        ) from exc
    return validate_artifacts_manifest(
        payload,
        expected_project_root=project_root,
        expected_session_id=expected_session_id,
    )


def _publish_intent(project_root: Path, manifest: Mapping[str, Any]) -> None:
    ensure_control_dir(project_root)
    path = artifacts_path(project_root)
    try:
        atomic_write_private_json(path, manifest, max_bytes=_ARTIFACTS_MAX_BYTES)
    except PrivatePathError as exc:
        raise LaunchError(
            "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
            exc.message,
            phase="publishing_artifact_intent",
            suggested_fix="Remove unsafe paths under .renforge/control and retry.",
        ) from exc
    fsync_directory(path.parent)


def _assert_allocation_clear(
    game_dir: Path,
    *,
    basenames: Iterable[str],
    assets_dirname: str | None,
) -> bool:
    for basename in basenames:
        for name in (basename, *(_generated_siblings(basename))):
            path = game_dir / name
            if not _path_absent_and_not_link(path):
                return False
            if path.is_symlink():
                return False
    if assets_dirname is not None:
        assets_dir = game_dir / assets_dirname
        if not _path_absent_and_not_link(assets_dir):
            return False
        if assets_dir.is_symlink():
            return False
    return True


def materialize_from_manifest(
    project_root: Path,
    *,
    payloads_by_role: Mapping[str, bytes],
    asset_payloads: Mapping[str, bytes] | None = None,
) -> None:
    """Create every source/asset listed by the on-disk intent (exclusive, no-follow)."""
    manifest = load_validated_manifest(project_root)
    if manifest is None:
        raise LaunchError(
            "BRIDGE_ARTIFACT_COLLISION",
            "artifact intent is missing before materialization",
            phase="materializing_artifacts",
        )
    game_dir = Path(project_root) / "game"
    for entry in manifest["sources"]:
        role = str(entry["role"])
        basename = str(entry["basename"])
        expected = str(entry["sha256"])
        data = payloads_by_role.get(role)
        if data is None or sha256_bytes(data) != expected:
            raise LaunchError(
                "BRIDGE_FILE_NOT_CREATED",
                f"artifact payload for role {role!r} does not match the intent digest",
                phase="materializing_artifacts",
            )
        target = game_dir / basename
        write_exclusive_bytes(target, data, mode=0o644)
        fsync_directory(target.parent)

    asset_tree = manifest.get("asset_tree")
    if asset_tree is None:
        return
    dirname = str(asset_tree["dirname"])
    assets_dir = game_dir / dirname
    assets_dir.mkdir(parents=True, exist_ok=False)
    asset_payloads = asset_payloads or {}
    for file_entry in asset_tree["files"]:
        rel = str(file_entry["path"])
        expected = str(file_entry["sha256"])
        data = asset_payloads.get(rel)
        if data is None or sha256_bytes(data) != expected:
            raise LaunchError(
                "BRIDGE_FILE_NOT_CREATED",
                f"asset payload for {rel!r} does not match the intent digest",
                phase="materializing_artifacts",
            )
        target = assets_dir / rel
        write_exclusive_bytes(target, data, mode=0o644)
    fsync_directory(assets_dir.parent)


def allocate_and_materialize(
    project: RenpyProject,
    *,
    bridge_payload: bytes,
    include_session_init: bool,
    editor_payload: bytes | None,
    editor_asset_files: list[tuple[str, bytes]] | None,
    editor_font_relative: str = "",
) -> MaterializedArtifacts:
    """Publish ownership intent then materialize exclusive sources/assets.

    Retries with a fresh session id when a planned path is occupied.
    """
    game_dir = project.game_dir
    last_error: BaseException | None = None
    for _attempt in range(_MAX_ALLOCATION_ATTEMPTS):
        session_id = secrets.token_hex(16)
        sources: list[dict[str, Any]] = []
        payloads_by_role: dict[str, bytes] = {_ROLE_BRIDGE: bridge_payload}
        basenames = [bridge_basename(session_id)]
        sources.append(
            _source_entry(
                role=_ROLE_BRIDGE,
                basename=basenames[0],
                digest=sha256_bytes(bridge_payload),
            )
        )
        if include_session_init:
            init_payload = session_init_payload()
            init_name = session_init_basename(session_id)
            basenames.append(init_name)
            payloads_by_role[_ROLE_SESSION_INIT] = init_payload
            sources.append(
                _source_entry(
                    role=_ROLE_SESSION_INIT,
                    basename=init_name,
                    digest=sha256_bytes(init_payload),
                )
            )

        asset_tree: dict[str, Any] | None = None
        asset_payloads: dict[str, bytes] = {}
        assets_dirname: str | None = None
        font_relative = ""
        if editor_payload is not None:
            edit_name = editor_basename(session_id)
            basenames.append(edit_name)
            payloads_by_role[_ROLE_EDITOR] = editor_payload
            sources.append(
                _source_entry(
                    role=_ROLE_EDITOR,
                    basename=edit_name,
                    digest=sha256_bytes(editor_payload),
                )
            )
            assets_dirname = editor_assets_dirname(session_id)
            files_meta: list[dict[str, str]] = []
            for rel, data in sorted(editor_asset_files or [], key=lambda item: item[0]):
                if not _is_safe_relative_posix(rel):
                    raise LaunchError(
                        "BRIDGE_FILE_NOT_CREATED",
                        f"editor asset path is unsafe: {rel}",
                        phase="injecting_editor",
                    )
                asset_payloads[rel] = data
                files_meta.append({"path": rel, "sha256": sha256_bytes(data)})
            font_relative = editor_font_relative or ""
            asset_tree = {"dirname": assets_dirname, "files": files_meta}

        if not _assert_allocation_clear(
            game_dir,
            basenames=basenames,
            assets_dirname=assets_dirname,
        ):
            continue

        manifest = build_manifest(
            project_root=project.root,
            session_id=session_id,
            sources=sources,
            asset_tree=asset_tree,
        )
        # Validate our own construction before publishing.
        validate_artifacts_manifest(manifest, expected_project_root=project.root)

        try:
            _publish_intent(project.root, manifest)
            materialize_from_manifest(
                project.root,
                payloads_by_role=payloads_by_role,
                asset_payloads=asset_payloads,
            )
        except BaseException as exc:
            last_error = exc
            try:
                remove_owned_artifacts(project.root, expected_session_id=session_id)
            except Exception:
                pass
            # Collision-style failures can retry; other errors re-raise after cleanup.
            if isinstance(exc, FileExistsError):
                continue
            if isinstance(exc, LaunchError) and exc.code == "BRIDGE_ARTIFACT_COLLISION":
                continue
            raise

        return MaterializedArtifacts(
            session_id=session_id,
            manifest=manifest,
            editor_assets_dirname=assets_dirname,
            editor_font_relative=font_relative,
        )

    raise LaunchError(
        "BRIDGE_ARTIFACT_COLLISION",
        "Could not allocate collision-free session artifact names.",
        phase="injecting_bridge",
        suggested_fix="Remove leftover zzrenforge_* injects under game/ and retry.",
    ) from last_error


def _validate_owned_regular(path: Path, *, expected_sha256: str | None = None) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            f"owned path became a symlink: {path.name}",
        )
    if not stat.S_ISREG(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            f"owned path is not a regular file: {path.name}",
        )
    if expected_sha256 is not None:
        try:
            digest = hash_file_nofollow(path)
        except (FileNotFoundError, OSError) as exc:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                f"owned path is unreadable: {path.name}",
            ) from exc
        if digest != expected_sha256:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                f"owned path digest changed: {path.name}",
            )
    return True


def _unlink_owned_regular(path: Path, *, expected_sha256: str | None = None) -> None:
    if not _validate_owned_regular(path, expected_sha256=expected_sha256):
        return
    path.unlink()


def _validate_asset_tree(game_dir: Path, asset_tree: Mapping[str, Any]) -> None:
    assets_dir = game_dir / str(asset_tree["dirname"])
    try:
        st = assets_dir.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "editor asset tree is not a regular directory",
        )

    expected_files = {
        str(entry["path"]): str(entry["sha256"])
        for entry in asset_tree["files"]
    }
    expected_dirs = {"."}
    for relative in expected_files:
        parent = Path(relative).parent
        while str(parent) not in {"", "."}:
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    for relative, digest in expected_files.items():
        _validate_owned_regular(assets_dir / relative, expected_sha256=digest)

    for dirpath, dirnames, filenames in os.walk(assets_dir, followlinks=False):
        current = Path(dirpath)
        for dirname in dirnames:
            candidate = current / dirname
            relative = candidate.relative_to(assets_dir).as_posix()
            candidate_st = candidate.lstat()
            if (
                relative not in expected_dirs
                or stat.S_ISLNK(candidate_st.st_mode)
                or not stat.S_ISDIR(candidate_st.st_mode)
            ):
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    f"editor asset tree has unexpected contents under {current.name}",
                )
        for filename in filenames:
            candidate = current / filename
            relative = candidate.relative_to(assets_dir).as_posix()
            if relative not in expected_files:
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    f"editor asset tree has unexpected contents under {current.name}",
                )


def _remove_asset_tree(game_dir: Path, asset_tree: Mapping[str, Any]) -> None:
    dirname = str(asset_tree["dirname"])
    assets_dir = game_dir / dirname
    try:
        st = assets_dir.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "editor asset tree became a symlink",
        )
    if not stat.S_ISDIR(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "editor asset tree is not a directory",
        )
    for file_entry in asset_tree["files"]:
        rel = str(file_entry["path"])
        expected = str(file_entry["sha256"])
        target = assets_dir / rel
        _unlink_owned_regular(target, expected_sha256=expected)
    # Remove empty directories bottom-up; leave non-empty trees as conflicts.
    for dirpath, dirnames, filenames in os.walk(assets_dir, topdown=False):
        current = Path(dirpath)
        if dirnames or filenames:
            # leftover unexpected content
            remaining = list(current.iterdir())
            if remaining:
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    f"editor asset tree has unexpected contents under {current.name}",
                )
        try:
            current.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ArtifactOwnershipError(
                "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                f"could not remove asset directory {current.name}: {exc}",
            ) from exc


def _validate_owned_bridge_info(
    project_root: Path,
    *,
    expected_session_id: str | None,
) -> Path | None:
    info_path = Path(project_root) / ".renforge" / "control" / _BRIDGE_INFO_BASENAME
    try:
        info_path.lstat()
    except FileNotFoundError:
        return None
    if info_path.is_symlink():
        raise LaunchError(
            "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
            "Bridge metadata path is a symlink and was left untouched.",
            phase="cleaning_bridge_artifacts",
            suggested_fix="Remove unsafe paths under .renforge/control and retry.",
        )
    try:
        read_bridge_info(
            project_root,
            require_ready=False,
            expected_session_id=expected_session_id,
        )
    except LaunchError:
        raise
    except (BridgeProtocolError, PrivatePathError, OSError, ValueError) as exc:
        raise LaunchError(
            "BRIDGE_INFO_CONFLICT",
            "Bridge metadata failed validation and was left untouched.",
            phase="cleaning_bridge_artifacts",
            suggested_fix="Remove the conflicting .renforge/control/bridge.json and retry.",
        ) from exc
    return info_path


def _remove_owned_bridge_info(
    project_root: Path,
    *,
    expected_session_id: str | None,
) -> None:
    info_path = _validate_owned_bridge_info(
        project_root,
        expected_session_id=expected_session_id,
    )
    if info_path is None:
        return
    try:
        info_path.unlink()
    except FileNotFoundError:
        return


def _validate_cleanup_targets(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    remove_bridge_info: bool,
) -> None:
    game_dir = root / "game"
    for entry in manifest["sources"]:
        _validate_owned_regular(
            game_dir / str(entry["basename"]),
            expected_sha256=str(entry["sha256"]),
        )
        for sibling in entry["generated_siblings"]:
            _validate_owned_regular(game_dir / str(sibling))
    asset_tree = manifest.get("asset_tree")
    if asset_tree is not None:
        _validate_asset_tree(game_dir, asset_tree)
    if remove_bridge_info:
        _validate_owned_bridge_info(
            root,
            expected_session_id=str(manifest["session_id"]),
        )


def remove_owned_artifacts(
    project_root: Path,
    *,
    expected_session_id: str | None = None,
    remove_bridge_info: bool = True,
) -> bool:
    """Remove schema-3 owned artifacts only after full ownership validation.

    Returns ``False`` when no manifest exists, ``True`` after complete removal.
    """
    root = Path(project_root)
    try:
        manifest = load_validated_manifest(root, expected_session_id=expected_session_id)
    except ArtifactOwnershipError:
        raise
    if manifest is None:
        # No schema-3 ownership — never delete unowned fixed names or legacy files.
        return False

    _validate_cleanup_targets(
        root,
        manifest,
        remove_bridge_info=remove_bridge_info,
    )

    game_dir = root / "game"
    for entry in manifest["sources"]:
        basename = str(entry["basename"])
        digest = str(entry["sha256"])
        source_path = game_dir / basename
        _unlink_owned_regular(source_path, expected_sha256=digest)
        for sibling in entry["generated_siblings"]:
            sibling_path = game_dir / str(sibling)
            # Compiled siblings may appear after Ren'Py runs; delete if regular.
            try:
                st = sibling_path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode):
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    f"generated sibling became a symlink: {sibling}",
                )
            if not stat.S_ISREG(st.st_mode):
                raise ArtifactOwnershipError(
                    "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
                    f"generated sibling is not a regular file: {sibling}",
                )
            sibling_path.unlink()

    asset_tree = manifest.get("asset_tree")
    if asset_tree is not None:
        _remove_asset_tree(game_dir, asset_tree)

    if remove_bridge_info:
        _remove_owned_bridge_info(root, expected_session_id=str(manifest["session_id"]))

    manifest_path = artifacts_path(root)
    try:
        st = manifest_path.lstat()
    except FileNotFoundError:
        return True
    if stat.S_ISLNK(st.st_mode):
        raise ArtifactOwnershipError(
            "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT",
            "artifacts manifest became a symlink during cleanup",
        )
    manifest_path.unlink()
    return True


__all__ = [
    "ArtifactOwnershipError",
    "MaterializedArtifacts",
    "allocate_and_materialize",
    "artifacts_path",
    "bridge_basename",
    "build_manifest",
    "editor_assets_dirname",
    "editor_basename",
    "load_validated_manifest",
    "remove_owned_artifacts",
    "session_init_basename",
    "session_init_payload",
    "sha256_bytes",
    "validate_artifacts_manifest",
]
