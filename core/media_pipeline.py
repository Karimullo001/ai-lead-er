from __future__ import annotations
import asyncio, base64, json, os, subprocess, tempfile
from pathlib import Path
from typing import Any

MAX_VIDEO_BYTES = 120 * 1024 * 1024

def extract_document(path: str):
    p=Path(path); ext=p.suffix.lower()
    if not p.exists():
        return {"type":"unknown","error":"file not found"}
    if ext in {".txt",".md",".csv",".json",".py",".js",".ts",".html",".css"}:
        return {"type":"text","text":p.read_text(encoding="utf-8",errors="ignore")[:200000]}
    if ext==".pdf":
        try:
            from pypdf import PdfReader
            r=PdfReader(str(p))
            return {"type":"pdf","pages":len(r.pages),"text":"\\n".join(x.extract_text() or "" for x in r.pages)[:200000]}
        except Exception as e: return {"type":"pdf","error":f"{type(e).__name__}: {e}"}
    if ext==".docx":
        try:
            from docx import Document
            d=Document(str(p))
            return {"type":"docx","text":"\\n".join(x.text for x in d.paragraphs)[:200000]}
        except Exception as e: return {"type":"docx","error":f"{type(e).__name__}: {e}"}
    return {"type":"unknown","path":str(p),"size":p.stat().st_size}

def _run_ffmpeg(ff: str, args: list[str], timeout: int = 90):
    return subprocess.run([ff, "-hide_banner", "-loglevel", "error", *args],
                          check=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _extract_frames(ff: str, path: str, out: Path, frames: int):
    pattern=str(out/"frame_%02d.jpg")
    _run_ffmpeg(ff, ["-i",path,"-vf","fps=1/15,scale=768:-2","-frames:v",str(frames),
                     "-q:v","6",pattern], timeout=90)
    return sorted(out.glob("frame_*.jpg"))

def _extract_audio(ff: str, path: str, out: Path):
    wav=out/"audio.wav"
    _run_ffmpeg(ff, ["-i",path,"-vn","-ac","1","-ar","16000","-t","300",
                     "-c:a","pcm_s16le",str(wav)], timeout=120)
    return wav if wav.exists() else None

def _transcribe_openai(audio: Path) -> str:
    key=os.getenv("OPENAI_API_KEY")
    if not key or not audio.exists():
        return ""
    import httpx
    with audio.open("rb") as f:
        files={"file":(audio.name,f,"audio/wav")}
        data={"model":os.getenv("TRANSCRIPTION_MODEL","gpt-4o-mini-transcribe")}
        r=httpx.post("https://api.openai.com/v1/audio/transcriptions",
                     headers={"Authorization":f"Bearer {key}"},
                     files=files,data=data,timeout=180)
    r.raise_for_status()
    return str(r.json().get("text") or "")[:20000]

async def video_analyze(path: str, prompt: str = "Describe what happens in this video and note important details.",
                        frames: int = 6):
    p=Path(path)
    if not p.exists(): return {"ok":False,"error":"video file not found"}
    if p.stat().st_size > MAX_VIDEO_BYTES:
        return {"ok":False,"error":"video exceeds 120MB safety limit"}
    try:
        import imageio_ffmpeg
        ff=imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        return {"ok":False,"error":f"ffmpeg unavailable: {e}"}
    out=Path(tempfile.mkdtemp(prefix="agentos_video_"))
    try:
        imgs=_extract_frames(ff,str(p),out,max(1,min(6,int(frames))))
        audio=_extract_audio(ff,str(p),out)
        transcript=""
        if audio:
            try:
                transcript=await asyncio.to_thread(_transcribe_openai,audio)
            except Exception:
                transcript=""
        result={"ok":True,"frames":[str(x) for x in imgs],
                "audio":str(audio) if audio else None,"transcript":transcript}
        # Use the existing fallback router for visual understanding.
        if imgs:
            try:
                from .model_router import ModelRouter
                parts=[{"type":"text","text":prompt + "\\nAudio transcript:\\n" + transcript[:12000]}]
                for img in imgs:
                    b64=base64.b64encode(img.read_bytes()).decode()
                    parts.append({"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}})
                comp=await ModelRouter().complete(
                    [{"role":"user","content":parts}],
                    task_type="vision", temperature=0.1, max_tokens=2000)
                result["analysis"]=comp.text
                result["provider"]=comp.provider
                result["model"]=comp.model
            except Exception as e:
                result["analysis_error"]=f"{type(e).__name__}: {e}"
        return result
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}

def video_extract(path: str, frames=4):
    # Backward-compatible synchronous frame extraction for existing callers.
    try:
        import imageio_ffmpeg
        ff=imageio_ffmpeg.get_ffmpeg_exe()
        out=Path(tempfile.mkdtemp(prefix="agentos_video_"))
        imgs=_extract_frames(ff,path,out,max(1,min(8,int(frames))))
        return {"ok":True,"frames":[str(x) for x in imgs]}
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}: {e}"}
