from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional
import httpx

from .base import BasePlugin, PluginAction, PluginActionResult, PluginScope

log = logging.getLogger("agentos.plugins.figma")


class FigmaPlugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="figma",
            description="Figma design integration: inspect design files, nodes, design tokens, and comments.",
            scopes=[PluginScope.READ, PluginScope.CREATE, PluginScope.EDIT],
        )

    def _register_actions(self) -> None:
        self.actions["get_file"] = PluginAction(
            name="get_file",
            description="Get metadata and structure of a Figma design file.",
            scope=PluginScope.READ,
            parameters_schema={"file_key": {"type": "string", "required": True}},
        )
        self.actions["get_comments"] = PluginAction(
            name="get_comments",
            description="Get comments on a Figma design file.",
            scope=PluginScope.READ,
            parameters_schema={"file_key": {"type": "string", "required": True}},
        )
        self.actions["post_comment"] = PluginAction(
            name="post_comment",
            description="Post a design feedback comment on a Figma file.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "file_key": {"type": "string", "required": True},
                "message": {"type": "string", "required": True},
            },
        )

    def _get_token(self) -> Optional[str]:
        return os.getenv("FIGMA_ACCESS_TOKEN") or os.getenv("FIGMA_TOKEN")

    def is_configured(self) -> bool:
        return bool(self._get_token())

    def _get_headers(self) -> Dict[str, str]:
        token = self._get_token()
        return {"X-Figma-Token": token or "", "Content-Type": "application/json"}

    async def execute(self, action_name: str, params: Dict[str, Any], user_id: str) -> PluginActionResult:
        token = self._get_token()
        if not token:
            return PluginActionResult(
                success=False,
                error="Figma token is not configured. Set FIGMA_ACCESS_TOKEN in environment.",
            )

        file_key = params.get("file_key", "").strip()
        if not file_key:
            return PluginActionResult(success=False, error="Figma file_key is required.")

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30.0) as client:
            try:
                if action_name == "get_file":
                    r = await client.get(f"https://api.figma.com/v1/files/{file_key}")
                    if r.status_code == 200:
                        data = r.json()
                        return PluginActionResult(success=True, data={"name": data.get("name"), "lastModified": data.get("lastModified"), "components": len(data.get("components", {}))})
                    return PluginActionResult(success=False, error=f"Figma error {r.status_code}: {r.text[:200]}")

                elif action_name == "get_comments":
                    r = await client.get(f"https://api.figma.com/v1/files/{file_key}/comments")
                    if r.status_code == 200:
                        return PluginActionResult(success=True, data=r.json().get("comments", []))
                    return PluginActionResult(success=False, error=f"Figma error {r.status_code}: {r.text[:200]}")

                elif action_name == "post_comment":
                    msg = params.get("message", "Design review by AgentOS")
                    r = await client.post(f"https://api.figma.com/v1/files/{file_key}/comments", json={"message": msg})
                    if r.status_code in (200, 201):
                        return PluginActionResult(success=True, data=r.json())
                    return PluginActionResult(success=False, error=f"Figma error {r.status_code}: {r.text[:200]}")

            except Exception as e:
                log.exception("Figma action failed: %s", e)
                return PluginActionResult(success=False, error=f"Figma exception: {str(e)}")

        return PluginActionResult(success=False, error="Unhandled action")
