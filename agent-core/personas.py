"""
Persona registry — named agent configurations with Redis persistence.

Each persona has:
  - A slug name (e.g. "summit_pine")
  - A display name
  - A system_prompt_extra injected after the base system prompt
  - An optional allowed_skills list (None = all skills)
  - An is_builtin flag (builtin personas cannot be deleted)

Session state (which persona is active for a user) is stored as:
  persona:session:{user_id}  STRING  — persona slug, absent = "default"

Persona definitions are stored as:
  persona:def:{name}   HASH   — name, display_name, system_prompt_extra,
                                allowed_skills (CSV or absent), is_builtin
  persona:names        SET    — all known persona slugs
"""

import os
from dataclasses import dataclass
from typing import List, Optional

import yaml

_PREFIX = "persona:def:"
_NAMES_KEY = "persona:names"
_SESSION_PREFIX = "persona:session:"


@dataclass
class Persona:
    name: str
    display_name: str
    system_prompt_extra: str
    allowed_skills: Optional[List[str]] = None  # None = all skills
    is_builtin: bool = False


class PersonaRegistry:
    def __init__(self, redis_client, yaml_path: str):
        self._redis = redis_client
        self._seed(yaml_path)

    # ── Seed ──────────────────────────────────────────────────────────────────

    def _seed(self, yaml_path: str) -> None:
        """Load YAML seed definitions into Redis.

        Builtin personas are always overwritten so that changes to
        allowed_skills or system_prompt_extra in personas.yaml take effect on
        the next container restart. User-created personas are never touched.
        """
        if not os.path.exists(yaml_path):
            return
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return
        for name, cfg in (data.get("personas") or {}).items():
            is_builtin = bool(cfg.get("is_builtin", False))
            already_exists = self._redis.exists(f"{_PREFIX}{name}")
            if not already_exists or is_builtin:
                self._write(
                    name=name,
                    display_name=cfg.get("display_name", name),
                    system_prompt_extra=cfg.get("system_prompt_extra", ""),
                    allowed_skills=cfg.get("allowed_skills"),
                    is_builtin=is_builtin,
                )

    # ── Internal read/write ───────────────────────────────────────────────────

    def _write(
        self,
        name: str,
        display_name: str,
        system_prompt_extra: str,
        allowed_skills: Optional[List[str]],
        is_builtin: bool = False,
    ) -> None:
        mapping = {
            "name": name,
            "display_name": display_name,
            "system_prompt_extra": system_prompt_extra or "",
            "is_builtin": "1" if is_builtin else "0",
        }
        if allowed_skills is not None:
            mapping["allowed_skills"] = ",".join(str(s) for s in allowed_skills)
        self._redis.hset(f"{_PREFIX}{name}", mapping=mapping)
        self._redis.sadd(_NAMES_KEY, name)

    def _read(self, name: str) -> Optional[Persona]:
        data = self._redis.hgetall(f"{_PREFIX}{name}")
        if not data:
            return None
        allowed_skills: Optional[List[str]] = None
        raw_skills = data.get("allowed_skills", "")
        if raw_skills:
            allowed_skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
        return Persona(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            system_prompt_extra=data.get("system_prompt_extra", ""),
            allowed_skills=allowed_skills,
            is_builtin=data.get("is_builtin", "0") == "1",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Persona]:
        """Return a Persona by slug, or None if not found."""
        return self._read(name)

    def list_all(self) -> List[Persona]:
        """Return all personas sorted: default first, then builtins, then user-created."""
        names = self._redis.smembers(_NAMES_KEY)
        personas = [p for name in names if (p := self._read(name))]

        def _key(p: Persona):
            if p.name == "default":
                return (0, p.name)
            if p.is_builtin:
                return (1, p.name)
            return (2, p.name)

        return sorted(personas, key=_key)

    def create(
        self,
        name: str,
        display_name: str,
        system_prompt_extra: str,
        allowed_skills: Optional[List[str]] = None,
    ) -> Persona:
        """Create or update a user persona. Raises ValueError if name is a builtin."""
        existing = self._read(name)
        if existing and existing.is_builtin:
            raise ValueError(f"'{name}' is a built-in persona and cannot be overwritten.")
        self._write(name, display_name, system_prompt_extra, allowed_skills, is_builtin=False)
        return self._read(name)

    def delete(self, name: str) -> bool:
        """Delete a user-created persona. Returns False if not found. Raises ValueError if builtin."""
        if name == "default":
            raise ValueError("Cannot delete the default persona.")
        existing = self._read(name)
        if not existing:
            return False
        if existing.is_builtin:
            raise ValueError(f"'{name}' is a built-in persona and cannot be deleted.")
        self._redis.delete(f"{_PREFIX}{name}")
        self._redis.srem(_NAMES_KEY, name)
        return True

    def get_session(self, user_id: str) -> str:
        """Return the active persona slug for this user (defaults to 'default')."""
        val = self._redis.get(f"{_SESSION_PREFIX}{user_id}")
        return val if val else "default"

    def set_session(self, user_id: str, name: str) -> None:
        """Set the active persona for a user. Pass 'default' to clear."""
        if name == "default":
            self._redis.delete(f"{_SESSION_PREFIX}{user_id}")
        else:
            self._redis.set(f"{_SESSION_PREFIX}{user_id}", name)
