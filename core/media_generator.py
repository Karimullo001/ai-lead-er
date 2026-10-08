from __future__ import annotations
import asyncio
import base64
import io
import json
import logging
import os
import random
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple
import httpx

from core.video_pipeline import generate_video_pipeline
from core.website_engine import (
    generate_modern_single_page,
    generate_fullstack_project_archive,
    research_trends_for_project,
)

log = logging.getLogger("agentos.media_gen")
SSL_CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _is_valid_image(data: bytes) -> bool:
    """Validate image magic bytes (PNG, JPEG, WebP, GIF)."""
    if not data or len(data) < 2048:
        return False
    # PNG: \x89PNG, JPEG: \xff\xd8\xff, WebP: RIFF...WEBP, GIF: GIF87a/GIF89a
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return True
    return False


def _enhance_image_prompt(prompt: str) -> str:
    """Refine user prompt based on visual style intent."""
    p_lower = prompt.lower()
    clean = prompt.strip()

    # Style: UI / Web mockup
    if any(k in p_lower for k in ("ui", "ux", "landing page", "web design", "app design", "dashboard", "mockup", "interface")):
        return f"{clean}, modern clean UI UX design mockup, Figma Dribbble aesthetic, minimalist, sharp typography, glassmorphism, 8k resolution"

    # Style: Logo / Icon
    if any(k in p_lower for k in ("logo", "icon", "emblem", "brand mark", "symbol")):
        return f"{clean}, clean vector logo design, minimalist, iconic graphic emblem, modern branding, crisp edges, vector art"

    # Style: Scientific / Diagram / Infographic
    if any(k in p_lower for k in ("diagram", "infographic", "schematic", "chart", "scientific", "architecture diagram")):
        return f"{clean}, professional technical diagram, high clarity, clean lines, educational infographic, isometric schematic"

    # Style: Photo / Realism
    if any(k in p_lower for k in ("photo", "realistic", "realism", "portrait", "cinema", "8k", "shot")):
        return f"{clean}, photorealistic, highly detailed 8k photography, natural cinematic lighting, sharp focus, masterwork"

    # Default artistic enhancement
    return f"{clean}, high quality, masterwork, detailed, vivid colors, cinematic lighting"


async def _generate_image_gemini_imagen(prompt: str) -> Optional[bytes]:
    """Generate image using Google Imagen 3 API."""
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_key:
        return None

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={gemini_key}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "1:1",
                "outputMimeType": "image/jpeg",
            },
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                predictions = data.get("predictions", [])
                if predictions and "bytesBase64Encoded" in predictions[0]:
                    img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
                    if _is_valid_image(img_bytes):
                        log.info("Image generated via Google Imagen 3 (%d bytes)", len(img_bytes))
                        return img_bytes
            else:
                log.warning("Google Imagen error %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Google Imagen failed: %s", e)
    return None


