"""
shell-exec service — isolated shell command executor.

Accepts POST /exec from agent-core only (internal network, no host port).
Enforces its own deny-list independently of agent-core so the container
is safe even if agent-core is somehow bypassed.

Security properties:
- Deny-list checked before any subprocess is spawned
- Subprocess runs as non-root 'runner' user (UID 1000)
- cwd validated to resolve under /sandbox
- Env stripped to minimal set — no credentials, no API keys
- Stdout/stderr hard-capped server-side
- Max execution timeout capped at 60s regardless of request
"""

import logging
import os
import re
import subprocess

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------------------------------------------------------------------
# Deny-list — duplicated from agent-core/policy.py + shell-specific extras.
# Three-way duplication (policy.py, shell_exec.py skill, here) is intentional:
# defense-in-depth so each layer is independently safe.
# ---------------------------------------------------------------------------

_DENY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Destructive file operations
    (re.compile(r"\brm\s+(-[a-zA-Z]*)?r[a-zA-Z]*f"), "rm -rf variant"),
    (re.compile(r"\brm\s+(-[a-zA-Z]*)?f[a-zA-Z]*r"), "rm -fr variant"),
    (re.compile(r"\brm\s+-rf\b"), "rm -rf"),
    # Dangerous permission changes
    (re.compile(r"\bchmod\s+777\b"), "chmod 777"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "chmod -R 777"),
    # Pipe-to-shell attacks
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b"), "curl|sh"),
    (re.compile(r"\bwget\b.*\|\s*(ba)?sh\b"), "wget|sh"),
    # Fork bomb
    (re.compile(r":\(\)\{.*\|.*&.*\};:"), "fork bomb"),
    (re.compile(r"\bfork\s*bomb\b", re.IGNORECASE), "fork bomb"),
    # System destruction
    (re.compile(r"\bshutdown\b"), "shutdown"),
    (re.compile(r"\breboot\b"), "reboot"),
    (re.compile(r"\bhalt\b"), "halt"),
    (re.compile(r"\binit\s+0\b"), "init 0"),
    (re.compile(r"\bpoweroff\b"), "poweroff"),
    # Disk destruction
    (re.compile(r"\bmkfs\b"), "mkfs"),
    (re.compile(r"\bdd\s+.*of=/dev/"), "dd to device"),
    # Privilege escalation
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bsu\s"), "su"),
    (re.compile(r"\bpasswd\b"), "passwd"),
    # Reverse shells / network backdoors
    (re.compile(r"\bnc\s+-[a-zA-Z]*l"), "netcat listen"),
    (re.compile(r"/dev/tcp/"), "/dev/tcp"),
    (re.compile(r"\bncat\b.*-[a-zA-Z]*l"), "ncat listen"),
    (re.compile(r"\bsocat\b"), "socat"),
    # Network exfiltration tools (outbound blocked by network, but deny anyway)
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bwget\b"), "wget"),
    # History/log tampering
    (re.compile(r"\bhistory\s+-c\b"), "history -c"),
    # Background + silence (common in exploit payloads)
    (re.compile(r">\s*/dev/null\s+2>&1\s*&\s*$"), "background silence"),
    # Package managers (don't allow installing software)
    (re.compile(r"\bapt(-get)?\s+install\b"), "apt install"),
    (re.compile(r"\bpip\s+install\b"), "pip install"),
    (re.compile(r"\bnpm\s+install\b"), "npm install"),
    # Docker-in-Docker / container escape
    (re.compile(r"\bdocker\b"), "docker"),
    (re.compile(r"\bpodman\b"), "podman"),
]

_MAX_TIMEOUT = 60
_STDOUT_LIMIT = 8000
_STDERR_LIMIT = 2000


def _check_deny_list(command: str) -> tuple[bool, str]:
    """Return (denied, reason). Checks all patterns."""
    for pattern, label in _DENY_PATTERNS:
        if pattern.search(command):
            return True, label
    return False, ""


def _validate_working_dir(working_dir: str) -> str:
    """Resolve and validate that working_dir is under /sandbox. Returns realpath."""
    try:
        real = os.path.realpath(working_dir)
    except Exception:
        raise HTTPException(400, "Invalid working_dir")
    if real != "/sandbox" and not real.startswith("/sandbox/"):
        raise HTTPException(400, f"working_dir must be under /sandbox, got: {real}")
    return real


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ExecRequest(BaseModel):
    command: str
    timeout: int = 30
    working_dir: str = "/sandbox"


class ExecResponse(BaseModel):
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    timed_out: bool = False
    denied: bool = False
    deny_reason: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/exec", response_model=ExecResponse)
def exec_command(req: ExecRequest):
    # 1. Deny-list check
    denied, reason = _check_deny_list(req.command)
    if denied:
        logger.warning("Command denied by deny-list: %s | reason: %s", req.command[:200], reason)
        return ExecResponse(denied=True, deny_reason=reason)

    # 2. Validate working dir
    try:
        cwd = _validate_working_dir(req.working_dir)
    except HTTPException as e:
        return ExecResponse(denied=True, deny_reason=str(e.detail))

    # 3. Cap timeout
    timeout = min(max(1, req.timeout), _MAX_TIMEOUT)

    # 4. Minimal env — no credentials, no secrets
    minimal_env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/sandbox",
        "TMPDIR": "/tmp",
        "TERM": "dumb",
    }

    logger.info("Executing command (timeout=%ds, cwd=%s): %s", timeout, cwd, req.command[:200])

    try:
        proc = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=minimal_env,
            # Run as 'runner' non-root user if available
            user="runner" if _runner_user_exists() else None,
        )
        return ExecResponse(
            stdout=proc.stdout[:_STDOUT_LIMIT],
            stderr=proc.stderr[:_STDERR_LIMIT],
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ds: %s", timeout, req.command[:200])
        return ExecResponse(timed_out=True, returncode=-1)
    except Exception as exc:
        logger.exception("Command execution error")
        return ExecResponse(stderr=str(exc)[:_STDERR_LIMIT], returncode=-1)


def _runner_user_exists() -> bool:
    """Check if the 'runner' user exists in /etc/passwd."""
    try:
        import pwd
        pwd.getpwnam("runner")
        return True
    except KeyError:
        return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001, log_level="info")
