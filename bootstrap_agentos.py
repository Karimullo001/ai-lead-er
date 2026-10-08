#!/usr/bin/env python3
"""
AgentOS bootstrap — writes the full project tree to disk.

Usage:
    python bootstrap_agentos.py [target_dir]   # default: ./agentos
Then:
    cd agentos
    python -m venv .venv && source .venv/bin/activate   # (Windows: .venv\\Scripts\\activate)
    pip install -e ".[dev]"
    pytest -q
    python -m demo.run       # needs OPENAI_API_KEY
"""
import os, sys, subprocess
from pathlib import Path

FILES = {}

# ------------------------------------------------------------------ core/__init__.py
FILES["core/__init__.py"] = r'''"""AgentOS core package."""
__version__ = "1.0.0"
'''

# ------------------------------------------------------------------ core/models.py
FILES["core/models.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/agent.py
FILES["core/agent.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/prompts.py
FILES["core/prompts.py"] = r'''from __future__ import annotations
import json
from typing import Any, List
from .models import Context, Plan, Reflection, Task


PLANNER_SYSTEM = """You are a planning module for an autonomous AI agent.
Respond with ONE valid JSON object only (no markdown fences, no prose) with schema:

{
  "goal": "<short restatement>",
  "rationale": "<why this plan>",
  "steps": [
    {
      "description": "<what this step does>",
      "action": "tool_call" | "handoff" | "reason" | "ask_human" | "finish",
      "tool_id": "<tool id if action=tool_call>",
      "arguments": { },
      "target_agent_id": "<agent id if action=handoff>",
      "subtask": "<delegated task if action=handoff>",
      "prompt": "<prompt if action=reason>",
      "expected_outcome": "<what success looks like>",
      "max_retries": 2
    }
  ]
}

Rules:
- Use ONLY listed tool/agent ids; do not invent.
- Prefer tool_call for concrete actions, reason for analysis, handoff for delegation.
- The LAST step MUST be action="finish".
- 1 to 8 steps total.
"""


def planning_prompt(task: Task, context: Context, tools: List[Any],
                    handoff_targets: List[str], instructions: str) -> str:
    tool_desc = "\n".join(
        f"- id={t.tool_id} | {t.description} | params={json.dumps(t.parameters)}"
        for t in tools) or "(no tools)"
    agents = ", ".join(handoff_targets) if handoff_targets else "(none)"
    return f"""{PLANNER_SYSTEM}

# Agent instructions
{instructions}

# Task
{task.description}
Domain: {task.domain}

# Available tools
{tool_desc}

# Available handoff agents
{agents}

# Context from memory
{context.render()}

Return ONLY the JSON plan.
"""


REPLANNER_SYSTEM = """You are a planning repair module. A step failed. Produce a NEW JSON plan
(same schema as before) that keeps the parts that worked and fixes what failed.
Respond with a single JSON object only."""


def replanning_prompt(task: Task, old_plan: Plan, reflection: Reflection,
                      tools: List[Any], handoff_targets: List[str]) -> str:
    tool_desc = "\n".join(f"- id={t.tool_id} | {t.description}" for t in tools) or "(no tools)"
    agents = ", ".join(handoff_targets) if handoff_targets else "(none)"
    return f"""{REPLANNER_SYSTEM}

Task: {task.description}

Previous plan:
{json.dumps(old_plan.model_dump(), indent=2)[:3000]}

Failure insight:
{reflection.insight}
Matched failure pattern: {reflection.pattern or "unknown"}

Tools: {tool_desc}
Handoff targets: {agents}

Return ONLY the new JSON plan.
"""


REASONING_SYSTEM = """You are a careful reasoning module. Think step-by-step, then
give a concise final answer. Do not include private deliberations in the final line."""


def reasoning_prompt(step_prompt: str, context: Context) -> str:
    return f"""{REASONING_SYSTEM}

# Context
{context.render()}

# Question
{step_prompt}

Answer concisely.
"""


SYNTHESIS_SYSTEM = """You are a synthesis module. Given the goal and the executed steps,
produce a single, clear final answer for the user. No meta-commentary."""


def synthesis_prompt(task: Task, plan: Plan, step_outputs: List[str]) -> str:
    body = "\n".join(f"### {s.id} - {s.description}\n{o}"
                     for s, o in zip(plan.steps, step_outputs))
    return f"""{SYNTHESIS_SYSTEM}

Goal: {task.description}

# Executed steps
{body}

# Final answer
"""


TOOL_CALL_SYSTEM = """You are a tool-selection module. Choose exactly ONE tool to invoke
for the given step, or answer directly if no tool fits. Respond with JSON:
{"tool_id": "<id>", "arguments": { }}  OR  {"tool_id": null, "answer": "<text>"}
Return JSON only.
"""


def tool_selection_prompt(step_desc: str, tools: List[Any], context: Context) -> str:
    tool_desc = "\n".join(
        f"- id={t.tool_id} | {t.description} | params={json.dumps(t.parameters)}"
        for t in tools) or "(no tools)"
    return f"""{TOOL_CALL_SYSTEM}

Step: {step_desc}

Tools:
{tool_desc}

Context:
{context.render(max_chars=2000)}

Return JSON only.
"""
'''

# ------------------------------------------------------------------ core/llm.py
FILES["core/llm.py"] = r'''from __future__ import annotations
import asyncio, json, logging, os, re
from typing import Any, Dict, List, Optional
import litellm

log = logging.getLogger("agentos.llm")


class LLMClient:
    def __init__(self, model: str = "gpt-4o-mini", api_key: Optional[str] = None,
                 base_url: Optional[str] = None, max_retries: int = 3):
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        self.max_retries = max_retries
        litellm.drop_params = True

    async def complete(self, prompt: str, temperature: float = 0.2,
                       max_tokens: int = 2048, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = await litellm.acompletion(
                    model=self.model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    api_key=self.api_key, base_url=self.base_url)
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"LLM failed after {self.max_retries} attempts: {last_err}")

    async def complete_json(self, prompt: str, temperature: float = 0.1,
                            max_tokens: int = 2048) -> Dict[str, Any]:
        text = await self.complete(prompt, temperature=temperature, max_tokens=max_tokens)
        return _extract_json(text)

    async def complete_with_tools(self, messages: List[Dict[str, Any]],
                                  tools: List[Dict[str, Any]],
                                  temperature: float = 0.1) -> Dict[str, Any]:
        resp = await litellm.acompletion(
            model=self.model, messages=messages, tools=tools, tool_choice="auto",
            temperature=temperature, api_key=self.api_key, base_url=self.base_url)
        msg = resp.choices[0].message
        return {"content": msg.content,
                "tool_calls": [{"id": tc.id, "name": tc.function.name,
                                "arguments": tc.function.arguments}
                               for tc in (msg.tool_calls or [])]}


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJ = re.compile(r"(\{.*\})", re.DOTALL)


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = _JSON_OBJ.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    log.error("Could not parse JSON from LLM: %r", text[:500])
    return {}
'''

# ------------------------------------------------------------------ core/tools.py
FILES["core/tools.py"] = r'''from __future__ import annotations
import asyncio, inspect, io, json, logging, time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("agentos.tools")


class ToolSpec(BaseModel):
    tool_id: str
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=lambda: {
        "type": "object", "properties": {}, "required": []})
    requires_sandbox: bool = False
    timeout_seconds: int = 60
    dangerous: bool = False


class ToolResult(BaseModel):
    output: Any = None
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    success: bool = True
    elapsed_ms: float = 0.0


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    async def invoke(self, arguments: Dict[str, Any],
                     sandbox: Optional["Sandbox"] = None) -> ToolResult: ...


class FunctionTool(Tool):
    def __init__(self, spec: ToolSpec, fn: Callable):
        self.spec = spec
        self.fn = fn

    async def invoke(self, arguments: Dict[str, Any],
                     sandbox: Optional["Sandbox"] = None) -> ToolResult:
        t0 = time.time()
        try:
            if sandbox and self.spec.requires_sandbox:
                res = await sandbox.run_callable(self.fn, arguments,
                                                 timeout=self.spec.timeout_seconds)
            else:
                if inspect.iscoroutinefunction(self.fn):
                    res = await asyncio.wait_for(self.fn(**arguments),
                                                 timeout=self.spec.timeout_seconds)
                else:
                    res = await asyncio.wait_for(
                        asyncio.to_thread(self.fn, **arguments),
                        timeout=self.spec.timeout_seconds)
            return ToolResult(output=res, success=True,
                              elapsed_ms=(time.time() - t0) * 1000)
        except Exception as e:
            log.exception("Tool %s failed", self.spec.tool_id)
            return ToolResult(output=None, error=f"{type(e).__name__}: {e}",
                              success=False, elapsed_ms=(time.time() - t0) * 1000)


class MCPTool(Tool):
    def __init__(self, spec: ToolSpec, mcp_client: Any):
        self.spec = spec
        self.mcp = mcp_client

    async def invoke(self, arguments: Dict[str, Any],
                     sandbox: Optional["Sandbox"] = None) -> ToolResult:
        try:
            res = await self.mcp.call_tool(self.spec.name, arguments)
            return ToolResult(output=res.get("output"),
                              artifacts=res.get("artifacts", []), success=True)
        except Exception as e:
            return ToolResult(output=None, error=str(e), success=False)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.tool_id] = tool

    def get(self, tool_id: str) -> Tool:
        if tool_id not in self._tools:
            raise KeyError(f"ToolNotFound: {tool_id}")
        return self._tools[tool_id]

    def list_tools(self, allowed: Optional[List[str]] = None) -> List[ToolSpec]:
        if allowed is None:
            return [t.spec for t in self._tools.values()]
        return [self._tools[i].spec for i in allowed if i in self._tools]

    def to_openai_schema(self, allowed: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return [{"type": "function",
                 "function": {"name": s.tool_id, "description": s.description,
                              "parameters": s.parameters}}
                for s in self.list_tools(allowed)]


class Sandbox:
    def __init__(self, image: str = "python:3.11-slim", prefer_docker: bool = False):
        self.image = image
        self.prefer_docker = prefer_docker
        self._docker = None
        if prefer_docker:
            try:
                import docker  # type: ignore
                self._docker = docker.from_env()
                self._docker.ping()
            except Exception as e:
                log.warning("Docker unavailable, using in-process sandbox: %s", e)
                self._docker = None

    async def run_callable(self, fn: Callable, arguments: Dict[str, Any],
                           timeout: int = 30) -> Any:
        if self._docker is None:
            return await self._run_inproc(fn, arguments, timeout)
        return await self._run_docker(fn, arguments, timeout)

    async def _run_inproc(self, fn: Callable, arguments: Dict[str, Any], timeout: int) -> Any:
        def _call():
            buf = io.StringIO()
            import sys
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf, buf
            try:
                result = fn(**arguments)
                return {"result": result, "stdout": buf.getvalue()}
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)

    async def _run_docker(self, fn: Callable, arguments: Dict[str, Any], timeout: int) -> Any:
        code = ("import json, sys\n"
                f"args = json.loads({json.dumps(json.dumps(arguments))})\n"
                f"{inspect.getsource(fn)}\n"
                f"result = {fn.__name__}(**args)\n"
                "print(json.dumps({'result': result}))\n")
        container = self._docker.containers.run(
            self.image, command=["python", "-c", code],
            detach=True, network_disabled=True, mem_limit="256m",
            cpu_period=100000, cpu_quota=50000, remove=False)
        try:
            container.wait(timeout=timeout)
            logs = container.logs().decode()
            try:
                return json.loads(logs.strip().splitlines()[-1])["result"]
            except Exception:
                return {"raw": logs}
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
'''

# ------------------------------------------------------------------ core/memory.py
FILES["core/memory.py"] = r'''from __future__ import annotations
import logging, time, uuid
from collections import deque
from typing import Any, Dict, List, Optional
from .models import Context, PlanStep, StepResult, Task

log = logging.getLogger("agentos.memory")


class WorkingMemory:
    def __init__(self, capacity: int = 50):
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)

    def add(self, item: str) -> None:
        self.buffer.append(item)

    def get_recent(self, n: int) -> List[str]:
        return list(self.buffer)[-n:]

    def summarize(self) -> str:
        if not self.buffer:
            return ""
        return " | ".join(list(self.buffer)[-10:])

    def clear(self) -> None:
        self.buffer.clear()


class EpisodicMemory:
    def __init__(self, collection_name: str = "episodic", persist_dir: Optional[str] = None):
        self._docs: List[Dict[str, Any]] = []
        self._client = None
        self._collection = None
        try:
            import chromadb  # type: ignore
            if persist_dir:
                self._client = chromadb.PersistentClient(path=persist_dir)
            else:
                self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(collection_name)
        except Exception as e:
            log.warning("Chroma unavailable, using in-memory fallback: %s", e)

    async def add(self, document: str, metadata: Dict[str, Any]) -> None:
        self._docs.append({"document": document, "metadata": metadata, "ts": time.time()})
        if self._collection is not None:
            try:
                self._collection.add(
                    documents=[document],
                    metadatas=[{k: v for k, v in metadata.items()
                                if isinstance(v, (str, int, float, bool))}],
                    ids=[f"{metadata.get('task_id','x')}-{uuid.uuid4().hex[:8]}"])
            except Exception as e:
                log.debug("Chroma add failed: %s", e)

    async def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if self._collection is not None and self._collection.count() > 0:
            try:
                res = self._collection.query(query_texts=[query],
                                             n_results=min(k, self._collection.count()))
                docs = res.get("documents", [[]])[0]
                metas = res.get("metadatas", [[]])[0]
                return [{"document": d, "metadata": m} for d, m in zip(docs, metas)]
            except Exception as e:
                log.debug("Chroma query failed: %s", e)
        q = query.lower()
        return sorted(self._docs, key=lambda e: -_overlap(q, e["document"].lower()))[:k]


def _overlap(a: str, b: str) -> int:
    return sum(1 for w in set(a.split()) if w in b)


class SemanticMemory:
    def __init__(self):
        self.facts: List[Dict[str, Any]] = []

    def add(self, fact: Dict[str, Any]) -> None:
        self.facts.append(fact)

    def query(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        q = query.lower()
        return sorted(self.facts, key=lambda f: -_overlap(q, str(f).lower()))[:k]


class ProceduralMemory:
    def __init__(self):
        self.skills: Dict[str, List[Dict[str, Any]]] = {}

    def register(self, domain: str, skill: Dict[str, Any]) -> None:
        self.skills.setdefault(domain, []).append(skill)

    def get_skills_for(self, domain: str) -> List[Dict[str, Any]]:
        out = list(self.skills.get(domain, []))
        out.extend(self.skills.get("general", []))
        return out


class MemoryManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.working = WorkingMemory(capacity=cfg.get("working_buffer_size", 50))
        self.episodic = EpisodicMemory(persist_dir=cfg.get("persist_dir"))
        self.semantic = SemanticMemory() if cfg.get("semantic_enabled", True) else None
        self.procedural = ProceduralMemory() if cfg.get("procedural_enabled", True) else None

    async def build_context(self, task: Task, working_buffer: int, episodic_k: int) -> Context:
        w = self.working.get_recent(working_buffer)
        e = await self.episodic.search(task.description, k=episodic_k)
        s = self.semantic.query(task.description) if self.semantic else []
        p = self.procedural.get_skills_for(task.domain) if self.procedural else []
        return Context(working=w, episodic=e, semantic=s, procedural=p)

    async def record_episode(self, task_id: str, step: PlanStep, result: StepResult,
                             verification_passed: bool) -> None:
        doc = (f"Task {task_id} | Step {step.id}: {step.description}\n"
               f"Action: {step.action.value} | Output: {str(result.output)[:500]}\n"
               f"Verified: {verification_passed}")
        await self.episodic.add(doc, {
            "task_id": task_id, "step_id": step.id,
            "success": verification_passed, "tools_used": ",".join(result.tools_used)})
        self.working.add(f"{step.id}: {result.summary or str(result.output)[:200]}")

    async def flush(self, task_id: str) -> None:
        s = self.working.summarize()
        if s:
            await self.episodic.add(f"Task {task_id} summary: {s}",
                                    {"task_id": task_id, "type": "task_summary"})
        self.working.clear()
'''

# ------------------------------------------------------------------ core/events.py
FILES["core/events.py"] = r'''from __future__ import annotations
import json, logging, time, uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

log = logging.getLogger("agentos.events")


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    stream_id: str = "global"
    type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
    version: int = 1


class EventStore:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn
        self.pool = None
        self._mem: List[Event] = []
        self._versions: Dict[str, int] = {}

    async def connect(self) -> None:
        if not self.dsn:
            return
        try:
            import asyncpg  # type: ignore
            self.pool = await asyncpg.create_pool(self.dsn)
            await self.pool.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    stream_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data JSONB NOT NULL,
                    timestamp DOUBLE PRECISION NOT NULL,
                    version INT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, version);
            """)
            log.info("EventStore connected to Postgres")
        except Exception as e:
            log.warning("Postgres unavailable, using in-memory event store: %s", e)
            self.pool = None

    async def append(self, event: Event) -> Event:
        v = self._versions.get(event.stream_id, 0) + 1
        self._versions[event.stream_id] = v
        event.version = v
        self._mem.append(event)
        if self.pool is not None:
            try:
                await self.pool.execute(
                    "INSERT INTO events(event_id, stream_id, type, data, timestamp, version) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    event.event_id, event.stream_id, event.type,
                    json.dumps(event.data), event.timestamp, event.version)
            except Exception as e:
                log.error("EventStore append failed: %s", e)
        return event

    async def get_events(self, stream_id: str, after_version: int = 0) -> List[Event]:
        if self.pool is not None:
            try:
                rows = await self.pool.fetch(
                    "SELECT event_id, stream_id, type, data, timestamp, version "
                    "FROM events WHERE stream_id=$1 AND version>$2 ORDER BY version",
                    stream_id, after_version)
                return [Event(event_id=r["event_id"], stream_id=r["stream_id"],
                              type=r["type"],
                              data=json.loads(r["data"]) if isinstance(r["data"], str) else r["data"],
                              timestamp=r["timestamp"], version=r["version"])
                        for r in rows]
            except Exception as e:
                log.error("EventStore read failed: %s", e)
        return [e for e in self._mem if e.stream_id == stream_id and e.version > after_version]
'''

# ------------------------------------------------------------------ core/reliability.py
FILES["core/reliability.py"] = r'''from __future__ import annotations
import asyncio, logging, random
from typing import Any, Awaitable, Callable, Dict, List, Optional
from pydantic import BaseModel
from .models import Verification

log = logging.getLogger("agentos.reliability")


class FailurePattern(BaseModel):
    name: str
    symptoms: List[str]
    retry_strategy: str = "exponential_jitter"
    max_retries: int = 3
    fallback: Optional[str] = None


class ReliabilityEngine:
    FAILURE_PATTERNS: List[FailurePattern] = [
        FailurePattern(name="rate_limit",
                       symptoms=["429", "rate limit", "too many requests"],
                       retry_strategy="exponential_jitter", max_retries=6),
        FailurePattern(name="timeout",
                       symptoms=["timeout", "timed out", "deadline exceeded"],
                       retry_strategy="exponential_jitter", max_retries=3),
        FailurePattern(name="silent_none",
                       symptoms=["none", "empty response", "missing output"],
                       retry_strategy="immediate", max_retries=2, fallback="return_error"),
        FailurePattern(name="tool_not_found",
                       symptoms=["toolnotfound", "unknown tool"],
                       retry_strategy="re_plan", max_retries=1),
        FailurePattern(name="verification_failed",
                       symptoms=["verification failed", "check failed", "checklist"],
                       retry_strategy="re_plan", max_retries=2),
        FailurePattern(name="context_overflow",
                       symptoms=["token", "context window", "maximum context"],
                       retry_strategy="summarize_and_retry", max_retries=1),
        FailurePattern(name="syntax_error",
                       symptoms=["syntaxerror", "syntax error", "indentationerror"],
                       retry_strategy="re_plan", max_retries=2),
    ]

    def __init__(self):
        self._checklists: Dict[str, List[Callable[[Dict[str, Any], Dict[str, Any]],
                                                  Awaitable[Dict[str, Any]]]]] = {}
        self._register_defaults()

    async def execute_with_recovery(self, operation, policy, context):
        max_retries = policy.get("max_retries", 3)
        base_ms = policy.get("backoff_base_ms", 500)
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await operation()
            except Exception as e:
                last_err = e
                pattern = self.match_failure([str(e)])
                if pattern and pattern.retry_strategy == "re_plan":
                    raise
                if attempt >= max_retries:
                    break
                delay_ms = self._compute_delay(pattern, attempt, base_ms)
                log.warning("Retry %d/%d for %s after %dms: %s",
                            attempt + 1, max_retries, context.get("step"), delay_ms, e)
                await asyncio.sleep(delay_ms / 1000.0)
        assert last_err is not None
        raise last_err

    def match_failure(self, symptoms: List[str]) -> Optional[FailurePattern]:
        text = " ".join(symptoms).lower()
        for p in self.FAILURE_PATTERNS:
            if any(s in text for s in p.symptoms):
                return p
        return None

    def _compute_delay(self, pattern, attempt, base_ms):
        if pattern and pattern.retry_strategy == "exponential_jitter":
            return base_ms * (2 ** attempt) + random.uniform(0, base_ms)
        return base_ms

    def register_checklist(self, name, checks):
        self._checklists[name] = checks

    async def verify(self, checklist, evidence, context) -> Verification:
        checks = self._checklists.get(checklist, self._checklists.get("default", []))
        failures: List[str] = []
        for check in checks:
            try:
                res = await check(evidence, context)
                if not res.get("passed", False):
                    failures.append(res.get("reason", "unnamed check failed"))
            except Exception as e:
                failures.append(f"checker error: {e}")
        return Verification(passed=len(failures) == 0,
                            reason="; ".join(failures) if failures else "All checks passed",
                            failure_signals=failures, evidence=evidence)

    def _register_defaults(self) -> None:
        async def not_empty(evidence, _ctx):
            out = evidence.get("output")
            ok = out is not None and (not isinstance(out, str) or out.strip() != "")
            return {"passed": ok, "reason": "empty output" if not ok else ""}

        async def success_flag(evidence, _ctx):
            return {"passed": bool(evidence.get("success", True)),
                    "reason": "step marked failed"}

        async def code_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            bad = ("traceback" in s) or ("syntaxerror" in s) or \
                  ("exception" in s and "handled" not in s)
            return {"passed": not bad, "reason": "code raised an error" if bad else ""}

        async def research_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            ok = any(k in s for k in ("source", "http", "cite"))
            return {"passed": ok, "reason": "no sources present" if not ok else ""}

        async def deployment_check(evidence, _ctx):
            s = str(evidence.get("output", "")).lower()
            ok = ("200" in s) or ("ok" in s) or ("healthy" in s)
            return {"passed": ok, "reason": "no healthy signal" if not ok else ""}

        self._checklists["default"] = [not_empty, success_flag]
        self._checklists["code"] = [not_empty, success_flag, code_check]
        self._checklists["research"] = [not_empty, success_flag, research_check]
        self._checklists["deployment"] = [not_empty, success_flag, deployment_check]
'''

# ------------------------------------------------------------------ core/guardrails.py
FILES["core/guardrails.py"] = r'''from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class GuardrailResult(BaseModel):
    passed: bool
    reason: str = ""
    redacted_text: Optional[str] = None


class Guardrail:
    name: str = "guardrail"

    async def check(self, text: str,
                    context: Optional[Dict[str, Any]] = None) -> GuardrailResult:
        return GuardrailResult(passed=True)


class PIIGuardrail(Guardrail):
    name = "pii"
    PATTERNS = {
        "email": re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "phone": re.compile(r"\b\+?\d[\d\s\-\(\)]{7,}\d\b"),
    }

    async def check(self, text, context=None):
        redacted = text
        found: List[str] = []
        for label, pat in self.PATTERNS.items():
            if pat.search(redacted):
                found.append(label)
                redacted = pat.sub(f"[REDACTED_{label.upper()}]", redacted)
        if found:
            return GuardrailResult(passed=False,
                                   reason=f"PII detected: {', '.join(found)}",
                                   redacted_text=redacted)
        return GuardrailResult(passed=True)


class PromptInjectionGuardrail(Guardrail):
    name = "prompt_injection"
    PATTERNS = [
        re.compile(r"ignore (all|previous) instructions", re.I),
        re.compile(r"disregard (all|previous) (instructions|prompts)", re.I),
        re.compile(r"you are now (a|an) ", re.I),
        re.compile(r"reveal your (system prompt|instructions)", re.I),
        re.compile(r"jailbreak", re.I),
    ]

    async def check(self, text, context=None):
        for pat in self.PATTERNS:
            if pat.search(text):
                return GuardrailResult(passed=False,
                                       reason=f"prompt injection signal: {pat.pattern}")
        return GuardrailResult(passed=True)


class LengthGuardrail(Guardrail):
    name = "length"

    def __init__(self, max_chars: int = 40000):
        self.max_chars = max_chars

    async def check(self, text, context=None):
        if len(text) > self.max_chars:
            return GuardrailResult(passed=False,
                                   reason=f"input exceeds {self.max_chars} chars")
        return GuardrailResult(passed=True)


class GuardrailChain:
    def __init__(self, guardrails: Optional[List[Guardrail]] = None):
        self.guardrails = guardrails or [
            LengthGuardrail(), PromptInjectionGuardrail(), PIIGuardrail()]

    async def check(self, text: str, context: Optional[Dict[str, Any]] = None) -> GuardrailResult:
        cur = text
        for g in self.guardrails:
            res = await g.check(cur, context)
            if not res.passed:
                return res
            if res.redacted_text:
                cur = res.redacted_text
        return GuardrailResult(passed=True, redacted_text=cur)
'''

# ------------------------------------------------------------------ core/approval.py
FILES["core/approval.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/comm_bus.py
FILES["core/comm_bus.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/kernel.py
FILES["core/kernel.py"] = r'''from __future__ import annotations
import asyncio, json, logging, time
from typing import Any, Dict, List, Optional

from .agent import AgentSpec, AgentState
from .approval import ApprovalGate, ApprovalRequest
from .comm_bus import CommunicationBus
from .events import Event, EventStore
from .guardrails import GuardrailChain
from .llm import LLMClient
from .memory import MemoryManager
from .models import (Context, Plan, PlanStep, PlanStepAction, Reflection,
                     StepResult, Task, TaskResult, Verification)
from .prompts import (planning_prompt, reasoning_prompt, replanning_prompt,
                      synthesis_prompt, tool_selection_prompt)
from .reliability import ReliabilityEngine
from .tools import Sandbox, ToolRegistry

log = logging.getLogger("agentos.kernel")


class MaxIterationsExceeded(Exception):
    pass


class AgentKernel:
    def __init__(self, spec: AgentSpec, llm: LLMClient, tools: ToolRegistry,
                 memory: MemoryManager, event_store: EventStore,
                 reliability: ReliabilityEngine, comm_bus: CommunicationBus,
                 approval_gate: Optional[ApprovalGate] = None,
                 guardrails: Optional[GuardrailChain] = None,
                 sandbox: Optional[Sandbox] = None,
                 orchestrator_ref: Optional[Any] = None):
        self.spec = spec
        self.llm = llm
        self.tools = tools
        self.memory = memory
        self.events = event_store
        self.reliability = reliability
        self.comm = comm_bus
        self.approval = approval_gate or ApprovalGate(auto_approve_in_dev=True)
        self.guardrails = guardrails or GuardrailChain()
        self.sandbox = sandbox or Sandbox()
        self.orchestrator = orchestrator_ref
        self.state: AgentState = AgentState.IDLE
        self._iteration = 0
        self._current_plan: Optional[Plan] = None
        self._step_outputs: List[str] = []
        self.comm.register(self.spec.agent_id)

    async def run(self, task: Task) -> TaskResult:
        t0 = time.time()
        stream_id = f"task:{task.id}"
        await self._emit(stream_id, "kernel.started", {"agent": self.spec.agent_id})
        self.state = AgentState.PLANNING
        self._iteration = 0
        self._step_outputs = []
        try:
            result = await asyncio.wait_for(self._pev_loop(task, stream_id),
                                            timeout=self.spec.timeout_seconds)
            await self._emit(stream_id, "kernel.completed",
                             {"summary": result.summary[:500]})
            self.state = AgentState.DONE
            result.elapsed_seconds = time.time() - t0
            return result
        except asyncio.TimeoutError:
            await self._emit(stream_id, "kernel.timeout",
                             {"timeout": self.spec.timeout_seconds})
            self.state = AgentState.FAILED
            return TaskResult(task_id=task.id, summary="Timed out", success=False,
                              error="timeout", elapsed_seconds=time.time() - t0)
        except Exception as e:
            log.exception("Kernel failed")
            await self._emit(stream_id, "kernel.failed", {"error": str(e)})
            self.state = AgentState.FAILED
            return TaskResult(task_id=task.id, summary=f"Failed: {e}", success=False,
                              error=str(e), elapsed_seconds=time.time() - t0)
        finally:
            await self.memory.flush(task.id)

    async def _pev_loop(self, task: Task, stream_id: str) -> TaskResult:
        context = await self.memory.build_context(
            task, working_buffer=self.spec.memory_config["working_buffer_size"],
            episodic_k=self.spec.memory_config["episodic_retrieval_k"])
        g = await self.guardrails.check(task.description, {"phase": "input"})
        if not g.passed:
            return TaskResult(task_id=task.id, summary=f"Blocked by guardrail: {g.reason}",
                              success=False, error=g.reason)
        await self._emit(stream_id, "kernel.perceived", {"tokens": context.token_count})

        plan = await self._plan(task, context)
        self._current_plan = plan
        await self._emit(stream_id, "kernel.planned",
                         {"steps": [s.description for s in plan.steps]})

        replans = 0
        max_replans = 3
        idx = 0
        while idx < len(plan.steps):
            step = plan.steps[idx]
            self._iteration += 1
            if self._iteration > self.spec.max_iterations:
                raise MaxIterationsExceeded(self.spec.max_iterations)
            if step.action == PlanStepAction.FINISH:
                break

            self.state = AgentState.EXECUTING
            try:
                step_result = await self._execute_step(step, task, context)
            except Exception as e:
                if replans >= max_replans:
                    raise
                reflection = Reflection(step_id=step.id,
                                        insight=f"Hard failure: {e}",
                                        pattern="unknown")
                plan = await self._replan(task, plan, reflection)
                replans += 1
                idx = 0
                continue

            self.state = AgentState.VERIFYING
            verification = await self._verify_step(step, step_result, task)

            if not verification.passed:
                self.state = AgentState.REFLECTING
                reflection = await self._reflect(step, step_result, verification)
                await self._emit(stream_id, "kernel.reflection",
                                 {"step": step.id, "insight": reflection.insight})
                if replans >= max_replans:
                    await self.memory.record_episode(task.id, step, step_result, False)
                    self._step_outputs.append(f"[UNVERIFIED] {step_result.summary}")
                    idx += 1
                    continue
                plan = await self._replan(task, plan, reflection)
                replans += 1
                idx = 0
                continue

            await self.memory.record_episode(task.id, step, step_result, True)
            self._step_outputs.append(step_result.summary or str(step_result.output)[:500])
            await self._emit(stream_id, "kernel.step_ok",
                             {"step": step.id, "summary": step_result.summary[:200]})
            idx += 1

        self.state = AgentState.DONE
        final = await self._synthesize(task, plan)
        og = await self.guardrails.check(final, {"phase": "output"})
        if og.redacted_text:
            final = og.redacted_text
        return TaskResult(task_id=task.id, summary=final, plan=plan,
                          artifacts=[{"step": s.id, "output": o}
                                     for s, o in zip(plan.steps, self._step_outputs)])

    async def _plan(self, task: Task, context: Context) -> Plan:
        tools = self.tools.list_tools(self.spec.tools)
        prompt = planning_prompt(task, context, tools,
                                 self.spec.handoff_targets, self.spec.instructions)
        raw = await self.llm.complete_json(prompt, temperature=0.1)
        plan = Plan.parse(raw, available_tools=tools,
                          available_agents=self.spec.handoff_targets)
        if not plan.steps:
            plan = Plan(goal=task.description, rationale="fallback single-shot plan",
                        steps=[PlanStep(description="Reason about the task",
                                        action=PlanStepAction.REASON,
                                        prompt=task.description,
                                        expected_outcome="An answer"),
                               PlanStep(description="Finish",
                                        action=PlanStepAction.FINISH)])
        if plan.steps[-1].action != PlanStepAction.FINISH:
            plan.steps.append(PlanStep(description="Finish",
                                       action=PlanStepAction.FINISH))
        return plan

    async def _replan(self, task: Task, old_plan: Plan, reflection: Reflection) -> Plan:
        tools = self.tools.list_tools(self.spec.tools)
        prompt = replanning_prompt(task, old_plan, reflection,
                                   tools, self.spec.handoff_targets)
        raw = await self.llm.complete_json(prompt, temperature=0.1)
        plan = Plan.parse(raw, available_tools=tools,
                          available_agents=self.spec.handoff_targets)
        plan.replan_count = old_plan.replan_count + 1
        if not plan.steps:
            plan = old_plan
        if plan.steps and plan.steps[-1].action != PlanStepAction.FINISH:
            plan.steps.append(PlanStep(description="Finish",
                                       action=PlanStepAction.FINISH))
        return plan

    async def _execute_step(self, step, task, context) -> StepResult:
        policy = self.spec.reliability_policy
        return await self.reliability.execute_with_recovery(
            operation=lambda: self._do_execute(step, task, context),
            policy={**policy,
                    "max_retries": min(policy["max_retries"], step.max_retries)},
            context={"step": step.id, "agent": self.spec.agent_id})

    async def _do_execute(self, step, task, context) -> StepResult:
        if step.action == PlanStepAction.TOOL_CALL:
            return await self._run_tool_step(step, task)
        if step.action == PlanStepAction.HANDOFF:
            return await self._run_handoff(step, task, context)
        if step.action == PlanStepAction.REASON:
            return await self._run_reason_step(step, context)
        if step.action == PlanStepAction.ASK_HUMAN:
            return await self._run_ask_human(step, task)
        if step.action == PlanStepAction.FINISH:
            return StepResult(action="finish", summary="Done", output="")
        raise ValueError(f"Unknown action {step.action}")

    async def _run_tool_step(self, step, task) -> StepResult:
        if not step.tool_id:
            picked = await self.llm.complete_json(
                tool_selection_prompt(step.description,
                                      self.tools.list_tools(self.spec.tools),
                                      Context(working=[step.description])),
                temperature=0.1)
            if picked.get("tool_id"):
                step.tool_id = picked["tool_id"]
                step.arguments = picked.get("arguments", {}) or {}
            else:
                ans = picked.get("answer", "")
                return StepResult(action="reason", output=ans,
                                  summary=str(ans)[:200])
        tool = self.tools.get(step.tool_id)
        spec = tool.spec
        if spec.dangerous or step.tool_id in self.spec.requires_approval_for:
            decision = await self.approval.request(ApprovalRequest(
                task_id=task.id, agent_id=self.spec.agent_id,
                action=f"tool:{step.tool_id}", arguments=step.arguments,
                reason="dangerous tool"))
            if not decision.approved:
                return StepResult(action="tool_call", success=False,
                                  error=f"Denied by {decision.decided_by}",
                                  summary=f"Tool {step.tool_id} denied")
        res = await tool.invoke(step.arguments, sandbox=self.sandbox)
        if not res.success:
            raise RuntimeError(f"{res.error}")
        summary = _summarize(res.output)
        return StepResult(action="tool_call", output=res.output, summary=summary,
                          artifacts=res.artifacts, tools_used=[step.tool_id],
                          success=True)

    async def _run_handoff(self, step, task, context) -> StepResult:
        if not step.target_agent_id:
            raise ValueError("Handoff step missing target_agent_id")
        if self.orchestrator is None:
            raise RuntimeError("Orchestrator reference not wired; cannot hand off")
        sub = Task(description=step.subtask or step.description,
                   domain=task.domain, parent_task_id=task.id)
        target = self.orchestrator.agents.get(step.target_agent_id)
        if target is None:
            raise KeyError(f"Handoff target not registered: {step.target_agent_id}")
        await self._emit(f"task:{task.id}", "kernel.handoff",
                         {"from": self.spec.agent_id, "to": step.target_agent_id})
        self.state = AgentState.HANDOFF
        sub_result: TaskResult = await target.run(sub)
        self.state = AgentState.EXECUTING
        return StepResult(action="handoff", output=sub_result.summary,
                          summary=f"[{step.target_agent_id}] {sub_result.summary[:400]}",
                          success=sub_result.success, error=sub_result.error)

    async def _run_reason_step(self, step, context) -> StepResult:
        prompt = reasoning_prompt(step.prompt or step.description, context)
        text = await self.llm.complete(prompt, temperature=self.spec.temperature)
        return StepResult(action="reason", output=text,
                          summary=text[:400] if text else "")

    async def _run_ask_human(self, step, task) -> StepResult:
        decision = await self.approval.request(ApprovalRequest(
            task_id=task.id, agent_id=self.spec.agent_id, action="ask_human",
            arguments={"question": step.description}, reason=step.description))
        return StepResult(action="ask_human", output=decision.model_dump(),
                          summary=f"Human decision: {decision.approved} ({decision.note})")

    async def _verify_step(self, step, result, task) -> Verification:
        checklist = self.spec.reliability_policy.get("verification_checklist", "default")
        return await self.reliability.verify(
            checklist=checklist, evidence=result.to_evidence(),
            context={"step": step.model_dump(), "task": task.model_dump()})

    async def _reflect(self, step, result, verification) -> Reflection:
        pattern = self.reliability.match_failure(verification.failure_signals)
        prompt = (f"A step in a plan failed.\n"
                  f"Step: {step.description} (action={step.action.value})\n"
                  f"Result summary: {result.summary}\n"
                  f"Verification failure: {verification.reason}\n"
                  f"Known failure pattern: {pattern.name if pattern else 'unknown'}\n"
                  f"Explain in 2-3 sentences what went wrong and how the plan should change.")
        insight = await self.llm.complete(prompt, temperature=0.3)
        return Reflection(step_id=step.id, insight=insight,
                          pattern=pattern.name if pattern else None)

    async def _synthesize(self, task: Task, plan: Plan) -> str:
        if not self._step_outputs:
            return f"No output produced for task: {task.description}"
        if len(self._step_outputs) == 1:
            return self._step_outputs[0]
        prompt = synthesis_prompt(task, plan, self._step_outputs)
        return await self.llm.complete(prompt, temperature=0.2)

    async def _emit(self, stream_id: str, etype: str, data: Dict[str, Any]) -> None:
        await self.events.append(Event(stream_id=stream_id, type=etype,
                                       data={"agent_id": self.spec.agent_id, **data}))


def _summarize(output: Any, limit: int = 400) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output[:limit]
    try:
        return json.dumps(output, default=str)[:limit]
    except Exception:
        return str(output)[:limit]
'''

# ------------------------------------------------------------------ core/orchestrator.py
FILES["core/orchestrator.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/factory.py
FILES["core/factory.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ core/tracing.py
FILES["core/tracing.py"] = r'''from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger("agentos.tracing")
_tracer = None


def setup_tracing(service_name: str = "agentos", otlp_endpoint: Optional[str] = None):
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        else:
            exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        log.info("Tracing initialized for %s", service_name)
        return _tracer
    except Exception as e:
        log.warning("Tracing unavailable: %s", e)
        return None


def get_tracer():
    return _tracer
'''

# ------------------------------------------------------------------ core/worker.py
FILES["core/worker.py"] = r'''from __future__ import annotations
import asyncio, json, logging, os, signal
from typing import Any, Dict

log = logging.getLogger("agentos.worker")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def _handle_task(payload: Dict[str, Any], orchestrator) -> Dict[str, Any]:
    from .models import Task
    task = Task(id=payload["task_id"], description=payload["description"],
                domain=payload.get("domain", "general"))
    result = await orchestrator.run_task(task, payload["entry_agent_id"])
    return result.model_dump()


async def main():
    from .approval import ApprovalGate
    from .comm_bus import CommunicationBus
    from .events import EventStore
    from .factory import AgentFactory
    from .llm import LLMClient
    from .orchestrator import Orchestrator
    from .tools import ToolRegistry
    from demo.agents import build_demo_specs, register_demo_tools

    tools = ToolRegistry()
    register_demo_tools(tools)
    events = EventStore(dsn=os.getenv("DATABASE_URL"))
    await events.connect()
    comm = CommunicationBus()
    llm = LLMClient(model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                    api_key=os.getenv("OPENAI_API_KEY"))
    factory = AgentFactory(llm=llm, tools=tools, event_store=events,
                           comm_bus=comm,
                           approval_gate=ApprovalGate(auto_approve_in_dev=True))
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        log.warning("REDIS_URL not set; running a single demo task instead of the queue loop")
        from .models import Task
        r = await orch.run_task(Task(description="Say hello and finish.",
                                     domain="general"), "researcher-1")
        print(json.dumps(r.model_dump(), indent=2, default=str))
        return

    import redis.asyncio as aioredis  # type: ignore
    r = aioredis.from_url(redis_url, decode_responses=True)
    queue = os.getenv("TASK_QUEUE", "agentos:tasks")
    log.info("Worker listening on %s", queue)
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    while not stop.is_set():
        try:
            item = await r.blpop(queue, timeout=2)
        except Exception as e:
            log.error("Queue error: %s", e)
            await asyncio.sleep(1)
            continue
        if not item:
            continue
        _, raw = item
        try:
            payload = json.loads(raw)
            result = await _handle_task(payload, orch)
            await r.publish(f"agentos:results:{payload['task_id']}",
                            json.dumps(result, default=str))
        except Exception as e:
            log.exception("Task failed: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
'''

# ------------------------------------------------------------------ api/__init__.py
FILES["api/__init__.py"] = r'''"""AgentOS HTTP API."""
'''

# ------------------------------------------------------------------ api/main.py
FILES["api/main.py"] = r'''from __future__ import annotations
import asyncio, logging, os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from core.agent import AgentSpec
from core.approval import ApprovalGate
from core.comm_bus import CommunicationBus
from core.events import EventStore
from core.factory import AgentFactory
from core.guardrails import GuardrailChain
from core.llm import LLMClient
from core.models import Task
from core.orchestrator import Orchestrator
from core.reliability import ReliabilityEngine
from core.tools import Sandbox, ToolRegistry
from core.tracing import setup_tracing
from demo.agents import build_demo_specs, register_demo_tools

log = logging.getLogger("agentos.api")
logging.basicConfig(level=logging.INFO)

STATE: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing("agentos")
    tools = ToolRegistry()
    register_demo_tools(tools)
    events = EventStore(dsn=os.getenv("DATABASE_URL"))
    await events.connect()
    comm = CommunicationBus()
    approval = ApprovalGate(
        auto_approve_in_dev=os.getenv("AUTO_APPROVE", "true") == "true",
        timeout_seconds=int(os.getenv("APPROVAL_TIMEOUT", "600")))
    llm = LLMClient(model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                    api_key=os.getenv("OPENAI_API_KEY"))
    factory = AgentFactory(
        llm=llm, tools=tools, event_store=events, comm_bus=comm,
        reliability=ReliabilityEngine(), approval_gate=approval,
        guardrails=GuardrailChain(), sandbox=Sandbox(prefer_docker=False),
        memory_persist_dir=os.getenv("CHROMA_DIR"))
    orch = Orchestrator(events, comm)
    for spec in build_demo_specs():
        orch.register_agent(factory.build(spec))
    STATE.update(dict(tools=tools, events=events, comm=comm, approval=approval,
                      llm=llm, factory=factory, orch=orch))
    log.info("AgentOS ready with %d agents", len(orch.agents))
    yield
    STATE.clear()


app = FastAPI(title="AgentOS", version="1.0.0", lifespan=lifespan)


class TaskRequest(BaseModel):
    description: str
    entry_agent_id: str = "researcher-1"
    domain: str = "general"


class TaskResponse(BaseModel):
    task_id: str
    success: bool
    summary: str
    plan: Optional[dict] = None
    artifacts: List[dict] = Field(default_factory=list)
    error: Optional[str] = None


class ApprovalResolve(BaseModel):
    request_id: str
    approved: bool
    decided_by: str = "user"
    note: str = ""


@app.post("/v1/tasks", response_model=TaskResponse)
async def submit_task(req: TaskRequest):
    orch: Orchestrator = STATE["orch"]
    if req.entry_agent_id not in orch.agents:
        raise HTTPException(404, f"Unknown entry agent: {req.entry_agent_id}")
    task = Task(description=req.description, domain=req.domain)
    result = await orch.run_task(task, req.entry_agent_id)
    return TaskResponse(task_id=result.task_id, success=result.success,
                        summary=result.summary,
                        plan=result.plan.model_dump() if result.plan else None,
                        artifacts=result.artifacts, error=result.error)


@app.get("/v1/tasks/{task_id}/events")
async def get_task_events(task_id: str):
    events: EventStore = STATE["events"]
    evs = await events.get_events(f"task:{task_id}")
    return {"events": [e.model_dump() for e in evs]}


@app.get("/v1/agents")
async def list_agents():
    orch: Orchestrator = STATE["orch"]
    return {"agents": orch.list_agents()}


@app.post("/v1/agents")
async def create_agent(spec: AgentSpec):
    orch: Orchestrator = STATE["orch"]
    factory: AgentFactory = STATE["factory"]
    if spec.agent_id in orch.agents:
        raise HTTPException(409, f"Agent {spec.agent_id} already exists")
    kernel = factory.build(spec)
    orch.register_agent(kernel)
    return {"agent_id": spec.agent_id, "status": "registered"}


@app.get("/v1/agents/{agent_id}")
async def get_agent(agent_id: str):
    orch: Orchestrator = STATE["orch"]
    a = orch.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "Agent not found")
    return {"spec": a.spec.model_dump(), "state": a.state.value}


@app.get("/v1/tools")
async def list_tools():
    tools: ToolRegistry = STATE["tools"]
    return {"tools": [t.model_dump() for t in tools.list_tools()]}


@app.get("/v1/approvals")
async def list_approvals():
    gate: ApprovalGate = STATE["approval"]
    return {"pending": [r.model_dump() for r in gate.requests.values()]}


@app.post("/v1/approvals/resolve")
async def resolve_approval(req: ApprovalResolve):
    gate: ApprovalGate = STATE["approval"]
    ok = gate.resolve(req.request_id, req.approved, req.decided_by, req.note)
    if not ok:
        raise HTTPException(404, "Approval request not found or already resolved")
    return {"status": "resolved", "request_id": req.request_id}


@app.get("/health")
async def health():
    orch: Orchestrator = STATE.get("orch")
    return {"status": "ok", "agents": len(orch.agents) if orch else 0}


@app.websocket("/ws/tasks/{task_id}")
async def ws_task_events(websocket: WebSocket, task_id: str):
    await websocket.accept()
    events: EventStore = STATE["events"]
    last_version = 0
    try:
        while True:
            new = await events.get_events(f"task:{task_id}", after_version=last_version)
            for e in new:
                await websocket.send_json(e.model_dump())
                last_version = e.version
            if new and new[-1].type in ("kernel.completed", "kernel.failed", "kernel.timeout"):
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
'''

# ------------------------------------------------------------------ demo/__init__.py
FILES["demo/__init__.py"] = r'''"""Demo agents, tools, and scripts."""
'''

# ------------------------------------------------------------------ demo/agents.py
FILES["demo/agents.py"] = r'''from __future__ import annotations
import ast, io, json, sys, urllib.parse, urllib.request
from typing import Any, Dict, List

from core.agent import AgentRole, AgentSpec
from core.tools import FunctionTool, ToolSpec, ToolRegistry


def web_search(query: str) -> str:
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
        "q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
        abstract = data.get("AbstractText") or ""
        related = [t.get("Text") for t in data.get("RelatedTopics", [])
                   if isinstance(t, dict)][:5]
        return json.dumps({"query": query, "abstract": abstract, "related": related,
                           "source": data.get("AbstractURL", "https://duckduckgo.com")})
    except Exception as e:
        return json.dumps({"query": query, "error": str(e), "source": "duckduckgo"})


def run_python(code: str) -> Dict[str, Any]:
    safe_globals = {
        "__builtins__": {
            "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
            "range": range, "print": print, "int": int, "float": float,
            "str": str, "list": list, "dict": dict, "set": set, "tuple": tuple,
            "bool": bool, "enumerate": enumerate, "zip": zip, "map": map,
            "filter": filter, "sorted": sorted, "any": any, "all": all,
            "round": round, "pow": pow, "divmod": divmod, "isinstance": isinstance,
        },
        "math": __import__("math"),
        "statistics": __import__("statistics"),
    }
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        tree = ast.parse(code, mode="exec")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError("imports are disabled")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
               node.func.id in {"exec", "eval", "open", "__import__", "compile"}:
                raise ValueError(f"{node.func.id} is disabled")
        exec(compile(tree, "<sandbox>", "exec"), safe_globals, {})
        return {"stdout": buf.getvalue(), "ok": True}
    except Exception as e:
        return {"stdout": buf.getvalue(), "ok": False,
                "error": f"{type(e).__name__}: {e}"}
    finally:
        sys.stdout = old


def write_file(path: str, content: str) -> str:
    import os, tempfile
    base = tempfile.gettempdir()
    safe = os.path.normpath(os.path.join(base, path)).replace("..", "")
    d = os.path.dirname(safe)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(safe, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {safe}"


def register_demo_tools(registry: ToolRegistry) -> None:
    registry.register(FunctionTool(
        ToolSpec(tool_id="web_search", name="web_search",
                 description="Search the web via DuckDuckGo Instant Answer API.",
                 parameters={"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}),
        web_search))
    registry.register(FunctionTool(
        ToolSpec(tool_id="run_python", name="run_python",
                 description="Execute a Python snippet in a sandboxed namespace. No imports.",
                 parameters={"type": "object",
                             "properties": {"code": {"type": "string"}},
                             "required": ["code"]},
                 requires_sandbox=True),
        run_python))
    registry.register(FunctionTool(
        ToolSpec(tool_id="write_file", name="write_file",
                 description="Write content to a temp file.",
                 parameters={"type": "object",
                             "properties": {"path": {"type": "string"},
                                            "content": {"type": "string"}},
                             "required": ["path", "content"]},
                 dangerous=True),
        write_file))


def build_demo_specs() -> List[AgentSpec]:
    return [
        AgentSpec(agent_id="coordinator-1", name="Coordinator",
                  role=AgentRole.COORDINATOR,
                  instructions=("You are the coordinator. Break complex tasks into "
                                "subtasks and delegate to specialists: researcher-1, "
                                "coder-1, verifier-1. Finish with a synthesized answer."),
                  model="gpt-4o-mini", tools=[],
                  handoff_targets=["researcher-1", "coder-1", "verifier-1"],
                  reliability_policy={"max_retries": 2, "backoff_base_ms": 400,
                                      "verification_checklist": "default"}),
        AgentSpec(agent_id="researcher-1", name="Researcher",
                  role=AgentRole.RESEARCHER,
                  instructions=("You are a meticulous researcher. Use web_search to "
                                "gather facts. Cite sources. Hand off to coder-1 when "
                                "code is required."),
                  model="gpt-4o-mini", tools=["web_search"],
                  handoff_targets=["coder-1"],
                  reliability_policy={"max_retries": 3, "backoff_base_ms": 500,
                                      "verification_checklist": "research"}),
        AgentSpec(agent_id="coder-1", name="Coder", role=AgentRole.CODER,
                  instructions=("You are a senior engineer. Use run_python to prototype. "
                                "Hand off to verifier-1 when done."),
                  model="gpt-4o-mini", tools=["run_python", "write_file"],
                  handoff_targets=["verifier-1"],
                  requires_approval_for=["write_file"],
                  reliability_policy={"max_retries": 3, "backoff_base_ms": 500,
                                      "verification_checklist": "code"}),
        AgentSpec(agent_id="verifier-1", name="Verifier", role=AgentRole.VERIFIER,
                  instructions=("You are an adversarial verifier. Try to falsify the "
                                "proposed answer. Use run_python to test edge cases."),
                  model="gpt-4o-mini", tools=["run_python"],
                  reliability_policy={"max_retries": 1, "verification_checklist": "code"}),
    ]
'''

# ------------------------------------------------------------------ demo/run.py
FILES["demo/run.py"] = r'''from __future__ import annotations
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
'''

# ------------------------------------------------------------------ tests/__init__.py
FILES["tests/__init__.py"] = r'''"""AgentOS tests."""
'''

# ------------------------------------------------------------------ tests/conftest.py
FILES["tests/conftest.py"] = r'''import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
'''

# ------------------------------------------------------------------ tests/test_tools.py
FILES["tests/test_tools.py"] = r'''import pytest
from core.tools import FunctionTool, ToolSpec, ToolRegistry, Sandbox


def test_registry_register_and_get():
    reg = ToolRegistry()
    t = FunctionTool(ToolSpec(tool_id="add", name="add", description="add two numbers"),
                     lambda a, b: a + b)
    reg.register(t)
    assert reg.get("add").spec.tool_id == "add"
    assert len(reg.list_tools()) == 1


@pytest.mark.asyncio
async def test_function_tool_invoke():
    t = FunctionTool(ToolSpec(tool_id="add", name="add", description=""),
                     lambda a, b: a + b)
    r = await t.invoke({"a": 2, "b": 3})
    assert r.success and r.output == 5


@pytest.mark.asyncio
async def test_sandbox_inproc_captures_output():
    def fn(x):
        print("hello")
        return x * 2
    sb = Sandbox(prefer_docker=False)
    out = await sb.run_callable(fn, {"x": 4}, timeout=5)
    assert out["result"] == 8
    assert "hello" in out["stdout"]
'''

# ------------------------------------------------------------------ tests/test_reliability.py
FILES["tests/test_reliability.py"] = r'''import pytest
from core.reliability import ReliabilityEngine


def test_match_rate_limit():
    r = ReliabilityEngine()
    p = r.match_failure(["HTTP 429 Too Many Requests"])
    assert p is not None and p.name == "rate_limit"


@pytest.mark.asyncio
async def test_verification_default_passes():
    r = ReliabilityEngine()
    v = await r.verify("default", {"success": True, "output": "hello"}, {})
    assert v.passed


@pytest.mark.asyncio
async def test_verification_empty_fails():
    r = ReliabilityEngine()
    v = await r.verify("default", {"success": True, "output": "  "}, {})
    assert not v.passed


@pytest.mark.asyncio
async def test_retry_recovers_from_transient():
    r = ReliabilityEngine()
    attempts = {"n": 0}

    async def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("timeout")
        return "ok"

    res = await r.execute_with_recovery(
        op, {"max_retries": 5, "backoff_base_ms": 10}, {"step": "s"})
    assert res == "ok" and attempts["n"] == 3
'''

# ------------------------------------------------------------------ tests/test_kernel.py
FILES["tests/test_kernel.py"] = r'''import json
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
'''

# ------------------------------------------------------------------ pyproject.toml
FILES["pyproject.toml"] = r'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "agentos"
version = "1.0.0"
description = "Autonomous multi-agent operating system"
requires-python = ">=3.10"
dependencies = [
  "pydantic>=2.6",
  "litellm>=1.40",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "httpx>=0.27",
  "chromadb>=0.4.22",
  "asyncpg>=0.29",
  "redis>=5.0",
  "opentelemetry-sdk>=1.24",
  "opentelemetry-exporter-otlp>=1.24",
  "websockets>=12.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4", "mypy>=1.10"]

[tool.setuptools.packages.find]
include = ["core*", "api*", "demo*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
filterwarnings = ["ignore::DeprecationWarning"]

[tool.ruff]
line-length = 100
'''

# ------------------------------------------------------------------ Dockerfile
FILES["Dockerfile"] = r'''FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY core ./core
COPY api ./api
COPY demo ./demo
RUN pip install --no-cache-dir -e .
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
'''

# ------------------------------------------------------------------ docker-compose.yml
FILES["docker-compose.yml"] = r'''version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agents
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: ${DB_PASSWORD:-agentpass}
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 5s

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  api:
    build: .
    command: uvicorn api.main:app --host 0.0.0.0 --port 8080
    environment:
      DATABASE_URL: postgresql://agent:${DB_PASSWORD:-agentpass}@postgres/agents
      REDIS_URL: redis://redis:6379
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      LLM_MODEL: ${LLM_MODEL:-gpt-4o-mini}
      AUTO_APPROVE: "true"
    ports: ["8080:8080"]
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_started}

  worker:
    build: .
    command: python -m core.worker
    environment:
      DATABASE_URL: postgresql://agent:${DB_PASSWORD:-agentpass}@postgres/agents
      REDIS_URL: redis://redis:6379
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      LLM_MODEL: ${LLM_MODEL:-gpt-4o-mini}
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_started}
    deploy:
      replicas: 2

volumes:
  pgdata:
'''

# ------------------------------------------------------------------ .gitignore
FILES[".gitignore"] = r'''.venv/
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.env
chroma_data/
*.log
'''

# ------------------------------------------------------------------ .env.example
FILES[".env.example"] = r'''# Copy to .env and fill in.
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
DB_PASSWORD=agentpass
AUTO_APPROVE=true
'''

# ------------------------------------------------------------------ README.md
FILES["README.md"] = r'''# AgentOS

Autonomous multi-agent operating system with a Plan-Execute-Verify kernel,
handoff-based delegation, 4-layer memory, sandboxed tools, adversarial
verification, human-in-the-loop approvals, and event-sourced state.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export OPENAI_API_KEY=sk-...
pytest -q
python -m demo.run
uvicorn api.main:app --reload
'''


from pathlib import Path as _Path
_target = _Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
_target.mkdir(parents=True, exist_ok=True)
for _rel, _content in FILES.items():
    _p = _target / _rel
    if not _p.exists():
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(_content, encoding="utf-8")
print(f"AgentOS generated: {len(FILES)} files -> {_target}")
