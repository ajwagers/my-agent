"""
Shared test fixtures — FakeRedis, policy engine, approval manager.
All tests run without Docker or real Redis.
"""

import os
import sys
import time
import threading
from typing import Any, Dict, List, Optional

import pytest

# Ensure agent-core is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeRedis:
    """In-memory mock of redis-py, supporting hash ops, list ops, sorted-set ops, and pub/sub."""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._zsets: Dict[str, Dict[str, float]] = {}
        self._subscribers: Dict[str, List] = {}
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
            self._zsets.pop(k, None)

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

    def hget(self, name: str, key: str) -> Optional[str]:
        h = self._hashes.get(name, {})
        return h.get(key)

    # -- List ops --
    def lpush(self, name: str, *values: str) -> int:
        if name not in self._lists:
            self._lists[name] = []
        for v in values:
            self._lists[name].insert(0, v)
        return len(self._lists[name])

    def ltrim(self, name: str, start: int, end: int) -> None:
        if name in self._lists:
            self._lists[name] = self._lists[name][start:end + 1]

    def lrange(self, name: str, start: int, end: int) -> List[str]:
        if name not in self._lists:
            return []
        if end == -1:
            return list(self._lists[name][start:])
        return list(self._lists[name][start:end + 1])

    def llen(self, name: str) -> int:
        return len(self._lists.get(name, []))

    # -- Pub/Sub --
    def publish(self, channel: str, message: str) -> int:
        with self._lock:
            subs = self._subscribers.get(channel, [])
            for q in subs:
                q.append({"type": "message", "channel": channel, "data": message})
            return len(subs)

    def pubsub(self) -> "FakePubSub":
        return FakePubSub(self)

    # -- Key scanning --
    def keys(self, pattern: str = "*") -> List[str]:
        import fnmatch
        all_keys = list(self._data.keys()) + list(self._hashes.keys()) + list(self._lists.keys())
        return [k for k in all_keys if fnmatch.fnmatch(k, pattern)]

    def expire(self, name: str, seconds: int) -> None:
        pass  # no-op for tests

    # -- Sorted-set ops --
    def zadd(self, name: str, mapping: Dict[str, float]) -> int:
        if name not in self._zsets:
            self._zsets[name] = {}
        added = 0
        for member, score in mapping.items():
            if member not in self._zsets[name]:
                added += 1
            self._zsets[name][member] = score
        return added

    def zcard(self, name: str) -> int:
        return len(self._zsets.get(name, {}))

    def zrem(self, name: str, *members: str) -> int:
        zset = self._zsets.get(name, {})
        removed = 0
        for m in members:
            if m in zset:
                del zset[m]
                removed += 1
        return removed

    def zremrangebyscore(self, name: str, min_score: float, max_score: float) -> int:
        zset = self._zsets.get(name, {})
        to_remove = [m for m, score in zset.items() if min_score <= score <= max_score]
        for m in to_remove:
            del zset[m]
        return len(to_remove)

    def pipeline(self) -> "FakePipeline":
        return FakePipeline(self)


class FakePubSub:
    def __init__(self, fake_redis: FakeRedis):
        self._redis = fake_redis
        self._queue: List[Dict] = []
        self._channels: List[str] = []

    def subscribe(self, *channels: str) -> None:
        with self._redis._lock:
            for ch in channels:
                self._channels.append(ch)
                if ch not in self._redis._subscribers:
                    self._redis._subscribers[ch] = []
                self._redis._subscribers[ch].append(self._queue)

    def get_message(self, timeout: float = 0) -> Optional[Dict]:
        if self._queue:
            return self._queue.pop(0)
        return None

    def close(self) -> None:
        with self._redis._lock:
            for ch in self._channels:
                subs = self._redis._subscribers.get(ch, [])
                if self._queue in subs:
                    subs.remove(self._queue)


class FakePipeline:
    """Queues FakeRedis operations and replays them on execute(), returning a results list."""

    def __init__(self, fake_redis: FakeRedis):
        self._redis = fake_redis
        self._ops: List = []

    def zremrangebyscore(self, name: str, min_score: float, max_score: float) -> "FakePipeline":
        self._ops.append(("zremrangebyscore", (name, min_score, max_score)))
        return self

    def zadd(self, name: str, mapping: Dict[str, float]) -> "FakePipeline":
        self._ops.append(("zadd", (name, mapping)))
        return self

    def zcard(self, name: str) -> "FakePipeline":
        self._ops.append(("zcard", (name,)))
        return self

    def expire(self, name: str, seconds: int) -> "FakePipeline":
        self._ops.append(("expire", (name, seconds)))
        return self

    def execute(self) -> List:
        results = []
        for method, args in self._ops:
            fn = getattr(self._redis, method)
            results.append(fn(*args))
        self._ops.clear()
        return results


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def policy_engine(tmp_path):
    """PolicyEngine with a test config written to tmp_path."""
    from policy import PolicyEngine

    config = tmp_path / "policy.yaml"
    config.write_text(f"""
zones:
  sandbox:
    path: {tmp_path / 'sandbox'}
    read: allow
    write: allow
    execute: allow
  identity:
    path: {tmp_path / 'identity'}
    read: allow
    write: requires_approval
    execute: deny
  system:
    path: {tmp_path / 'system'}
    read: allow
    write: deny
    execute: deny

rate_limits:
  default:
    max_calls: 30
    window_seconds: 60
  test_skill:
    max_calls: 3
    window_seconds: 60

approval:
  timeout_seconds: 300
  redis_prefix: approval
  pubsub_channel: "approvals:pending"

external_access:
  http_get: allow
  http_post: requires_approval
  http_put: requires_approval
  http_delete: requires_approval
  denied_url_patterns:
    - ".*paypal\\\\.com.*"
    - ".*stripe\\\\.com/v1/charges.*"
    - ".*billing.*"
""")
    # Create zone directories
    (tmp_path / "sandbox").mkdir(exist_ok=True)
    (tmp_path / "identity").mkdir(exist_ok=True)
    (tmp_path / "system").mkdir(exist_ok=True)

    return PolicyEngine(config_path=str(config))


@pytest.fixture
def approval_manager(fake_redis):
    """ApprovalManager with FakeRedis and short timeout."""
    from approval import ApprovalManager
    return ApprovalManager(redis_client=fake_redis, default_timeout=2)


@pytest.fixture
def redis_policy_engine(tmp_path, fake_redis):
    """PolicyEngine backed by FakeRedis — exercises the Redis rate-limit path."""
    from policy import PolicyEngine

    config = tmp_path / "policy.yaml"
    config.write_text(f"""
zones:
  sandbox:
    path: {tmp_path / 'sandbox'}
    read: allow
    write: allow
    execute: allow
  identity:
    path: {tmp_path / 'identity'}
    read: allow
    write: requires_approval
    execute: deny
  system:
    path: {tmp_path / 'system'}
    read: allow
    write: deny
    execute: deny

rate_limits:
  default:
    max_calls: 30
    window_seconds: 60
  test_skill:
    max_calls: 3
    window_seconds: 60

approval:
  timeout_seconds: 300
  redis_prefix: approval
  pubsub_channel: "approvals:pending"

external_access:
  http_get: allow
  http_post: requires_approval
  http_put: requires_approval
  http_delete: requires_approval
  denied_url_patterns: []
""")
    (tmp_path / "sandbox").mkdir(exist_ok=True)
    (tmp_path / "identity").mkdir(exist_ok=True)
    (tmp_path / "system").mkdir(exist_ok=True)

    return PolicyEngine(config_path=str(config), redis_client=fake_redis)
