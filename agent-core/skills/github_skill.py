"""
GitHub skill — interact with GitHub repositories via the github-gateway container.

The gateway holds the GITHUB_TOKEN; this skill never sees the raw credential.
All write operations require owner approval. Read operations are also approval-gated
since they can expose repository contents.

Private channels only.
"""

import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata, PRIVATE_CHANNELS

GITHUB_GW_URL = os.getenv("GITHUB_GW_URL", "http://github-gateway:9002")
GITHUB_ALLOW_REPOS = os.getenv("GITHUB_ALLOW_REPOS", "")

_READ_ACTIONS = frozenset({
    "get_file", "list_directory", "list_issues", "list_prs", "search_code", "get_tree",
})
_WRITE_ACTIONS = frozenset({
    "create_file", "update_file", "create_pr", "merge_pr", "delete_branch", "create_issue",
})
_ALL_ACTIONS = _READ_ACTIONS | _WRITE_ACTIONS

# Scrub suspicious prompt-injection patterns from file content returned to the LLM
_INJECTION_PATTERN = re.compile(
    r"(ignore (previous|prior|above|all) instructions?|"
    r"you are now|new (persona|role|instructions?)|"
    r"<\s*(system|assistant|user)\s*>|"
    r"\[INST\]|\[SYS\])",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    return _INJECTION_PATTERN.sub("[REDACTED]", text)


class GitHubSkill(SkillBase):
    """Read from and write to GitHub repositories via the isolated github-gateway."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="github",
            description=(
                "Interact with GitHub repositories. "
                "Read actions: get_file, list_directory, list_issues, list_prs, search_code, get_tree. "
                "Write actions (approval required): create_file, update_file, create_pr, merge_pr, "
                "delete_branch, create_issue. "
                "Always specify owner (GitHub user/org) and repo. "
                "Owner approval required before execution."
            ),
            risk_level=RiskLevel.HIGH,
            rate_limit="github",
            requires_approval=True,
            max_calls_per_turn=5,
            private_channels=PRIVATE_CHANNELS,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(_ALL_ACTIONS),
                        "description": "The GitHub operation to perform.",
                    },
                    "owner": {
                        "type": "string",
                        "description": "GitHub org or username (e.g. 'octocat').",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name (e.g. 'hello-world').",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path within the repo.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search_code.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message (required for create_file, update_file).",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content as plain text (for create_file, update_file).",
                    },
                    "sha": {
                        "type": "string",
                        "description": "Blob SHA of the existing file (required for update_file).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for create_pr or create_issue.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Description body for create_pr or create_issue.",
                    },
                    "head": {
                        "type": "string",
                        "description": "Source branch name for create_pr.",
                    },
                    "base": {
                        "type": "string",
                        "description": "Target branch for create_pr (e.g. 'main').",
                    },
                    "pull_number": {
                        "type": "integer",
                        "description": "PR number for merge_pr.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name for delete_branch or file write target.",
                    },
                    "state": {
                        "type": "string",
                        "description": "Issue/PR state filter: 'open', 'closed', or 'all'.",
                    },
                    "merge_method": {
                        "type": "string",
                        "description": "Merge strategy for merge_pr: 'merge', 'squash', or 'rebase'.",
                    },
                },
                "required": ["action", "owner", "repo"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if action not in _ALL_ACTIONS:
            return False, f"Unknown action '{action}'. Valid: {', '.join(sorted(_ALL_ACTIONS))}"

        owner = params.get("owner", "").strip()
        repo = params.get("repo", "").strip()
        if not owner or not repo:
            return False, "owner and repo are required"
        if "/" in owner or ".." in owner or "/" in repo or ".." in repo:
            return False, "owner and repo must not contain / or .."

        # Skill-level allow-list pre-check (gateway enforces it too — defense in depth)
        if GITHUB_ALLOW_REPOS:
            allowed = frozenset(r.strip() for r in GITHUB_ALLOW_REPOS.split(",") if r.strip())
            if allowed and f"{owner}/{repo}" not in allowed:
                return False, f"{owner}/{repo} is not in the configured allow-list"

        # Write-action field validation
        if action in ("create_file", "update_file"):
            if not params.get("path", "").strip():
                return False, "path is required for create_file/update_file"
            if not params.get("message", "").strip():
                return False, "message (commit message) is required for create_file/update_file"
            if not params.get("content", "").strip():
                return False, "content is required for create_file/update_file"
            if action == "update_file" and not params.get("sha", "").strip():
                return False, "sha (blob SHA of existing file) is required for update_file"
            if len(params.get("content", "")) > 100_000:
                return False, "content exceeds 100,000 character limit"

        if action == "create_pr":
            for f in ("title", "head", "base"):
                if not params.get(f, "").strip():
                    return False, f"'{f}' is required for create_pr"

        if action == "create_issue":
            if not params.get("title", "").strip():
                return False, "title is required for create_issue"

        if action == "merge_pr" and not params.get("pull_number"):
            return False, "pull_number is required for merge_pr"

        if action == "delete_branch" and not params.get("branch", "").strip():
            return False, "branch is required for delete_branch"

        if action == "search_code" and not params.get("query", "").strip():
            return False, "query is required for search_code"

        return True, ""

    async def pre_approval_description(self, params: Dict[str, Any]) -> Optional[str]:
        action = params.get("action", "")
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        is_write = action in _WRITE_ACTIONS

        lines = [f"**GitHub {'WRITE' if is_write else 'Read'} Request**\n"]
        lines.append(f"**Action:** `{action}`")
        lines.append(f"**Repo:** `{owner}/{repo}`")

        if params.get("path"):
            lines.append(f"**Path:** `{params['path']}`")
        if params.get("ref"):
            lines.append(f"**Ref:** `{params['ref']}`")
        if params.get("branch"):
            lines.append(f"**Branch:** `{params['branch']}`")
        if params.get("title"):
            lines.append(f"**Title:** {params['title']}")
        if params.get("message"):
            lines.append(f"**Commit message:** {params['message']}")
        if params.get("head") and params.get("base"):
            lines.append(f"**PR:** `{params['head']}` → `{params['base']}`")
        if params.get("pull_number"):
            lines.append(f"**PR number:** #{params['pull_number']}")
        if params.get("query"):
            lines.append(f"**Search query:** `{params['query']}`")

        if is_write:
            lines.append("\n⚠️ **This is a write operation** — it will modify the repository.")
        else:
            lines.append("\nThis is a read-only operation.")

        return "\n".join(lines)

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)

        action = params["action"]
        owner = params["owner"].strip()
        repo = params["repo"].strip()

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                result = await _dispatch(client, action, owner, repo, params)
            return {"action": action, "owner": owner, "repo": repo, "data": result}
        except httpx.HTTPStatusError as exc:
            return {"error": f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}"}
        except httpx.ConnectError:
            return {"error": "github-gateway service is unreachable"}
        except Exception as exc:
            return {"error": str(exc)}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)[:500]
        if "error" in result:
            return f"[github] Error: {result['error']}"

        action = result.get("action", "")
        owner = result.get("owner", "")
        repo = result.get("repo", "")
        data = result.get("data", {})

        prefix = f"{owner}/{repo}"

        if action == "get_file":
            content_b64 = data.get("content", "")
            try:
                import base64
                raw = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="replace")
                raw = raw[:5000]
                raw = _scrub(raw)
                name = data.get("name", "file")
                sha = data.get("sha", "")[:8]
                return f"File: {prefix}/{data.get('path', '')} (sha: {sha})\n\n{raw}"
            except Exception:
                return f"[github] Could not decode file content"

        if action == "list_directory":
            items = data if isinstance(data, list) else []
            lines = [f"Directory listing: {prefix} ({len(items)} items)"]
            for item in items[:50]:
                t = item.get("type", "?")
                n = item.get("name", "?")
                lines.append(f"  [{t}] {n}")
            if len(items) > 50:
                lines.append(f"  ... and {len(items) - 50} more")
            return "\n".join(lines)

        if action in ("list_issues", "list_prs"):
            items = data if isinstance(data, list) else []
            label = "Issues" if action == "list_issues" else "Pull Requests"
            lines = [f"{label} for {prefix} ({len(items)} items)"]
            for item in items[:20]:
                num = item.get("number", "?")
                title = item.get("title", "?")[:80]
                state = item.get("state", "?")
                lines.append(f"  #{num} [{state}] {title}")
            return "\n".join(lines)

        if action == "search_code":
            items = data.get("items", [])
            total = data.get("total_count", len(items))
            lines = [f"Code search: {total} total results (showing {len(items)})"]
            for item in items[:10]:
                repo_name = item.get("repository", {}).get("full_name", "?")
                path = item.get("path", "?")
                lines.append(f"  {repo_name}/{path}")
            return "\n".join(lines)

        if action == "get_tree":
            tree = data.get("tree", [])
            lines = [f"File tree for {prefix} ({len(tree)} entries)"]
            for entry in tree[:100]:
                lines.append(f"  {entry.get('path', '?')} [{entry.get('type', '?')}]")
            if len(tree) > 100:
                lines.append(f"  ... and {len(tree) - 100} more")
            return "\n".join(lines)

        if action in ("create_file", "update_file"):
            commit = data.get("commit", {})
            sha = commit.get("sha", "?")[:8]
            path = data.get("content", {}).get("path", "?")
            verb = "Created" if action == "create_file" else "Updated"
            return f"{verb} `{path}` in `{prefix}`. Commit: {sha}"

        if action == "create_pr":
            num = data.get("number", "?")
            url = data.get("html_url", "")
            title = data.get("title", "?")
            return f"Created PR #{num}: {title}\n{url}"

        if action == "create_issue":
            num = data.get("number", "?")
            url = data.get("html_url", "")
            title = data.get("title", "?")
            return f"Created issue #{num}: {title}\n{url}"

        if action == "merge_pr":
            sha = data.get("sha", "?")[:8]
            msg = data.get("message", "Merged")
            return f"PR merged. {msg} (sha: {sha})"

        if action == "delete_branch":
            return f"Branch deleted in {prefix}"

        # Fallback
        return f"[github] {action} completed on {prefix}"


# ---------------------------------------------------------------------------
# Dispatch helper — maps action → gateway endpoint
# ---------------------------------------------------------------------------

async def _dispatch(
    client: httpx.AsyncClient,
    action: str,
    owner: str,
    repo: str,
    params: Dict[str, Any],
) -> Any:
    base = GITHUB_GW_URL

    if action == "get_file":
        path = params.get("path", "")
        query = {"ref": params["ref"]} if params.get("ref") else {}
        r = await client.get(f"{base}/repos/{owner}/{repo}/contents/{path}", params=query)
        r.raise_for_status()
        return r.json()

    if action == "list_directory":
        path = params.get("path", "")
        query = {"ref": params["ref"]} if params.get("ref") else {}
        r = await client.get(f"{base}/repos/{owner}/{repo}/contents/{path}", params=query)
        r.raise_for_status()
        return r.json()

    if action == "get_tree":
        tree_sha = params.get("ref", "HEAD")
        r = await client.get(
            f"{base}/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
        r.raise_for_status()
        return r.json()

    if action == "list_issues":
        query = {"state": params.get("state", "open"), "per_page": 30}
        r = await client.get(f"{base}/repos/{owner}/{repo}/issues", params=query)
        r.raise_for_status()
        return r.json()

    if action == "list_prs":
        query = {"state": params.get("state", "open"), "per_page": 30}
        r = await client.get(f"{base}/repos/{owner}/{repo}/pulls", params=query)
        r.raise_for_status()
        return r.json()

    if action == "search_code":
        r = await client.get(f"{base}/search/code", params={"q": params["query"], "per_page": 10})
        r.raise_for_status()
        return r.json()

    if action in ("create_file", "update_file"):
        payload = {
            "message": params["message"],
            "content": params["content"],
            "sha": params.get("sha"),
            "branch": params.get("branch"),
        }
        r = await client.put(
            f"{base}/repos/{owner}/{repo}/contents/{params['path']}",
            json={k: v for k, v in payload.items() if v is not None},
        )
        r.raise_for_status()
        return r.json()

    if action == "create_pr":
        payload = {
            "title": params["title"],
            "body": params.get("body", ""),
            "head": params["head"],
            "base": params["base"],
        }
        r = await client.post(f"{base}/repos/{owner}/{repo}/pulls", json=payload)
        r.raise_for_status()
        return r.json()

    if action == "create_issue":
        payload = {
            "title": params["title"],
            "body": params.get("body", ""),
            "labels": params.get("labels", []),
        }
        r = await client.post(f"{base}/repos/{owner}/{repo}/issues", json=payload)
        r.raise_for_status()
        return r.json()

    if action == "merge_pr":
        payload = {"merge_method": params.get("merge_method", "merge")}
        if params.get("commit_title"):
            payload["commit_title"] = params["commit_title"]
        r = await client.put(
            f"{base}/repos/{owner}/{repo}/pulls/{params['pull_number']}/merge",
            json=payload,
        )
        r.raise_for_status()
        return r.json()

    if action == "delete_branch":
        r = await client.delete(
            f"{base}/repos/{owner}/{repo}/git/refs/heads/{params['branch']}"
        )
        r.raise_for_status()
        return {}

    raise ValueError(f"Unhandled action: {action}")
