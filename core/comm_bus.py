from __future__ import annotations
import asyncio, logging, time, uuid
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("agentos.comm")


class MessageType(str, Enum):
    HANDOFF = "handoff"
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    BROADCAST = "broadcast"


class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType
    from_agent: str
    to_agent: str
    task_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    reply_to: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class CommunicationBus:
    def __init__(self):
        self._inboxes: Dict[str, asyncio.Queue] = {}
        self._handlers: Dict[str, Callable[[AgentMessage], Awaitable[None]]] = {}
        self._pending: Dict[str, asyncio.Future] = {}

    def register(self, agent_id: str, handler=None) -> None:
        self._inboxes.setdefault(agent_id, asyncio.Queue())
        if handler is not None:
            self._handlers[agent_id] = handler

    async def send(self, message: AgentMessage) -> None:
        if message.to_agent not in self._inboxes:
            raise KeyError(f"AgentNotFound: {message.to_agent}")
        await self._inboxes[message.to_agent].put(message)

    async def receive(self, agent_id: str) -> AgentMessage:
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = asyncio.Queue()
        return await self._inboxes[agent_id].get()

    async def request(self, message: AgentMessage, timeout: float = 300.0) -> AgentMessage:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[message.message_id] = fut
        await self.send(message)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(message.message_id, None)

    async def reply(self, original: AgentMessage, from_agent: str,
                    payload: Dict[str, Any]) -> None:
        fut = self._pending.get(original.message_id)
        if fut and not fut.done():
            fut.set_result(AgentMessage(
                type=MessageType.RESPONSE, from_agent=from_agent,
                to_agent=original.from_agent, task_id=original.task_id,
                payload=payload, reply_to=original.message_id))

    async def broadcast(self, from_agent: str, payload: Dict[str, Any],
                        exclude: Optional[List[str]] = None) -> None:
        exclude = set(exclude or [])
        for aid in list(self._inboxes.keys()):
            if aid == from_agent or aid in exclude:
                continue
            await self.send(AgentMessage(type=MessageType.BROADCAST,
                                         from_agent=from_agent, to_agent=aid,
                                         payload=payload))
