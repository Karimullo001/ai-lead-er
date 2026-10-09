from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional
import httpx

from .base import BasePlugin, PluginAction, PluginActionResult, PluginScope

log = logging.getLogger("agentos.plugins.canva")


class CanvaPlugin(BasePlugin):
    """Canva Connector: design generation, templates, and asset creation."""

    def __init__(self):
        super().__init__(
            name="canva",
            description="Canva integration: create presentations, social graphics, website UI templates, and export designs.",
            scopes=[PluginScope.READ, PluginScope.CREATE, PluginScope.EDIT, PluginScope.PUBLISH],
        )

    def _register_actions(self) -> None:
        self.actions["create_design"] = PluginAction(
            name="create_design",
            description="Create a new design in Canva (presentation, website, poster, social_media).",
            scope=PluginScope.CREATE,
            parameters_schema={
                "design_type": {"type": "string", "default": "website", "enum": ["website", "presentation", "social_media", "logo"]},
                "title": {"type": "string", "required": True},
            },
        )
        self.actions["get_design"] = PluginAction(
            name="get_design",
            description="Get metadata and export link of a Canva design.",
            scope=PluginScope.READ,
            parameters_schema={"design_id": {"type": "string", "required": True}},
        )
        self.actions["export_design"] = PluginAction(
            name="export_design",
            description="Export Canva design as PNG, PDF, or HTML template.",
            scope=PluginScope.READ,
            parameters_schema={
                "design_id": {"type": "string", "required": True},
                "format": {"type": "string", "default": "png", "enum": ["png", "pdf", "jpg"]},
            },
        )

    def _get_token(self) -> Optional[str]:
        return os.getenv("CANVA_API_KEY") or os.getenv("CANVA_ACCESS_TOKEN") or os.getenv("CANVA_TOKEN")

    def is_configured(self) -> bool:
        return bool(self._get_token())

    def _get_headers(self) -> Dict[str, str]:
        token = self._get_token()
        return {
            "Authorization": f"Bearer {token}" if token else "",
            "Content-Type": "application/json",
        }

    async def execute(self, action_name: str, params: Dict[str, Any], user_id: str) -> PluginActionResult:
        token = self._get_token()
        if not token:
            return PluginActionResult(
                success=False,
                error="Canva API key/token is not configured. Set CANVA_API_KEY or CANVA_ACCESS_TOKEN in environment.",
            )

        if action_name not in self.actions:
            return PluginActionResult(success=False, error=f"Unknown action: {action_name}")

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30.0) as client:
            try:
                if action_name == "create_design":
                    design_type = params.get("design_type", "website")
                    title = params.get("title", "AgentOS Generated Design")
                    payload = {
                        "design_type": {"type": design_type},
                        "title": title,
                    }
                    r = await client.post("https://api.canva.com/rest/v1/designs", json=payload)
                    if r.status_code in (200, 201):
                        data = r.json().get("design", {})
                        return PluginActionResult(success=True, data={"design_id": data.get("id"), "url": data.get("urls", {}).get("edit_url"), "title": title})
                    return PluginActionResult(success=False, error=f"Canva API error {r.status_code}: {r.text[:200]}")

                elif action_name == "get_design":
                    design_id = params["design_id"]
                    r = await client.get(f"https://api.canva.com/rest/v1/designs/{design_id}")
                    if r.status_code == 200:
                        return PluginActionResult(success=True, data=r.json())
                    return PluginActionResult(success=False, error=f"Canva error {r.status_code}: {r.text[:200]}")

                elif action_name == "export_design":
                    design_id = params["design_id"]
                    fmt = params.get("format", "png")
                    r = await client.post(f"https://api.canva.com/rest/v1/exports", json={"design_id": design_id, "format": {"type": fmt}})
                    if r.status_code in (200, 201):
                        return PluginActionResult(success=True, data=r.json())
                    return PluginActionResult(success=False, error=f"Canva export error {r.status_code}: {r.text[:200]}")

            except Exception as e:
                log.exception("Canva action failed: %s", e)
                return PluginActionResult(success=False, error=f"Canva exception: {str(e)}")

        return PluginActionResult(success=False, error="Unhandled action")
