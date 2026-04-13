"""
Redis-backed job queue manager.

Key structure:
  jobs:{job_id}           HASH  — job metadata
  jobs:scheduled          ZSET  — score=run_at, member=job_id (active jobs only)
  jobs:user:{user_id}     SET   — all job IDs for a user
  jobs:lock:{job_id}      STRING (EX 300, NX) — execution lock, prevents overlap
"""

import time
import uuid
from typing import Dict, List, Optional

_SCHEDULED_KEY = "jobs:scheduled"


class JobManager:
    def __init__(self, redis_client):
        self._redis = redis_client

    def find_duplicate(self, user_id: str, prompt: str, job_type: str) -> Optional[str]:
        """Return the ID of an existing active job with the same prompt/type, or None."""
        job_ids = self._redis.smembers(f"jobs:user:{user_id}")
        for job_id in job_ids:
            job = self.get(job_id)
            if (
                job
                and job.get("prompt") == prompt
                and job.get("job_type") == job_type
                and job.get("status") in ("pending", "running")
            ):
                return job_id
        return None

    def create(
        self,
        user_id: str,
        prompt: str,
        job_type: str,
        run_at: Optional[float] = None,
        delay_seconds: int = 0,
        interval_seconds: Optional[int] = None,
        persona: str = "default",
    ) -> str:
        """Create a new job and return its ID.

        For recurring jobs, returns the existing job ID if an identical active
        job already exists, preventing duplicates from repeated requests.
        """
        if job_type == "recurring":
            existing = self.find_duplicate(user_id, prompt, job_type)
            if existing:
                return existing

        job_id = uuid.uuid4().hex
        now = time.time()

        if job_type == "one_shot":
            scheduled_at = now + delay_seconds
        elif job_type == "scheduled":
            scheduled_at = float(run_at)
        else:  # recurring — first run is immediate
            scheduled_at = now

        mapping: Dict[str, str] = {
            "id": job_id,
            "user_id": user_id,
            "prompt": prompt,
            "job_type": job_type,
            "status": "pending",
            "created_at": str(now),
            "run_at": str(scheduled_at),
            "persona": persona,
        }
        if interval_seconds is not None:
            mapping["interval_seconds"] = str(interval_seconds)

        self._redis.hset(f"jobs:{job_id}", mapping=mapping)
        self._redis.zadd(_SCHEDULED_KEY, {job_id: scheduled_at})
        self._redis.sadd(f"jobs:user:{user_id}", job_id)

        return job_id

    def get(self, job_id: str) -> Optional[Dict]:
        """Return job dict or None if not found."""
        data = self._redis.hgetall(f"jobs:{job_id}")
        if not data:
            return None
        for float_field in ("created_at", "run_at", "last_run"):
            if float_field in data:
                try:
                    data[float_field] = float(data[float_field])
                except (ValueError, TypeError):
                    pass
        if "interval_seconds" in data:
            try:
                data["interval_seconds"] = int(data["interval_seconds"])
            except (ValueError, TypeError):
                pass
        return data

    def list_for_user(self, user_id: str) -> List[Dict]:
        """Return all jobs owned by user_id."""
        job_ids = self._redis.smembers(f"jobs:user:{user_id}")
        if not job_ids:
            return []
        jobs = []
        for job_id in job_ids:
            job = self.get(job_id)
            if job:
                jobs.append(job)
        return jobs

    def cancel(self, job_id: str) -> bool:
        """Cancel a job. Returns False if not found or already running."""
        job = self.get(job_id)
        if not job:
            return False
        if job.get("status") == "running":
            return False
        self._redis.hset(f"jobs:{job_id}", mapping={"status": "cancelled"})
        self._redis.zrem(_SCHEDULED_KEY, job_id)
        return True

    def get_due_jobs(self) -> List[Dict]:
        """Return all jobs whose run_at <= now (ZRANGEBYSCORE 0 to now)."""
        now = time.time()
        job_ids = self._redis.zrangebyscore(_SCHEDULED_KEY, 0, now)
        jobs = []
        for job_id in job_ids:
            job = self.get(job_id)
            if job:
                jobs.append(job)
        return jobs

    def mark_running(self, job_id: str) -> bool:
        """Acquire execution lock. Returns True only if lock was acquired (SET NX)."""
        result = self._redis.set(f"jobs:lock:{job_id}", "1", ex=300, nx=True)
        if result:
            self._redis.hset(f"jobs:{job_id}", mapping={"status": "running"})
        return bool(result)

    def mark_complete(self, job_id: str, result_preview: str) -> None:
        """Mark job as completed and remove from ZSET (unless recurring)."""
        now = time.time()
        self._redis.hset(f"jobs:{job_id}", mapping={
            "status": "completed",
            "last_run": str(now),
            "result_preview": result_preview[:200],
        })
        job = self.get(job_id)
        if job and job.get("job_type") != "recurring":
            self._redis.zrem(_SCHEDULED_KEY, job_id)

    def mark_failed(self, job_id: str, error: str) -> None:
        """Mark job as failed."""
        now = time.time()
        self._redis.hset(f"jobs:{job_id}", mapping={
            "status": "failed",
            "last_run": str(now),
            "error": error[:500],
        })
        job = self.get(job_id)
        if job and job.get("job_type") != "recurring":
            self._redis.zrem(_SCHEDULED_KEY, job_id)

    def reschedule(self, job_id: str) -> None:
        """For recurring jobs: set run_at = now + interval and re-add to ZSET."""
        job = self.get(job_id)
        if not job:
            return
        interval = job.get("interval_seconds", 0)
        if not interval:
            return
        now = time.time()
        new_run_at = now + float(interval)
        self._redis.hset(f"jobs:{job_id}", mapping={
            "run_at": str(new_run_at),
            "status": "pending",
        })
        self._redis.zadd(_SCHEDULED_KEY, {job_id: new_run_at})

    def release_lock(self, job_id: str) -> None:
        """Release execution lock."""
        self._redis.delete(f"jobs:lock:{job_id}")

    def list_all_scheduled(self) -> List[Dict]:
        """Return all jobs in the scheduled ZSET, sorted by run_at ascending."""
        pairs = self._redis.zrange(_SCHEDULED_KEY, 0, -1, withscores=True)
        jobs = []
        for job_id, score in pairs:
            if isinstance(job_id, bytes):
                job_id = job_id.decode()
            job = self.get(job_id)
            if job:
                jobs.append(job)
        return jobs

    def list_recent(self, user_id: str, limit: int = 30) -> List[Dict]:
        """Return recent jobs for a user sorted by created_at descending.

        Includes jobs in all states (pending, running, completed, failed).
        """
        raw_ids = self._redis.smembers(f"jobs:user:{user_id}")
        jobs = []
        for job_id in raw_ids:
            if isinstance(job_id, bytes):
                job_id = job_id.decode()
            job = self.get(job_id)
            if job:
                jobs.append(job)
        jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
        return jobs[:limit]
