"""
Bootstrap Proposal Parser — Extracts and validates file proposals from LLM output.

During bootstrap, the LLM proposes identity files using markers:
    <<PROPOSE:FILENAME.md>>
    content here
    <<END_PROPOSE>>

This module extracts, strips, and validates those proposals.
"""

import os
import re

import identity as identity_module

PROPOSAL_PATTERN = re.compile(
    r'<<PROPOSE:([\w.]+)>>\s*\n(.*?)\n<<END_PROPOSE>>', re.DOTALL
)

ALLOWED_FILES = {"SOUL.md", "IDENTITY.md", "USER.md"}
MAX_PROPOSAL_CHARS = 10_000


def extract_proposals(response: str) -> list[tuple[str, str]]:
    """Extract (filename, content) pairs from LLM response.
    Returns list of tuples; empty list if no markers found.
    """
    return [(m.group(1), m.group(2).strip()) for m in PROPOSAL_PATTERN.finditer(response)]


def strip_proposals(response: str) -> str:
    """Remove proposal markers and their content from response text for display."""
    cleaned = PROPOSAL_PATTERN.sub("", response)
    # Collapse runs of 3+ newlines into 2
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def validate_proposal(filename: str, content: str) -> tuple[bool, str]:
    """Check filename is in ALLOWED_FILES and content is reasonable.
    Returns (ok, reason).
    """
    if filename not in ALLOWED_FILES:
        return False, f"File '{filename}' is not in the allowed set: {ALLOWED_FILES}"

    if not content or not content.strip():
        return False, "Proposed content is empty"

    if len(content) > MAX_PROPOSAL_CHARS:
        return False, f"Content exceeds {MAX_PROPOSAL_CHARS} character limit ({len(content)} chars)"

    return True, "ok"


REQUIRED_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]


def check_bootstrap_complete():
    """If SOUL.md, IDENTITY.md, and USER.md all exist with non-template content,
    delete BOOTSTRAP.md to exit bootstrap mode."""
    for fname in REQUIRED_FILES:
        path = os.path.join(identity_module.IDENTITY_DIR, fname)
        if not os.path.exists(path):
            return
        content = identity_module.load_file(fname)
        if not content or not content.strip():
            return
    # All files exist with content — delete bootstrap marker
    bootstrap_path = os.path.join(identity_module.IDENTITY_DIR, "BOOTSTRAP.md")
    if os.path.exists(bootstrap_path):
        os.remove(bootstrap_path)
