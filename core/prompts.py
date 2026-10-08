from __future__ import annotations
import json
from typing import Any, List
from .models import Context, Plan, Reflection, Task
from .expert_layer import context as expert_context


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
    return f"""{PLANNER_SYSTEM}\n{expert_context(task.description)}

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
    return f"""{REASONING_SYSTEM}\n{expert_context(step_prompt)}

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
    return f"""{SYNTHESIS_SYSTEM}\n{expert_context(task.description)}

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
