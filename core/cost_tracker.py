from __future__ import annotations
import asyncio, time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class CostEntry(BaseModel):
    ts: float = Field(default_factory=time.time)
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    task_id: Optional[str] = None


class CostTracker:
    """In-process cost ledger. Persists via TaskManager if provided."""
    def __init__(self, task_manager: Optional[object] = None):
        self._entries: List[CostEntry] = []
        self._per_task: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.task_manager = task_manager

    async def record(self, entry: CostEntry) -> None:
        async with self._lock:
            self._entries.append(entry)
            if entry.task_id:
                self._per_task[entry.task_id] = self._per_task.get(entry.task_id, 0.0) + entry.cost_usd
        if self.task_manager and entry.task_id:
            try:
                await self.task_manager.add_task_cost(entry.task_id, entry.cost_usd)
            except Exception:
                pass

    async def task_cost(self, task_id: str) -> float:
        async with self._lock:
            return self._per_task.get(task_id, 0.0)

    async def total_cost(self) -> float:
        async with self._lock:
            return sum(e.cost_usd for e in self._entries)

    def summary(self) -> dict:
        by_provider: Dict[str, float] = {}
        for e in self._entries:
            by_provider[e.provider] = by_provider.get(e.provider, 0.0) + e.cost_usd
        return {"total_usd": sum(by_provider.values()),
                "by_provider": by_provider,
                "calls": len(self._entries)}
