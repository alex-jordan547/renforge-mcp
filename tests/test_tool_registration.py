import asyncio
import hashlib
import inspect
import json

from renforge.server import create_app
from renforge.tool_definitions import TOOL_DEFINITIONS
from renforge.tool_registration import DOMAIN_MODULES
from renforge.tool_registration.registry import ToolRegistrar
from renforge.tool_registration.wrappers import build_tool_wrappers


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


def test_public_tool_contract_matches_pre_refactor_snapshot() -> None:
    contract = _public_contract()
    payload = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(contract) == 54
    assert hashlib.sha256(payload).hexdigest() == (
        "768d929452553d953c107d9a4e04e21b43e6e9dfd41df12dffff6095d7d5c3d7"
    )
