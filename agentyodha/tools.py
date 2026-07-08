"""Tool definitions: the @tool decorator, JSON-schema generation, and a global registry.

Schemas are generated from Python type hints and Google-style docstrings, so a
plain function becomes an API-ready tool with no hand-written JSON:

    @tool
    def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> str:
        '''Get current weather for a location.

        Args:
            location: City and state, e.g. San Francisco, CA.
            unit: Temperature unit.
        '''
        ...
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}

_NONE_TYPE = type(None)


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation into a JSON-schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)

    if origin is Literal:
        values = list(get_args(annotation))
        base = type(values[0]) if values else str
        return {"type": _PY_TO_JSON.get(base, "string"), "enum": values}

    if origin is Union:
        # Optional[X] -> schema for X (optionality is handled via `required`)
        non_none = [a for a in get_args(annotation) if a is not _NONE_TYPE]
        if len(non_none) == 1:
            return _annotation_to_schema(non_none[0])
        return {"anyOf": [_annotation_to_schema(a) for a in non_none]}

    if origin in (list, tuple, set):
        args = get_args(annotation)
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}

    if origin is dict:
        return {"type": "object"}

    return {"type": _PY_TO_JSON.get(annotation, "string")}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (summary, {param: description})."""
    if not doc:
        return "", {}
    lines = doc.strip().splitlines()

    summary_lines: list[str] = []
    for line in lines:
        if re.match(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", line):
            break
        summary_lines.append(line)
    summary = " ".join(l.strip() for l in summary_lines if l.strip())

    params: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in lines:
        if re.match(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", line):
            in_args = True
            continue
        if in_args:
            if re.match(r"^\s*(Returns|Raises|Yields|Examples?)\s*:\s*$", line):
                break
            m = re.match(r"^\s+(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$", line)
            if m:
                current = m.group(1)
                params[current] = m.group(2).strip()
            elif current and line.strip():
                params[current] += " " + line.strip()
    return summary, params


def build_input_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON schema for a function's parameters."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    _, param_docs = _parse_docstring(inspect.getdoc(fn) or "")

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        schema = _annotation_to_schema(hints.get(name, param.annotation))
        if name in param_docs:
            schema["description"] = param_docs[name]
        properties[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


class Tool:
    """A callable tool with an API-ready definition."""

    def __init__(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ):
        self.fn = fn
        self.name = name or fn.__name__
        summary, _ = _parse_docstring(inspect.getdoc(fn) or "")
        self.description = description or summary or self.name
        self.input_schema = input_schema or build_input_schema(fn)

    def to_api(self) -> dict[str, Any]:
        """The tool definition dict to pass in the API `tools` parameter."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __call__(self, **kwargs: Any) -> Any:
        return self.fn(**kwargs)

    def __repr__(self) -> str:
        return f"Tool({self.name!r})"


class ToolRegistry:
    """Global name -> Tool mapping so YAML configs can reference tools by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> Tool:
        self._tools[t.name] = t
        return t

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            available = ", ".join(sorted(self._tools)) or "(none registered)"
            raise KeyError(f"Unknown tool {name!r}. Registered tools: {available}") from None

    def resolve(self, names: list[str]) -> list[Tool]:
        return [self.get(n) for n in names]

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools


registry = ToolRegistry()


def tool(fn: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None):
    """Decorator that turns a function into a registered Tool.

    Usage: @tool  or  @tool(name="custom_name", description="...")
    """

    def wrap(f: Callable[..., Any]) -> Tool:
        return registry.register(Tool(f, name=name, description=description))

    if fn is not None:
        return wrap(fn)
    return wrap
