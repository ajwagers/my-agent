"""
Tests for the Approval Gate Manager.
Runnable without Docker: python -m pytest tests/test_approval.py -v
"""

import asyncio
import time

import pytest

from approval import ApprovalManager


class TestApprovalCreate:

    def test_create_returns_uuid(self, approval_manager):
        aid = approval_manager.create_request(
            action="write",
            zone="identity",
            risk_level="medium",
            description="Write to soul.md",
            target="/agent/soul.md",
        )
        assert aid is not None
        assert len(aid) == 36  # UUID format

    def test_create_stores_in_redis(self, approval_manager, fake_redis):
        aid = approval_manager.create_request(
            action="write",
            zone="identity",
            risk_level="medium",
            description="Test action",
        )
        data = fake_redis.hgetall(f"approval:{aid}")
        assert data["status"] == "pending"
        assert data["action"] == "write"
        assert data["zone"] == "identity"

    def test_create_publishes_notification(self, fake_redis):
        """Verify create_request publishes to the approvals:pending channel."""
        pubsub = fake_redis.pubsub()
        pubsub.subscribe("approvals:pending")

        manager = ApprovalManager(redis_client=fake_redis, default_timeout=2)
        manager.create_request(
            action="write",
            zone="identity",
            risk_level="medium",
            description="Test publish",
        )

        msg = pubsub.get_message()
        assert msg is not None
        assert msg["channel"] == "approvals:pending"


class TestApprovalResolve:

    def test_resolve_approved(self, approval_manager, fake_redis):
        aid = approval_manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Test",
        )
        ok = approval_manager.resolve(aid, "approved", "owner")
        assert ok is True

        data = fake_redis.hgetall(f"approval:{aid}")
        assert data["status"] == "approved"
        assert data["resolved_by"] == "owner"

    def test_resolve_denied(self, approval_manager, fake_redis):
        aid = approval_manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Test",
        )
        ok = approval_manager.resolve(aid, "denied", "owner")
        assert ok is True

        data = fake_redis.hgetall(f"approval:{aid}")
        assert data["status"] == "denied"

    def test_double_resolve_rejected(self, approval_manager):
        aid = approval_manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Test",
        )
        ok1 = approval_manager.resolve(aid, "approved", "owner")
        ok2 = approval_manager.resolve(aid, "denied", "attacker")
        assert ok1 is True
        assert ok2 is False  # Already resolved

    def test_resolve_nonexistent_returns_false(self, approval_manager):
        ok = approval_manager.resolve("nonexistent-uuid", "approved", "owner")
        assert ok is False


class TestApprovalTimeout:

    @pytest.mark.asyncio
    async def test_timeout_auto_denies(self, fake_redis):
        """With a 1-second timeout, wait_for_resolution should return 'timeout'."""
        manager = ApprovalManager(redis_client=fake_redis, default_timeout=1)
        aid = manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Timeout test",
        )
        result = await manager.wait_for_resolution(aid, timeout=1)
        assert result == "timeout"

        data = fake_redis.hgetall(f"approval:{aid}")
        assert data["status"] == "timeout"
        assert data["resolved_by"] == "system:timeout"

    @pytest.mark.asyncio
    async def test_resolution_before_timeout(self, fake_redis):
        """If resolved quickly, wait_for_resolution returns the decision."""
        manager = ApprovalManager(redis_client=fake_redis, default_timeout=10)
        aid = manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Quick approval",
        )

        # Resolve immediately in background
        async def approve_later():
            await asyncio.sleep(0.2)
            manager.resolve(aid, "approved", "owner")

        task = asyncio.create_task(approve_later())
        result = await manager.wait_for_resolution(aid, timeout=5)
        assert result == "approved"
        await task


class TestGetPending:

    def test_get_pending_returns_pending_only(self, approval_manager):
        aid1 = approval_manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Pending 1",
        )
        aid2 = approval_manager.create_request(
            action="write", zone="identity",
            risk_level="medium", description="Pending 2",
        )
        # Resolve one
        approval_manager.resolve(aid1, "approved", "owner")

        pending = approval_manager.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == aid2

    def test_get_pending_empty(self, approval_manager):
        assert approval_manager.get_pending() == []


class TestGetRequest:

    def test_get_existing(self, approval_manager):
        aid = approval_manager.create_request(
            action="read", zone="sandbox",
            risk_level="low", description="Get test",
        )
        data = approval_manager.get_request(aid)
        assert data is not None
        assert data["id"] == aid

    def test_get_nonexistent(self, approval_manager):
        assert approval_manager.get_request("no-such-id") is None
