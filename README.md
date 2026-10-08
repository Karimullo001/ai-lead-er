# AgentOS

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
