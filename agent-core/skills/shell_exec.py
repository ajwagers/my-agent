"""
Shell execution skill — run shell commands in the isolated shell-exec container.

Requires owner approval for every command. Two-layer deny-list:
  1. validate() checks here before the HTTP call is ever made
  2. shell-exec service has its own independent copy (defense in depth)

Private channels only — shell access is never available on public channels.
"""

import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from policy import RiskLevel, HARD_DENY_PATTERNS
from skills.base import SkillBase, SkillMetadata, PRIVATE_CHANNELS

SHELL_EXEC_URL = os.getenv("SHELL_EXEC_URL", "http://shell-exec:9001")

# ---------------------------------------------------------------------------
# Additional deny patterns beyond policy.py's HARD_DENY_PATTERNS.
# These are shell-exec-specific risks not covered by the base list.
# ---------------------------------------------------------------------------
_EXTRA_DENY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcurl\b"), "curl (network exfiltration risk)"),
    (re.compile(r"\bwget\b"), "wget (network exfiltration risk)"),
    (re.compile(r"\bnc\b|\bncat\b"), "netcat"),
    (re.compile(r"\bsocat\b"), "socat"),
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bsu\s"), "su"),
    (re.compile(r"\bapt(-get)?\s+install\b"), "package install"),
    (re.compile(r"\bpip\s+install\b"), "pip install"),
    (re.compile(r"\bnpm\s+install\b"), "npm install"),
    (re.compile(r"\bdocker\b"), "docker (container escape risk)"),
    (re.compile(r"\bpodman\b"), "podman"),
]


def _is_denied(command: str) -> tuple[bool, str]:
    """Check command against both hard-deny and extra deny patterns."""
    for pattern in HARD_DENY_PATTERNS:
        if pattern.search(command):
            return True, pattern.pattern
    for pattern, label in _EXTRA_DENY:
        if pattern.search(command):
            return True, label
    return False, ""


def _is_safe_working_dir(path: str) -> bool:
    """Validate that working_dir resolves under /sandbox."""
    real = os.path.realpath(path)
    return real == "/sandbox" or real.startswith("/sandbox/")


class ShellExecSkill(SkillBase):
    """Execute a shell command in the isolated shell-exec container."""

    def __init__(self, ollama_host: str, reasoning_model: str):
        self._ollama_host = ollama_host
        self._reasoning_model = reasoning_model

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="shell_exec",
            description=(
                "Execute a shell command in a sandboxed container with access to /sandbox only. "
                "Use for file operations, text processing, git commands, and scripting tasks. "
                "Always provide a 'description' explaining what the command does. "
                "Network access is blocked — cannot curl or wget. "
                "Owner approval required before every execution."
            ),
            risk_level=RiskLevel.HIGH,
            rate_limit="shell_exec",
            requires_approval=True,
            max_calls_per_turn=3,
            private_channels=PRIVATE_CHANNELS,
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Plain-English summary of what this command does and why.",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Working directory for the command. "
                            "Must be /sandbox or a subdirectory. Defaults to /sandbox."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (1–60). Defaults to 30.",
                    },
                },
                "required": ["command", "description"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return False, "command must be a non-empty string"
        if len(command) > 2000:
            return False, "command must be under 2000 characters"
        if not params.get("description", "").strip():
            return False, "description is required — explain what the command does"

        denied, reason = _is_denied(command)
        if denied:
            return False, f"Command blocked by deny-list: {reason}"

        working_dir = params.get("working_dir", "/sandbox")
        if not _is_safe_working_dir(working_dir):
            return False, f"working_dir must be under /sandbox, got: {working_dir}"

        timeout = params.get("timeout", 30)
        if not isinstance(timeout, int) or not (1 <= timeout <= 60):
            return False, "timeout must be an integer between 1 and 60"

        return True, ""

    async def pre_approval_description(self, params: Dict[str, Any]) -> Optional[str]:
        import ollama

        command = params.get("command", "").strip()
        description = params.get("description", "").strip()
        working_dir = params.get("working_dir", "/sandbox")

        try:
            client = ollama.AsyncClient(host=self._ollama_host)
            response = await client.chat(
                model=self._reasoning_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "You are a shell command security reviewer. Analyze this command:\n"
                        "1. 2-3 sentence plain-English summary of what it does\n"
                        "2. Specific risks: file deletion, data modification, "
                        "process spawning, privilege escalation, output to sensitive paths\n"
                        "3. Overall risk: LOW / MEDIUM / HIGH\n\n"
                        f"```bash\n{command[:2000]}\n```"
                    ),
                }],
                options={"num_predict": 250},
            )
            review = response.message.content.strip()
        except Exception as exc:
            review = f"(Review unavailable: {exc})"

        lines = [
            "**Shell Command Execution Request**\n",
            f"**Agent says:** {description}\n",
            f"**Command:**\n```bash\n{command}\n```\n",
            f"**Working dir:** `{working_dir}`\n",
            f"**Independent Review:**\n{review}",
        ]
        return "\n".join(lines)

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        payload = {
            "command": params["command"].strip(),
            "timeout": params.get("timeout", 30),
            "working_dir": params.get("working_dir", "/sandbox"),
        }
        try:
            async with httpx.AsyncClient(timeout=75) as client:
                resp = await client.post(f"{SHELL_EXEC_URL}/exec", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.ConnectError:
            return {"error": "shell-exec service is unreachable"}
        except Exception as exc:
            return {"error": str(exc)}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)[:1000]
        if "error" in result:
            return f"[shell_exec] Error: {result['error']}"
        if result.get("denied"):
            return f"[shell_exec] Command blocked: {result.get('deny_reason', 'deny-list')}"
        if result.get("timed_out"):
            return "[shell_exec] Command timed out"

        rc = result.get("returncode", -1)
        stdout = (result.get("stdout") or "")[:3000]
        stderr = (result.get("stderr") or "")[:1000]

        if rc == 0 and not stderr:
            return f"Exit 0\n\n{stdout}" if stdout else "Exit 0 (no output)"
        parts = [f"Exit {rc}"]
        if stdout:
            parts.append(f"\nSTDOUT:\n{stdout}")
        if stderr:
            parts.append(f"\nSTDERR:\n{stderr}")
        return "\n".join(parts)
