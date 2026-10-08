from __future__ import annotations
import asyncio, time, uuid
from typing import Any, Dict
from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    agent_id: str
    action: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    created_at: float = Field(default_factory=time.time)


class ApprovalDecision(BaseModel):
    request_id: str
    approved: bool
    decided_by: str = "system"
    note: str = ""


class ApprovalGate:
    def __init__(self, auto_approve_in_dev: bool = False, timeout_seconds: int = 600):
        self.pending: Dict[str, asyncio.Future] = {}
        self.requests: Dict[str, ApprovalRequest] = {}
        self.auto_approve_in_dev = auto_approve_in_dev
        self.timeout_seconds = timeout_seconds

    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        if self.auto_approve_in_dev:
            return ApprovalDecision(request_id=req.request_id, approved=True,
                                    decided_by="auto_dev", note="auto-approved in dev mode")
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[req.request_id] = fut
        self.requests[req.request_id] = req
        try:
            decision: ApprovalDecision = await asyncio.wait_for(fut, timeout=self.timeout_seconds)
            return decision
        except asyncio.TimeoutError:
            return ApprovalDecision(request_id=req.request_id, approved=False,
                                    decided_by="timeout", note="approval timed out")
        finally:
            self.pending.pop(req.request_id, None)
            self.requests.pop(req.request_id, None)

    def resolve(self, request_id: str, approved: bool,
                decided_by: str = "user", note: str = "") -> bool:
        fut = self.pending.get(request_id)
        if not fut or fut.done():
            return False
        fut.set_result(ApprovalDecision(request_id=request_id, approved=approved,
                                        decided_by=decided_by, note=note))
        return True
