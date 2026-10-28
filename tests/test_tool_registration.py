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

    assert len(names) == len(set(names)) == 55
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

    assert len(contract) == 55
    assert set(contract) == set(TOOL_DEFINITIONS)
    assert contract == snapshot


def test_launch_marks_language_not_reported_by_an_old_dashboard(tmp_path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_client,
        "launch_game",
        lambda *_args, **_kwargs: {
            "ok": True,
            "ready": True,
            "already_running": False,
            "current_label": "start",
            "via": "dashboard",
        },
    )
    launch = build_tool_wrappers(_MetadataToolRegistry())["renforge_launch"]

    result = launch(str(project), language="german")

    assert result["ok"] is True
    assert result["requested_language"] == "german"
    assert result["language_applied"] is False
    assert "did not report language support" in result["message"]
    assert result["via"] == "dashboard"


def test_launch_warns_when_a_delegated_language_uses_the_default_savedir(tmp_path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_client,
        "launch_game",
        lambda *_args, **_kwargs: {
            "ok": True,
            "ready": True,
            "requested_language": "german",
            "active_language": "german",
            "language_applied": True,
        },
    )
    launch = build_tool_wrappers(_MetadataToolRegistry())["renforge_launch"]

    default_savedir = launch(str(project), language="german")
    isolated = launch(str(project), language="german", savedir="temporary")

    assert "warning" in default_savedir
    assert "warning" not in isolated


def test_failed_delegated_launch_never_claims_a_language(tmp_path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_client,
        "launch_game",
        lambda *_args, **_kwargs: {
            "ok": False,
            "ready": False,
            "error": "dashboard refused",
            "language_applied": True,
        },
    )
    launch = build_tool_wrappers(_MetadataToolRegistry())["renforge_launch"]

    result = launch(str(project), language="german")

    assert result["ok"] is False
    assert result["language_applied"] is False
