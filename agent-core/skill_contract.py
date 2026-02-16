"""
Skill Security Contract — abstract base class for all agent skills.

Every skill must declare its risk level, rate limit, and whether it needs
owner approval. The policy engine uses these declarations to enforce guardrails.
Concrete implementations come in Chunk 4A.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

from policy import RiskLevel


@dataclass
class SkillMetadata:
    name: str
    description: str
    risk_level: RiskLevel
    rate_limit: str  # key into policy.yaml rate_limits section
    requires_approval: bool


class SkillBase(ABC):
    """Abstract base class that all skills must implement."""

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return skill metadata for policy engine inspection."""
        ...

    @abstractmethod
    def validate(self, params: Dict[str, Any]) -> bool:
        """Validate parameters before execution. Raise ValueError on invalid input."""
        ...

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> Any:
        """Execute the skill. Called only after policy checks pass."""
        ...

    @abstractmethod
    def sanitize_output(self, result: Any) -> Any:
        """Sanitize skill output before returning to the agent/user."""
        ...

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def risk_level(self) -> RiskLevel:
        return self.metadata.risk_level

    @property
    def requires_approval(self) -> bool:
        return self.metadata.requires_approval
