from __future__ import annotations
import base64
import logging
import os
from typing import Any, Dict, List, Optional
import httpx

from .base import BasePlugin, PluginAction, PluginActionResult, PluginScope

log = logging.getLogger("agentos.plugins.github")


class GitHubPlugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="github",
            description="GitHub integration: manage repositories, push project files, create issues and pull requests.",
            scopes=[
                PluginScope.READ,
                PluginScope.CREATE,
                PluginScope.EDIT,
                PluginScope.DELETE,
                PluginScope.PUBLISH,
            ],
        )

    def _register_actions(self) -> None:
        self.actions["list_repos"] = PluginAction(
            name="list_repos",
            description="List user repositories on GitHub.",
            scope=PluginScope.READ,
        )
        self.actions["create_repo"] = PluginAction(
            name="create_repo",
            description="Create a new GitHub repository for the user.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "name": {"type": "string", "required": True},
                "description": {"type": "string", "required": False},
                "private": {"type": "boolean", "default": False},
            },
        )
        self.actions["push_file"] = PluginAction(
            name="push_file",
            description="Create or update a file in a GitHub repository.",
            scope=PluginScope.EDIT,
            parameters_schema={
                "repo": {"type": "string", "required": True},
                "path": {"type": "string", "required": True},
                "content": {"type": "string", "required": True},
                "message": {"type": "string", "default": "Update from AgentOS"},
                "branch": {"type": "string", "default": "main"},
            },
        )
        self.actions["push_project"] = PluginAction(
            name="push_project",
            description="Push multiple files to a repository in a commit.",
            scope=PluginScope.PUBLISH,
            is_high_risk=True,
            parameters_schema={
                "repo": {"type": "string", "required": True},
                "files": {"type": "object", "description": "dict of filepath -> content string", "required": True},
                "message": {"type": "string", "default": "Initial commit from AgentOS"},
                "branch": {"type": "string", "default": "main"},
            },
        )
        self.actions["get_file"] = PluginAction(
            name="get_file",
            description="Read file content from a repository.",
            scope=PluginScope.READ,
            parameters_schema={
                "repo": {"type": "string", "required": True},
                "path": {"type": "string", "required": True},
                "branch": {"type": "string", "default": "main"},
            },
        )
        self.actions["create_issue"] = PluginAction(
            name="create_issue",
            description="Create an issue in a repository.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "repo": {"type": "string", "required": True},
                "title": {"type": "string", "required": True},
                "body": {"type": "string", "required": False},
            },
        )

    def _get_token(self) -> Optional[str]:
        return os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT")

    def is_configured(self) -> bool:
        return bool(self._get_token())

    def _get_headers(self) -> Dict[str, str]:
        token = self._get_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AgentOS-Personal-AI",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def execute(self, action_name: str, params: Dict[str, Any], user_id: str) -> PluginActionResult:
        token = self._get_token()
        if not token:
            return PluginActionResult(
                success=False,
                error="GitHub token is not configured. Set GITHUB_TOKEN or GITHUB_PAT in environment.",
            )

        if action_name not in self.actions:
            return PluginActionResult(success=False, error=f"Unknown action: {action_name}")

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30.0) as client:
            try:
                if action_name == "list_repos":
                    r = await client.get("https://api.github.com/user/repos?sort=updated&per_page=10")
                    if r.status_code == 200:
                        repos = [{"name": it["full_name"], "url": it["html_url"], "private": it["private"]} for it in r.json()]
                        return PluginActionResult(success=True, data=repos)
                    return PluginActionResult(success=False, error=f"GitHub error {r.status_code}: {r.text[:200]}")

                elif action_name == "create_repo":
                    repo_name = params.get("name", "").strip()
                    if not repo_name:
                        return PluginActionResult(success=False, error="Repository name is required.")
                    payload = {
                        "name": repo_name,
                        "description": params.get("description", "Created by AgentOS personal AI"),
                        "private": params.get("private", False),
                        "auto_init": True,
                    }
                    r = await client.post("https://api.github.com/user/repos", json=payload)
                    if r.status_code in (200, 201):
                        data = r.json()
                        return PluginActionResult(success=True, data={"html_url": data["html_url"], "clone_url": data["clone_url"], "full_name": data["full_name"]})
                    return PluginActionResult(success=False, error=f"GitHub error {r.status_code}: {r.text[:200]}")

                elif action_name == "push_file":
                    repo = params["repo"]
                    path = params["path"].lstrip("/")
                    content = params["content"]
                    message = params.get("message", "Update from AgentOS")
                    branch = params.get("branch", "main")

                    # Check if file already exists to get sha
                    get_r = await client.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}")
                    sha = get_r.json().get("sha") if get_r.status_code == 200 else None

                    b64_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
                    put_payload: Dict[str, Any] = {
                        "message": message,
                        "content": b64_content,
                        "branch": branch,
                    }
                    if sha:
                        put_payload["sha"] = sha

                    put_r = await client.put(f"https://api.github.com/repos/{repo}/contents/{path}", json=put_payload)
                    if put_r.status_code in (200, 201):
                        return PluginActionResult(success=True, data={"path": path, "commit": put_r.json().get("commit", {}).get("sha")})
                    return PluginActionResult(success=False, error=f"GitHub push failed {put_r.status_code}: {put_r.text[:200]}")

                elif action_name == "push_project":
                    repo = params["repo"]
                    files: Dict[str, str] = params.get("files", {})
                    message = params.get("message", "Push project from AgentOS")
                    branch = params.get("branch", "main")

                    # Push files iteratively
                    results = []
                    for fpath, fcontent in files.items():
                        res = await self.execute("push_file", {
                            "repo": repo,
                            "path": fpath,
                            "content": fcontent,
                            "message": f"{message}: {fpath}",
                            "branch": branch,
                        }, user_id)
                        if not res.success:
                            return PluginActionResult(success=False, error=f"Failed to push {fpath}: {res.error}")
                        results.append(fpath)
                    return PluginActionResult(success=True, data={"pushed_files": results, "count": len(results)})

                elif action_name == "get_file":
                    repo = params["repo"]
                    path = params["path"].lstrip("/")
                    branch = params.get("branch", "main")
                    r = await client.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}")
                    if r.status_code == 200:
                        b64 = r.json().get("content", "")
                        raw = base64.b64decode(b64).decode("utf-8", errors="replace")
                        return PluginActionResult(success=True, data={"content": raw, "size": r.json().get("size")})
                    return PluginActionResult(success=False, error=f"File not found or error {r.status_code}: {r.text[:200]}")

                elif action_name == "create_issue":
                    repo = params["repo"]
                    payload = {
                        "title": params["title"],
                        "body": params.get("body", "Reported by AgentOS"),
                    }
                    r = await client.post(f"https://api.github.com/repos/{repo}/issues", json=payload)
                    if r.status_code in (200, 201):
                        return PluginActionResult(success=True, data={"html_url": r.json()["html_url"], "number": r.json()["number"]})
                    return PluginActionResult(success=False, error=f"Issue creation error {r.status_code}: {r.text[:200]}")

            except Exception as e:
                log.exception("GitHub action failed: %s", e)
                return PluginActionResult(success=False, error=f"GitHub exception: {str(e)}")

        return PluginActionResult(success=False, error="Unhandled action")
