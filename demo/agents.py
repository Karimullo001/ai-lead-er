from __future__ import annotations
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
                  model="gpt-4o-mini", tools=["web_search", "analyze_video", "read_document"],
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
