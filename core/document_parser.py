from __future__ import annotations
import io
import logging
from pathlib import Path

log = logging.getLogger("agentos.docs")


def extract_text_from_file(file_path: str | Path, original_filename: str = "") -> str:
    """Extract readable text from PDF, DOCX, TXT, or code documents."""
    path = Path(file_path)
    if not path.exists():
        return ""

    name = (original_filename or path.name).lower()

    # 1. PDF
    if name.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            pages = []
            for idx, page in enumerate(reader.pages[:40]):
                txt = page.extract_text() or ""
                if txt.strip():
                    pages.append(f"--- Page {idx+1} ---\n{txt.strip()}")
            return "\n\n".join(pages)
        except Exception as e:
            log.warning("PDF extraction failed: %s", e)
            return f"(PDF text extraction failed: {e})"

    # 2. DOCX
    if name.endswith(".docx"):
        try:
            import docx
            doc = docx.Document(str(path))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paras)
        except Exception as e:
            log.warning("DOCX extraction failed: %s", e)
            return f"(DOCX extraction failed: {e})"

    # 3. Plain text / code / csv / json / md / html
    try:
        raw_bytes = path.read_bytes()
        # Try UTF-8 first, fallback to latin-1
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return raw_bytes.decode("latin-1", errors="ignore")
    except Exception as e:
        log.warning("Text read failed: %s", e)
        return ""
