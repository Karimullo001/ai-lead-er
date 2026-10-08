from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    RESEARCHER = "researcher"
    CODER = "coder"
    ANALYST = "analyst"
    VERIFIER = "verifier"
    WRITER = "writer"
    CUSTOM = "custom"


class AgentState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REFLECTING = "reflecting"
    WAITING_APPROVAL = "waiting_approval"
    HANDOFF = "handoff"
    DONE = "done"
    FAILED = "failed"


class AgentSpec(BaseModel):
    agent_id: str
    name: str
    role: AgentRole = AgentRole.CUSTOM
    instructions: str = "You are a helpful autonomous agent."
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    tools: List[str] = Field(default_factory=list)
    handoff_targets: List[str] = Field(default_factory=list)
    max_iterations: int = 25
    timeout_seconds: int = 600
    guardrails: List[str] = Field(default_factory=list)
    memory_config: Dict[str, Any] = Field(default_factory=lambda: {
        "working_buffer_size": 50,
        "episodic_retrieval_k": 5,
        "semantic_enabled": True,
        "procedural_enabled": True,
    })
    reliability_policy: Dict[str, Any] = Field(default_factory=lambda: {
        "max_retries": 3,
        "backoff_base_ms": 500,
        "verification_checklist": "default",
        "failure_patterns_enabled": True,
    })
    requires_approval_for: List[str] = Field(default_factory=list)
