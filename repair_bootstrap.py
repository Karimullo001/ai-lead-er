from pathlib import Path

def repair(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    s = p.read_text(encoding="utf-8")
    if s.count("'''") % 2 == 1:
        p.write_text(s.rstrip() + "\n'''\n", encoding="utf-8")

repair("bootstrap_agentos.py")
repair("upgrade_agentos.py")
