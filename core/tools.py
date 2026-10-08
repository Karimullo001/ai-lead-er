from __future__ import annotations
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
