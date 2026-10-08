from __future__ import annotations
from typing import Optional
from .agent import AgentSpec
from .approval import ApprovalGate
from .comm_bus import CommunicationBus
from .events import EventStore
from .guardrails import GuardrailChain
from .kernel import AgentKernel
from .llm import LLMClient
from .memory import MemoryManager
from .reliability import ReliabilityEngine
from .tools import Sandbox, ToolRegistry


class AgentFactory:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, event_store: EventStore,
                 comm_bus: CommunicationBus,
                 reliability: Optional[ReliabilityEngine] = None,
                 approval_gate: Optional[ApprovalGate] = None,
                 guardrails: Optional[GuardrailChain] = None,
                 sandbox: Optional[Sandbox] = None,
                 memory_persist_dir: Optional[str] = None):
        self.llm = llm
        self.tools = tools
        self.events = event_store
        self.comm = comm_bus
        self.reliability = reliability or ReliabilityEngine()
        self.approval = approval_gate or ApprovalGate(auto_approve_in_dev=False)
        self.guardrails = guardrails or GuardrailChain()
        self.sandbox = sandbox or Sandbox()
        self.memory_persist_dir = memory_persist_dir

    def build(self, spec: AgentSpec) -> AgentKernel:
        cfg = dict(spec.memory_config)
        if self.memory_persist_dir:
            cfg.setdefault("persist_dir", self.memory_persist_dir)
        memory = MemoryManager(cfg)
        return AgentKernel(
            spec=spec, llm=self.llm, tools=self.tools, memory=memory,
            event_store=self.events, reliability=self.reliability,
            comm_bus=self.comm, approval_gate=self.approval,
            guardrails=self.guardrails, sandbox=self.sandbox)
