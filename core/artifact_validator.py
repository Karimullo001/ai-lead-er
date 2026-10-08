
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse

def validate_artifact(a: dict):
    if a.get("url"):
        u=urlparse(str(a["url"]))
        return {"valid":u.scheme in {"http","https","tg"},"url":a["url"]}
    p=a.get("path") or a.get("file_path")
    if p:
        x=Path(str(p)); return {"valid":x.exists(),"path":str(x)}
    if "content" in a: return {"valid":True,"inline":True}
    return {"valid":False,"reason":"missing artifact target"}

def validate_all(items):
    checks=[validate_artifact(x) for x in items]
    return {"valid":all(x["valid"] for x in checks),"checks":checks}
