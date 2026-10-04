from pathlib import Path

WRITER = '''
from pathlib import Path as _Path
_target = _Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
_target.mkdir(parents=True, exist_ok=True)
for _rel, _content in FILES.items():
    _p = _target / _rel
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(_content, encoding="utf-8")
print(f"AgentOS generated: {len(FILES)} files -> {_target}")
'''

def repair(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    s = p.read_text(encoding="utf-8")
    # Close a truncated raw triple-quoted source literal.
    if s.count("'''") % 2 == 1:
        s = s.rstrip() + "\n'''\n"
    # The uploaded generator sources were truncated before their FILES writer.
    # Restore the minimal deterministic writer so Render actually materializes
    # the generated project instead of silently doing nothing.
    if "for _rel, _content in FILES.items()" not in s:
        s = s.rstrip() + "\n\n" + WRITER
    p.write_text(s, encoding="utf-8")

repair("bootstrap_agentos.py")
repair("upgrade_agentos.py")
