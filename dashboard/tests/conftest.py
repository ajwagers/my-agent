"""
Shared test fixtures for dashboard tests.
FakeRedis replicates the pattern from agent-core/tests/conftest.py.
"""

import os
import sys
import threading
from typing import Any, Dict, List, Optional

import pytest

# Ensure dashboard modules are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeRedis:
    """In-memory mock of redis-py with string, hash, list, and key ops."""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._lock = threading.Lock()

    # -- String ops --
    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def set(self, key: str, value: str, **kwargs) -> None:
        self._data[key] = value

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._data.pop(k, None)
            self._hashes.pop(k, None)
            self._lists.pop(k, None)

    # -- Hash ops --
    def hset(self, name: str, mapping: Optional[Dict] = None, **kwargs) -> None:
        if name not in self._hashes:
            self._hashes[name] = {}
        if mapping:
            for k, v in mapping.items():
                self._hashes[name][str(k)] = str(v)
        for k, v in kwargs.items():
            self._hashes[name][str(k)] = str(v)

    def hgetall(self, name: str) -> Dict[str, str]:
        return dict(self._hashes.get(name, {}))

    # -- List ops --
    def lpush(self, name: str, *values: str) -> int:
        if name not in self._lists:
            self._lists[name] = []
        for v in values:
            self._lists[name].insert(0, v)
        return len(self._lists[name])

    def ltrim(self, name: str, start: int, end: int) -> None:
        if name in self._lists:
            self._lists[name] = self._lists[name][start : end + 1]

    def lrange(self, name: str, start: int, end: int) -> List[str]:
        if name not in self._lists:
            return []
        if end == -1:
            return list(self._lists[name][start:])
        return list(self._lists[name][start : end + 1])

    def llen(self, name: str) -> int:
        return len(self._lists.get(name, []))

    # -- Key scanning --
    def keys(self, pattern: str = "*") -> List[str]:
        import fnmatch

        all_keys = (
            list(self._data.keys())
            + list(self._hashes.keys())
            + list(self._lists.keys())
        )
        return [k for k in all_keys if fnmatch.fnmatch(k, pattern)]

    # -- Info (for health probes) --
    def ping(self) -> bool:
        return True

    def info(self, section: str = "") -> Dict:
        return {"used_memory_human": "1.00M"}

    def expire(self, name: str, seconds: int) -> None:
        pass


@pytest.fixture
def fake_redis():
    return FakeRedis()
