"""Backend-aware MCP tool registration with fail-closed contract checks."""

from __future__ import annotations

from functools import update_wrapper
from inspect import signature
from types import FunctionType
from typing import Annotated, Any, Callable, Literal, get_args, get_origin, get_type_hints

from pydantic import Field

from ..tool_definitions import TOOL_DEFINITIONS


class ToolRegistrar:
    def __init__(self, app: Any) -> None:
        tool_decorator = getattr(app, "tool", None)
        if not callable(tool_decorator):
            raise TypeError("MCP backend does not expose a callable tool decorator")
        self.app = app
        self.tool_decorator = tool_decorator
        self.registered_names: set[str] = set()

    @staticmethod
    def _to_annotations(raw: dict[str, bool]) -> Any:
        try:
            from mcp.types import ToolAnnotations
        except Exception:  # pragma: no cover - compatibility with older backends
            return raw
        return ToolAnnotations(
            readOnlyHint=raw["readOnlyHint"],
            idempotentHint=raw["idempotentHint"],
            destructiveHint=raw["destructiveHint"],
            openWorldHint=raw["openWorldHint"],
        )

    @staticmethod
    def _clone_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
        clone = FunctionType(
            fn.__code__,
            fn.__globals__,
            name=fn.__name__,
            argdefs=fn.__defaults__,
            closure=fn.__closure__,
        )
        clone.__kwdefaults__ = dict(fn.__kwdefaults__ or {})
        update_wrapper(clone, fn)
        del clone.__wrapped__
        clone.__dict__.update(fn.__dict__)
        return clone

    @staticmethod
    def _annotate_parameters(
        fn: Callable[..., Any],
        metadata: Any,
    ) -> Callable[..., Any]:
        annotated = ToolRegistrar._clone_wrapper(fn)
        resolved_hints = get_type_hints(fn, include_extras=True)
        decorated_annotations: dict[str, Any] = {}
        for param, annotation in resolved_hints.items():
            if param == "return":
                continue
            annotation = metadata.parameter_types.get(param, annotation)
            schema = metadata.parameter_schemas.get(param, {})
            enum_values = schema.get("enum")
            if enum_values:
                literal = Literal.__getitem__(tuple(enum_values))
                annotation = literal | None if type(None) in get_args(annotation) else literal
            item_enum = (schema.get("items") or {}).get("enum")
            if item_enum:
                literal_item = Literal.__getitem__(tuple(item_enum))
                origin = get_origin(annotation)
                args = get_args(annotation)
                if origin is list:
                    annotation = list[literal_item]
                elif type(None) in args:
                    annotation = list[literal_item] | None
            decorated_annotations[param] = Annotated[
                annotation,
                Field(
                    description=metadata.parameters[param],
                    ge=schema.get("minimum"),
                    le=schema.get("maximum"),
                    pattern=schema.get("pattern"),
                    json_schema_extra=schema or None,
                ),
            ]
        if "return" in resolved_hints:
            decorated_annotations["return"] = resolved_hints["return"]
        annotated.__annotations__ = decorated_annotations
        annotated.__doc__ = metadata.description
        return annotated

    def register(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        name = fn.__name__
        metadata = TOOL_DEFINITIONS.get(name)
        if metadata is None:
            raise RuntimeError(f"Missing tool definition for {name}")
        if name in self.registered_names:
            raise RuntimeError(f"Duplicate tool registration for {name}")

        expected_parameters = set(signature(fn).parameters)
        documented_parameters = set(metadata.parameters)
        if expected_parameters != documented_parameters:
            missing = sorted(expected_parameters - documented_parameters)
            extra = sorted(documented_parameters - expected_parameters)
            raise RuntimeError(
                f"Tool definition drift for {name}: params mismatch "
                f"missing={missing} extra={extra}"
            )

        annotated_tool = self._annotate_parameters(fn, metadata)
        decorator_kwargs = {
            "description": metadata.description,
            "annotations": self._to_annotations(metadata.annotations),
        }
        try:
            decorator_signature = signature(self.tool_decorator)
        except (TypeError, ValueError):
            if not getattr(self.app, "_renforge_testing_registry", False):
                raise RuntimeError(
                    "MCP tool backend cannot prove support for required description and annotations metadata"
                )
            supported_kwargs = {}
        else:
            accepts_kwargs = any(
                parameter.kind == parameter.VAR_KEYWORD
                for parameter in decorator_signature.parameters.values()
            )
            supported_kwargs = (
                decorator_kwargs
                if accepts_kwargs
                else {
                    key: value
                    for key, value in decorator_kwargs.items()
                    if key in decorator_signature.parameters
                }
            )
            missing_metadata = set(decorator_kwargs) - set(supported_kwargs)
            if missing_metadata and not getattr(self.app, "_renforge_testing_registry", False):
                raise RuntimeError(
                    "MCP tool backend cannot accept required description and annotations metadata"
                )

        registered = self.tool_decorator(**supported_kwargs)(annotated_tool)
        self.registered_names.add(name)
        self._apply_object_parameter_schemas(name, metadata)
        return registered

    def register_many(
        self,
        wrappers: dict[str, Callable[..., Any]],
        names: tuple[str, ...],
    ) -> None:
        for name in names:
            try:
                wrapper = wrappers[name]
            except KeyError as exc:
                raise RuntimeError(f"Missing tool wrapper for {name}") from exc
            self.register(wrapper)

    def _apply_object_parameter_schemas(self, name: str, metadata: Any) -> None:
        component = None
        local_provider = getattr(self.app, "_local_provider", None)
        components = getattr(local_provider, "_components", None)
        if isinstance(components, dict):
            component = next(
                (item for item in components.values() if getattr(item, "name", None) == name),
                None,
            )
        tool_manager = getattr(self.app, "_tool_manager", None)
        managed_tools = getattr(tool_manager, "_tools", None)
        if component is None and isinstance(managed_tools, dict):
            component = managed_tools.get(name)
        parameters = getattr(component, "parameters", None)
        if not isinstance(parameters, dict):
            return
        for param, schema in metadata.parameter_schemas.items():
            if schema.get("type") != "object":
                continue
            property_schema = parameters.get("properties", {}).get(param)
            if not isinstance(property_schema, dict) or "anyOf" not in property_schema:
                continue
            description = property_schema.get("description")
            default = property_schema.get("default")
            property_schema.clear()
            property_schema["anyOf"] = [schema, {"type": "null"}]
            property_schema["default"] = default
            property_schema["description"] = description
        parameters.update(metadata.input_schema)