async def _generate_image_openai_dalle(prompt: str) -> Optional[bytes]:
    """Generate image using OpenAI DALL-E 3."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "dall-e-3",
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "response_format": "b64_json",
                },
            )
            if resp.status_code == 200:
                b64_data = resp.json()["data"][0]["b64_json"]
                img_bytes = base64.b64decode(b64_data)
                if _is_valid_image(img_bytes):
                    log.info("Image generated via OpenAI DALL-E 3 (%d bytes)", len(img_bytes))
                    return img_bytes
            else:
                log.warning("DALL-E 3 error %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("DALL-E 3 generation failed: %s", e)
    return None


async def _generate_image_pollinations_flux(prompt: str) -> Optional[bytes]:
    """Generate image using Pollinations AI (Flux / SDXL model)."""
    try:
        seed = random.randint(1000, 999999)
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&nologo=true&model=flux"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        loop = asyncio.get_event_loop()

        def _fetch():
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=40) as r:
                return r.read()

        img_bytes = await loop.run_in_executor(None, _fetch)
        if _is_valid_image(img_bytes):
            log.info("Image generated via Pollinations Flux (%d bytes)", len(img_bytes))
            return img_bytes
    except Exception as e:
        log.warning("Pollinations image generation failed: %s", e)
    return None


async def generate_image(prompt: str) -> Optional[bytes]:
    """
    Generate high-quality image with robust multi-provider fallback:
    1. Google Imagen 3
    2. OpenAI DALL-E 3
    3. Pollinations Flux
    """
    clean_p = prompt.strip()
    if not clean_p:
        return None

    enhanced_prompt = _enhance_image_prompt(clean_p)

    # 1. Google Imagen 3
    img = await _generate_image_gemini_imagen(enhanced_prompt)
    if img:
        return img

    # 2. OpenAI DALL-E 3
    img = await _generate_image_openai_dalle(clean_p)
    if img:
        return img

    # 3. Pollinations Flux (Fast, high-fidelity fallback)
    img = await _generate_image_pollinations_flux(enhanced_prompt)
    if img:
        return img

    return None


async def generate_video(
    prompt: str,
    on_status: Optional[Callable[[str], Any]] = None,
) -> Optional[bytes]:
    """Generate actual, valid MP4 video using the robust multi-provider video pipeline."""
    return await generate_video_pipeline(prompt, on_status)


async def generate_website(
    prompt: str,
    router: Any,
    on_status: Optional[Callable[[str], Any]] = None,
) -> str:
    """Generate a modern single-page web app with live trend research."""
    if on_status:
        await on_status("🔍 2026-yilgi zamonaviy dizayn trendlari va UI/UX qoidalari tahlil qilinmoqda...")
    trends = research_trends_for_project(prompt)
    if on_status:
        await on_status("💻 HTML5, Tailwind CSS va JavaScript komponentlari kodlanmoqda...")
    return await generate_modern_single_page(prompt, router, trends)


async def generate_fullstack(
    prompt: str,
    router: Any,
    on_status: Optional[Callable[[str], Any]] = None,
) -> Tuple[bytes, str]:
    """Generate a complete full-stack project archive (.zip) with live trend research."""
    if on_status:
        await on_status("🔍 Real-time arxitektura va zamonaviy kutubxonalar tahlil qilinmoqda...")
    trends = research_trends_for_project(prompt)
    if on_status:
        await on_status("⚙️ Frontend va Backend fayllari, API yo'nalishlari va modellar yaratilmoqda...")
    return await generate_fullstack_project_archive(prompt, router, trends)


async def generate_presentation_html(topic: str, router: Any) -> str:
    """Generate an executive slide deck powered by Reveal.js in standalone HTML."""
    sys_prompt = (
        "You are a top-tier executive presentation designer (McKinsey / Apple keynote style). "
        "Create a standalone, interactive slide presentation in a single HTML file using Reveal.js CDN. "
        "Requirements:\n"
        "1. Include Reveal.js CDN (<link rel='stylesheet' href='https://cdnjs.cloudflare.com/ajax/libs/reveal.js/4.5.0/reveal.min.css'>, "
        "<link rel='stylesheet' href='https://cdnjs.cloudflare.com/ajax/libs/reveal.js/4.5.0/theme/black.min.css'>, "
        "<script src='https://cdnjs.cloudflare.com/ajax/libs/reveal.js/4.5.0/reveal.min.js'></script>).\n"
        "2. 6 to 10 slides: Title slide, The Problem, The Vision/Opportunity, Core Solution/Architecture, Traction/Market, Financials/Strategy, Call to Action.\n"
        "3. Beautiful executive visual styling, high contrast, clean typography, structured bullet points, metrics, and quotes.\n"
        "4. Initialize Reveal with Reveal.initialize({ hash: true, slideNumber: true, transition: 'slide' }).\n"
        "5. Return ONLY raw HTML code without markdown fences."
    )
    msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Create this executive slide deck: {topic}"}]
    comp = await router.complete(msgs, task_type="coding", max_tokens=3500, temperature=0.3)
    raw = (comp.text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()
