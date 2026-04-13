"""
github-gateway — thin, opinionated proxy to the GitHub API.

Holds GITHUB_TOKEN so agent-core never sees it.
Enforces GITHUB_ALLOW_REPOS allow-list before every request.
Reachable only on the internal agent_net Docker network (no host port).
"""

import base64
import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
_raw_allow = os.getenv("GITHUB_ALLOW_REPOS", "").strip()
ALLOW_REPOS: frozenset[str] = (
    frozenset(r.strip() for r in _raw_allow.split(",") if r.strip())
    if _raw_allow else frozenset()
)

GITHUB_API = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

if not GITHUB_TOKEN:
    logger.warning("GITHUB_TOKEN not set — all requests will fail with 401")
if ALLOW_REPOS:
    logger.info("Repo allow-list: %s", sorted(ALLOW_REPOS))
else:
    logger.warning("GITHUB_ALLOW_REPOS not set — write operations will be rejected")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    return {**_HEADERS, "Authorization": f"Bearer {GITHUB_TOKEN}"}


def _check_repo(owner: str, repo: str) -> None:
    """Raise 403 if the repo is not in the allow-list (when a list is configured)."""
    if ALLOW_REPOS and f"{owner}/{repo}" not in ALLOW_REPOS:
        raise HTTPException(403, f"Repo {owner}/{repo} is not in the configured allow-list")


def _check_write_allowed(owner: str, repo: str) -> None:
    """Write operations require an explicit allow-list entry."""
    if not ALLOW_REPOS:
        raise HTTPException(
            403,
            "Write operations require GITHUB_ALLOW_REPOS to be configured. "
            "Set it to a comma-separated list of owner/repo pairs."
        )
    _check_repo(owner, repo)


async def _gh(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated GitHub API call. Raises HTTPException on error."""
    url = f"{GITHUB_API}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=_auth_headers(), **kwargs)
        if resp.status_code == 404:
            raise HTTPException(404, f"GitHub resource not found: {path}")
        if resp.status_code == 403:
            raise HTTPException(403, "GitHub API permission denied")
        if resp.status_code == 422:
            detail = resp.json().get("message", "Validation failed")
            raise HTTPException(422, f"GitHub validation error: {detail}")
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "GitHub API timed out")
    except Exception as exc:
        logger.exception("GitHub API error")
        raise HTTPException(502, f"GitHub API error: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "allow_repos": sorted(ALLOW_REPOS) if ALLOW_REPOS else "unrestricted (reads only)",
        "token_configured": bool(GITHUB_TOKEN),
    }


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

@app.get("/repos/{owner}/{repo}/contents/{path:path}")
async def get_contents(owner: str, repo: str, path: str, ref: Optional[str] = Query(None)):
    _check_repo(owner, repo)
    params = {"ref": ref} if ref else {}
    return await _gh("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)


@app.get("/repos/{owner}/{repo}/git/trees/{tree_sha}")
async def get_tree(owner: str, repo: str, tree_sha: str, recursive: Optional[str] = Query(None)):
    _check_repo(owner, repo)
    params = {"recursive": recursive} if recursive else {}
    return await _gh("GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params)


@app.get("/repos/{owner}/{repo}/issues")
async def list_issues(
    owner: str, repo: str,
    state: str = Query("open"),
    labels: Optional[str] = Query(None),
    per_page: int = Query(30),
):
    _check_repo(owner, repo)
    params = {"state": state, "per_page": min(per_page, 100)}
    if labels:
        params["labels"] = labels
    return await _gh("GET", f"/repos/{owner}/{repo}/issues", params=params)


@app.get("/repos/{owner}/{repo}/pulls")
async def list_pulls(
    owner: str, repo: str,
    state: str = Query("open"),
    per_page: int = Query(30),
):
    _check_repo(owner, repo)
    params = {"state": state, "per_page": min(per_page, 100)}
    return await _gh("GET", f"/repos/{owner}/{repo}/pulls", params=params)


@app.get("/search/code")
async def search_code(q: str = Query(...), per_page: int = Query(10)):
    # Scope query to allowed repos when allow-list is set
    query = q
    if ALLOW_REPOS:
        repo_terms = " ".join(f"repo:{r}" for r in sorted(ALLOW_REPOS))
        query = f"{q} {repo_terms}"
    params = {"q": query, "per_page": min(per_page, 30)}
    return await _gh("GET", "/search/code", params=params)


# ---------------------------------------------------------------------------
# Write endpoints — all require allow-list
# ---------------------------------------------------------------------------

class FileWriteRequest(BaseModel):
    message: str           # commit message
    content: str           # plain text — gateway base64-encodes it
    sha: Optional[str] = None   # required when updating existing file
    branch: Optional[str] = None


@app.put("/repos/{owner}/{repo}/contents/{path:path}")
async def write_file(owner: str, repo: str, path: str, body: FileWriteRequest):
    _check_write_allowed(owner, repo)
    payload: dict = {
        "message": body.message,
        "content": base64.b64encode(body.content.encode()).decode(),
    }
    if body.sha:
        payload["sha"] = body.sha
    if body.branch:
        payload["branch"] = body.branch
    return await _gh("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=payload)


class PullRequestRequest(BaseModel):
    title: str
    body: str = ""
    head: str       # source branch
    base: str       # target branch (e.g. "main")


@app.post("/repos/{owner}/{repo}/pulls")
async def create_pull_request(owner: str, repo: str, body: PullRequestRequest):
    _check_write_allowed(owner, repo)
    return await _gh("POST", f"/repos/{owner}/{repo}/pulls", json=body.model_dump())


class IssueRequest(BaseModel):
    title: str
    body: str = ""
    labels: list[str] = []


@app.post("/repos/{owner}/{repo}/issues")
async def create_issue(owner: str, repo: str, body: IssueRequest):
    _check_write_allowed(owner, repo)
    return await _gh("POST", f"/repos/{owner}/{repo}/issues", json=body.model_dump())


class MergeRequest(BaseModel):
    commit_title: Optional[str] = None
    merge_method: str = "merge"   # merge | squash | rebase


@app.put("/repos/{owner}/{repo}/pulls/{pull_number}/merge")
async def merge_pull_request(owner: str, repo: str, pull_number: int, body: MergeRequest):
    _check_write_allowed(owner, repo)
    payload = {"merge_method": body.merge_method}
    if body.commit_title:
        payload["commit_title"] = body.commit_title
    return await _gh("PUT", f"/repos/{owner}/{repo}/pulls/{pull_number}/merge", json=payload)


@app.delete("/repos/{owner}/{repo}/git/refs/heads/{branch}")
async def delete_branch(owner: str, repo: str, branch: str):
    _check_write_allowed(owner, repo)
    return await _gh("DELETE", f"/repos/{owner}/{repo}/git/refs/heads/{branch}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9002, log_level="info")
