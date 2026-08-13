"""Contract tests: agents and humans can discover the Live Editor.

These tests pin public discoverability only — capability payload, tool
docstring, guide, screenshots, and gitignore trackability. They do not run
the live Ren'Py editor suites.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from renforge.server import _FallbackServer, _register_tools, create_app


ROOT = Path(__file__).resolve().parents[1]

PUBLIC_SCREENSHOTS = (
    ROOT / ".github" / "screenshots" / "live-editor-editable-selection.png",
    ROOT / ".github" / "screenshots" / "live-editor-locked-reason.png",
)

LIVE_EDITOR_GUIDE = ROOT / "docs" / "LIVE_EDITOR.md"

# Public MCP tools that must appear in the agent workflow (never private handlers).
PUBLIC_WORKFLOW_TOOLS = (
    "renforge_info",
    "renforge_launch",
    "renforge_launch_status",
    "renforge_screenshot",
    "renforge_scene_tree",
    "renforge_click_at",
    "renforge_click_element",
    "renforge_stop",
)

PRIVATE_HANDLERS = (
    "editor_task0_",
    "editor_task0_status",
    "editor_task0_select",
)


class _ToolRegistry:
    _renforge_testing_registry = True

    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


def _live_editor_from_payload(payload: dict) -> dict:
    assert "live_editor" in payload, "renforge_info/context must advertise live_editor"
    live = payload["live_editor"]
    assert isinstance(live, dict)
    return live


def test_info_and_context_advertise_live_editor_capability() -> None:
    """renforge_info and renforge_context share a structured live_editor capability."""
    app = _ToolRegistry()
    _register_tools(app)

    for name in ("renforge_info", "renforge_context"):
        payload = app.tools[name]()
        assert payload["ok"] is True
        live = _live_editor_from_payload(payload)

        assert live["enabled_by_default"] is True
        assert live["launch_tool"] == "renforge_launch"
        assert live["guide"] == "docs/LIVE_EDITOR.md"

        workflow = live["agent_workflow"]
        assert isinstance(workflow, list)
        assert workflow, "agent_workflow must be a non-empty list of public steps"
        joined = " ".join(str(step) for step in workflow)
        for tool in PUBLIC_WORKFLOW_TOOLS:
            assert tool in joined, f"{name}: agent_workflow missing public tool {tool}"
        # Workflow steps must be public tools only — never private handlers.
        for private in PRIVATE_HANDLERS:
            assert private not in joined
        assert all(
            isinstance(step, str) and step.startswith("renforge_") for step in workflow
        )


def test_launch_tool_description_mentions_live_editor_default() -> None:
    """Tool discovery must state that renforge_launch enables the Live Editor by default."""
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    app = create_app()
    if isinstance(app, _FallbackServer):
        pytest.skip("MCP backend (mcp/fastmcp) not installed")

    tools = asyncio.run(app.list_tools())
    launch = next(tool for tool in tools if tool.name == "renforge_launch")
    description = launch.description or ""

    assert "Live Editor" in description
    assert "enabled by default" in description.lower() or "by default" in description.lower()
    assert "editor=false" in description.lower()
    # First safe follow-ups for agents that only read the tool catalogue.
    assert "renforge_launch_status" in description
    assert "renforge_screenshot" in description or "renforge_scene_tree" in description


def test_public_live_editor_docs_and_screenshots_exist() -> None:
    """Public guide, screenshots, README, MCP guide, and changelog advertise the Live Editor."""
    assert LIVE_EDITOR_GUIDE.is_file(), "docs/LIVE_EDITOR.md must exist for both audiences"

    guide = LIVE_EDITOR_GUIDE.read_text(encoding="utf-8")
    for tool in PUBLIC_WORKFLOW_TOOLS:
        assert f"`{tool}`" in guide or tool in guide, f"guide missing public tool {tool}"
    # Explicit forbid is required so agents do not invent private bridges.
    # The guide may name editor_task0_* only to ban them — never as a recipe.
    assert "editor_task0" in guide.lower()
    assert "never" in guide.lower() or "do not" in guide.lower() or "forbid" in guide.lower()
    assert "call `editor_task0" not in guide.lower()
    recommended = guide.split("Recommended sequence", 1)[-1].split("###", 1)[0]
    assert "editor_task0" not in recommended

    # Source-safety topics for both human and agent readers.
    for marker in (
        "preview",
        "Save",
        "locked",
        "CAS",
        "reload",
    ):
        assert marker.lower() in guide.lower(), f"guide missing safety topic: {marker}"

    # Screenshots referenced from the guide with public paths.
    for shot in PUBLIC_SCREENSHOTS:
        assert shot.is_file(), f"missing public screenshot {shot.name}"
        assert shot.stat().st_size > 10_000
        assert shot.name in guide

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Live Editor" in readme
    assert "docs/LIVE_EDITOR.md" in readme
    assert "live-editor-editable-selection.png" in readme

    mcp = (ROOT / "docs" / "MCP.md").read_text(encoding="utf-8")
    assert "Live Editor" in mcp
    assert "docs/LIVE_EDITOR.md" in mcp or "LIVE_EDITOR.md" in mcp

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    added = changelog.split("## [0.7.0]", 1)[1].split("### Added", 1)[1].split("### ", 1)[0]
    # Headline user-facing capability, not merely editor fixes under Fixed.
    assert "Live Editor" in added
    # Contributor thanks remain intact.
    assert "AxelBeary" in changelog


def test_live_editor_guide_is_trackable_despite_docs_glob() -> None:
    """docs/* is gitignored; LIVE_EDITOR.md must be explicitly un-ignored."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/*" in gitignore
    assert "!docs/LIVE_EDITOR.md" in gitignore

    # git check-ignore exits 1 when the path is not ignored.
    result = subprocess.run(
        ["git", "check-ignore", "-q", "docs/LIVE_EDITOR.md"],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 1, "docs/LIVE_EDITOR.md must not be ignored by git"


def test_generated_live_editor_outputs_stay_ignored_but_product_assets_are_trackable() -> None:
    """Local proof/media output must not hide the editor assets shipped to users."""
    generated_probes = (
        ".rf_polish.pid",
        ".rf_polish_launch.py",
        ".rf_verify.py",
        ".rf_verify_out/__probe__.png",
        ".rf_verify_work/__probe__.rpy",
        "out_2/__probe__.png",
        "marketing/remotion/audio-master/__probe__.wav",
        "marketing/remotion/out/__probe__.png",
        "marketing/remotion/out_2/__probe__.png",
        "uv.lock",
    )
    for probe in generated_probes:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", probe],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, f"generated output is not ignored: {probe}"

    product_asset_probe = "src/renforge/bridge/editor_assets/icons/__probe__.svg"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", product_asset_probe],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 1, "shipped Live Editor assets must remain trackable"
