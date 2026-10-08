from __future__ import annotations
from typing import Any, Dict, Optional
from .models import Plan, Task


class CheckpointState:
    """Serializable snapshot of an in-flight task."""

    @staticmethod
    def serialize(task: Task, plan: Optional[Plan], step_index: int,
                  step_outputs: list, iteration: int, replans: int,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "task": task.model_dump(),
            "plan": plan.model_dump() if plan else None,
            "step_index": step_index,
            "step_outputs": step_outputs,
            "iteration": iteration,
            "replans": replans,
            "extra": extra or {},
        }

    @staticmethod
    def deserialize(state: Dict[str, Any]):
        t = Task(**state["task"])
        p = Plan(**state["plan"]) if state.get("plan") else None
        return t, p, state.get("step_index", 0), state.get("step_outputs", []), \
               state.get("iteration", 0), state.get("replans", 0)
