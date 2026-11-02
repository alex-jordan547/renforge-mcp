import asyncio
import inspect
import json
from pathlib import Path

from renforge.server import create_app
from renforge.tool_definitions import TOOL_DEFINITIONS
from renforge.tool_registration import DOMAIN_MODULES
from renforge.tool_registration.registry import ToolRegistrar
from renforge.tool_registration.wrappers import build_tool_wrappers

CONTRACT_SNAPSHOT = (
    Path(__file__).resolve().parent / "snapshots" / "mcp_public_tool_contract.json"
)


class _MetadataToolRegistry:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, description=None, annotations=None):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


def _public_contract() -> dict:
    tools = asyncio.run(create_app().list_tools())
    contract = {}
    for tool in tools:
        if not tool.name.startswith("renforge_"):
            continue
        annotations = tool.annotations
        contract[tool.name] = {
            "description": tool.description,
            "annotations": {
                key: getattr(annotations, key, None)
                for key in (
                    "readOnlyHint",
                    "idempotentHint",
                    "destructiveHint",
                    "openWorldHint",
                )
            },
            "parameters": tool.parameters,
        }
    return contract


def test_domain_modules_partition_the_public_tool_catalog() -> None:
    names = [name for domain in DOMAIN_MODULES for name in domain.TOOL_NAMES]

    assert len(names) == len(set(names)) == 56
    assert set(names) == set(TOOL_DEFINITIONS)


def test_registration_does_not_mutate_wrapper_annotations_or_docstring() -> None:
    app = _MetadataToolRegistry()
    wrapper = build_tool_wrappers(app)["renforge_send_input"]
    annotations_before = dict(wrapper.__annotations__)
    doc_before = wrapper.__doc__

    registered = ToolRegistrar(app).register(wrapper)

    assert wrapper.__annotations__ == annotations_before
    assert wrapper.__doc__ == doc_before
    assert registered is not wrapper


def test_server_bootstrap_no_longer_owns_tool_wrapper_bodies() -> None:
    from renforge import server

    functions = {
        name
        for name, value in vars(server).items()
        if name.startswith("renforge_") and inspect.isfunction(value)
    }

    assert functions == set()


def test_public_tool_contract_matches_agent_safe_baseline() -> None:
    contract = _public_contract()
    snapshot = json.loads(CONTRACT_SNAPSHOT.read_text(encoding="utf-8"))

    assert len(contract) == 56
    assert set(contract) == set(TOOL_DEFINITIONS)
    assert contract == snapshot


def _fake_sdk(tmp_path: Path):
    from renforge.sdk import RenpySdk

    sdk_root = tmp_path / "sdk"
    sdk_root.mkdir()
    (sdk_root / "renpy.py").write_text(
        "import sys\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    return RenpySdk(version="8.5.3", root=sdk_root)


def _validate_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text(
        "label start:\n    return\n",
        encoding="utf-8",
    )
    return project


def test_validate_wrapper_normalizes_staged_paths(monkeypatch, tmp_path) -> None:
    import shutil

    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    for staged_path in ("script.rpy", "game/script.rpy"):
        result = wrapper(
            str(project),
            staged={staged_path: "label start:\n    return\n# staged\n"},
            keep_shadow=True,
        )
        assert result["ok"] is True, result
        assert result["files"] == 1
        shadow_root = Path(result["shadow_path"])
        staged_file = shadow_root / "game" / "script.rpy"
        assert staged_file.read_text(encoding="utf-8").endswith("# staged\n")
        assert (project / "game" / "script.rpy").read_text(encoding="utf-8") == (
            "label start:\n    return\n"
        )
        assert not (project / ".renforge" / "editor-transactions").exists()
        shutil.rmtree(shadow_root, ignore_errors=True)


def test_validate_reports_media_placeholder_count(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    (project / "game" / "bg.png").write_bytes(b"x" * 100)
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )

    result = validate_tool.validate_project(str(project))

    assert result["ok"] is True, result
    assert result["files"] == 2
    assert result["placeholders"] == 1
    assert result["bytes"] == (project / "game" / "script.rpy").stat().st_size


def test_validate_project_does_not_create_project_state(monkeypatch, tmp_path) -> None:
    import shutil

    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )

    result = validate_tool.validate_project(str(project), keep_shadow=True)

    assert result["ok"] is True, result
    # Validation is read-only for the project: the SDK cache and the shadow
    # live elsewhere, so it must not create .renforge/ either.
    assert not (project / ".renforge").exists()
    shutil.rmtree(Path(result["shadow_path"]), ignore_errors=True)


