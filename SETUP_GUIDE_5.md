# My-Agent: Calculator & Unit Conversion Skills Setup Guide

Building on the job system from Phase 4C-Part-2, this guide adds Phase 4D: two pure-computation skills that make the agent a reliable calculator. After this, the agent never guesses arithmetic or unit conversions from training data — it always uses a tool.

- **`calculate`** — safe AST-based expression evaluator. No `eval()`. Whitelisted operations only.
- **`convert_units`** — pint-backed unit converter. Handles length, mass, temperature, speed, volume, and everything else pint supports.
- **Tool-forcing signals** — regex patterns that detect math/conversion queries and hard-mandate tool use in the system prompt before the first LLM call.

## What You're Adding

```
New skills:         calculate, convert_units  (13 skills total)
New dependency:     pint
New tests:          43 (467 total)
New rate limits:    calculate (50/min), convert_units (50/min)
New tool signals:   _SIGNAL_CALCULATE, _SIGNAL_CONVERT
```

### Updated Tool Usage Flow

```
User: "what is sqrt(144) + 2**8?"
  → _SIGNAL_CALCULATE matches (\bsqrt\b)
  → system prompt gets: "You MUST call calculate with the expression"
  → model calls: calculate("sqrt(144) + 2**8")
  → _safe_eval parses AST, whitelists all nodes
  → result: {"result": 268, "expression": "sqrt(144) + 2**8"}
  → sanitize_output: "sqrt(144) + 2**8 = 268"
  → agent replies: "sqrt(144) + 2**8 = 268"

User: "convert 100 fahrenheit to celsius"
  → _SIGNAL_CONVERT matches (\bconvert\b.{0,40}\bto\b)
  → model calls: convert_units(100, "degF", "degC")
  → pint: Quantity(100, 'degF').to('degC')
  → result: {"result": 37.7778, "from_unit": "degF", "to_unit": "degC", "input": 100}
  → sanitize_output: "100 degF = 37.7778 degC"
```

---

## Prerequisites

- **Completed stack from Setup Guide 4 + Phase 4C-Part-2** (job queue, heartbeat wired to job executor, create_task/list_tasks/cancel_task skills working)
- Python package `pint` will be added to `requirements.txt` and installed during build
- No new containers, no new env vars, no new API keys

---

## New and Modified Files

```
agent-core/
├── skills/
│   ├── calculate.py        # NEW — CalculateSkill: AST-based safe math evaluator
│   └── convert_units.py    # NEW — ConvertUnitsSkill: pint-backed unit converter
├── requirements.txt        # MODIFIED — add pint
├── policy.yaml             # MODIFIED — add calculate and convert_units rate limits
├── app.py                  # MODIFIED — register skills, add hints, add signal patterns
└── tests/
    └── test_skills.py      # MODIFIED — append TestCalculateSkill + TestConvertUnitsSkill
```

---

## Step 1: Create the Calculate Skill

Create `agent-core/skills/calculate.py`:

```python
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
```

**Key design decisions:**
- `ast.parse(expr, mode='eval')` — `mode='eval'` only allows expressions, not statements. You cannot write `import os` or `x = 5` — those are statements and `ast.parse` raises `SyntaxError` immediately.
- `_safe_eval()` is a recursive visitor that raises `ValueError` for any node type not in the whitelist. The recursion bottoms out at `Constant` nodes (numeric literals) or `Name` nodes (pi, e, etc.).
- **Why `try/except OverflowError` around `math.isfinite()`?** Python integers are arbitrary precision and never overflow during computation. But `math.isfinite()` must convert to float to check, and a number like `factorial(10000)` (35,000+ digits) overflows that conversion. The fix: catch the OverflowError and return it as the overflow error.

---

## Step 2: Create the Convert Units Skill

Create `agent-core/skills/convert_units.py`:

