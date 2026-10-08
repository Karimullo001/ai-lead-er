from __future__ import annotations
import io
import json
import logging
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from core.web_research import research

log = logging.getLogger("agentos.website_engine")


def research_trends_for_project(topic: str) -> str:
    """Fetch real-time web design & tech trends for the requested topic."""
    try:
        q = f"modern web design UI UX layout trends 2026 {topic}"
        results = research(q, max_results=3)
        return results or ""
    except Exception as e:
        log.warning("Web trend research failed: %s", e)
        return ""


async def generate_modern_single_page(
    prompt: str,
    router: Any,
    trends_info: str = "",
) -> str:
    """Generate a production-grade, stunning, interactive single-file web app."""
    trends_context = f"\n\nCURRENT DESIGN & TECH TRENDS (2026):\n{trends_info}" if trends_info else ""

    system_prompt = (
        "You are an elite, award-winning Principal Frontend Architect and UI/UX Designer. "
        "Your task is to build a COMPLETE, production-ready, beautiful single-file HTML5 application. "
        "Strict Requirements:\n"
        "1. Modern Visual Identity: Use Tailwind CSS via CDN (<script src='https://cdn.tailwindcss.com'></script>), "
        "clean modern color palette (deep slate, vibrant indigo/emerald accents, subtle gradients, glassmorphism).\n"
        "2. Icons & Typography: Include Lucide icons (<script src='https://unpkg.com/lucide@latest'></script>) or FontAwesome, "
        "and Google Fonts (Inter / Plus Jakarta Sans).\n"
        "3. Real Interactivity & State: Write fully functional JavaScript (tabs, filters, modals, search, dynamic calculation, local storage persistence, theme switch).\n"
        "4. Authentic Copy: Zero placeholders, zero 'Lorem ipsum'. Write authentic, compelling, professional copy tailored to the user's domain.\n"
        "5. Responsive & Accessible: Flawless on mobile, tablet, and desktop.\n"
        "6. Return ONLY the raw valid HTML code without markdown fences (no ```html, just <!DOCTYPE html> to </html>)."
        f"{trends_context}"
    )

    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Build this complete, impressive web application: {prompt}"},
    ]

    comp = await router.complete(msgs, task_type="coding", max_tokens=4000, temperature=0.3)
    raw = (comp.text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


async def generate_fullstack_project_archive(
    prompt: str,
    router: Any,
    trends_info: str = "",
) -> Tuple[bytes, str]:
    """
    Generate a complete full-stack application (Frontend + Backend + DB schema + config)
    packaged inside an in-memory ZIP archive.
    Returns: (zip_bytes, project_summary_markdown)
    """
    trends_context = f"\n\nTrends context:\n{trends_info}" if trends_info else ""

    system_prompt = (
        "You are a Principal Full-Stack Software Architect. "
        "Create a COMPLETE full-stack project codebase for the user's specification. "
        "The project MUST have both a modern Frontend (React/TypeScript or Next.js layout) and a robust Backend (FastAPI Python or Node.js Express). "
        "Output the files in a JSON object with this exact structure:\n"
        "{\n"
        '  "summary": "Short 2-paragraph overview of architecture and features",\n'
        '  "files": [\n'
        '    {"path": "backend/main.py", "content": "..."},\n'
        '    {"path": "backend/requirements.txt", "content": "..."},\n'
        '    {"path": "backend/models.py", "content": "..."},\n'
        '    {"path": "frontend/package.json", "content": "..."},\n'
        '    {"path": "frontend/src/App.tsx", "content": "..."},\n'
        '    {"path": "frontend/src/components/Dashboard.tsx", "content": "..."},\n'
        '    {"path": "frontend/index.html", "content": "..."},\n'
        '    {"path": "README.md", "content": "..."},\n'
        '    {"path": ".env.example", "content": "..."}\n'
        "  ]\n"
        "}\n"
        "Requirements:\n"
        "1. Write complete, working, bug-free code for every file (no truncation, no '// TODO').\n"
        "2. Backend must have working REST APIs, input validation, and data persistence models.\n"
        "3. Frontend must have responsive components, Tailwind CSS styling, and fetch calls to the backend APIs.\n"
        "4. Return ONLY valid JSON."
        f"{trends_context}"
    )

    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Architect and code this full-stack project: {prompt}"},
    ]

    comp = await router.complete(msgs, task_type="coding", max_tokens=4000, temperature=0.2)
    raw = (comp.text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    files_dict: Dict[str, str] = {}
    summary = "Full-stack project generated by AgentOS."

    try:
        data = json.loads(raw)
        summary = data.get("summary", summary)
        for f in data.get("files", []):
            if "path" in f and "content" in f:
                files_dict[f["path"]] = f["content"]
    except Exception as e:
        log.warning("JSON parse failed on full-stack generator: %s. Attempting heuristic file extraction.", e)
        # Fallback file extraction using regex patterns like === FILE: path ===
        matches = re.findall(r"(?:###|===)\s*(?:FILE:)?\s*([a-zA-Z0-9_\-./]+)\s*(?:###|===)?\n([\s\S]*?)(?=(?:###|===)|$)", raw)
        for path, content in matches:
            files_dict[path.strip()] = content.strip()

    # Self-healing: Ensure essential files exist
    if not any(k.startswith("backend/") for k in files_dict):
        files_dict["backend/main.py"] = (
            "from fastapi import FastAPI\nfrom fastapi.middleware.cors import CORSMiddleware\n\n"
            "app = FastAPI(title='AgentOS Generated API')\n"
            "app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])\n\n"
            "@app.get('/api/health')\ndef health(): return {'status': 'ok'}\n"
        )
        files_dict["backend/requirements.txt"] = "fastapi>=0.110.0\nuvicorn>=0.29.0\npydantic>=2.6.0\n"

    if not files_dict.get("README.md"):
        files_dict["README.md"] = f"# {prompt}\n\nGenerated by AgentOS Polymath Engine (2026).\n\n## Quickstart\n\n### Backend\n```bash\ncd backend\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n\n### Frontend\n```bash\ncd frontend\nnpm install\nnpm run dev\n```\n"

    # Zip the project
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fpath, content in files_dict.items():
            zf.writestr(fpath, content)

    return zip_buf.getvalue(), summary
