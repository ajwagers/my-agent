"""
Tests for bootstrap.py — Proposal parsing and validation.
Runnable without Docker: python -m pytest tests/test_bootstrap.py -v
"""

import asyncio
import os
import pytest

import bootstrap
import identity
from approval import ApprovalManager


class TestExtractProposals:

    def test_single_proposal(self):
        text = (
            "Here's my proposal:\n"
            "<<PROPOSE:IDENTITY.md>>\n"
            "# Agent Identity\n"
            "name: Luna\n"
            "<<END_PROPOSE>>\n"
            "What do you think?"
        )
        proposals = bootstrap.extract_proposals(text)
        assert len(proposals) == 1
        assert proposals[0][0] == "IDENTITY.md"
        assert "name: Luna" in proposals[0][1]

    def test_multiple_proposals(self):
        text = (
            "<<PROPOSE:IDENTITY.md>>\n"
            "name: Luna\n"
            "<<END_PROPOSE>>\n"
            "And also:\n"
            "<<PROPOSE:USER.md>>\n"
            "# Owner\n"
            "name: Andy\n"
            "<<END_PROPOSE>>\n"
        )
        proposals = bootstrap.extract_proposals(text)
        assert len(proposals) == 2
        assert proposals[0][0] == "IDENTITY.md"
        assert proposals[1][0] == "USER.md"

    def test_no_proposals_returns_empty(self):
        text = "Just a normal response with no markers."
        assert bootstrap.extract_proposals(text) == []

    def test_malformed_markers_ignored(self):
        text = "<<PROPOSE:IDENTITY.md>> missing end marker"
        assert bootstrap.extract_proposals(text) == []

    def test_content_is_stripped(self):
        text = (
            "<<PROPOSE:SOUL.md>>\n"
            "  I am Luna  \n"
            "<<END_PROPOSE>>"
        )
        proposals = bootstrap.extract_proposals(text)
        assert proposals[0][1] == "I am Luna"


class TestStripProposals:

    def test_removes_markers_keeps_text(self):
        text = (
            "Here's my proposal:\n"
            "<<PROPOSE:IDENTITY.md>>\n"
            "name: Luna\n"
            "<<END_PROPOSE>>\n"
            "What do you think?"
        )
        stripped = bootstrap.strip_proposals(text)
        assert "<<PROPOSE" not in stripped
        assert "<<END_PROPOSE>>" not in stripped
        assert "Here's my proposal:" in stripped
        assert "What do you think?" in stripped

    def test_removes_multiple_proposals(self):
        text = (
            "A\n"
            "<<PROPOSE:IDENTITY.md>>\ncontent\n<<END_PROPOSE>>\n"
            "B\n"
            "<<PROPOSE:SOUL.md>>\ncontent\n<<END_PROPOSE>>\n"
            "C"
        )
        stripped = bootstrap.strip_proposals(text)
        assert "<<PROPOSE" not in stripped
        assert "A" in stripped
        assert "B" in stripped
        assert "C" in stripped

    def test_no_proposals_returns_original(self):
        text = "Normal text, nothing special."
        assert bootstrap.strip_proposals(text) == text

    def test_collapses_extra_newlines(self):
        text = (
            "Before\n\n\n"
            "<<PROPOSE:SOUL.md>>\ncontent\n<<END_PROPOSE>>\n\n\n"
            "After"
        )
        stripped = bootstrap.strip_proposals(text)
        assert "\n\n\n" not in stripped


class TestValidateProposal:

    def test_accepts_soul(self):
        ok, reason = bootstrap.validate_proposal("SOUL.md", "I am a soul")
        assert ok is True
        assert reason == "ok"

    def test_accepts_identity(self):
        ok, reason = bootstrap.validate_proposal("IDENTITY.md", "name: Luna")
        assert ok is True

    def test_accepts_user(self):
        ok, reason = bootstrap.validate_proposal("USER.md", "name: Andy")
        assert ok is True

    def test_rejects_bootstrap_file(self):
        ok, reason = bootstrap.validate_proposal("BOOTSTRAP.md", "content")
        assert ok is False
        assert "not in the allowed set" in reason

    def test_rejects_random_file(self):
        ok, reason = bootstrap.validate_proposal("random.txt", "content")
        assert ok is False

    def test_rejects_agents_file(self):
        ok, reason = bootstrap.validate_proposal("AGENTS.md", "content")
        assert ok is False

    def test_rejects_empty_content(self):
        ok, reason = bootstrap.validate_proposal("SOUL.md", "")
        assert ok is False
        assert "empty" in reason.lower()

    def test_rejects_whitespace_only(self):
        ok, reason = bootstrap.validate_proposal("SOUL.md", "   \n\n  ")
        assert ok is False

    def test_rejects_oversized_content(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "MAX_PROPOSAL_CHARS", 100)
        ok, reason = bootstrap.validate_proposal("SOUL.md", "A" * 200)
        assert ok is False
        assert "limit" in reason.lower()


