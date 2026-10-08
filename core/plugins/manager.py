from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from .base import BasePlugin, PluginActionResult, PluginScope, HIGH_RISK_SCOPES
from .github_connector import GitHubPlugin
from .google_connector import GoogleWorkspacePlugin
from .figma_connector import FigmaPlugin
from .notion_connector import NotionPlugin

log = logging.getLogger("agentos.plugins.manager")


class PluginManager:
    """Central registry and execution manager for external integrations and plugins."""

    def __init__(self):
        self._plugins: Dict[str, BasePlugin] = {}
        self._register_default_plugins()

    def _register_default_plugins(self) -> None:
        self.register(GitHubPlugin())
        self.register(GoogleWorkspacePlugin())
        self.register(FigmaPlugin())
        self.register(NotionPlugin())

    def register(self, plugin: BasePlugin) -> None:
        self._plugins[plugin.name] = plugin
        log.info("Registered plugin '%s' (configured=%s)", plugin.name, plugin.is_configured())

    def get_plugin(self, name: str) -> Optional[BasePlugin]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "configured": p.is_configured(),
                "actions": list(p.actions.keys()),
                "scopes": [s.value for s in p.scopes],
            }
            for p in self._plugins.values()
        ]

    def get_overview_markdown(self) -> str:
        lines = ["🔌 *Integratsiyalar va Plaginlar holati:*\n"]
        for p in self._plugins.values():
            status = "🟢 Uланган" if p.is_configured() else "⚪ Ulanmagan (API key kiritilmagan)"
            lines.append(f"• *{p.name.capitalize()}* ({status})")
            lines.append(f"  _{p.description}_")
            acts = ", ".join(f"`{a}`" for a in p.actions.keys())
            lines.append(f"  Imkoniyatlar: {acts}\n")
        lines.append("_Yangi servisni ulash uchun server muhitida (Environment variables) tegishli tokenni kiriting._")
        return "\n".join(lines)

    async def execute_action(
        self,
        plugin_name: str,
        action_name: str,
        params: Dict[str, Any],
        user_id: str,
        confirmed: bool = False,
    ) -> PluginActionResult:
        plugin = self.get_plugin(plugin_name)
        if not plugin:
            return PluginActionResult(success=False, error=f"Plugin '{plugin_name}' not found.")

        if not plugin.is_configured():
            return PluginActionResult(
                success=False,
                error=f"Plugin '{plugin_name}' is not configured with valid credentials in the environment.",
            )

        action = plugin.actions.get(action_name)
        if not action:
            return PluginActionResult(
                success=False,
                error=f"Action '{action_name}' not found on plugin '{plugin_name}'.",
            )

        # High-risk security check: Require explicit confirmation if not yet confirmed
        if (action.is_high_risk or action.scope in HIGH_RISK_SCOPES) and not confirmed:
            prompt = (
                f"⚠️ *Xavfsizlik tasdig'i talab qilinadi:*\n"
                f"Servis: `{plugin_name}`\n"
                f"Amal: `{action_name}` ({action.scope.value})\n"
                f"Tavsif: {action.description}\n\n"
                f"Ushbu amalni bajarishga ruxsat berasizmi?"
            )
            return PluginActionResult(
                success=False,
                needs_confirmation=True,
                confirmation_prompt=prompt,
                data={"plugin": plugin_name, "action": action_name, "params": params},
            )

        # Execute safe action
        return await plugin.execute(action_name, params, user_id)


_MANAGER_INSTANCE: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    global _MANAGER_INSTANCE
    if _MANAGER_INSTANCE is None:
        _MANAGER_INSTANCE = PluginManager()
    return _MANAGER_INSTANCE
