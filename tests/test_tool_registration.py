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

    assert len(names) == len(set(names)) == 54
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

    assert len(contract) == 54
    assert set(contract) == set(TOOL_DEFINITIONS)
    assert contract == snapshot