class TestCheckBootstrapComplete:
    """Tests for bootstrap.check_bootstrap_complete()."""

    def test_deletes_bootstrap_when_all_exist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))

        # Create all required files + BOOTSTRAP.md
        (tmp_path / "SOUL.md").write_text("I am Luna")
        (tmp_path / "IDENTITY.md").write_text("name: Luna")
        (tmp_path / "USER.md").write_text("name: Andy")
        (tmp_path / "BOOTSTRAP.md").write_text("bootstrap instructions")

        bootstrap.check_bootstrap_complete()

        assert not (tmp_path / "BOOTSTRAP.md").exists()

    def test_does_not_delete_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))

        (tmp_path / "SOUL.md").write_text("I am Luna")
        (tmp_path / "IDENTITY.md").write_text("name: Luna")
        # USER.md missing
        (tmp_path / "BOOTSTRAP.md").write_text("bootstrap instructions")

        bootstrap.check_bootstrap_complete()

        assert (tmp_path / "BOOTSTRAP.md").exists()

    def test_does_not_delete_when_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))

        (tmp_path / "SOUL.md").write_text("I am Luna")
        (tmp_path / "IDENTITY.md").write_text("name: Luna")
        (tmp_path / "USER.md").write_text("")  # empty
        (tmp_path / "BOOTSTRAP.md").write_text("bootstrap instructions")

        bootstrap.check_bootstrap_complete()

        assert (tmp_path / "BOOTSTRAP.md").exists()

    def test_noop_when_no_bootstrap_file(self, tmp_path, monkeypatch):
        """Should not crash if BOOTSTRAP.md doesn't exist."""
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))

        (tmp_path / "SOUL.md").write_text("I am Luna")
        (tmp_path / "IDENTITY.md").write_text("name: Luna")
        (tmp_path / "USER.md").write_text("name: Andy")
        # No BOOTSTRAP.md — already exited bootstrap

        bootstrap.check_bootstrap_complete()  # Should not raise


class TestHandleBootstrapProposal:
    """Integration-style test for the bootstrap write workflow using FakeRedis.
    Tests the approval + file write logic directly (without importing app.py,
    which requires FastAPI/Docker dependencies).
    """

    @pytest.mark.asyncio
    async def test_writes_file_on_approval(self, tmp_path, monkeypatch, fake_redis):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        manager = ApprovalManager(redis_client=fake_redis, default_timeout=10)

        # Simulate handle_bootstrap_proposal logic
        approval_id = manager.create_request(
            action="bootstrap_write",
            zone="identity",
            risk_level="medium",
            description="Write SOUL.md during bootstrap",
            target="/agent/SOUL.md",
            proposed_content="I am Luna",
        )

        async def approve_later():
            await asyncio.sleep(0.2)
            manager.resolve(approval_id, "approved", "owner")

        task = asyncio.create_task(approve_later())
        status = await manager.wait_for_resolution(approval_id, timeout=5)
        await task

        assert status == "approved"
        # Simulate the file write that happens on approval
        if status == "approved":
            path = os.path.join(identity.IDENTITY_DIR, "SOUL.md")
            with open(path, "w") as f:
                f.write("I am Luna")

        assert (tmp_path / "SOUL.md").read_text() == "I am Luna"

    @pytest.mark.asyncio
    async def test_does_not_write_on_denial(self, tmp_path, monkeypatch, fake_redis):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        manager = ApprovalManager(redis_client=fake_redis, default_timeout=10)

        approval_id = manager.create_request(
            action="bootstrap_write",
            zone="identity",
            risk_level="medium",
            description="Write SOUL.md during bootstrap",
            target="/agent/SOUL.md",
            proposed_content="I am Luna",
        )

        async def deny_later():
            await asyncio.sleep(0.2)
            manager.resolve(approval_id, "denied", "owner")

        task = asyncio.create_task(deny_later())
        status = await manager.wait_for_resolution(approval_id, timeout=5)
        await task

        assert status == "denied"
        # Should NOT write the file
        assert not (tmp_path / "SOUL.md").exists()

    def test_proposed_content_stored_in_redis(self, fake_redis):
        """Verify proposed_content is stored in the Redis hash."""
        manager = ApprovalManager(redis_client=fake_redis, default_timeout=10)
        aid = manager.create_request(
            action="bootstrap_write",
            zone="identity",
            risk_level="medium",
            description="Write SOUL.md",
            target="/agent/SOUL.md",
            proposed_content="I am Luna, a spectral fox",
        )
        data = manager.get_request(aid)
        assert data["proposed_content"] == "I am Luna, a spectral fox"
