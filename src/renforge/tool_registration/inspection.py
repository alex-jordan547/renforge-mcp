"""Screenshot, scene, measurement, and visual-inspection MCP tools."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

TOOL_NAMES = (
    "renforge_diff_screenshots",
    "renforge_scene_tree",
    "renforge_measure",
    "renforge_screenshot",
    "renforge_capture_screenshot",
    "renforge_estimate_translation",
    "renforge_find_image_on_screen",
)


def build_wrappers(context):
    live = context.live
    _log_tool_call = context.log_tool_call
    _png_content = context.png_content

    def renforge_diff_screenshots(
        project_path: str,
        before_path: str,
        after_path: str = "",
        threshold: int = 0,
    ) -> dict:
        """Diff two frames and return the bounding box of what changed.

        ``before_path`` is a saved PNG. ``after_path`` is another saved PNG, or
        empty to diff against the current live frame. Use it to measure how far
        an element moved or to confirm a tweak left everything else untouched.
        """
        def _diff() -> dict:
            from ..image_ops import diff_images

            before = Path(before_path).expanduser()
            if not before.is_absolute():
                before = Path(project_path).expanduser() / before
            if after_path:
                after: Any = Path(after_path).expanduser()
                if not after.is_absolute():
                    after = Path(project_path).expanduser() / after
            else:
                try:
                    after = live.screenshot_png(project_path)
                except FileNotFoundError:
                    return {"ok": False, "error": "no running game; call renforge_launch first"}
            return diff_images(before, after, threshold=threshold)

        return _log_tool_call(
            name="renforge_diff_screenshots",
            params={
                "project_path": project_path,
                "before_path": before_path,
                "after_path": after_path,
                "threshold": threshold,
            },
            project_root=project_path,
            fn=_diff,
            args=(),
            kwargs={},
        )


    def renforge_scene_tree(
        project_path: str,
        detail: str = "semantic",
        layers: list[str] | None = None,
        types: list[str] | None = None,
        screen: str = "",
        ids: list[str] | None = None,
        include: list[str] | None = None,
        format: str = "json",
        save_as: str = "",
        diff_against: str = "",
        max_output_depth: int = 6,
        max_items: int = 50,
        max_output_bytes: int = 65_536,
    ) -> dict:
        """Perceive the whole scene as structured data (logical coordinates).

        Unlike `renforge_list_ui_elements` (focusables only), this reports every
        layer displayable, focusable control, and text block with `id`, `type`,
        `bounds`, `center`, `zorder` and `screen`. Every reply carries an
        `omitted` completeness hint. `detail` is `semantic` (default), `layout`
        or `raw`; `layers`/`types`/`screen`/`ids` scope the result.
        `include=["color","style","overflow"]` opts into composited colour,
        declared style, and best-effort text overflow. `format="wireframe"` adds
        an ASCII map. `save_as` persists a snapshot under `.renforge/scenes/`;
        `diff_against` diffs the live scene against a saved snapshot.
        """
        return _log_tool_call(
            name="renforge_scene_tree",
            params={
                "project_path": project_path,
                "detail": detail,
                "layers": layers,
                "types": types,
                "screen": screen,
                "ids": ids,
                "include": include,
                "format": format,
                "save_as": save_as,
                "diff_against": diff_against,
                "max_items": max_items,
                "max_output_depth": max_output_depth,
                "max_output_bytes": max_output_bytes,
            },
            project_root=project_path,
            fn=live.scene_tree,
            args=(project_path,),
            kwargs={
                "detail": detail or None,
                "layers": layers or None,
                "types": types or None,
                "screen": screen or None,
                "ids": ids or None,
                "include": include or None,
                "format": format or "json",
                "save_as": save_as or None,
                "diff_against": diff_against or None,
                "max_items": max_items,
                "max_output_depth": max_output_depth,
                "max_output_bytes": max_output_bytes,
            },
        )


    def renforge_measure(
        project_path: str,
        action: str,
        targets: list[Any],
        within: Any = None,
        tolerance: float | None = None,
    ) -> dict:
        """Measure pixel relationships between scene nodes, without eyes.

        `action` is one of `align`, `gap`, `distribute`, `center`, `overlap`,
        `fit`, `contrast`. Each target (and `within`) is a `renforge_scene_tree`
        node `id` (string, resolved live) or a literal bounds object
        `{x,y,width,height}`. Returns actionable deltas in logical pixels; when
        `tolerance` is given, adds a `pass` verdict. `contrast` samples the live
        frame and reports a WCAG ratio (one target = its internal fg/bg, two
        targets = between the two elements).
        """
        return _log_tool_call(
            name="renforge_measure",
            params={
                "project_path": project_path,
                "action": action,
                "targets": targets,
                "within": within,
                "tolerance": tolerance,
            },
            project_root=project_path,
            fn=live.measure,
            args=(project_path,),
            kwargs={
                "action": action,
                "targets": targets,
                "within": within,
                "tolerance": tolerance,
            },
        )


    def renforge_screenshot(
        project_path: str,
        width: int = 0,
        height: int = 0,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_width: int = 0,
        crop_height: int = 0,
        scale: float = 1.0,
        grid: int = 0,
        crosshair_x: int = -1,
        crosshair_y: int = -1,
        rulers: bool = False,
    ):
        """Capture a game frame, optionally resizing, cropping, and zooming it.

        Measurement guides help pixel-perfect placement: ``grid`` draws lines
        every N pixels, ``rulers`` labels those steps along the edges, and
        ``crosshair_x``/``crosshair_y`` mark a point. Capture at the game's
        logical resolution (``width``/``height``) so the labels read as logical
        coordinates. Passing only one of ``width``/``height`` keeps the game's
        aspect ratio.
        """
        def _tool() -> Any:
            try:
                if width < 0 or height < 0:
                    raise ValueError("width and height must be non-negative")
                if (crosshair_x < 0) != (crosshair_y < 0):
                    raise ValueError("crosshair_x and crosshair_y must be provided together")
                if width or height:
                    png = live.screenshot_png(project_path, width=width, height=height)
                else:
                    png = live.screenshot_png(project_path)
                if crop_width or crop_height or crop_x or crop_y or scale != 1.0:
                    from ..image_ops import transform_png

                    png = transform_png(
                        png,
                        crop_x=crop_x,
                        crop_y=crop_y,
                        crop_width=crop_width,
                        crop_height=crop_height,
                        scale=scale,
                    )
                if grid or rulers or crosshair_x >= 0:
                    from ..image_ops import annotate_png

                    png = annotate_png(
                        png,
                        grid=grid,
                        rulers=rulers,
                        crosshair=(crosshair_x, crosshair_y) if crosshair_x >= 0 else None,
                    )
            except FileNotFoundError:
                return {"ok": False, "error": "no running game; call renforge_launch first"}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            # Return a raw MCP content block: helper classes like fastmcp.Image
            # moved between fastmcp versions, and an Image object from the
            # wrong package gets stringified instead of rendered.
            return _png_content(png)

        return _log_tool_call(
            name="renforge_screenshot",
            params={
                "project_path": project_path,
                "width": width,
                "height": height,
                "crop_x": crop_x,
                "crop_y": crop_y,
                "crop_width": crop_width,
                "crop_height": crop_height,
                "scale": scale,
                "grid": grid,
                "crosshair_x": crosshair_x,
                "crosshair_y": crosshair_y,
                "rulers": rulers,
            },
            project_root=project_path,
            fn=_tool,
            args=(),
            kwargs={},
        )


    def renforge_capture_screenshot(
        project_path: str,
        name: str = "capture",
        width: int = 0,
        height: int = 0,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_width: int = 0,
        crop_height: int = 0,
        scale: float = 1.0,
        grid: int = 0,
        crosshair_x: int = -1,
        crosshair_y: int = -1,
        rulers: bool = False,
    ) -> dict:
        """Persist a screenshot under the project's controlled capture directory."""
        def _capture() -> dict:
            from io import BytesIO
            from PIL import Image
            from ..captures import validate_capture_name, write_project_capture

            validate_capture_name(name)
            if width < 0 or height < 0:
                raise ValueError("width and height must be non-negative")
            if (crosshair_x < 0) != (crosshair_y < 0):
                raise ValueError("crosshair_x and crosshair_y must be provided together")
            png = live.screenshot_png(project_path, width=width, height=height)
            if crop_width or crop_height or crop_x or crop_y or scale != 1.0:
                from ..image_ops import transform_png
                png = transform_png(png, crop_x=crop_x, crop_y=crop_y,
                                    crop_width=crop_width, crop_height=crop_height,
                                    scale=scale)
            if grid or rulers or crosshair_x >= 0:
                from ..image_ops import annotate_png
                png = annotate_png(png, grid=grid, rulers=rulers,
                                   crosshair=(crosshair_x, crosshair_y) if crosshair_x >= 0 else None)
            project_root, target = write_project_capture(project_path, name, png)
            with Image.open(BytesIO(png)) as image:
                size = {"width": image.width, "height": image.height}
            return {"ok": True, "name": name, "path": str(target),
                    "relative_path": str(target.relative_to(project_root)),
                    "sha256": hashlib.sha256(png).hexdigest(), "size": size}

        return _log_tool_call(
            name="renforge_capture_screenshot",
            params={"project_path": project_path, "name": name, "width": width, "height": height},
            project_root=project_path, fn=_capture, args=(), kwargs={})


    def renforge_estimate_translation(
        before_path: str,
        after_path: str,
        region_x: int = 0,
        region_y: int = 0,
        region_width: int = 0,
        region_height: int = 0,
        threshold: int = 16,
        max_shift: int = 64,
    ) -> dict:
        """Estimate stable visual translation between two saved frames."""
        def _estimate() -> dict:
            from ..image_ops import estimate_translation
            region = None
            if region_width or region_height or region_x or region_y:
                region = (region_x, region_y, region_width, region_height)
            return estimate_translation(
                before_path,
                after_path,
                region=region,
                threshold=threshold,
                max_shift=max_shift,
            )

        return _log_tool_call(
            name="renforge_estimate_translation",
            params={"before_path": before_path, "after_path": after_path},
            project_root=None,
            fn=_estimate,
            args=(),
            kwargs={},
        )


    def renforge_find_image_on_screen(
        project_path: str,
        template_path: str,
        threshold: float = 0.92,
        max_matches: int = 20,
        region_x: int = 0,
        region_y: int = 0,
        region_width: int = 0,
        region_height: int = 0,
    ) -> dict:
        """Find a template image in the current frame and return its bounds."""
        def _find() -> dict:
            from ..image_ops import find_image_matches

            if (region_width == 0) != (region_height == 0):
                raise ValueError("region_width and region_height must be provided together")
            if (region_x or region_y) and not (region_width and region_height):
                raise ValueError("region coordinates require region_width and region_height")
            screenshot = live.screenshot_png(project_path)
            template = Path(template_path).expanduser()
            if not template.is_absolute():
                template = Path(project_path).expanduser() / template
            region = (
                (region_x, region_y, region_width, region_height)
                if region_width and region_height
                else None
            )
            result = find_image_matches(
                screenshot,
                template,
                threshold=threshold,
                max_matches=max_matches,
                region=region,
            )
            result["frame_id"] = hashlib.sha256(screenshot).hexdigest()
            result["coordinate_space"] = "screenshot"
            result["click_hint"] = {
                "coordinate_space": "screenshot",
                "expected_frame_id": result["frame_id"],
            }
            return result

        return _log_tool_call(
            name="renforge_find_image_on_screen",
            params={
                "project_path": project_path,
                "template_path": template_path,
                "threshold": threshold,
                "max_matches": max_matches,
                "region_x": region_x,
                "region_y": region_y,
                "region_width": region_width,
                "region_height": region_height,
            },
            project_root=project_path,
            fn=_find,
            args=(),
            kwargs={},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
