from pathlib import Path

p = Path("api/main.py")
s = p.read_text(encoding="utf-8")

if "HOSTED_RUNTIME" not in s:
    marker = "STATE: Dict[str, Any] = {}"
    if marker not in s:
        raise SystemExit("STATE marker not found")
    s = s.replace(marker, marker + "\nHOSTED_RUNTIME: Dict[str, Any] = {}", 1)

if "start_hosted_runtime" not in s:
    old = """    STATE.update(dict(tools=tools, events=events, comm=comm, approval=approval,
                      llm=llm, factory=factory, orch=orch))
    log.info(\"AgentOS ready with %d agents\", len(orch.agents))
    yield
    STATE.clear()"""
    new = """    STATE.update(dict(tools=tools, events=events, comm=comm, approval=approval,
                      llm=llm, factory=factory, orch=orch))
    log.info(\"AgentOS ready with %d agents\", len(orch.agents))
    if os.getenv(\"EMBEDDED_RUNTIME\", \"true\").lower() == \"true\":
        from hosted_runtime import start_hosted_runtime
        HOSTED_RUNTIME.update(await start_hosted_runtime())
    yield
    if HOSTED_RUNTIME:
        from hosted_runtime import stop_hosted_runtime
        await stop_hosted_runtime(HOSTED_RUNTIME)
        HOSTED_RUNTIME.clear()
    STATE.clear()"""
    if old not in s:
        raise SystemExit("lifespan block not found")
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("ok")
