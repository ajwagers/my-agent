"""Tests for dashboard/health_probes.py."""

from unittest.mock import patch, MagicMock

import pytest

from health_probes import (
    check_agent_core,
    check_ollama,
    check_chromadb,
    check_redis,
    check_web_ui,
    check_telegram_gateway,
    check_all,
)


class TestCheckAgentCore:
    @patch("health_probes.requests.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"status": "healthy"}
        )
        status, details = check_agent_core()
        assert status == "healthy"
        assert details["status"] == "healthy"

    @patch("health_probes.requests.get")
    def test_unhealthy_on_error(self, mock_get):
        mock_get.side_effect = ConnectionError("refused")
        status, details = check_agent_core()
        assert status == "unhealthy"
        assert "error" in details


class TestCheckOllama:
    @patch("health_probes.requests.get")
    def test_healthy_with_models(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": [{"name": "phi3:latest"}, {"name": "llama3.1:8b"}]},
        )
        status, details = check_ollama()
        assert status == "healthy"
        assert "phi3:latest" in details["models"]
        assert "llama3.1:8b" in details["models"]

    @patch("health_probes.requests.get")
    def test_unhealthy_on_timeout(self, mock_get):
        mock_get.side_effect = TimeoutError("timeout")
        status, _ = check_ollama()
        assert status == "unhealthy"


class TestCheckChromaDB:
    @patch("health_probes.requests.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
        status, _ = check_chromadb()
        assert status == "healthy"


class TestCheckRedis:
    def test_healthy(self, fake_redis):
        status, details = check_redis(fake_redis)
        assert status == "healthy"
        assert "memory" in details

    def test_unhealthy_on_error(self):
        bad_client = MagicMock()
        bad_client.ping.side_effect = ConnectionError("down")
        status, details = check_redis(bad_client)
        assert status == "unhealthy"
        assert "error" in details


class TestCheckWebUI:
    @patch("health_probes.requests.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        status, _ = check_web_ui()
        assert status == "healthy"

    @patch("health_probes.requests.get")
    def test_unhealthy(self, mock_get):
        mock_get.side_effect = ConnectionError("refused")
        status, _ = check_web_ui()
        assert status == "unhealthy"


class TestCheckTelegramGateway:
    def test_always_unknown(self):
        status, details = check_telegram_gateway()
        assert status == "unknown"
        assert "note" in details


class TestCheckAll:
    @patch("health_probes.requests.get")
    def test_returns_all_services(self, mock_get, fake_redis):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"status": "healthy", "models": []}
        )
        results = check_all(fake_redis)
        assert set(results.keys()) == {
            "agent_core", "ollama", "chromadb", "redis", "web_ui", "telegram_gateway",
        }
