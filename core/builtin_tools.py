
from .tools import FunctionTool, ToolRegistry, ToolSpec
from .web_research import research
from .media_pipeline import extract_document, video_extract, video_analyze
from .artifact_validator import validate_all

def register_builtins(registry: ToolRegistry):
    registry.register(FunctionTool(ToolSpec(
        tool_id="web_research",name="web_research",
        description="Live web research with retrieved evidence and source URLs.",
        parameters={"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","minimum":1,"maximum":8}},"required":["query"]}), research))
    registry.register(FunctionTool(ToolSpec(
        tool_id="read_document",name="read_document",
        description="Extract text from PDF, DOCX and text documents.",
        parameters={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}), lambda path: extract_document(path)))
    registry.register(FunctionTool(ToolSpec(
        tool_id="analyze_video",name="analyze_video",
        description="Extract representative frames from a video.",
        parameters={"type":"object","properties":{"path":{"type":"string"},"frames":{"type":"integer"}},"required":["path"]}), video_analyze))
    registry.register(FunctionTool(ToolSpec(
        tool_id="validate_artifacts",name="validate_artifacts",
        description="Verify artifact files and URLs before completion.",
        parameters={"type":"object","properties":{"artifacts":{"type":"array"}},"required":["artifacts"]}), lambda artifacts: validate_all(artifacts)))
