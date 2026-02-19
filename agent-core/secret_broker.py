"""
Secret broker — credential injection at skill execution time.

The LLM never sees raw secret values. Skills call get() inside execute(),
not in validate() or __init__(), ensuring credentials are only accessed
when actually needed and never appear in prompt text or log output.

Usage inside a skill:
    from secret_broker import get as get_secret
    api_key = get_secret("TAVILY_API_KEY")
"""

import os


def get(key: str) -> str:
    """Read a secret from the environment at call time.

    No caching — reads from os.environ on every call so that rotated
    secrets are picked up without a container restart.

    Args:
        key: Environment variable name (e.g. "TAVILY_API_KEY").

    Returns:
        The secret value.

    Raises:
        RuntimeError: If the environment variable is not set or is empty.
    """
    value = os.environ.get(key, "")
    if not value:
        raise RuntimeError(
            f"Secret '{key}' is not configured. "
            f"Set the {key} environment variable."
        )
    return value
