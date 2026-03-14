"""
Job REST endpoints — FastAPI APIRouter.

GET    /jobs               — list all jobs; optional ?user_id= and ?status= filters
GET    /jobs/{job_id}      — get single job by ID
DELETE /jobs/{job_id}      — cancel a job; requires X-Api-Key

Pattern mirrors approval_endpoints.py exactly.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader

router = APIRouter(prefix="/jobs", tags=["jobs"])

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


def _require_api_key(api_key: str = Security(_api_key_header)):
    expected = os.getenv("AGENT_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="AGENT_API_KEY not configured on server")
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("")
async def list_jobs(request: Request, user_id: str = None, status: str = None):
    """List all jobs, optionally filtered by user_id and/or status."""
    job_manager = request.app.state.job_manager
    redis = request.app.state.redis_client

    all_user_keys = redis.keys("jobs:user:*")
    seen = set()
    all_jobs = []

    for key in all_user_keys:
        uid = key.removeprefix("jobs:user:")
        if user_id is not None and uid != user_id:
            continue
        job_ids = redis.smembers(key)
        for jid in job_ids:
            if jid in seen:
                continue
            seen.add(jid)
            job = job_manager.get(jid)
            if job:
                all_jobs.append(job)

    if status:
        all_jobs = [j for j in all_jobs if j.get("status") == status]

    return {"jobs": all_jobs}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    """Get a single job by ID."""
    job_manager = request.app.state.job_manager
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/{job_id}", dependencies=[Depends(_require_api_key)])
async def cancel_job(job_id: str, request: Request):
    """Cancel a job by ID. Requires X-Api-Key."""
    job_manager = request.app.state.job_manager
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    ok = job_manager.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled (may be running)")
    return {"job_id": job_id, "status": "cancelled"}