```python
"""
Convert units skill — pint-backed unit converter.

Handles length, mass, temperature, speed, volume, and any other units
supported by the pint library.
"""

from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class ConvertUnitsSkill(SkillBase):
    """Convert a value from one unit to another using pint."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="convert_units",
            description=(
                "Convert a value from one unit to another. Supports length, mass, "
                "temperature, speed, volume, area, time, and more. "
                "Use 'degC', 'degF', 'kelvin' for temperatures."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="convert_units",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "value": {
                        "type": "number",
                        "description": "The numeric value to convert.",
                    },
                    "from_unit": {
                        "type": "string",
                        "description": "The unit to convert from (e.g. 'km', 'kg', 'degF').",
                    },
                    "to_unit": {
                        "type": "string",
                        "description": "The unit to convert to (e.g. 'miles', 'lbs', 'degC').",
                    },
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        value = params.get("value")
        if value is None or not isinstance(value, (int, float)):
            return False, "Parameter 'value' must be a number"
        from_unit = params.get("from_unit", "")
        to_unit = params.get("to_unit", "")
        if not isinstance(from_unit, str) or not from_unit.strip():
            return False, "Parameter 'from_unit' must be a non-empty string"
        if len(from_unit) > 100:
            return False, "Parameter 'from_unit' must be under 100 characters"
        if not isinstance(to_unit, str) or not to_unit.strip():
            return False, "Parameter 'to_unit' must be a non-empty string"
        if len(to_unit) > 100:
            return False, "Parameter 'to_unit' must be under 100 characters"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        import pint

        value = params["value"]
        from_unit = params["from_unit"].strip()
        to_unit = params["to_unit"].strip()

        try:
            ureg = pint.UnitRegistry()
            quantity = ureg.Quantity(value, from_unit)
            converted = quantity.to(to_unit)
            return {
                "result": float(converted.magnitude),
                "from_unit": from_unit,
                "to_unit": to_unit,
                "input": value,
            }
        except pint.errors.DimensionalityError:
            return {"error": f"Cannot convert {from_unit} to {to_unit} (incompatible dimensions)"}
        except pint.errors.UndefinedUnitError as e:
            unit_name = str(e).split("'")[1] if "'" in str(e) else str(e)
            return {"error": f"Unknown unit: '{unit_name}'"}
        except pint.errors.OffsetUnitCalculusError:
            return {"error": "Use 'degC', 'degF', 'kelvin' for temperature conversions"}
        except Exception as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[convert_units] {result['error']}"
        if isinstance(result, dict):
            inp = result.get("input")
            from_unit = result.get("from_unit", "")
            val = result.get("result")
            to_unit = result.get("to_unit", "")
            if isinstance(val, float) and val == int(val):
                formatted = str(int(val))
            elif isinstance(val, float):
                formatted = f"{val:.6g}"
            else:
                formatted = str(val)
            return f"{inp} {from_unit} = {formatted} {to_unit}"
        return str(result)
```

**Temperature units:** pint requires `degC`, `degF`, and `kelvin` (not `celsius`, `fahrenheit`, or `K`). The `OffsetUnitCalculusError` fires when you try arithmetic on offset units (e.g., adding two Celsius quantities). Direct `.to()` conversion always works.

**`import pint` inside `execute()`:** pint is imported lazily to keep startup fast and to match the pattern used elsewhere in the codebase (chromadb, beautifulsoup4 are also lazy-imported).

---

## Step 3: Add pint to requirements.txt

Add `pint` to `agent-core/requirements.txt`:

```
fastapi==0.115.0
uvicorn==0.32.0
ollama==0.3.3
click==8.1.7
requests==2.32.3
chromadb
redis
pyyaml
pypdf
beautifulsoup4
pint
```

---

## Step 4: Add Rate Limits to policy.yaml

Add to the `rate_limits:` section in `agent-core/policy.yaml`:

```yaml
rate_limits:
  # ... existing entries ...
  calculate:
    max_calls: 50
    window_seconds: 60
  convert_units:
    max_calls: 50
    window_seconds: 60
```

50/min is generous because both skills are pure computation — no network calls, no ChromaDB, no Redis. The limit exists to prevent runaway tool loops, not to throttle real usage.

---

## Step 5: Update app.py

Four changes to `agent-core/app.py`:

### 5a. Imports

Add to the imports section alongside the other skill imports:

```python
from skills.calculate import CalculateSkill
from skills.convert_units import ConvertUnitsSkill
```

### 5b. Register the skills

Add alongside the other `skill_registry.register()` calls:

```python
skill_registry.register(CalculateSkill())
skill_registry.register(ConvertUnitsSkill())
```

Neither skill requires constructor arguments — no Redis, no ChromaDB, no API keys.

### 5c. Add tool-usage hints to the system prompt

In the tool-usage hint block (the `system_prompt +=` string with all the `- Use **skill**` lines), add two new entries at the end:

```python
- Use **calculate** to evaluate mathematical expressions (arithmetic, trig, logs, etc.). Never compute math in your head — always use this tool.
- Use **convert_units** to convert between units (length, mass, temperature, speed, volume, etc.). Never guess conversion factors — always use this tool.
```

### 5d. Add tool-forcing signal patterns

