"""
Central Policy Engine — enforces the four-zone permission model.

Zones:
  1. sandbox  (/sandbox)  — agent playground, full access
  2. identity (/agent)    — soul/config files, read ok, write needs approval
  3. system   (/app)      — application code, read-only
  4. external             — HTTP access, governed by method + URL rules

HARD_DENY_PATTERNS are module-level constants, NOT loaded from YAML,
so the agent cannot weaken the deny-list by editing config.
"""

import enum
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Zone(enum.Enum):
    SANDBOX = "sandbox"
    IDENTITY = "identity"
    SYSTEM = "system"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ActionType(enum.Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    HTTP_GET = "http_get"
    HTTP_POST = "http_post"
    HTTP_PUT = "http_put"
    HTTP_DELETE = "http_delete"
    SHELL = "shell"


class Decision(enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


class RiskLevel(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Policy result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PolicyResult:
    decision: Decision
    zone: Zone
    action: ActionType
    reason: str = ""
    risk_level: RiskLevel = RiskLevel.LOW


# ---------------------------------------------------------------------------
# Hard-coded deny patterns — NOT loaded from YAML
# ---------------------------------------------------------------------------

HARD_DENY_PATTERNS: list[re.Pattern] = [
    # Destructive file operations
    re.compile(r"\brm\s+(-[a-zA-Z]*)?r[a-zA-Z]*f"),          # rm -rf / rm -fr
    re.compile(r"\brm\s+(-[a-zA-Z]*)?f[a-zA-Z]*r"),          # rm -fr variants
    re.compile(r"\brm\s+-rf\b"),                               # explicit rm -rf
    # Dangerous permission changes
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchmod\s+-R\s+777\b"),
    # Pipe-to-shell attacks
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b"),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b"),
    # Fork bombs
    re.compile(r":\(\)\{.*\|.*&.*\};:"),                       # classic :(){ :|:& };:
    re.compile(r"\bfork\s*bomb\b", re.IGNORECASE),
    # System destruction
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bhalt\b"),
    re.compile(r"\binit\s+0\b"),
    re.compile(r"\bpoweroff\b"),
    # Disk destruction
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+.*of=/dev/"),
    # Privilege escalation
    re.compile(r"\bsudo\s+su\b"),
    re.compile(r"\bsu\s+-\s*$"),
    re.compile(r"\bpasswd\b"),
    # Network exfiltration / reverse shells
    re.compile(r"\bnc\s+-[a-zA-Z]*l"),                         # netcat listen
    re.compile(r"/dev/tcp/"),
    # Package manager as root
    re.compile(r"\bsudo\s+pip\b"),
    re.compile(r"\bsudo\s+npm\b"),
    # History/log tampering
    re.compile(r"\bhistory\s+-c\b"),
    re.compile(r">\s*/dev/null\s+2>&1\s*&\s*$"),               # background + silence
]


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Central policy engine — enforces zone rules, deny-lists, rate limits."""

    def __init__(self, config_path: str = "policy.yaml", redis_client=None):
        self.config_path = config_path
        self.redis_client = redis_client
        self.config: dict = {}
        self._zone_paths: list[Tuple[str, Zone]] = []
        self._rate_counters: dict[str, list[float]] = {}
        self.load_config()

    # ---- Config loading ---------------------------------------------------

    def load_config(self) -> None:
        """Read and parse policy.yaml."""
        path = Path(self.config_path)
        if not path.exists():
            raise FileNotFoundError(f"Policy config not found: {self.config_path}")
        with open(path) as f:
            self.config = yaml.safe_load(f)
        self._build_zone_paths()

    def _build_zone_paths(self) -> None:
        """Pre-compute zone path mappings sorted longest-first for specificity."""
        zones_cfg = self.config.get("zones", {})
        self._zone_paths = []
        zone_map = {
            "sandbox": Zone.SANDBOX,
            "identity": Zone.IDENTITY,
            "system": Zone.SYSTEM,
        }
        for name, zone_enum in zone_map.items():
            cfg = zones_cfg.get(name, {})
            zpath = cfg.get("path")
            if zpath:
                self._zone_paths.append((os.path.realpath(zpath), zone_enum))
        # Sort longest path first so /app/subdir matches before /app
        self._zone_paths.sort(key=lambda x: len(x[0]), reverse=True)

    # ---- Zone resolution --------------------------------------------------

    def resolve_zone(self, path: str) -> Zone:
        """Map a filesystem path to its Zone. Uses realpath to prevent symlink escape."""
        real = os.path.realpath(path)
        for zone_path, zone_enum in self._zone_paths:
            if real == zone_path or real.startswith(zone_path + "/"):
                return zone_enum
        return Zone.UNKNOWN

    # ---- File access checks -----------------------------------------------

    def check_file_access(self, path: str, action: ActionType) -> PolicyResult:
        """Enforce zone rules for file read/write/execute."""
        zone = self.resolve_zone(path)
        zones_cfg = self.config.get("zones", {})

        # Map zone enum to config key
        zone_key_map = {
            Zone.SANDBOX: "sandbox",
            Zone.IDENTITY: "identity",
            Zone.SYSTEM: "system",
        }

        action_key = action.value  # "read", "write", "execute"

        if zone == Zone.UNKNOWN:
            return PolicyResult(
                decision=Decision.DENY,
                zone=zone,
                action=action,
                reason=f"Path {path} is outside all known zones",
                risk_level=RiskLevel.HIGH,
            )

        cfg_key = zone_key_map.get(zone)
        if cfg_key is None:
            return PolicyResult(
                decision=Decision.DENY,
                zone=zone,
                action=action,
                reason=f"No config for zone {zone.value}",
                risk_level=RiskLevel.HIGH,
            )

        zone_cfg = zones_cfg.get(cfg_key, {})
        rule = zone_cfg.get(action_key, "deny")
        decision = self._rule_to_decision(rule)

        risk = RiskLevel.LOW
        if decision == Decision.REQUIRES_APPROVAL:
            risk = RiskLevel.MEDIUM
        elif decision == Decision.DENY:
            risk = RiskLevel.HIGH

        return PolicyResult(
            decision=decision,
            zone=zone,
            action=action,
            reason=f"{action_key} in {zone.value} zone: {rule}",
            risk_level=risk,
        )

    # ---- Shell command checks ---------------------------------------------

    def is_denied_command(self, command: str) -> Tuple[bool, Optional[str]]:
        """Check command against hard-coded deny patterns. Returns (denied, pattern_str)."""
        for pattern in HARD_DENY_PATTERNS:
            if pattern.search(command):
                return True, pattern.pattern
        return False, None

    def check_shell_command(self, command: str) -> PolicyResult:
        """Deny-list first, then basic zone inference for shell commands."""
        denied, pattern = self.is_denied_command(command)
        if denied:
            return PolicyResult(
                decision=Decision.DENY,
                zone=Zone.SYSTEM,
                action=ActionType.SHELL,
                reason=f"Command matches deny pattern: {pattern}",
                risk_level=RiskLevel.CRITICAL,
            )
        return PolicyResult(
            decision=Decision.ALLOW,
            zone=Zone.SANDBOX,
            action=ActionType.SHELL,
            reason="Command not on deny list",
            risk_level=RiskLevel.LOW,
        )

    # ---- HTTP access checks -----------------------------------------------

    def check_http_access(self, url: str, method: str = "GET") -> PolicyResult:
        """GET allowed, write methods need approval, hard deny on financial/signup URLs."""
        ext_cfg = self.config.get("external_access", {})

        # Check denied URL patterns first
        denied_patterns = ext_cfg.get("denied_url_patterns", [])
        for pat_str in denied_patterns:
            if re.search(pat_str, url, re.IGNORECASE):
                return PolicyResult(
                    decision=Decision.DENY,
                    zone=Zone.EXTERNAL,
                    action=self._method_to_action(method),
                    reason=f"URL matches denied pattern: {pat_str}",
                    risk_level=RiskLevel.CRITICAL,
                )

        method_upper = method.upper()
        action = self._method_to_action(method_upper)

        # Map HTTP method to config key
        method_key_map = {
            "GET": "http_get",
            "POST": "http_post",
            "PUT": "http_put",
            "DELETE": "http_delete",
        }
        cfg_key = method_key_map.get(method_upper, "http_post")  # default to restrictive
        rule = ext_cfg.get(cfg_key, "requires_approval")
        decision = self._rule_to_decision(rule)

        risk = RiskLevel.LOW if decision == Decision.ALLOW else RiskLevel.MEDIUM

        return PolicyResult(
            decision=decision,
            zone=Zone.EXTERNAL,
            action=action,
            reason=f"HTTP {method_upper}: {rule}",
            risk_level=risk,
        )

    # ---- Rate limiting ----------------------------------------------------

    def check_rate_limit(self, skill_name: str) -> bool:
        """Returns True if the call is within limits, False if rate-limited.

        Uses Redis ZSET (sorted set) when a redis_client is available so that
        rate-limit windows survive container restarts. Falls back to an
        in-memory sliding window when Redis is not configured.
        """
        limits_cfg = self.config.get("rate_limits", {})
        skill_cfg = limits_cfg.get(skill_name, limits_cfg.get("default", {}))
        max_calls = skill_cfg.get("max_calls", 30)
        window = skill_cfg.get("window_seconds", 60)

        if self.redis_client is not None:
            return self._check_rate_limit_redis(skill_name, max_calls, window)
        return self._check_rate_limit_memory(skill_name, max_calls, window)

    def _check_rate_limit_redis(self, skill_name: str, max_calls: int, window: int) -> bool:
        """Redis-backed sliding window using a sorted set (score = timestamp)."""
        now = time.time()
        key = f"ratelimit:{skill_name}"
        call_id = str(uuid.uuid4())
        try:
            pipe = self.redis_client.pipeline()
            pipe.zremrangebyscore(key, 0, now - window)  # remove expired entries
            pipe.zadd(key, {call_id: now})               # record this call
            pipe.zcard(key)                              # count (includes our call)
            pipe.expire(key, window + 1)                 # auto-clean TTL
            results = pipe.execute()
            count = results[2]  # zcard result
            if count > max_calls:
                self.redis_client.zrem(key, call_id)     # undo our call
                return False
            return True
        except Exception:
            # Redis unavailable — fall back to in-memory for this call
            return self._check_rate_limit_memory(skill_name, max_calls, window)

    def _check_rate_limit_memory(self, skill_name: str, max_calls: int, window: int) -> bool:
        """In-memory sliding window fallback."""
        now = time.time()
        if skill_name not in self._rate_counters:
            self._rate_counters[skill_name] = []
        self._rate_counters[skill_name] = [
            t for t in self._rate_counters[skill_name] if now - t < window
        ]
        if len(self._rate_counters[skill_name]) >= max_calls:
            return False
        self._rate_counters[skill_name].append(now)
        return True

    # ---- Helpers ----------------------------------------------------------

    @staticmethod
    def _rule_to_decision(rule: str) -> Decision:
        """Convert a YAML rule string to a Decision enum."""
        mapping = {
            "allow": Decision.ALLOW,
            "deny": Decision.DENY,
            "requires_approval": Decision.REQUIRES_APPROVAL,
        }
        return mapping.get(rule, Decision.DENY)

    @staticmethod
    def _method_to_action(method: str) -> ActionType:
        mapping = {
            "GET": ActionType.HTTP_GET,
            "POST": ActionType.HTTP_POST,
            "PUT": ActionType.HTTP_PUT,
            "DELETE": ActionType.HTTP_DELETE,
        }
        return mapping.get(method.upper(), ActionType.HTTP_POST)
