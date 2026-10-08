import json
import pytest

from core.agent import AgentRole, AgentSpec
from core.approval import ApprovalGate
from core.comm_bus import CommunicationBus
from core.events import EventStore
from core.kernel import AgentKernel
from core.memory import MemoryManager
from core.models import Task
from core.reliability import ReliabilityEngine
from core.tools import FunctionTool, ToolRegistry, ToolSpec


class FakeLLM:
    def __init__(self, plan, reasoning="Final answer.", synthesis="Synthesized."):
        self._plan = plan
        self._reasoning = reasoning
        self._synthesis = synthesis

    async def complete_json(self, prompt, temperature=0.1, max_tokens=2048):
        return self._plan

    async def complete(self, prompt, temperature=0.2, max_tokens=2048, system=None):
        low = prompt.lower()
        if "synthesis" in low or "single, clear final answer" in low:
            return self._synthesis
        if "plan" in low:
            return json.dumps(self._plan)
        return self._reasoning


@pytest.mark.asyncio
async def test_kernel_reason_then_finish():
    plan = {"goal": "say hi", "rationale": "trivial",
            "steps": [{"description": "Reason", "action": "reason",
                       "prompt": "Say hi", "expected_outcome": "greeting"},
                      {"description": "Finish", "action": "finish"}]}
    spec = AgentSpec(agent_id="a1", name="A", role=AgentRole.CUSTOM,
                     instructions="test", model="fake")
    kernel = AgentKernel(
        spec=spec, llm=FakeLLM(plan, reasoning="Hi there!", synthesis="Hi there!"),
        tools=ToolRegistry(), memory=MemoryManager({}),
        event_store=EventStore(), reliability=ReliabilityEngine(),
        comm_bus=CommunicationBus(),
        approval_gate=ApprovalGate(auto_approve_in_dev=True))
    result = await kernel.run(Task(description="say hi"))
    assert result.success
    assert "Hi there!" in result.summary


@pytest.mark.asyncio
async def test_kernel_tool_call_success():
    def echo(msg):
        return f"echo:{msg}"

    tools = ToolRegistry()
    tools.register(FunctionTool(
        ToolSpec(tool_id="echo", name="echo", description="Echo input"), echo))
    plan = {"goal": "echo", "rationale": "test",
            "steps": [{"description": "Echo", "action": "tool_call",
                       "tool_id": "echo", "arguments": {"msg": "hello"},
                       "expected_outcome": "echo:hello"},
                      {"description": "Finish", "action": "finish"}]}
    spec = AgentSpec(agent_id="a2", name="B", role=AgentRole.CUSTOM,
                     instructions="test", tools=["echo"])
    kernel = AgentKernel(
        spec=spec, llm=FakeLLM(plan), tools=tools, memory=MemoryManager({}),
        event_store=EventStore(), reliability=ReliabilityEngine(),
        comm_bus=CommunicationBus(),
        approval_gate=ApprovalGate(auto_approve_in_dev=True))
    result = await kernel.run(Task(description="echo hello"))
    assert result.success
    assert "echo:hello" in str(result.artifacts)
