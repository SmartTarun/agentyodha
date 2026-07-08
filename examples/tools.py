"""Demo tools. Import via --tools-module examples.tools so @tool registers them."""

from __future__ import annotations

import ast
import operator
from typing import Literal

from agentyodha import tool

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"Unsupported expression element: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """Evaluate an arithmetic expression and return the result.

    Args:
        expression: A plain arithmetic expression, e.g. "1847 * 392" or "(2+3)**4".
    """
    result = _safe_eval(ast.parse(expression, mode="eval"))
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


@tool
def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> str:
    """Get the current weather for a location (demo stub — returns fake data).

    Args:
        location: City name, e.g. "Hyderabad" or "San Francisco, CA".
        unit: Temperature unit to report.
    """
    temp = 22 if unit == "celsius" else 72
    return f"The weather in {location} is sunny, {temp} degrees {unit}."
