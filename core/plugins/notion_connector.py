from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional
import httpx

from .base import BasePlugin, PluginAction, PluginActionResult, PluginScope

log = logging.getLogger("agentos.plugins.notion")


class NotionPlugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="notion",
            description="Notion workspace integration: search pages, create notes, append tasks and database items.",
            scopes=[PluginScope.READ, PluginScope.CREATE, PluginScope.EDIT],
        )

    def _register_actions(self) -> None:
        self.actions["search"] = PluginAction(
            name="search",
            description="Search Notion pages and databases.",
            scope=PluginScope.READ,
            parameters_schema={"query": {"type": "string", "required": False}},
        )
        self.actions["create_page"] = PluginAction(
            name="create_page",
            description="Create a new Notion page under a parent page or database.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "parent_id": {"type": "string", "required": True},
                "title": {"type": "string", "required": True},
                "content": {"type": "string", "required": False},
            },
        )

    def _get_token(self) -> Optional[str]:
        return os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")

    def is_configured(self) -> bool:
        return bool(self._get_token())

    def _get_headers(self) -> Dict[str, str]:
        token = self._get_token()
        return {
            "Authorization": f"Bearer {token}" if token else "",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def execute(self, action_name: str, params: Dict[str, Any], user_id: str) -> PluginActionResult:
        token = self._get_token()
        if not token:
            return PluginActionResult(
                success=False,
                error="Notion API key is not configured. Set NOTION_API_KEY in environment.",
            )

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30.0) as client:
            try:
                if action_name == "search":
                    query = params.get("query", "")
                    r = await client.post("https://api.notion.com/v1/search", json={"query": query, "page_size": 10})
                    if r.status_code == 200:
                        results = []
                        for it in r.json().get("results", []):
                            title = "Untitled"
                            props = it.get("properties", {})
                            if "title" in props and props["title"].get("title"):
                                title = props["title"]["title"][0].get("plain_text", title)
                            results.append({"id": it["id"], "type": it["object"], "title": title, "url": it.get("url")})
                        return PluginActionResult(success=True, data=results)
                    return PluginActionResult(success=False, error=f"Notion error {r.status_code}: {r.text[:200]}")

                elif action_name == "create_page":
                    parent_id = params["parent_id"]
                    title = params.get("title", "New Note")
                    content = params.get("content", "")
                    payload = {
                        "parent": {"page_id": parent_id},
                        "properties": {
                            "title": [{"type": "text", "text": {"content": title}}]
                        },
                        "children": [
                            {
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                                },
                            }
                        ] if content else [],
                    }
                    r = await client.post("https://api.notion.com/v1/pages", json=payload)
                    if r.status_code in (200, 201):
                        data = r.json()
                        return PluginActionResult(success=True, data={"id": data.get("id"), "url": data.get("url")})
                    return PluginActionResult(success=False, error=f"Notion error {r.status_code}: {r.text[:200]}")

            except Exception as e:
                log.exception("Notion action failed: %s", e)
                return PluginActionResult(success=False, error=f"Notion exception: {str(e)}")

        return PluginActionResult(success=False, error="Unhandled action")
