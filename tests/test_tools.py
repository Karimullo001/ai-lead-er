import pytest
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
