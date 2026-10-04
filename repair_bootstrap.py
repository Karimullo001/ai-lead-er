from pathlib import Path
p = Path("bootstrap_agentos.py")
s = p.read_text(encoding="utf-8")
# The generated bootstrap source currently ends inside the README raw triple-quoted literal.
# Add the missing terminator only when the file is actually unterminated.
if s.count("'''") % 2 == 1:
    p.write_text(s.rstrip() + "\n'''\n", encoding="utf-8")
