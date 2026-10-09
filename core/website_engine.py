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
        q = f"modern web design UI UX layout trends 2026 Figma Canva {topic}"
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
    """
    Generate an elite, production-grade, stunning Silicon Valley web application
    following Figma & Canva high-end design systems.
    """
    trends_context = f"\n\nCURRENT DESIGN & TECH TRENDS (2026):\n{trends_info}" if trends_info else ""

    system_prompt = (
        "You are an elite, world-class Principal UI/UX Architect and Design Engineer (Figma Design Lead & Linear.app / Apple caliber). "
        "Create a COMPLETE, stunning, production-ready, interactive single-file HTML5 web application based on the user's specification.\n\n"
        "STRICT DESIGN & ENGINEERING RULES:\n"
        "1. Visual Excellence & Aesthetics:\n"
        "   - Include Tailwind CSS CDN (<script src='https://cdn.tailwindcss.com'></script>).\n"
        "   - Include Lucide Icons (<script src='https://unpkg.com/lucide@latest'></script>).\n"
        "   - Include Google Fonts (<link href='https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap' rel='stylesheet'>).\n"
        "   - Apply modern dark luxury aesthetic: deep obsidian/slate backgrounds (`bg-[#0a0d14]`, `bg-slate-950`), glowing gradient text (`bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 via-indigo-400 to-fuchsia-500`), subtle borders (`border border-white/10`), and glassmorphism (`backdrop-blur-xl bg-white/[0.03]`).\n\n"
        "2. Comprehensive Layout Structure:\n"
        "   - Sticky Modern Navbar with logo, links, and glowing CTA button.\n"
        "   - High-Converting Hero Section with animated badges, headline, subheadline, dual action buttons, and live stats/metrics.\n"
        "   - Dynamic Interactive Feature Grid with icon cards, hover glow effects, and interactive filter tabs.\n"
        "   - Live Interactive Functional Widget (e.g. dynamic calculator, search filter, pricing plan toggle, or live preview simulator).\n"
        "   - Customer Testimonials / Social Proof marquee or cards.\n"
        "   - Interactive FAQ accordion or Contact Modal dialog.\n"
        "   - Modern Footer with newsletter subscribe form and social links.\n\n"
        "3. Working JavaScript Logic:\n"
        "   - Write pure, bug-free, vanilla JavaScript to power all interactive elements (theme toggles, modal open/close, calculators, filters, form submissions with toast notifications, Lucide icon initialization via `lucide.createIcons()`).\n"
        "   - Zero dummy lorem ipsum: write authentic, compelling, professional Silicon Valley copy.\n"
        "   - Fully responsive on mobile, tablet, and widescreen.\n\n"
        "4. Return ONLY valid, raw HTML from `<!DOCTYPE html>` to `</html>` without markdown code fences."
        f"{trends_context}"
    )

    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Build this world-class, stunning web application: {prompt}"},
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
    Generate a complete full-stack application (FastAPI Backend + React Frontend + DB schema)
    packaged inside an in-memory ZIP archive.
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
