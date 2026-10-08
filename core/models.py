from __future__ import annotations
import time, uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_approval"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    domain: str = "general"
    parent_task_id: Optional[str] = None
    priority: int = 5
    status: TaskStatus = TaskStatus.PENDING
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    deadline: Optional[float] = None


class TaskResult(BaseModel):
    task_id: str
    summary: str
    plan: Optional["Plan"] = None
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    elapsed_seconds: float = 0.0


class PlanStepAction(str, Enum):
    TOOL_CALL = "tool_call"
    HANDOFF = "handoff"
    REASON = "reason"
    ASK_HUMAN = "ask_human"
    FINISH = "finish"


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    description: str
    action: PlanStepAction
    tool_id: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    target_agent_id: Optional[str] = None
    subtask: Optional[str] = None
    prompt: Optional[str] = None
    expected_outcome: str = ""
    depends_on: List[str] = Field(default_factory=list)
    max_retries: int = 2


class Plan(BaseModel):
    goal: str
    steps: List[PlanStep] = Field(default_factory=list)
    rationale: str = ""
    replan_count: int = 0

    @classmethod
    def parse(cls, raw: dict, available_tools: List[Any], available_agents: Optional[List[str]] = None) -> "Plan":
        tool_ids = {getattr(t, "tool_id", None) for t in available_tools} if available_tools else set()
        agent_ids = set(available_agents or [])
        steps: List[PlanStep] = []
        for raw_step in raw.get("steps", []):
            action = raw_step.get("action")
            if action not in {a.value for a in PlanStepAction}:
                continue
            tool_id = raw_step.get("tool_id")
            if action == PlanStepAction.TOOL_CALL.value and tool_ids and tool_id not in tool_ids:
                continue
            target = raw_step.get("target_agent_id")
            if action == PlanStepAction.HANDOFF.value and agent_ids and target not in agent_ids:
                continue
            steps.append(PlanStep(
                description=raw_step.get("description", ""),
                action=PlanStepAction(action),
                tool_id=tool_id,
                arguments=raw_step.get("arguments", {}) or {},
                target_agent_id=target,
                subtask=raw_step.get("subtask"),
                prompt=raw_step.get("prompt"),
                expected_outcome=raw_step.get("expected_outcome", ""),
                depends_on=raw_step.get("depends_on", []) or [],
                max_retries=raw_step.get("max_retries", 2),
            ))
        return cls(goal=raw.get("goal", "Unspecified goal"), steps=steps,
                   rationale=raw.get("rationale", ""))


class StepResult(BaseModel):
    action: str
    output: Any = None
    summary: str = ""
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None

    def to_evidence(self) -> Dict[str, Any]:
        return {"action": self.action, "output": self.output, "success": self.success,
                "artifacts": self.artifacts, "tools_used": self.tools_used}


class Verification(BaseModel):
    passed: bool
    reason: str = ""
    failure_signals: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)


class Reflection(BaseModel):
    step_id: str
    insight: str
    pattern: Optional[str] = None


class Context(BaseModel):
    working: List[str] = Field(default_factory=list)
    episodic: List[Dict[str, Any]] = Field(default_factory=list)
    semantic: List[Dict[str, Any]] = Field(default_factory=list)
    procedural: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def token_count(self) -> int:
        text = " ".join(self.working) + " " + " ".join(str(e) for e in self.episodic)
        return max(1, len(text) // 4)

    def render(self, max_chars: int = 6000) -> str:
        parts = []
        if self.working:
            parts.append("## Working memory\\n" + "\\n".join(f"- {w}" for w in self.working[-20:]))
        if self.episodic:
            parts.append("## Relevant past episodes\\n" + "\\n".join(
                f"- {e.get('document','')[:300]}" for e in self.episodic[:5]))
        if self.semantic:
            parts.append("## Knowledge\\n" + "\\n".join(str(s)[:300] for s in self.semantic[:5]))
        if self.procedural:
            parts.append("## Skills available\\n" + "\\n".join(str(p)[:200] for p in self.procedural[:5]))
        return "\\n\\n".join(parts)[:max_chars]


TaskResult.model_rebuild()
