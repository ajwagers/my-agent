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
            # Extract the unit name from the exception if possible
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
