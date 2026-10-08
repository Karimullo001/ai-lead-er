from __future__ import annotations
import asyncio
import io
import json
import logging
import os
import random
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional
import httpx

log = logging.getLogger("agentos.media_gen")
SSL_CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def generate_image(prompt: str) -> Optional[bytes]:
    """Generate high-quality image using OpenAI DALL-E 3 or Pollinations Flux."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return None

    openai_key = os.getenv("OPENAI_API_KEY")

    # 1. Try OpenAI DALL-E 3 if key is available
    if openai_key:
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
                        "prompt": clean_prompt,
                        "n": 1,
                        "size": "1024x1024",
                        "response_format": "b64_json",
                    },
                )
                if resp.status_code == 200:
                    import base64
                    b64_data = resp.json()["data"][0]["b64_json"]
                    log.info("Image generated via OpenAI DALL-E 3")
                    return base64.b64decode(b64_data)
                else:
                    log.warning("DALL-E 3 error %d: %s, falling back to Pollinations", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("DALL-E 3 generation failed: %s", e)

    # 2. Resilient fallback: Pollinations AI (Flux model, free, keyless, fast)
    try:
        seed = random.randint(1000, 999999)
        encoded_prompt = urllib.parse.quote(clean_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&nologo=true&model=flux"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        loop = asyncio.get_event_loop()
        def _fetch():
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=40) as r:
                return r.read()
        img_bytes = await loop.run_in_executor(None, _fetch)
        if img_bytes and len(img_bytes) > 1000:
            log.info("Image generated via Pollinations Flux (%d bytes)", len(img_bytes))
            return img_bytes
    except Exception as e:
        log.error("Pollinations image generation failed: %s", e)

    return None


async def generate_video(prompt: str) -> Optional[bytes]:
    """Generate or animate a short video clip for the prompt."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return None

    # Try Pollinations video endpoint or animated visual generator
    try:
        seed = random.randint(1000, 999999)
        encoded = urllib.parse.quote(clean_prompt)
        # Pollinations video generator URL
        url = f"https://video.pollinations.ai/prompt/{encoded}?seed={seed}&model=cogvideox"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        loop = asyncio.get_event_loop()
        def _fetch():
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=60) as r:
                return r.read()
        video_bytes = await loop.run_in_executor(None, _fetch)
        if video_bytes and len(video_bytes) > 5000:
            log.info("Video generated via Pollinations (%d bytes)", len(video_bytes))
            return video_bytes
    except Exception as e:
        log.warning("Pollinations video endpoint failed: %s", e)

    return None


async def generate_website(prompt: str, router: Any) -> str:
    """Generate a complete, self-contained, high-converting modern website."""
    sys_prompt = (
        "You are an elite, world-class Silicon Valley frontend engineer and designer. "
        "Create a COMPLETE, single-file HTML5 website/web application based on the user's request. "
        "Requirements:\n"
        "1. Include modern Tailwind CSS via CDN (<script src='https://cdn.tailwindcss.com'></script>).\n"
        "2. Include FontAwesome or Lucide icons, Google Fonts (Inter/Plus Jakarta Sans).\n"
        "3. Stunning modern UI: gradient accents, clean typography, responsive navigation, dark/light aesthetics, interactive components.\n"
        "4. Include interactive vanilla JavaScript (working buttons, modal dialogs, filters, tabs, calculators, animations).\n"
        "5. NO placeholders or 'Lorem Ipsum': write authentic, compelling, production-ready copy.\n"
        "6. Return ONLY the raw valid HTML code without markdown code fences (no ```html, just <!DOCTYPE html> to </html>)."
    )
    user_msg = f"Build this complete, stunning website/app: {prompt}"
    msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]

    comp = await router.complete(msgs, task_type="coding", max_tokens=3500, temperature=0.3)
    raw = (comp.text or "").strip()
    # Strip any stray markdown fences if model included them
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


async def generate_presentation_html(topic: str, router: Any) -> str:
    """Generate a slide deck powered by Reveal.js in standalone HTML."""
    sys_prompt = (
        "You are a top-tier executive presentation designer (McKinsey/Apple keynote style). "
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
