"""
Python execution skill — run Python code in a sandboxed subprocess.

A second LLM (REASONING_MODEL) reviews the code and produces a plain-English
risk summary before the approval request is sent to the owner.
"""

import os
import subprocess
import uuid
from typing import Any, Dict, Optional, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class PythonExecSkill(SkillBase):
    """Run Python code in a subprocess. Always requires owner approval."""

    def __init__(self, ollama_host: str, reasoning_model: str):
        self._ollama_host = ollama_host
        self._reasoning_model = reasoning_model

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="python_exec",
            description=(
                "Execute Python code in a sandboxed subprocess. "
                "Always provide a 'description' of what the code does. "
                "Owner approval required before execution."
            ),
            risk_level=RiskLevel.HIGH,
            rate_limit="python_exec",
            requires_approval=True,
            max_calls_per_turn=3,
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief summary of what the code does (from the coding agent).",
                    },
                },
                "required": ["code"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        code = params.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return False, "Parameter 'code' must be a non-empty string"
        if len(code) > 8000:
            return False, "Parameter 'code' must be under 8000 characters"
        return True, ""

    async def pre_approval_description(self, params: Dict[str, Any]) -> Optional[str]:
        import ollama

        code = params.get("code", "").strip()
        agent_desc = params.get("description", "").strip()

        try:
            client = ollama.AsyncClient(host=self._ollama_host)
            response = await client.chat(
                model=self._reasoning_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "You are a code security reviewer. Analyze this Python code:\n"
                        "1. 2-3 sentence plain-English summary of what it does\n"
                        "2. Specific risks: network calls, file access, subprocess/shell, "
                        "data deletion, credential access\n"
                        "3. Overall risk: LOW / MEDIUM / HIGH\n\n"
                        f"```python\n{code[:4000]}\n```"
                    ),
                }],
                options={"num_predict": 300},
            )
            review = response.message.content.strip()
        except Exception as exc:
            review = f"(Code review unavailable: {exc})"

        code_preview = code[:2000] + ("\n... [truncated]" if len(code) > 2000 else "")
        parts = ["**Python Code Execution Request**\n"]
        if agent_desc:
            parts.append(f"**Agent says:** {agent_desc}\n")
        parts.append(f"**Code:**\n```python\n{code_preview}\n```\n")
        parts.append(f"**Independent Code Review:**\n{review}")
        return "\n".join(parts)

    async def execute(self, params: Dict[str, Any]) -> Any:
        code = params["code"].strip()
        tmp_path = f"/sandbox/exec_{uuid.uuid4().hex[:8]}.py"
        try:
            with open(tmp_path, "w") as f:
                f.write(code)
            minimal_env = {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/sandbox",
                "PYTHONPATH": "",
                "PYTHONUTF8": "1",
            }
            proc = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
                cwd="/sandbox",
                env=minimal_env,
            )
            return {
                "stdout": proc.stdout[:4000],
                "stderr": proc.stderr[:2000],
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Execution timed out (30s limit)"}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[python_exec] {result['error']}"
        if isinstance(result, dict):
            rc = result.get("returncode", 0)
            stdout = result.get("stdout", "")[:3000]
            stderr = result.get("stderr", "")
            if rc == 0:
                return f"Exit 0\n\n{stdout}"
            return f"Exit {rc}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        return str(result)
