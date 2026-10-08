from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger("agentos.plugins.base")


class PluginScope(str, Enum):
    READ = "read"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    PUBLISH = "publish"
    EXECUTE = "execute"
    MANAGE_ACCOUNTS = "manage_accounts"
    ACCESS_FILES = "access_files"
    ACCESS_DEVICES = "access_devices"


# Actions requiring explicit confirmation before execution if not pre-approved
HIGH_RISK_SCOPES = {
    PluginScope.DELETE,
    PluginScope.PUBLISH,
    PluginScope.EXECUTE,
    PluginScope.MANAGE_ACCOUNTS,
}


@dataclass
class PluginAction:
    name: str
    description: str
    scope: PluginScope
    parameters_schema: Dict[str, Any] = field(default_factory=dict)
    is_high_risk: bool = False


@dataclass
class PluginActionResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    needs_confirmation: bool = False
    confirmation_prompt: Optional[str] = None


class BasePlugin(ABC):
    """Abstract base class for all AgentOS connectors and plugins."""

    def __init__(self, name: str, description: str, scopes: List[PluginScope]):
        self.name = name
        self.description = description
        self.scopes = set(scopes)
        self.actions: Dict[str, PluginAction] = {}
        self._register_actions()

    @abstractmethod
    def _register_actions(self) -> None:
        """Register supported actions and their schemas."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials or required config are set."""
        pass

    @abstractmethod
    async def execute(self, action_name: str, params: Dict[str, Any], user_id: str) -> PluginActionResult:
        """Execute an action provided all scopes and configs are valid."""
        pass

    def get_capabilities_summary(self) -> str:
        status = "🟢 Configured" if self.is_configured() else "⚪ Not Configured"
        lines = [f"**{self.name}** ({status}): {self.description}"]
        for act in self.actions.values():
            risk = "⚠️ [High Risk]" if (act.is_high_risk or act.scope in HIGH_RISK_SCOPES) else ""
            lines.append(f"  • `{act.name}` ({act.scope.value}) {risk} - {act.description}")
        return "\n".join(lines)
