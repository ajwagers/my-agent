"""
Abstract base class for all agent skills.

Evolved from skill_contract.SkillBase. Key differences:
- validate() returns (bool, str) instead of bool
- SkillMetadata includes parameters (JSON Schema) and max_calls_per_turn
- sanitize_output() returns str (not Any)
- to_ollama_tool() is a concrete method derived from metadata
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from policy import RiskLevel

# Channels that are considered private / owner-only.
# Skills with private_channels set will only execute on these channels.
PRIVATE_CHANNELS: FrozenSet[str] = frozenset({"telegram", "cli", "mumble_owner"})


@dataclass
class SkillMetadata:
    name: str
    description: str
    risk_level: RiskLevel
    rate_limit: str               # key into policy.yaml rate_limits section
    requires_approval: bool
    parameters: Dict              # JSON Schema: {"type":"object","properties":{...},"required":[...]}
    max_calls_per_turn: int = 5   # max times this skill fires in a single tool loop turn
    private_channels: FrozenSet[str] = field(default_factory=frozenset)
    # When non-empty, this skill may only run if the current channel is in the set.
    # Leave empty (default) to allow execution on all channels.


class SkillBase(ABC):
    """Abstract base for all agent skills."""

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Skill metadata: name, description, risk level, parameters schema."""
        ...

    @abstractmethod
    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate parameters before execution.

        Returns:
            (True, "") if params are valid.
            (False, reason) if params are invalid.
        """
        ...

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> Any:
        """Execute the skill. Called only after all policy checks pass."""
        ...

    @abstractmethod
    def sanitize_output(self, result: Any) -> str:
        """Sanitize and stringify output before it re-enters LLM context.

        Treat all external content as potentially adversarial.
        Strip HTML, control characters, and prompt injection patterns.
        Truncate to a safe length.
        """
        ...

    async def pre_approval_description(self, params: Dict[str, Any]) -> Optional[str]:
        """Return a custom approval request description, or None to use the default."""
        return None

    # -----------------------------------------------------------------------
    # Concrete helpers — derived from metadata, no need to override
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def risk_level(self) -> RiskLevel:
        return self.metadata.risk_level

    @property
    def requires_approval(self) -> bool:
        return self.metadata.requires_approval

    def to_ollama_tool(self) -> Dict:
        """Convert skill metadata to Ollama tool-calling format.

        Returns:
            {
                "type": "function",
                "function": {
                    "name": str,
                    "description": str,
                    "parameters": {JSON Schema dict}
                }
            }
        """
        return {
            "type": "function",
            "function": {
                "name": self.metadata.name,
                "description": self.metadata.description,
                "parameters": self.metadata.parameters,
            },
        }