def test_validate_wrapper_excludes_manifest_owned_artifacts(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool
    from renforge.util.files import atomic_write_private_json, ensure_private_directory

    project = _validate_project(tmp_path)
    injected_name = "zzrenforge_custom_inject_deadbeef.rpy"
    (project / "game" / injected_name).write_text(
        "init python:\n    pass\n",
        encoding="utf-8",
    )
    nested = project / "game" / "scenes"
    nested.mkdir()
    (nested / injected_name).write_text(
        "init python:\n    pass\n# user file\n",
        encoding="utf-8",
    )
    control_dir = ensure_private_directory(project / ".renforge" / "control")
    atomic_write_private_json(
        control_dir / "artifacts.json",
        {
            "schema_version": 3,
            "session_id": "a" * 32,
            "project_root": str(project.resolve()),
            "bridge_info": "bridge.json",
            "sources": [
                {
                    "role": "bridge",
                    "basename": injected_name,
                    "sha256": "b" * 64,
                    "generated_siblings": [
                        injected_name + "c",
                        injected_name + "c.bak",
                    ],
                }
            ],
            "asset_tree": None,
        },
        max_bytes=1024 * 1024,
    )
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    result = wrapper(str(project), keep_shadow=True)

    assert result["ok"] is True, result
    # The injected root file is excluded; the nested user file with the same
    # basename must survive.
    assert result["files"] == 2
    shadow_root = Path(result["shadow_path"])
    assert (shadow_root / "game" / "scenes" / injected_name).is_file()
    # The returned path is the temporary root itself, so deleting it leaves no
    # empty RenForge directory behind.
    assert shadow_root.name.startswith("renforge-validate-")
    import shutil

    shutil.rmtree(shadow_root, ignore_errors=True)


def test_validate_never_uses_a_project_local_sdk(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    sdk = _fake_sdk(tmp_path)
    captured: dict[str, object] = {}

    def fake_sdk(version="stable", **kwargs):
        captured["version"] = version
        captured.update(kwargs)
        return sdk

    monkeypatch.setattr(validate_tool, "get_or_install_sdk", fake_sdk)
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    result = wrapper(str(project))

    assert result["ok"] is True, result
    assert result["sdk_version"] == "8.5.3"
    assert captured == {"version": "stable"}

    captured.clear()
    explicit = wrapper(str(project), version="8.5.3")

    assert explicit["ok"] is True, explicit
    assert explicit["sdk_version"] == "8.5.3"
    assert captured == {"version": "8.5.3"}


def test_validate_does_not_log_staged_source_text(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(validate_tool, "get_or_install_sdk", lambda *args, **kwargs: sdk)
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]
    secret = "SECRET_STAGED_SOURCE_MUST_NOT_APPEAR"

    result = wrapper(
        str(project),
        staged={"script.rpy": f"label start:\n    '{secret}'\n    return\n"},
    )

    assert result["ok"] is True, result
    activity = (project / ".renforge" / "activity.jsonl").read_text(encoding="utf-8")
    assert secret not in activity
    assert "script.rpy" in activity


def test_validate_invalid_project_returns_structured_error(tmp_path) -> None:
    from renforge.tools.validate import validate_project

    result = validate_project(str(tmp_path / "missing"))

    assert result["ok"] is False
    assert result["code"] == "PROJECT_INVALID"
    assert "project_path" in result["details"]


def test_validate_wrapper_ignores_manifest_names_without_injected_prefix(
    monkeypatch, tmp_path
) -> None:
    import renforge.tools.validate as validate_tool
    from renforge.util.files import atomic_write_private_json, ensure_private_directory

    project = _validate_project(tmp_path)
    # A crafted or stale manifest must not be able to exclude user source.
    control_dir = ensure_private_directory(project / ".renforge" / "control")
    atomic_write_private_json(
        control_dir / "artifacts.json",
        {
            "schema_version": 3,
            "session_id": "a" * 32,
            "project_root": str(project.resolve()),
            "bridge_info": "bridge.json",
            "sources": [
                {
                    "role": "bridge",
                    "basename": "script.rpy",
                    "sha256": "b" * 64,
                    "generated_siblings": ["script.rpyc", "script.rpyc.bak"],
                }
            ],
            "asset_tree": None,
        },
        max_bytes=1024 * 1024,
    )
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    result = wrapper(str(project))

    assert result["ok"] is True, result
    assert result["files"] == 1


def test_validate_wrapper_excludes_known_injected_prefixes(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    (project / "game" / "zzrenforge_bridge_deadbeef.rpy").write_text(
        "init python:\n    pass\n",
        encoding="utf-8",
    )
    injected_assets = project / "game" / "zzrenforge_editor_deadbeef"
    injected_assets.mkdir()
    (injected_assets / "font.ttf").write_bytes(b"font")
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    result = wrapper(str(project))

    assert result["ok"] is True, result
    assert result["files"] == 1


def test_validate_wrapper_returns_editor_error_measurements(monkeypatch, tmp_path) -> None:
    import renforge.tools.validate as validate_tool

    project = _validate_project(tmp_path)
    sdk = _fake_sdk(tmp_path)
    monkeypatch.setattr(
        validate_tool,
        "get_or_install_sdk",
        lambda *args, **kwargs: sdk,
    )
    monkeypatch.setenv("RENFORGE_SHADOW_MAX_FILES", "1")
    monkeypatch.delenv("RENFORGE_SHADOW_MAX_BYTES", raising=False)
    wrapper = build_tool_wrappers(_MetadataToolRegistry())["renforge_validate"]

    result = wrapper(str(project), staged={"extra.rpy": "label extra:\n    return\n"})

    assert result["ok"] is False
    assert isinstance(result["error"], str)
    assert result["code"] == "SHADOW_QUOTA_EXCEEDED"
    details = result["details"]
    assert set(details) == {
        "files",
        "bytes",
        "max_files",
        "max_bytes",
        "offending_path",
        "offending_size",
        "largest_path",
        "largest_size",
    }
    assert details["files"] == 2
    assert details["max_files"] == 1
    assert details["max_bytes"] == 500 * 1024 * 1024
    assert details["offending_path"] == "game/extra.rpy"
    assert details["offending_size"] == len("label extra:\n    return\n")
    assert details["bytes"] >= details["offending_size"]
    assert details["largest_path"] == "game"
    assert details["largest_size"] > 0
    assert result["checks"] == ["lint"]

    invalid = wrapper(str(project), staged={"../escape.rpy": "x"})
    assert invalid["ok"] is False
    assert isinstance(invalid["error"], str)
    assert invalid["code"] == "SHADOW_PATH_INVALID"

    unsupported = wrapper(str(project), checks=["assets"])
    assert unsupported["ok"] is False
    assert isinstance(unsupported["error"], str)
    assert unsupported["code"] == "VALIDATE_CHECKS_UNSUPPORTED"