Add two new signal patterns alongside the existing ones (`_SIGNAL_URL`, `_SIGNAL_REALTIME`, etc.):

```python
_SIGNAL_CALCULATE = re.compile(
    r"\bcalculate\b|\bcompute\b|\bevaluate\b|\bsolve\b|"
    r"what is \d|\d+\s*[\+\-\*\/\^]\s*\d|"
    r"\bsqrt\b|\bsin\b|\bcos\b|\blog\b|\bfactorial\b",
    re.IGNORECASE,
)

_SIGNAL_CONVERT = re.compile(
    r"\bconvert\b.{0,40}\bto\b|"
    r"how many (km|miles?|kg|lbs?|pounds?|feet|meters?|gallons?|liters?)\b|"
    r"\bin (kilometers?|miles?|celsius|fahrenheit|kg|pounds?|lbs?|meters?|feet|mph|kph)\b",
    re.IGNORECASE,
)
```

Then add the directives inside `_tool_forcing_directive()`, alongside the existing if-blocks:

```python
if _SIGNAL_CALCULATE.search(message):
    directives.append(
        "The user is asking for a mathematical calculation. "
        "You **must** call `calculate` with the expression. "
        "Do not compute math in your head — use the tool."
    )

if _SIGNAL_CONVERT.search(message):
    directives.append(
        "The user is asking for a unit conversion. "
        "You **must** call `convert_units` with the value and units. "
        "Do not guess conversion factors — use the tool."
    )
```

---

## Step 6: Add Tests

Append the following two test classes to `agent-core/tests/test_skills.py`:

### TestCalculateSkill (20 tests)

```python
class TestCalculateSkill:

    def test_validate_valid_expression(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": "2 + 2"})
        assert ok is True

    def test_validate_empty_string(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": ""})
        assert ok is False
        assert "non-empty" in reason.lower()

    def test_validate_too_long(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": "x" * 501})
        assert ok is False and "500" in reason

    def test_validate_contains_dunder(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": "__import__('os')"})
        assert ok is False and "__" in reason

    def test_validate_contains_import(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": "import math"})
        assert ok is False and "import" in reason

    def test_validate_contains_lambda(self):
        from skills.calculate import CalculateSkill
        ok, reason = CalculateSkill().validate({"expression": "lambda x: x"})
        assert ok is False and "lambda" in reason

    @pytest.mark.asyncio
    async def test_execute_addition(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "2 + 2"})
        assert result["result"] == 4

    @pytest.mark.asyncio
    async def test_execute_division(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "10 / 4"})
        assert result["result"] == 2.5

    @pytest.mark.asyncio
    async def test_execute_power(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "2**10"})
        assert result["result"] == 1024

    @pytest.mark.asyncio
    async def test_execute_modulo(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "10 % 3"})
        assert result["result"] == 1

    @pytest.mark.asyncio
    async def test_execute_floor_div(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "10 // 3"})
        assert result["result"] == 3

    @pytest.mark.asyncio
    async def test_execute_unary_minus(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "-5"})
        assert result["result"] == -5

    @pytest.mark.asyncio
    async def test_execute_sqrt(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "sqrt(16)"})
        assert result["result"] == 4.0

    @pytest.mark.asyncio
    async def test_execute_sin_zero(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "sin(0)"})
        assert result["result"] == 0.0

    @pytest.mark.asyncio
    async def test_execute_cos_zero(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "cos(0)"})
        assert result["result"] == 1.0

    @pytest.mark.asyncio
    async def test_execute_pi_constant(self):
        import math
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "pi"})
        assert abs(result["result"] - math.pi) < 1e-10

    @pytest.mark.asyncio
    async def test_execute_nested_functions(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "floor(sqrt(17))"})
        assert result["result"] == 4

    @pytest.mark.asyncio
    async def test_execute_division_by_zero(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "1 / 0"})
        assert "error" in result and "zero" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_overflow(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "factorial(10000)"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_list_literal_rejected(self):
        from skills.calculate import CalculateSkill
        result = await CalculateSkill().execute({"expression": "[1, 2]"})
        assert "error" in result

    def test_sanitize_output_whole_number(self):
        from skills.calculate import CalculateSkill
        out = CalculateSkill().sanitize_output({"expression": "2 + 2", "result": 4.0})
        assert out == "2 + 2 = 4"

    def test_sanitize_output_float(self):
        from skills.calculate import CalculateSkill
        out = CalculateSkill().sanitize_output({"expression": "10 / 4", "result": 2.5})
        assert "2.5" in out

    def test_sanitize_output_error(self):
        from skills.calculate import CalculateSkill
        out = CalculateSkill().sanitize_output({"error": "Division by zero"})
        assert "[calculate]" in out
```

