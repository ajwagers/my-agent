"""
URL fetch skill — fetches a URL and returns its text content.

Security:
- Scheme must be http or https (no file://, ftp://, etc.)
- Hostname blocklist prevents requests to internal Docker services
- DNS resolution check prevents SSRF to private IP ranges
- Response size capped at 1 MB
- HTML stripped to readable text via BeautifulSoup
- Output sanitized against prompt injection patterns
"""

import ipaddress
import re
import socket
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

import requests

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel

MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_OUTPUT_CHARS = 5_000

# Docker-internal service names and common localhost aliases
BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "redis",
    "ollama-runner",
    "chroma-rag",
    "agent-core",
    "telegram-gateway",
    "web-ui",
    "dashboard",
})

# RFC-1918 private ranges + loopback + link-local
PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
]

# Reuse the prompt-injection / HTML sanitizer from web_search
_SUSPICIOUS_PATTERN = re.compile(
    r"<[^>]+>"
    r"|javascript:"
    r"|data:"
    r"|ignore\s+previous"
    r"|system\s+prompt"
    r"|disregard\s+instructions",
    re.IGNORECASE,
)


def _check_url(url: str) -> Tuple[bool, str]:
    """Validate URL for SSRF safety. Returns (safe, reason)."""
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed; use http or https"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    if hostname.lower() in BLOCKED_HOSTNAMES:
        return False, f"Hostname '{hostname}' is a blocked internal service"

    # DNS-resolve and check for private IPs
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
                for private_range in PRIVATE_RANGES:
                    if ip in private_range:
                        return False, "URL resolves to a private/internal IP address"
            except ValueError:
                pass  # not a valid IP string — skip
    except socket.gaierror:
        pass  # DNS failed — let execute() handle the connection error naturally

    return True, ""


class UrlFetchSkill(SkillBase):
    """Fetch the content of a URL and return its readable text."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="url_fetch",
            description=(
                "Fetch the text content of a web page or URL. Use this to read a "
                "specific page when you have its URL, such as documentation, articles, "
                "or public data. Only http and https URLs are supported."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="url_fetch",
            requires_approval=False,
            max_calls_per_turn=3,
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch (must be http or https).",
                    }
                },
                "required": ["url"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        url = params.get("url", "")
        if not isinstance(url, str):
            return False, "Parameter 'url' must be a string"
        if not url.strip():
            return False, "Parameter 'url' must not be empty"
        if len(url) > 2048:
            return False, "Parameter 'url' must be under 2048 characters"
        safe, reason = _check_url(url)
        if not safe:
            return False, reason
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params["url"]
        try:
            resp = requests.get(
                url,
                timeout=15,
                stream=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; my-agent/1.0)"},
            )
            resp.raise_for_status()

            # Read up to MAX_RESPONSE_BYTES
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_RESPONSE_BYTES:
                    break
            raw = b"".join(chunks)

            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type.lower():
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(raw, "html.parser")
                # Remove script and style blocks before extracting text
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(separator="\n")
            else:
                text = raw.decode("utf-8", errors="replace")

            return {"url": url, "content": text, "status_code": resp.status_code}

        except requests.exceptions.Timeout:
            return {"error": f"Request to {url} timed out"}
        except requests.exceptions.ConnectionError as e:
            return {"error": f"Could not connect to {url}: {e}"}
        except requests.exceptions.HTTPError as e:
            return {"error": f"HTTP error from {url}: {e}"}
        except Exception as e:
            return {"error": f"Fetch failed: {e}"}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[url_fetch] {result['error']}"
        if isinstance(result, dict):
            url = result.get("url", "")
            content = result.get("content", "")
            status = result.get("status_code", "")

            # Sanitize content
            content = _SUSPICIOUS_PATTERN.sub("", content)
            # Collapse excessive whitespace
            content = re.sub(r"\n{3,}", "\n\n", content).strip()

            if len(content) > MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS] + "\n[truncated]"

            return f"[{url}] (HTTP {status})\n\n{content}"
        return str(result)
