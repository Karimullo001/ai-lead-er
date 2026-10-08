from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional
import httpx

from .base import BasePlugin, PluginAction, PluginActionResult, PluginScope

log = logging.getLogger("agentos.plugins.google")


class GoogleWorkspacePlugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="google_workspace",
            description="Google Workspace integration: Google Drive, Docs, Sheets, and Calendar management.",
            scopes=[
                PluginScope.READ,
                PluginScope.CREATE,
                PluginScope.EDIT,
                PluginScope.DELETE,
            ],
        )

    def _register_actions(self) -> None:
        self.actions["list_drive_files"] = PluginAction(
            name="list_drive_files",
            description="List recent files in Google Drive.",
            scope=PluginScope.READ,
        )
        self.actions["create_doc"] = PluginAction(
            name="create_doc",
            description="Create a new Google Doc with title and content.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "title": {"type": "string", "required": True},
                "content": {"type": "string", "required": False},
            },
        )
        self.actions["create_calendar_event"] = PluginAction(
            name="create_calendar_event",
            description="Add an event or meeting to Google Calendar.",
            scope=PluginScope.CREATE,
            parameters_schema={
                "summary": {"type": "string", "required": True},
                "start_time": {"type": "string", "description": "ISO 8601 string e.g. 2026-10-09T10:00:00Z", "required": True},
                "end_time": {"type": "string", "required": True},
                "description": {"type": "string", "required": False},
            },
        )
        self.actions["list_calendar_events"] = PluginAction(
            name="list_calendar_events",
            description="List upcoming calendar events.",
            scope=PluginScope.READ,
        )

    def _get_token(self) -> Optional[str]:
        return os.getenv("GOOGLE_ACCESS_TOKEN") or os.getenv("GOOGLE_WORKSPACE_TOKEN")

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
                error="Google Workspace access token is not configured. Provide GOOGLE_ACCESS_TOKEN or GOOGLE_WORKSPACE_TOKEN.",
            )

        if action_name not in self.actions:
            return PluginActionResult(success=False, error=f"Unknown action: {action_name}")

        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30.0) as client:
            try:
                if action_name == "list_drive_files":
                    r = await client.get("https://www.googleapis.com/drive/v3/files?pageSize=10&fields=files(id,name,mimeType,webViewLink)")
                    if r.status_code == 200:
                        return PluginActionResult(success=True, data=r.json().get("files", []))
                    return PluginActionResult(success=False, error=f"Google Drive API error {r.status_code}: {r.text[:200]}")

                elif action_name == "create_doc":
                    title = params.get("title", "Untitled Document")
                    r = await client.post("https://docs.googleapis.com/v1/documents", json={"title": title})
                    if r.status_code == 200:
                        doc = r.json()
                        doc_id = doc.get("documentId")
                        link = f"https://docs.google.com/document/d/{doc_id}/edit"
                        return PluginActionResult(success=True, data={"document_id": doc_id, "link": link, "title": title})
                    return PluginActionResult(success=False, error=f"Google Docs API error {r.status_code}: {r.text[:200]}")

                elif action_name == "create_calendar_event":
                    payload = {
                        "summary": params["summary"],
                        "description": params.get("description", "Created by AgentOS"),
                        "start": {"dateTime": params["start_time"]},
                        "end": {"dateTime": params["end_time"]},
                    }
                    r = await client.post("https://www.googleapis.com/calendar/v3/calendars/primary/events", json=payload)
                    if r.status_code in (200, 201):
                        ev = r.json()
                        return PluginActionResult(success=True, data={"event_id": ev.get("id"), "htmlLink": ev.get("htmlLink")})
                    return PluginActionResult(success=False, error=f"Calendar API error {r.status_code}: {r.text[:200]}")

                elif action_name == "list_calendar_events":
                    r = await client.get("https://www.googleapis.com/calendar/v3/calendars/primary/events?maxResults=10&orderBy=startTime&singleEvents=true")
                    if r.status_code == 200:
                        items = [{"summary": it.get("summary"), "start": it.get("start"), "link": it.get("htmlLink")} for it in r.json().get("items", [])]
                        return PluginActionResult(success=True, data=items)
                    return PluginActionResult(success=False, error=f"Calendar error {r.status_code}: {r.text[:200]}")

            except Exception as e:
                log.exception("Google action failed: %s", e)
                return PluginActionResult(success=False, error=f"Google API exception: {str(e)}")

        return PluginActionResult(success=False, error="Unhandled action")