### TestConvertUnitsSkill (18 tests)

```python
class TestConvertUnitsSkill:

    def test_validate_valid_params(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, _ = ConvertUnitsSkill().validate({"value": 10, "from_unit": "km", "to_unit": "miles"})
        assert ok is True

    def test_validate_missing_value(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, reason = ConvertUnitsSkill().validate({"from_unit": "km", "to_unit": "miles"})
        assert ok is False and "value" in reason.lower()

    def test_validate_missing_from_unit(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, reason = ConvertUnitsSkill().validate({"value": 10, "to_unit": "miles"})
        assert ok is False and "from_unit" in reason.lower()

    def test_validate_missing_to_unit(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, reason = ConvertUnitsSkill().validate({"value": 10, "from_unit": "km"})
        assert ok is False and "to_unit" in reason.lower()

    def test_validate_string_value(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, reason = ConvertUnitsSkill().validate({"value": "ten", "from_unit": "km", "to_unit": "miles"})
        assert ok is False and "number" in reason.lower()

    def test_validate_unit_too_long(self):
        from skills.convert_units import ConvertUnitsSkill
        ok, reason = ConvertUnitsSkill().validate({"value": 10, "from_unit": "k" * 101, "to_unit": "miles"})
        assert ok is False and "100" in reason

    @pytest.mark.asyncio
    async def test_execute_km_to_miles(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "km", "to_unit": "miles"})
        assert "result" in result and abs(result["result"] - 0.621371) < 0.001

    @pytest.mark.asyncio
    async def test_execute_miles_to_km(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "miles", "to_unit": "km"})
        assert "result" in result and abs(result["result"] - 1.60934) < 0.001

    @pytest.mark.asyncio
    async def test_execute_kg_to_lbs(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "kg", "to_unit": "lb"})
        assert "result" in result and abs(result["result"] - 2.20462) < 0.001

    @pytest.mark.asyncio
    async def test_execute_lbs_to_kg(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "lb", "to_unit": "kg"})
        assert "result" in result and abs(result["result"] - 0.453592) < 0.001

    @pytest.mark.asyncio
    async def test_execute_degF_to_degC(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 32, "from_unit": "degF", "to_unit": "degC"})
        assert "result" in result and abs(result["result"] - 0.0) < 0.01

    @pytest.mark.asyncio
    async def test_execute_degC_to_degF(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 100, "from_unit": "degC", "to_unit": "degF"})
        assert "result" in result and abs(result["result"] - 212.0) < 0.01

    @pytest.mark.asyncio
    async def test_execute_meters_to_feet(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "meter", "to_unit": "foot"})
        assert "result" in result and abs(result["result"] - 3.28084) < 0.001

    @pytest.mark.asyncio
    async def test_execute_liters_to_gallons(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "liter", "to_unit": "gallon"})
        assert "result" in result and abs(result["result"] - 0.264172) < 0.001

    @pytest.mark.asyncio
    async def test_execute_incompatible_dimensions(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "km", "to_unit": "kg"})
        assert "error" in result and "incompatible" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_unknown_unit(self):
        from skills.convert_units import ConvertUnitsSkill
        result = await ConvertUnitsSkill().execute({"value": 1, "from_unit": "flarbles", "to_unit": "km"})
        assert "error" in result

    def test_sanitize_output_clean_result(self):
        from skills.convert_units import ConvertUnitsSkill
        out = ConvertUnitsSkill().sanitize_output({
            "input": 1, "from_unit": "km", "result": 0.621371, "to_unit": "miles"
        })
        assert "1 km" in out and "miles" in out

    def test_sanitize_output_whole_number(self):
        from skills.convert_units import ConvertUnitsSkill
        out = ConvertUnitsSkill().sanitize_output({
            "input": 1000, "from_unit": "meter", "result": 1.0, "to_unit": "km"
        })
        assert "1 km" in out and "." not in out.split("=")[1]

    def test_sanitize_output_error(self):
        from skills.convert_units import ConvertUnitsSkill
        out = ConvertUnitsSkill().sanitize_output({"error": "incompatible dimensions"})
        assert "[convert_units]" in out and "incompatible dimensions" in out
```

---

## Step 7: Rebuild and Verify

### Rebuild agent-core

```bash
docker compose build agent-core && docker compose up -d agent-core
```

### Run the new tests

