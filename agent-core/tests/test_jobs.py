"""
Tests for Phase 4C-Part-2: Job Queue & Scheduled Tasks.

Coverage:
  - JobManager CRUD, ZSET, lock, reschedule
  - CreateTaskSkill, ListTasksSkill, CancelTaskSkill
  - job_endpoints (GET /jobs, GET /jobs/{id}, DELETE /jobs/{id})
  - heartbeat _process_due_jobs / _run_job helpers
"""

import asyncio
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from job_manager import JobManager, _SCHEDULED_KEY


# ---------------------------------------------------------------------------
# TestJobManager
# ---------------------------------------------------------------------------

class TestJobManager:
    def test_create_one_shot(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("user1", "do something", "one_shot")
        assert job_id
        job = jm.get(job_id)
        assert job["job_type"] == "one_shot"
        assert job["status"] == "pending"
        assert job["user_id"] == "user1"
        assert job["prompt"] == "do something"
        # should be in ZSET
        assert fake_redis.zcard(_SCHEDULED_KEY) == 1

    def test_create_scheduled(self, fake_redis):
        jm = JobManager(fake_redis)
        future = time.time() + 3600
        job_id = jm.create("u", "run later", "scheduled", run_at=future)
        job = jm.get(job_id)
        assert job["job_type"] == "scheduled"
        assert abs(job["run_at"] - future) < 1

    def test_create_recurring(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "repeat", "recurring", interval_seconds=300)
        job = jm.get(job_id)
        assert job["job_type"] == "recurring"
        assert job["interval_seconds"] == 300
        # recurring first run should be ~ now
        assert abs(job["run_at"] - time.time()) < 2

    def test_create_one_shot_with_delay(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "delayed", "one_shot", delay_seconds=120)
        job = jm.get(job_id)
        assert job["run_at"] > time.time() + 100

    def test_get_existing(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        assert jm.get(job_id) is not None

    def test_get_missing(self, fake_redis):
        jm = JobManager(fake_redis)
        assert jm.get("nonexistent_id") is None

    def test_list_for_user_empty(self, fake_redis):
        jm = JobManager(fake_redis)
        assert jm.list_for_user("nobody") == []

    def test_list_for_user_populated(self, fake_redis):
        jm = JobManager(fake_redis)
        jm.create("alice", "task 1", "one_shot")
        jm.create("alice", "task 2", "one_shot")
        jm.create("bob", "bob task", "one_shot")
        alice_jobs = jm.list_for_user("alice")
        assert len(alice_jobs) == 2
        bob_jobs = jm.list_for_user("bob")
        assert len(bob_jobs) == 1

    def test_cancel_pending(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        result = jm.cancel(job_id)
        assert result is True
        job = jm.get(job_id)
        assert job["status"] == "cancelled"
        # Removed from ZSET
        assert fake_redis.zcard(_SCHEDULED_KEY) == 0

    def test_cancel_running_rejected(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        jm.mark_running(job_id)
        result = jm.cancel(job_id)
        assert result is False

    def test_cancel_missing(self, fake_redis):
        jm = JobManager(fake_redis)
        assert jm.cancel("no_such_id") is False

    def test_get_due_jobs_none(self, fake_redis):
        jm = JobManager(fake_redis)
        # Create a job 1 hour in the future
        jm.create("u", "future", "scheduled", run_at=time.time() + 3600)
        assert jm.get_due_jobs() == []

    def test_get_due_jobs_returns_due(self, fake_redis):
        jm = JobManager(fake_redis)
        # one_shot with no delay -> run_at ~ now
        job_id = jm.create("u", "now job", "one_shot")
        due = jm.get_due_jobs()
        assert any(j["id"] == job_id for j in due)

    def test_get_due_jobs_future_not_returned(self, fake_redis):
        jm = JobManager(fake_redis)
        jm.create("u", "future", "scheduled", run_at=time.time() + 3600)
        due = jm.get_due_jobs()
        assert due == []

    def test_mark_running_success(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        assert jm.mark_running(job_id) is True
        job = jm.get(job_id)
        assert job["status"] == "running"

    def test_mark_running_already_locked(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        assert jm.mark_running(job_id) is True
        # Second call should fail — NX means key already exists
        assert jm.mark_running(job_id) is False

    def test_mark_complete_updates_hash(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        jm.mark_running(job_id)
        jm.mark_complete(job_id, "the result")
        job = jm.get(job_id)
        assert job["status"] == "completed"
        assert "the result" in job["result_preview"]

    def test_mark_complete_removes_from_zset_non_recurring(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        jm.mark_running(job_id)
        jm.mark_complete(job_id, "done")
        assert fake_redis.zcard(_SCHEDULED_KEY) == 0

    def test_mark_complete_keeps_recurring_in_zset_until_reschedule(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "repeat", "recurring", interval_seconds=60)
        jm.mark_running(job_id)
        # mark_complete does NOT remove recurring from ZSET
        jm.mark_complete(job_id, "done")
        # job stays (will be rescheduled by reschedule())
        assert fake_redis.zcard(_SCHEDULED_KEY) >= 1

    def test_mark_failed(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        jm.mark_running(job_id)
        jm.mark_failed(job_id, "something broke")
        job = jm.get(job_id)
        assert job["status"] == "failed"
        assert "something broke" in job["error"]
        # removed from ZSET
        assert fake_redis.zcard(_SCHEDULED_KEY) == 0

    def test_reschedule_updates_run_at(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "repeat", "recurring", interval_seconds=100)
        before = time.time()
        jm.reschedule(job_id)
        job = jm.get(job_id)
        assert job["run_at"] >= before + 95  # approximately now + 100

    def test_reschedule_readds_to_zset(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "repeat", "recurring", interval_seconds=100)
        # Remove from zset manually to simulate completion
        fake_redis.zrem(_SCHEDULED_KEY, job_id)
        jm.reschedule(job_id)
        assert fake_redis.zcard(_SCHEDULED_KEY) == 1

    def test_release_lock(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "test", "one_shot")
        jm.mark_running(job_id)
        jm.release_lock(job_id)
        # After release, lock should be acquirable again
        assert jm.mark_running(job_id) is True


# ---------------------------------------------------------------------------
# TestCreateTaskSkill
# ---------------------------------------------------------------------------

class TestCreateTaskSkill:
    def _skill(self, fake_redis):
        from skills.create_task import CreateTaskSkill
        return CreateTaskSkill(fake_redis)

    def test_validate_one_shot_valid(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "do it", "job_type": "one_shot"})
        assert ok

    def test_validate_scheduled_valid(self, fake_redis):
        s = self._skill(fake_redis)
        future = "2099-01-01T00:00:00Z"
        ok, reason = s.validate({"prompt": "do it", "job_type": "scheduled", "run_at": future})
        assert ok

    def test_validate_recurring_valid(self, fake_redis):
        s = self._skill(fake_redis)
        ok, _ = s.validate({"prompt": "repeat", "job_type": "recurring", "interval_seconds": 60})
        assert ok

    def test_validate_missing_prompt(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"job_type": "one_shot"})
        assert not ok
        assert "prompt" in reason.lower()

    def test_validate_invalid_job_type(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "x", "job_type": "invalid"})
        assert not ok

    def test_validate_scheduled_missing_run_at(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "x", "job_type": "scheduled"})
        assert not ok
        assert "run_at" in reason.lower()

    def test_validate_scheduled_past_time(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({
            "prompt": "x",
            "job_type": "scheduled",
            "run_at": "2000-01-01T00:00:00Z",
        })
        assert not ok
        assert "future" in reason.lower()

    def test_validate_recurring_missing_interval(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "x", "job_type": "recurring"})
        assert not ok

    def test_validate_recurring_interval_zero(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "x", "job_type": "recurring", "interval_seconds": 0})
        assert not ok

    def test_validate_prompt_too_long(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"prompt": "x" * 501, "job_type": "one_shot"})
        assert not ok
        assert "500" in reason

    @pytest.mark.asyncio
    async def test_execute_one_shot(self, fake_redis):
        s = self._skill(fake_redis)
        result = await s.execute({"prompt": "test", "job_type": "one_shot", "_user_id": "u1"})
        assert "job_id" in result
        assert result["job_type"] == "one_shot"

    @pytest.mark.asyncio
    async def test_execute_scheduled(self, fake_redis):
        s = self._skill(fake_redis)
        result = await s.execute({
            "prompt": "later",
            "job_type": "scheduled",
            "run_at": "2099-06-01T12:00:00Z",
            "_user_id": "u1",
        })
        assert result["job_type"] == "scheduled"

    @pytest.mark.asyncio
    async def test_execute_recurring(self, fake_redis):
        s = self._skill(fake_redis)
        result = await s.execute({
            "prompt": "every day",
            "job_type": "recurring",
            "interval_seconds": 86400,
            "_user_id": "u1",
        })
        assert result["job_type"] == "recurring"

    def test_sanitize_output(self, fake_redis):
        s = self._skill(fake_redis)
        out = s.sanitize_output({"job_id": "abc123", "job_type": "one_shot", "run_at": 0.0})
        assert "abc123" in out
        assert "one_shot" in out


# ---------------------------------------------------------------------------
# TestListTasksSkill
# ---------------------------------------------------------------------------

class TestListTasksSkill:
    def _skill(self, fake_redis):
        from skills.list_tasks import ListTasksSkill
        return ListTasksSkill(fake_redis)

    def test_validate_no_params(self, fake_redis):
        s = self._skill(fake_redis)
        ok, _ = s.validate({})
        assert ok

    def test_validate_with_status(self, fake_redis):
        s = self._skill(fake_redis)
        ok, _ = s.validate({"status": "pending"})
        assert ok

    def test_validate_invalid_status(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"status": "unknown"})
        assert not ok

    @pytest.mark.asyncio
    async def test_execute_empty(self, fake_redis):
        s = self._skill(fake_redis)
        result = await s.execute({"_user_id": "nobody"})
        assert result["jobs"] == []

    @pytest.mark.asyncio
    async def test_execute_multiple(self, fake_redis):
        jm = JobManager(fake_redis)
        jm.create("alice", "task 1", "one_shot")
        jm.create("alice", "task 2", "one_shot")
        s = self._skill(fake_redis)
        result = await s.execute({"_user_id": "alice"})
        assert len(result["jobs"]) == 2

    @pytest.mark.asyncio
    async def test_execute_filter_by_status(self, fake_redis):
        jm = JobManager(fake_redis)
        j1 = jm.create("alice", "task 1", "one_shot")
        jm.create("alice", "task 2", "one_shot")
        jm.cancel(j1)
        s = self._skill(fake_redis)
        result = await s.execute({"status": "pending", "_user_id": "alice"})
        assert all(j["status"] == "pending" for j in result["jobs"])

    def test_sanitize_output_empty(self, fake_redis):
        s = self._skill(fake_redis)
        out = s.sanitize_output({"jobs": []})
        assert "No jobs" in out

    def test_sanitize_output_with_jobs(self, fake_redis):
        s = self._skill(fake_redis)
        out = s.sanitize_output({"jobs": [
            {"id": "abc", "status": "pending", "prompt": "do something", "run_at": 0.0},
        ]})
        assert "abc" in out
        assert "pending" in out


# ---------------------------------------------------------------------------
# TestCancelTaskSkill
# ---------------------------------------------------------------------------

class TestCancelTaskSkill:
    def _skill(self, fake_redis):
        from skills.cancel_task import CancelTaskSkill
        return CancelTaskSkill(fake_redis)

    def test_validate_valid_id(self, fake_redis):
        s = self._skill(fake_redis)
        ok, _ = s.validate({"job_id": "abc123"})
        assert ok

    def test_validate_empty_id(self, fake_redis):
        s = self._skill(fake_redis)
        ok, reason = s.validate({"job_id": ""})
        assert not ok

    def test_validate_id_too_long(self, fake_redis):
        s = self._skill(fake_redis)
        ok, _ = s.validate({"job_id": "x" * 65})
        assert not ok

    @pytest.mark.asyncio
    async def test_execute_found_and_owned(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("alice", "task", "one_shot")
        s = self._skill(fake_redis)
        result = await s.execute({"job_id": job_id, "_user_id": "alice"})
        assert result.get("cancelled") is True

    @pytest.mark.asyncio
    async def test_execute_not_found(self, fake_redis):
        s = self._skill(fake_redis)
        result = await s.execute({"job_id": "no_such_job", "_user_id": "alice"})
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_wrong_owner(self, fake_redis):
        jm = JobManager(fake_redis)
        job_id = jm.create("alice", "task", "one_shot")
        s = self._skill(fake_redis)
        result = await s.execute({"job_id": job_id, "_user_id": "bob"})
        assert "error" in result
        assert "belong" in result["error"].lower()

    def test_sanitize_output_cancelled(self, fake_redis):
        s = self._skill(fake_redis)
        out = s.sanitize_output({"job_id": "abc", "cancelled": True})
        assert "abc" in out
        assert "cancelled" in out.lower()

    def test_sanitize_output_error(self, fake_redis):
        s = self._skill(fake_redis)
        out = s.sanitize_output({"error": "Job xyz not found."})
        assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# TestJobEndpoints
# ---------------------------------------------------------------------------

class TestJobEndpoints:
    @pytest.fixture
    def app_client(self, fake_redis):
        """Build a TestClient with job_manager and redis wired to app.state."""
        import os
        os.environ["AGENT_API_KEY"] = "test-key"

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from job_endpoints import router as jobs_router
        from job_manager import JobManager

        app = FastAPI()
        app.include_router(jobs_router)
        app.state.redis_client = fake_redis
        app.state.job_manager = JobManager(fake_redis)

        return TestClient(app), app.state.job_manager

    def test_get_jobs_empty(self, app_client):
        client, _ = app_client
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    def test_get_jobs_with_jobs(self, app_client):
        client, jm = app_client
        jm.create("u1", "test job", "one_shot")
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert len(resp.json()["jobs"]) == 1

    def test_get_jobs_filter_by_user_id(self, app_client):
        client, jm = app_client
        jm.create("alice", "alice task", "one_shot")
        jm.create("bob", "bob task", "one_shot")
        resp = client.get("/jobs?user_id=alice")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert all(j["user_id"] == "alice" for j in jobs)

    def test_get_job_found(self, app_client):
        client, jm = app_client
        job_id = jm.create("u", "a job", "one_shot")
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_get_job_not_found(self, app_client):
        client, _ = app_client
        resp = client.get("/jobs/no_such_job")
        assert resp.status_code == 404

    def test_delete_job_success(self, app_client):
        client, jm = app_client
        job_id = jm.create("u", "a job", "one_shot")
        resp = client.delete(f"/jobs/{job_id}", headers={"X-Api-Key": "test-key"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_delete_job_not_found(self, app_client):
        client, _ = app_client
        resp = client.delete("/jobs/no_such_job", headers={"X-Api-Key": "test-key"})
        assert resp.status_code == 404

    def test_delete_job_no_auth(self, app_client):
        client, jm = app_client
        job_id = jm.create("u", "a job", "one_shot")
        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestHeartbeatJobProcessing
# ---------------------------------------------------------------------------

class TestHeartbeatJobProcessing:
    def _make_state(self, fake_redis, job_manager=None, **kwargs):
        """Build a minimal state object for heartbeat helpers."""
        class State:
            pass
        s = State()
        s.redis_client = fake_redis
        s.job_manager = job_manager
        for k, v in kwargs.items():
            setattr(s, k, v)
        return s

    @pytest.mark.asyncio
    async def test_process_due_jobs_no_manager(self, fake_redis):
        from heartbeat import _process_due_jobs
        state = self._make_state(fake_redis, job_manager=None)
        # Should return without raising
        await _process_due_jobs(state)

    @pytest.mark.asyncio
    async def test_process_due_jobs_no_due_jobs(self, fake_redis):
        from heartbeat import _process_due_jobs
        jm = JobManager(fake_redis)
        # Future job only
        jm.create("u", "later", "scheduled", run_at=time.time() + 3600)
        state = self._make_state(fake_redis, job_manager=jm)
        await _process_due_jobs(state)
        # No tasks spawned (nothing to assert — just no error)

    @pytest.mark.asyncio
    async def test_process_due_jobs_skips_locked(self, fake_redis):
        from heartbeat import _process_due_jobs
        jm = JobManager(fake_redis)
        job_id = jm.create("u", "now job", "one_shot")
        # Pre-acquire the lock
        jm.mark_running(job_id)

        tasks_created = []
        original_create_task = asyncio.create_task

        async def noop():
            pass

        import unittest.mock as mock
        with mock.patch("asyncio.create_task") as mock_ct:
            state = self._make_state(fake_redis, job_manager=jm)
            await _process_due_jobs(state)
            # create_task should NOT have been called because job is locked
            mock_ct.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_due_jobs_fires_task(self, fake_redis):
        from heartbeat import _process_due_jobs
        jm = JobManager(fake_redis)
        jm.create("u", "now job", "one_shot")

        import unittest.mock as mock
        with mock.patch("asyncio.create_task") as mock_ct:
            state = self._make_state(fake_redis, job_manager=jm)
            await _process_due_jobs(state)
            mock_ct.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_job_success(self, fake_redis):
        from heartbeat import _run_job
        import unittest.mock as mock

        jm = JobManager(fake_redis)
        job_id = jm.create("u", "do something", "one_shot")
        jm.mark_running(job_id)
        job = jm.get(job_id)

        published = []

        class FakeRegistry:
            def to_ollama_tools(self):
                return []

        class FakeOllama:
            pass

        class FakePolicyEngine:
            pass

        class FakeApprovalManager:
            pass

        state = self._make_state(
            fake_redis,
            job_manager=jm,
            ollama_client=FakeOllama(),
            skill_registry=FakeRegistry(),
            tool_model="test-model",
            num_ctx=2048,
            max_tool_iterations=3,
            policy_engine=FakePolicyEngine(),
            approval_manager=FakeApprovalManager(),
        )

        async def mock_run_tool_loop(**kwargs):
            return "result text", [], {"iterations": 0, "skills_called": []}

        with mock.patch("skill_runner.run_tool_loop", new=mock_run_tool_loop):
            await _run_job(state, job)

        completed = jm.get(job_id)
        assert completed["status"] == "completed"

    @pytest.mark.asyncio
    async def test_run_job_failure_caught(self, fake_redis):
        from heartbeat import _run_job
        import unittest.mock as mock

        jm = JobManager(fake_redis)
        job_id = jm.create("u", "broken job", "one_shot")
        jm.mark_running(job_id)
        job = jm.get(job_id)

        class FakeRegistry:
            def to_ollama_tools(self):
                return []

        state = self._make_state(
            fake_redis,
            job_manager=jm,
            ollama_client=object(),
            skill_registry=FakeRegistry(),
            tool_model="m",
            num_ctx=2048,
            max_tool_iterations=3,
            policy_engine=object(),
            approval_manager=object(),
        )

        async def mock_run_tool_loop(**kwargs):
            raise RuntimeError("model exploded")

        with mock.patch("skill_runner.run_tool_loop", new=mock_run_tool_loop):
            await _run_job(state, job)

        failed = jm.get(job_id)
        assert failed["status"] == "failed"
        assert "model exploded" in failed["error"]
