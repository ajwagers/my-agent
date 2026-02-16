"""
Tests for identity.py — Identity file loader and system prompt builder.
Runnable without Docker: python -m pytest tests/test_identity.py -v
"""

import os
import pytest

import identity


class TestIsBootstrapMode:

    def test_bootstrap_mode_when_file_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        (tmp_path / "BOOTSTRAP.md").write_text("bootstrap instructions")
        assert identity.is_bootstrap_mode() is True

    def test_normal_mode_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        assert identity.is_bootstrap_mode() is False

    def test_normal_mode_when_directory_has_other_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        (tmp_path / "SOUL.md").write_text("soul content")
        assert identity.is_bootstrap_mode() is False


class TestLoadFile:

    def test_loads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        (tmp_path / "SOUL.md").write_text("I am a soul")
        assert identity.load_file("SOUL.md") == "I am a soul"

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        assert identity.load_file("NOPE.md") is None

    def test_truncates_at_max_chars(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        monkeypatch.setattr(identity, "MAX_FILE_CHARS", 10)
        (tmp_path / "BIG.md").write_text("A" * 100)
        content = identity.load_file("BIG.md")
        assert len(content) == 10

    def test_handles_empty_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        (tmp_path / "EMPTY.md").write_text("")
        assert identity.load_file("EMPTY.md") == ""


class TestLoadIdentity:

    def test_returns_all_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        result = identity.load_identity()
        assert set(result.keys()) == {"bootstrap", "soul", "identity", "user", "agents"}

    def test_missing_files_are_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        result = identity.load_identity()
        for key in result:
            assert result[key] is None

    def test_existing_files_loaded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(identity, "IDENTITY_DIR", str(tmp_path))
        (tmp_path / "SOUL.md").write_text("soul content")
        (tmp_path / "AGENTS.md").write_text("agents content")
        result = identity.load_identity()
        assert result["soul"] == "soul content"
        assert result["agents"] == "agents content"
        assert result["bootstrap"] is None


class TestParseIdentityFields:

    def test_parses_all_fields(self):
        content = (
            "# Agent Identity\n"
            "name: Luna\n"
            "nature: spectral fox\n"
            "vibe: curious and warm\n"
            "emoji: 🦊\n"
        )
        fields = identity.parse_identity_fields(content)
        assert fields == {
            "name": "Luna",
            "nature": "spectral fox",
            "vibe": "curious and warm",
            "emoji": "🦊",
        }

    def test_ignores_unknown_fields(self):
        content = "name: Luna\nfavorite_color: blue\n"
        fields = identity.parse_identity_fields(content)
        assert fields == {"name": "Luna"}

    def test_ignores_comments_and_blanks(self):
        content = "# Header\n\nname: Luna\n# comment\nvibe: chill\n"
        fields = identity.parse_identity_fields(content)
        assert fields == {"name": "Luna", "vibe": "chill"}

    def test_handles_empty_content(self):
        assert identity.parse_identity_fields("") == {}

    def test_handles_malformed_lines(self):
        content = "this has no colon\nname: Valid\njust text\n"
        fields = identity.parse_identity_fields(content)
        assert fields == {"name": "Valid"}


class TestBuildSystemPrompt:

    def test_bootstrap_mode_includes_bootstrap_and_agents(self):
        loaded = {
            "bootstrap": "Bootstrap instructions here",
            "soul": "Soul content",
            "identity": "Identity content",
            "user": "User content",
            "agents": "Agent rules",
        }
        prompt = identity.build_system_prompt(loaded)
        assert "Bootstrap instructions here" in prompt
        assert "Agent rules" in prompt
        assert "Soul content" not in prompt

    def test_normal_mode_includes_soul_agents_user(self):
        loaded = {
            "bootstrap": None,
            "soul": "I am Luna",
            "identity": "name: Luna",
            "user": "Owner: Andy",
            "agents": "Be concise",
        }
        prompt = identity.build_system_prompt(loaded)
        assert "I am Luna" in prompt
        assert "Be concise" in prompt
        assert "Owner: Andy" in prompt

    def test_normal_mode_omits_missing_files(self):
        loaded = {
            "bootstrap": None,
            "soul": "I am Luna",
            "identity": None,
            "user": None,
            "agents": None,
        }
        prompt = identity.build_system_prompt(loaded)
        assert prompt == "I am Luna"

    def test_bootstrap_mode_without_agents(self):
        loaded = {
            "bootstrap": "Bootstrap text",
            "soul": None,
            "identity": None,
            "user": None,
            "agents": None,
        }
        prompt = identity.build_system_prompt(loaded)
        assert prompt == "Bootstrap text"

    def test_all_none_returns_empty(self):
        loaded = {
            "bootstrap": None,
            "soul": None,
            "identity": None,
            "user": None,
            "agents": None,
        }
        prompt = identity.build_system_prompt(loaded)
        assert prompt == ""
