"""
Skill registry — central store for all registered skills.

Loaded at startup, queried per-request. Adding a skill:
  registry.register(MySkill())

No remote fetching, no auto-discovery from external sources.
All skills are explicitly registered in app.py.
"""

from typing import Dict, List, Optional

from skills.base import SkillBase


class SkillRegistry:
    """Central skill registry. Thread-safe for reads after startup registration."""

    def __init__(self):
        self._skills: Dict[str, SkillBase] = {}

    def register(self, skill: SkillBase) -> None:
        """Register a skill. Raises ValueError if name already registered."""
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' is already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[SkillBase]:
        """Return skill by name, or None if not registered."""
        return self._skills.get(name)

    def all_skills(self) -> List[SkillBase]:
        """Return all registered skills in registration order."""
        return list(self._skills.values())

    def to_ollama_tools(self) -> List[Dict]:
        """Convert all skills to Ollama tool-calling format.

        Returns an empty list if no skills are registered.
        Callers should use: tools = registry.to_ollama_tools() or None
        to avoid passing tools=[] to Ollama (some versions treat it
        differently from omitting the parameter entirely).
        """
        return [skill.to_ollama_tool() for skill in self._skills.values()]

    def __len__(self) -> int:
        return len(self._skills)

    def __repr__(self) -> str:
        names = list(self._skills.keys())
        return f"SkillRegistry({names})"
