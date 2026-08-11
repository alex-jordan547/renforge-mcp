from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dev_editor() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "renforge_dev_editor", REPO_ROOT / "scripts" / "dev_editor.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(role: str, basename: str, digest: str) -> dict[str, object]:
    return {
        "role": role,
        "basename": basename,
        "sha256": digest,
        "generated_siblings": [basename + "c", basename + "c.bak"],
    }


def _write_schema3_manifest(project_root: Path) -> tuple[Path, str]:
    from renforge.bridge.artifacts import _publish_intent, artifacts_path

    editor_basename = "zzrenforge_editor_" + "a" * 32 + ".rpy"
    manifest = {
        "schema_version": 3,
        "session_id": "a" * 32,
        "project_root": str(project_root.resolve()),
        "bridge_info": "bridge.json",
        "sources": [
            _source("bridge", "zzrenforge_bridge_" + "a" * 32 + ".rpy", "1" * 64),
            _source("editor", editor_basename, "2" * 64),
        ],
        "asset_tree": {
            "dirname": editor_basename.removesuffix(".rpy"),
            "files": [],
        },
    }
    _publish_intent(project_root, manifest)
    return artifacts_path(project_root), editor_basename


def test_injected_editor_path_reads_schema3_artifact_manifest(tmp_path: Path) -> None:
    module = _load_dev_editor()
    project_root = tmp_path / "project"
    (project_root / "game").mkdir(parents=True)
    _manifest_path, editor_basename = _write_schema3_manifest(project_root)

    assert module._injected_editor_path(project_root) == project_root / "game" / editor_basename


def test_restamp_manifest_updates_schema3_editor_digest(tmp_path: Path) -> None:
    module = _load_dev_editor()
    project_root = tmp_path / "project"
    (project_root / "game").mkdir(parents=True)
    manifest_path, _editor_basename = _write_schema3_manifest(project_root)
    payload = b"updated editor payload"

    module._restamp_manifest(project_root, payload)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    editor = next(entry for entry in manifest["sources"] if entry["role"] == "editor")
    assert editor["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "source_sha256" not in manifest
