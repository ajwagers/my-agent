"""
Calculate skill — safe AST-based arithmetic and math-function evaluator.

No eval(). Only whitelisted node types and functions are permitted.
"""

import ast
import math
import operator
from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "ceil": math.ceil,
    "floor": math.floor,
    "factorial": math.factorial,
    "degrees": math.degrees,
    "radians": math.radians,
    "hypot": math.hypot,
    "round": round,
    "gcd": math.gcd,
}

_FORBIDDEN_TOKENS = ("__", "import", "exec", "eval", "open", "lambda")


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Expression type not allowed: constant {type(node.value).__name__}")
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINOPS:
            raise ValueError(f"Expression type not allowed: operator {op_type.__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return _BINOPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARYOPS:
            raise ValueError(f"Expression type not allowed: unary operator {op_type.__name__}")
        return _UNARYOPS[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id not in _NAMES:
            raise ValueError(f"Expression type not allowed: name '{node.id}'")
        return _NAMES[node.id]
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError(f"Expression type not allowed: function '{getattr(node.func, 'id', '?')}'")
        args = [_safe_eval(a) for a in node.args]
        return _FUNCTIONS[node.func.id](*args)
    raise ValueError(f"Expression type not allowed: {type(node).__name__}")


class CalculateSkill(SkillBase):
    """Evaluate mathematical expressions safely using an AST whitelist."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="calculate",
            description=(
                "Evaluate a mathematical expression. Supports arithmetic, "
                "exponentiation, modulo, and math functions (sqrt, sin, cos, tan, "
                "log, exp, factorial, etc.) and constants (pi, e, tau)."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="calculate",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate, e.g. 'sqrt(144) + 2**8'.",
                    },
                },
                "required": ["expression"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        expr = params.get("expression", "")
        if not isinstance(expr, str) or not expr.strip():
            return False, "Parameter 'expression' must be a non-empty string"
        if len(expr) > 500:
            return False, "Parameter 'expression' must be under 500 characters"
        for token in _FORBIDDEN_TOKENS:
            if token in expr:
                return False, f"Expression contains forbidden token: '{token}'"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        expression = params["expression"].strip()
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as e:
            return {"error": f"Syntax error: {e}"}
        try:
            result = _safe_eval(tree)
        except ZeroDivisionError:
            return {"error": "Division by zero"}
        except OverflowError:
            return {"error": "Result is too large (overflow)"}
        except (ValueError, TypeError) as e:
            return {"error": str(e)}
        try:
            finite = math.isfinite(result)
        except OverflowError:
            return {"error": "Result is too large (overflow)"}
        if not isinstance(result, (int, float)) or not finite:
            return {"error": f"Result is not finite: {result}"}
        return {"result": result, "expression": expression}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[calculate] {result['error']}"
        if isinstance(result, dict):
            expr = result.get("expression", "")
            val = result.get("result")
            if isinstance(val, float) and val == int(val):
                formatted = str(int(val))
            elif isinstance(val, float):
                formatted = f"{val:.10g}"
            else:
                formatted = str(val)
            return f"{expr} = {formatted}"
        return str(result)
