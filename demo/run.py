from __future__ import annotations
import asyncio, json, os
from core.approval import ApprovalGate
from core.comm_bus import CommunicationBus
from core.events import EventStore
from core.factory import AgentFactory
from core.llm import LLMClient
from core.models import Task
from core.orchestrator import Orchestrator
from core.tools import ToolRegistry
from demo.agents import build_demo_specs, register_demo_tools


async def main():
    tools = ToolRegistry()
    register_demo_tools(tools)
    events = EventStore(dsn=os.getenv("DATABASE_URL"))
    await events.connect()
    comm = CommunicationBus()
    llm = LLMClient(model=os.getenv("LLM_MODEL", "gpt-4o-mini"))
    factory = AgentFactory(llm=llm, tools=tools, event_store=events, comm_bus=comm,
                           approval_gate=ApprovalGate(auto_approve_in_dev=True))
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    task = Task(description=(
        "Research the surface code for quantum error correction, then produce a small "
        "Python simulation that encodes 1 logical qubit and reports its syndrome for a "
        "single bit-flip error. Verify the result."),
        domain="quantum")
    result = await orch.run_task(task, entry_agent_id="researcher-1")
    print("=" * 80)
    print("TASK:", task.description)
    print("=" * 80)
    print("SUCCESS:", result.success)
    print("SUMMARY:", result.summary)
    print("ELAPSED:", f"{result.elapsed_seconds:.1f}s")
    if result.plan:
        print(f"\nPLAN (replans={result.plan.replan_count}):")
        for s in result.plan.steps:
            print(f"  - [{s.action.value}] {s.description}")

    print("\n--- EVENT STREAM ---")
    for e in await events.get_events(f"task:{task.id}"):
        print(f"  {e.version:03d} {e.type:30s} {json.dumps(e.data)[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
