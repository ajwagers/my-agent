"""
PDF parse skill — extracts text from a PDF file in /sandbox.

Uses pypdf (pure-Python, no shell calls). Restricted to /sandbox to prevent
the agent reading sensitive PDFs outside its designated workspace.
"""

import os
from typing import Any, Dict, Tuple

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel

SANDBOX_ROOT = "/sandbox"
MAX_OUTPUT_CHARS = 20_000


def _safe_realpath(path: str) -> Tuple[bool, str, str]:
    """Resolve path and verify it's within /sandbox.

    Returns (allowed, reason, real_path).
    """
    real = os.path.realpath(path)
    if real == SANDBOX_ROOT or real.startswith(SANDBOX_ROOT + "/"):
        return True, "", real
    return False, f"pdf_parse is restricted to /sandbox (resolved to '{real}')", real


class PdfParseSkill(SkillBase):
    """Extract text from a PDF file located in /sandbox."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="pdf_parse",
            description=(
                "Extract and return the text content of a PDF file in /sandbox. "
                "Use this to read documents, papers, or reports that have been "
                "saved to the sandbox."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="pdf_parse",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the PDF file in /sandbox.",
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
        if not path.lower().endswith(".pdf"):
            return False, "Parameter 'path' must point to a .pdf file"
        allowed, reason, _ = _safe_realpath(path)
        if not allowed:
            return False, reason
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params["path"]
        _, _, real = _safe_realpath(path)
        try:
            import pypdf
            reader = pypdf.PdfReader(real)
            pages_text = []
            for page in reader.pages:
                pages_text.append(page.extract_text() or "")
            text = "\n\n".join(pages_text)
            return {"text": text, "pages": len(reader.pages), "path": real}
        except FileNotFoundError:
            return {"error": f"File not found: {real}"}
        except Exception as e:
            return {"error": f"Could not parse PDF: {e}"}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[pdf_parse] {result['error']}"
        if isinstance(result, dict):
            text = result.get("text", "")
            pages = result.get("pages", 0)
            path = result.get("path", "")
            truncated = len(text) > MAX_OUTPUT_CHARS
            if truncated:
                text = text[:MAX_OUTPUT_CHARS] + "\n[truncated]"
            header = f"[{path}] ({pages} page{'s' if pages != 1 else ''})\n\n"
            return header + text
        return str(result)
