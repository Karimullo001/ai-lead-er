from __future__ import annotations
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
