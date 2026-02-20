"""
File write skill — writes or appends to a file in /sandbox only.

Restricted to Zone 1 (sandbox) for autonomous use. Identity-zone writes
require the proposal/approval flow (handled separately, not here).
Uses os.path.realpath() to block path traversal and symlink escape.
"""

import os
from typing import Any, Dict, Tuple

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel

SANDBOX_ROOT = "/sandbox"
MAX_CONTENT_CHARS = 100_000


def _safe_realpath(path: str) -> Tuple[bool, str, str]:
    """Resolve path and verify it's within /sandbox.

    Returns (allowed, reason, real_path).
    """
    real = os.path.realpath(path)
    if real == SANDBOX_ROOT or real.startswith(SANDBOX_ROOT + "/"):
        return True, "", real
    return False, f"file_write is restricted to /sandbox (resolved to '{real}')", real


class FileWriteSkill(SkillBase):
    """Write or append content to a file in /sandbox."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="file_write",
            description=(
                "Write or append content to a file in /sandbox (the agent's workspace). "
                "Creates the file and any missing parent directories automatically. "
                "Use mode='write' to create/overwrite, mode='append' to add to an existing file."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="file_write",
            requires_approval=False,
            max_calls_per_turn=10,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path within /sandbox to write to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "'write' (default, creates or overwrites) or 'append'.",
                        "enum": ["write", "append"],
                    },
                },
                "required": ["path", "content"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        path = params.get("path", "")
        if not isinstance(path, str):
            return False, "Parameter 'path' must be a string"
        if not path.strip():
            return False, "Parameter 'path' must not be empty"

        content = params.get("content", "")
        if not isinstance(content, str):
            return False, "Parameter 'content' must be a string"
        if len(content) > MAX_CONTENT_CHARS:
            return False, f"Parameter 'content' must be under {MAX_CONTENT_CHARS} characters"

        mode = params.get("mode", "write")
        if mode not in ("write", "append"):
            return False, "Parameter 'mode' must be 'write' or 'append'"

        allowed, reason, _ = _safe_realpath(path)
        if not allowed:
            return False, reason
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params["path"]
        content = params["content"]
        mode = params.get("mode", "write")
        _, _, real = _safe_realpath(path)
        try:
            os.makedirs(os.path.dirname(real) or SANDBOX_ROOT, exist_ok=True)
            file_mode = "w" if mode == "write" else "a"
            with open(real, file_mode, encoding="utf-8") as f:
                f.write(content)
            return {"path": real, "bytes_written": len(content.encode("utf-8")), "mode": mode}
        except PermissionError:
            return {"error": f"Permission denied: {real}"}
        except Exception as e:
            return {"error": f"Could not write file: {e}"}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[file_write] {result['error']}"
        if isinstance(result, dict):
            path = result.get("path", "")
            n = result.get("bytes_written", 0)
            mode = result.get("mode", "write")
            action = "Written" if mode == "write" else "Appended"
            return f"{action} {n} bytes to {path}."
        return str(result)
