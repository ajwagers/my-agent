"""
Approval Gate Manager — Redis-based approval workflow.

Flow:
  1. Policy engine determines an action needs approval
  2. ApprovalManager.create_request() stores hash in Redis, publishes to channel
  3. agent-core async-waits via wait_for_resolution() (polls Redis hash)
  4. telegram-gateway picks up notification, shows Approve/Deny to owner
  5. Owner clicks → telegram-gateway calls resolve()
  6. agent-core unblocks, reads the decision
  7. Timeout → auto-deny after configured seconds
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ApprovalRequest:
    id: str
    action: str
    zone: str
    risk_level: str
    description: str
    target: str
    status: str  # pending, approved, denied, timeout
    created_at: float
    resolved_at: Optional[float] = None
    resolved_by: Optional[str] = None


class ApprovalManager:
    """Manages approval requests via Redis hashes and pub/sub."""

    def __init__(self, redis_client, default_timeout: int = 300):
        self.redis = redis_client
        self.default_timeout = default_timeout
        self.prefix = "approval"
        self.channel = "approvals:pending"

    def create_request(
        self,
        action: str,
        zone: str,
        risk_level: str,
        description: str,
        target: str = "",
        proposed_content: Optional[str] = None,
    ) -> str:
        """Create an approval request. Returns the approval ID (UUID)."""
        approval_id = str(uuid.uuid4())
        key = f"{self.prefix}:{approval_id}"

        record = ApprovalRequest(
            id=approval_id,
            action=action,
            zone=zone,
            risk_level=risk_level,
            description=description,
            target=target,
            status="pending",
            created_at=time.time(),
        )

        mapping = {
            "id": record.id,
            "action": record.action,
            "zone": record.zone,
            "risk_level": record.risk_level,
            "description": record.description,
            "target": record.target,
            "status": record.status,
            "created_at": str(record.created_at),
        }
        if proposed_content is not None:
            mapping["proposed_content"] = proposed_content

        self.redis.hset(key, mapping=mapping)

        # Auto-expire after 2x timeout as cleanup
        self.redis.expire(key, self.default_timeout * 2)

        # Notify subscribers
        notification = {
            "approval_id": approval_id,
            "action": action,
            "zone": zone,
            "risk_level": risk_level,
            "description": description,
            "target": target,
        }
        if proposed_content is not None:
            notification["proposed_content"] = proposed_content

        self.redis.publish(self.channel, json.dumps(notification))

        return approval_id

    async def wait_for_resolution(
        self, approval_id: str, timeout: Optional[int] = None
    ) -> str:
        """Async poll Redis until approval is resolved or timeout.
        Returns status string: 'approved', 'denied', or 'timeout'.
        """
        timeout = timeout or self.default_timeout
        key = f"{self.prefix}:{approval_id}"
        deadline = time.time() + timeout

        while time.time() < deadline:
            status = self.redis.hget(key, "status")
            if status is None:
                return "timeout"  # Record disappeared
            if status != "pending":
                return status
            await asyncio.sleep(0.5)

        # Timeout reached — auto-deny
        self.redis.hset(key, mapping={
            "status": "timeout",
            "resolved_at": str(time.time()),
            "resolved_by": "system:timeout",
        })
        return "timeout"

    def resolve(
        self, approval_id: str, status: str, resolved_by: str = "owner"
    ) -> bool:
        """Resolve an approval request. Returns False if already resolved or not found."""
        key = f"{self.prefix}:{approval_id}"
        current = self.redis.hgetall(key)

        if not current:
            return False

        if current.get("status") != "pending":
            return False  # Already resolved — reject double-resolve

        self.redis.hset(key, mapping={
            "status": status,
            "resolved_at": str(time.time()),
            "resolved_by": resolved_by,
        })
        return True

    def get_request(self, approval_id: str) -> Optional[dict]:
        """Get a single approval request by ID."""
        key = f"{self.prefix}:{approval_id}"
        data = self.redis.hgetall(key)
        return data if data else None

    def get_pending(self) -> list[dict]:
        """Return all pending approval requests. For startup catch-up."""
        pending = []
        keys = self.redis.keys(f"{self.prefix}:*")
        for key in keys:
            data = self.redis.hgetall(key)
            if data and data.get("status") == "pending":
                pending.append(data)
        return pending
