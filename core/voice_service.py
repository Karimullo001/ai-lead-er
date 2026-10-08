from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional
import httpx

log = logging.getLogger("agentos.voice")


async def transcribe_audio(file_path: str | Path) -> str:
    """Transcribe audio file using Groq Whisper (ultra-fast) or OpenAI Whisper."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    groq_key = os.getenv("GROQ_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    # 1. Try Groq Whisper (very fast and cost-effective)
    if groq_key:
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                with open(path, "rb") as f:
                    files = {"file": (path.name, f, "audio/mpeg")}
                    data = {"model": "whisper-large-v3", "response_format": "json"}
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        files=files,
                        data=data,
                    )
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    if text:
                        log.info("Transcribed audio via Groq Whisper (%d chars)", len(text))
                        return text
                else:
                    log.warning("Groq whisper error %d: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Groq whisper transcription failed: %s", e)

    # 2. Try OpenAI Whisper
    if openai_key:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(path, "rb") as f:
                    files = {"file": (path.name, f, "audio/mpeg")}
                    data = {"model": "whisper-1", "response_format": "json"}
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        files=files,
                        data=data,
                    )
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    if text:
                        log.info("Transcribed audio via OpenAI Whisper (%d chars)", len(text))
                        return text
                else:
                    log.warning("OpenAI whisper error %d: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("OpenAI whisper transcription failed: %s", e)

    raise RuntimeError("No transcription service available. Please configure OPENAI_API_KEY or GROQ_API_KEY.")


async def text_to_speech(text: str, voice: str = "alloy") -> Optional[bytes]:
    """Generate speech audio using OpenAI TTS."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        log.warning("text_to_speech: OPENAI_API_KEY not configured")
        return None

    # TTS limit per request is 4096 chars, best kept concise for voice notes
    clean_text = text[:1500].strip()
    if not clean_text:
        return None

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "tts-1",
                    "input": clean_text,
                    "voice": voice,
                    "response_format": "opus",
                },
            )
            if resp.status_code == 200:
                log.info("Generated %d bytes of speech audio", len(resp.content))
                return resp.content
            log.warning("OpenAI TTS error %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("OpenAI TTS generation failed: %s", e)
    return None
