from __future__ import annotations
import asyncio, logging
from typing import Dict, List, Optional
from .comm_bus import CommunicationBus
from .events import Event, EventStore
from .kernel import AgentKernel
from .models import Task, TaskResult

log = logging.getLogger("agentos.orchestrator")


class Orchestrator:
    def __init__(self, event_store: EventStore, comm_bus: CommunicationBus):
        self.agents: Dict[str, AgentKernel] = {}
        self.events = event_store
        self.comm = comm_bus

    def register_agent(self, kernel: AgentKernel) -> None:
        kernel.orchestrator = self
        self.agents[kernel.spec.agent_id] = kernel
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.events.append(Event(
                stream_id="registry", type="agent.registered",
                data={"agent_id": kernel.spec.agent_id,
                      "role": kernel.spec.role.value})))
        except RuntimeError:
            pass
        log.info("Registered agent: %s (%s)", kernel.spec.agent_id, kernel.spec.role.value)

    async def run_task(self, task: Task, entry_agent_id: str) -> TaskResult:
        if entry_agent_id not in self.agents:
            raise KeyError(f"Unknown entry agent: {entry_agent_id}")
        await self.events.append(Event(stream_id=f"task:{task.id}", type="task.submitted",
                                       data={"agent_id": entry_agent_id,
                                             "description": task.description}))
        return await self.agents[entry_agent_id].run(task)

    async def run_parallel(self, tasks: List[Task], agent_ids: List[str]) -> List[TaskResult]:
        if len(tasks) != len(agent_ids):
            raise ValueError("tasks and agent_ids length mismatch")
        return await asyncio.gather(*(self.agents[a].run(t)
                                      for a, t in zip(agent_ids, tasks)))

    async def run_hierarchical(self, task: Task, coordinator_id: str) -> TaskResult:
        return await self.run_task(task, coordinator_id)

    def get_agent(self, agent_id: str) -> Optional[AgentKernel]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[dict]:
        return [k.spec.model_dump() for k in self.agents.values()]
