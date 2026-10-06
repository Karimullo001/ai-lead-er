from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text(encoding="utf-8")

if "HOSTED_RUNTIME" not in s:
    marker = "STATE: Dict[str, Any] = {}"
    if marker not in s:
        raise SystemExit("STATE marker not found")
    s = s.replace(marker, marker + "\nHOSTED_RUNTIME: Dict[str, Any] = {}", 1)

if "start_hosted_runtime" in s:
    p.write_text(s, encoding="utf-8")
    print("already patched")
    raise SystemExit(0)

pattern = re.compile(
    r'(    STATE\\.update\\(dict\\(tools=tools, events=events, comm=comm, approval=approval,\\n'
    r'                      llm=llm, factory=factory, orch=orch\\)\\n'
    r'    log\\.info\\("AgentOS ready with %d agents", len\\(orch\\.agents\\)\\)\\n)'
    r'(    yield\\n    STATE\\.clear\\(\\))'
)

replacement = r'''\1    if os.getenv("EMBEDDED_RUNTIME", "true").lower() == "true":
        from hosted_runtime import start_hosted_runtime
        HOSTED_RUNTIME.update(await start_hosted_runtime())
\n\2'''

if not pattern.search(s):
    raise SystemExit("lifespan block not found")

s = pattern.sub(replacement, s, count=1)
# Replace shutdown tail with graceful embedded-runtime shutdown.
s = s.replace(
    '    yield\n    STATE.clear()',
    '    yield\n    if HOSTED_RUNTIME:\n        from hosted_runtime import stop_hosted_runtime\n        await stop_hosted_runtime(HOSTED_RUNTIME)\n        HOSTED_RUNTIME.clear()\n    STATE.clear()',
    1,
)
p.write_text(s, encoding="utf-8")
print("ok")
