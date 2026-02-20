"""
File read skill — reads a file from an allowed zone.

Allowed zones: /sandbox (Zone 1), /agent (Zone 2, identity), /app (Zone 3, system).
Uses os.path.realpath() to block path traversal and symlink escape.
"""

import os
from typing import Any, Dict, Tuple

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel

MAX_READ_CHARS = 20_000

ALLOWED_ROOTS = ("/sandbox", "/agent", "/app")


def _safe_realpath(path: str) -> Tuple[bool, str, str]:
    """Resolve path and verify it's within an allowed root.

    Returns (allowed, reason, real_path).
    """
    real = os.path.realpath(path)
    for root in ALLOWED_ROOTS:
        if real == root or real.startswith(root + "/"):
            return True, "", real
    return False, f"Path is outside all readable zones (resolved to '{real}')", real


class FileReadSkill(SkillBase):
    """Read the contents of a file from /sandbox, /agent, or /app."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="file_read",
            description=(
                "Read the contents of a file. Allowed locations: /sandbox (agent's "
                "workspace), /agent (identity files), /app (application code). "
                "Use this to inspect files, read notes, or load data."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="file_read",
            requires_approval=False,
            max_calls_per_turn=10,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read.",
                    }
                },
                "required": ["path"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        path = params.get("path", "")
        if not isinstance(path, str):
            return False, "Parameter 'path' must be a string"
        if not path.strip():
            return False, "Parameter 'path' must not be empty"
        allowed, reason, _ = _safe_realpath(path)
        if not allowed:
            return False, reason
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params["path"]
        _, _, real = _safe_realpath(path)
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_READ_CHARS + 1)
            truncated = len(content) > MAX_READ_CHARS
            if truncated:
                content = content[:MAX_READ_CHARS]
            return {"content": content, "path": real, "truncated": truncated}
        except FileNotFoundError:
            return {"error": f"File not found: {real}"}
        except IsADirectoryError:
            return {"error": f"Path is a directory, not a file: {real}"}
        except PermissionError:
            return {"error": f"Permission denied: {real}"}
        except Exception as e:
            return {"error": f"Could not read file: {e}"}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[file_read] {result['error']}"
        if isinstance(result, dict):
            content = result.get("content", "")
            path = result.get("path", "")
            truncated = result.get("truncated", False)
            header = f"[{path}]\n"
            suffix = f"\n[truncated at {MAX_READ_CHARS} chars]" if truncated else ""
            return header + content + suffix
        return str(result)