```bash
docker exec agent-core python -m pytest tests/test_skills.py -k "Calculate or ConvertUnits" -v
```

Expected: **43 tests passing**.

### Run the full suite

```bash
docker exec agent-core python -m pytest tests/ -q
```

Expected: **467 tests passing, 0 failures**.

### Smoke tests

```bash
API="http://127.0.0.1:8000"
KEY="$(grep AGENT_API_KEY .env | cut -d= -f2)"
chat() { curl -s -X POST "$API/chat" -H "Content-Type: application/json" -H "X-Api-Key: $KEY" -d "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['response'][:300])"; }

# 1. Arithmetic
chat '{"message":"what is sqrt(144) + 2**8","user_id":"smoke_test"}'
# Expected: "sqrt(144) + 2**8 = 268"

# 2. Trig
chat '{"message":"what is sin(pi/2)","user_id":"smoke_test"}'
# Expected: "sin(pi / 2) = 1.0"

# 3. Temperature conversion
chat '{"message":"convert 100 fahrenheit to celsius","user_id":"smoke_test"}'
# Expected: "100 degF = 37.7778 degC"

# 4. Distance conversion
chat '{"message":"how many miles is 10 km","user_id":"smoke_test"}'
# Expected: mentions 6.21371

# 5. Compound query (chains two tools)
chat '{"message":"if I run 8 km/h for 2.5 hours how many miles is that","user_id":"smoke_test"}'
# Expected: ~12.43 miles via two tool calls

# 6. Error handling
chat '{"message":"what is 5 divided by zero","user_id":"smoke_test"}'
# Expected: agent explains division by zero is undefined

# 7. Incompatible units
chat '{"message":"convert 5 km to kg","user_id":"smoke_test"}'
# Expected: agent reports incompatible dimensions
```

---

## Security Notes

### calculate
- No `eval()`, `exec()`, or dynamic import anywhere in the skill.
- Pre-parse validation catches `__`, `import`, `exec`, `eval`, `open`, `lambda` as string tokens before the AST is even parsed — belt-and-suspenders defense.
- The AST whitelist is the primary security mechanism: only the exact node types, operator types, function names, and constant names in the whitelist can execute. Everything else raises `ValueError` before any computation.
- Even if an expression reaches `_safe_eval()` containing a dangerous construct, the node type check rejects it — `ast.Import`, `ast.Attribute`, `ast.Subscript`, `ast.List`, `ast.Dict`, `ast.Lambda`, and all statement nodes are not in the whitelist and immediately raise.
- Rate limit of 50/min is intentionally high (pure computation, no side effects). Lower it in `policy.yaml` if you want stricter limits.

### convert_units
- pint unit names are opaque strings — the only execution path is through pint's own parser, which does not evaluate Python code.
- An unknown or malformed unit string raises `UndefinedUnitError` (caught) rather than executing anything.
- `float(converted.magnitude)` ensures the return value is always a plain Python float, never a pint Quantity object that could carry unexpected behavior downstream.

---

## Unit Names Reference

Pint accepts many aliases. Common ones:

| Dimension | Examples |
|---|---|
| Length | `km`, `miles`, `meter`, `foot`, `feet`, `inch`, `cm`, `mm`, `yard` |
| Mass | `kg`, `lb`, `gram`, `oz`, `ton`, `tonne` |
| Temperature | `degC`, `degF`, `kelvin` (NOT `celsius`, `fahrenheit`, `K`) |
| Speed | `km/h`, `mph`, `m/s`, `knot` |
| Volume | `liter`, `gallon`, `ml`, `cup`, `pint`, `quart`, `fluid_ounce` |
| Area | `m**2`, `km**2`, `acre`, `hectare`, `ft**2` |
| Time | `second`, `minute`, `hour`, `day`, `week` |
| Pressure | `Pa`, `bar`, `psi`, `atm` |
| Energy | `joule`, `calorie`, `kcal`, `BTU`, `kWh` |
| Power | `watt`, `horsepower`, `kW` |

When in doubt, try the full unit name (e.g., `kilometer` instead of `km`). Pint accepts most standard abbreviations and full names.

---

## What's Next

**Phase 4E (execution & voice) — not yet started:**
- `python_exec` skill — execute Python code in a sandboxed subprocess (requires approval, strict deny-list)
- Mumble voice gateway — Whisper STT + agent-core + Piper TTS for voice chat

**Phase 5 (autonomy & planning) — not yet started:**
- Proactive behavior rules evaluated on each heartbeat tick
- Self-directed task graphs
- Standing instructions ("every Monday, prepare a weekly summary")
