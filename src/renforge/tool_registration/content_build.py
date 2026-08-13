"""Assets, translation, documentation, and build MCP tools."""

from __future__ import annotations

TOOL_NAMES = (
    "renforge_assets",
    "renforge_languages",
    "renforge_translation_stats",
    "renforge_generate_translations",
    "renforge_export_dialogue",
    "renforge_web_build",
    "renforge_distribute",
    "renforge_search_docs",
    "renforge_get_doc",
    "renforge_list_docs",
)


def build_wrappers(context):
    project_ops = context.project_ops
    _log_tool_call = context.log_tool_call

    def renforge_assets(project_path: str) -> dict:
        """Find orphaned and missing image/audio assets in the project."""
        return _log_tool_call(
            name="renforge_assets",
            params={"project_path": project_path},
            project_root=project_path,
            fn=project_ops.assets,
            args=(project_path,),
            kwargs={},
        )


    def renforge_languages(project_path: str) -> dict:
        """List translation languages present under game/tl/."""
        return _log_tool_call(
            name="renforge_languages",
            params={"project_path": project_path},
            project_root=project_path,
            fn=project_ops.languages,
            args=(project_path,),
            kwargs={},
        )


    def renforge_translation_stats(project_path: str, language: str) -> dict:
        """Report missing dialogue/string translation counts for a language."""
        return _log_tool_call(
            name="renforge_translation_stats",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.translation_stats,
            args=(project_path, language),
            kwargs={},
        )


    def renforge_generate_translations(project_path: str, language: str) -> dict:
        """Generate/update translation files for a language (writes game/tl/<language>/)."""
        return _log_tool_call(
            name="renforge_generate_translations",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.generate_translations,
            args=(project_path, language),
            kwargs={},
        )


    def renforge_export_dialogue(project_path: str, language: str = "None") -> dict:
        """Export the game's dialogue as plain text."""
        return _log_tool_call(
            name="renforge_export_dialogue",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.export_dialogue,
            args=(project_path, language),
            kwargs={},
        )


    def renforge_web_build(project_path: str, destination: str = "") -> dict:
        """Package the project as a browser-playable build (needs the web DLC)."""
        return _log_tool_call(
            name="renforge_web_build",
            params={"project_path": project_path, "destination": destination},
            project_root=project_path,
            fn=project_ops.web_build,
            args=(project_path,),
            kwargs={"destination": destination},
        )


    def renforge_distribute(project_path: str, package: str = "", destination: str = "") -> dict:
        """Build desktop distributions (e.g. package='pc', 'mac', 'linux')."""
        return _log_tool_call(
            name="renforge_distribute",
            params={"project_path": project_path, "package": package, "destination": destination},
            project_root=project_path,
            fn=project_ops.distribute,
            args=(project_path,),
            kwargs={"package": package, "destination": destination},
        )


    def renforge_search_docs(query: str) -> dict:
        """Search Ren'Py's offline documentation for a keyword."""
        return _log_tool_call(
            name="renforge_search_docs",
            params={"query": query},
            project_root=None,
            fn=project_ops.search_docs,
            args=(query,),
            kwargs={},
        )


    def renforge_get_doc(topic: str) -> dict:
        """Read a Ren'Py documentation page as plain text (e.g. topic='cli')."""
        return _log_tool_call(
            name="renforge_get_doc",
            params={"topic": topic},
            project_root=None,
            fn=project_ops.get_doc,
            args=(topic,),
            kwargs={},
        )


    def renforge_list_docs() -> dict:
        """List available Ren'Py documentation topics."""
        return _log_tool_call(
            name="renforge_list_docs",
            params={},
            project_root=None,
            fn=project_ops.list_docs,
            args=(),
            kwargs={},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
