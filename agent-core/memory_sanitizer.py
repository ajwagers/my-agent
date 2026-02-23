"""
Memory sanitizer — cleans content before writing to long-term memory
and detects prompt injection attempts.
"""

import re


class MemoryPoisonError(ValueError):
    """Raised when content contains prompt injection patterns."""


INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|prior|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"disregard\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?:", re.IGNORECASE),
    re.compile(r"<\s*/?system", re.IGNORECASE),
    re.compile(r"\[INST\]"),
    re.compile(r"<<SYS>>"),
]

# Control characters to strip — keep \t (9), \n (10), \r (13)
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# HTML tags
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Excess whitespace within lines (multiple spaces or tabs in a row)
_EXCESS_SPACE_RE = re.compile(r"[ \t]{2,}")


def sanitize(content: str) -> str:
    """Clean content for safe storage in long-term memory.

    Steps:
      1. Strip null bytes and dangerous control chars (keep \\t \\n \\r)
      2. Check injection patterns → raise MemoryPoisonError if found
         (checked before HTML stripping so patterns like <<SYS>> are still intact)
      3. Strip HTML tags
      4. Collapse excess whitespace

    Returns:
        Cleaned string.

    Raises:
        MemoryPoisonError: if content contains prompt injection patterns.
    """
    # 1. Strip null bytes and control chars (keep \t \n \r)
    cleaned = _CTRL_CHARS_RE.sub("", content)

    # 2. Check injection patterns before HTML stripping alters structure
    for pattern in INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise MemoryPoisonError(
                "Content rejected: potential prompt injection detected."
            )

    # 3. Strip HTML tags
    cleaned = _HTML_TAG_RE.sub("", cleaned)

    # 4. Collapse excess whitespace (within lines)
    cleaned = _EXCESS_SPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip()

    return cleaned
