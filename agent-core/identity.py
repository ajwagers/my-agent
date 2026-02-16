"""
Identity File Loader — Reads identity files and builds system prompts.

Files live in IDENTITY_DIR (default /agent, bind-mounted from agent-identity/).
Bootstrap mode is detected by the presence of BOOTSTRAP.md.
"""

import os
from typing import Optional

IDENTITY_DIR = os.environ.get("IDENTITY_DIR", "/agent")
MAX_FILE_CHARS = 20_000

# Mapping of logical names to filenames
_FILES = {
    "bootstrap": "BOOTSTRAP.md",
    "soul": "SOUL.md",
    "identity": "IDENTITY.md",
    "user": "USER.md",
    "agents": "AGENTS.md",
}


def is_bootstrap_mode() -> bool:
    """Check if BOOTSTRAP.md exists in the identity directory."""
    return os.path.isfile(os.path.join(IDENTITY_DIR, "BOOTSTRAP.md"))


def load_file(filename: str) -> Optional[str]:
    """Load a single identity file, truncated to MAX_FILE_CHARS.
    Returns None if file doesn't exist.
    """
    path = os.path.join(IDENTITY_DIR, filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(MAX_FILE_CHARS)
        return content
    except OSError:
        return None


def load_identity() -> dict:
    """Load all identity files. Returns dict: {key: content_or_None}."""
    return {key: load_file(fname) for key, fname in _FILES.items()}


def parse_identity_fields(content: str) -> dict:
    """Parse IDENTITY.md YAML-like fields into a dict.
    Extracts: name, nature, vibe, emoji.
    Returns dict with found keys (missing keys omitted).
    """
    fields = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in ("name", "nature", "vibe", "emoji") and value:
                fields[key] = value
    return fields


def build_system_prompt(identity: dict) -> str:
    """Build composite system prompt from loaded identity files.

    Bootstrap mode: return BOOTSTRAP.md content + AGENTS.md
    Normal mode: return SOUL.md + AGENTS.md + USER.md context
    """
    parts = []

    if identity.get("bootstrap") is not None:
        # Bootstrap mode
        parts.append(identity["bootstrap"])
        if identity.get("agents"):
            parts.append(identity["agents"])
    else:
        # Normal mode
        if identity.get("soul"):
            parts.append(identity["soul"])
        if identity.get("agents"):
            parts.append(identity["agents"])
        if identity.get("user"):
            parts.append(identity["user"])

    return "\n\n".join(parts)
