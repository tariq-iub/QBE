"""Document text extraction, cleaning and semantic chunking (Phase 3).

Design doc 05, RAG pipeline stages: Document -> clean -> normalize -> chunks.
Extraction is best-effort and dependency-optional so the platform runs on a
minimal install; PDF support requires PyMuPDF (pinned in requirements.txt).

Security note: extracted text is UNTRUSTED DATA (§35). We strip HTML/script
content here; prompt-injection framing happens at prompt-assembly time.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass


@dataclass
class RawPage:
    page: int | None
    section: str | None
    text: str


# --------------------------------------------------------------------------
# Cleaning / normalization
# --------------------------------------------------------------------------

_SCRIPT_STYLE = re.compile(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>",
                           re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]{0,4096}>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RUN = re.compile(r"[ \t\u00a0]+")
_MANY_BLANK = re.compile(r"\n{3,}")
_SOFT_HYPHEN_RE = re.compile(r"\u00ad")
# Hyphenated line-breaks from justified PDFs: "resolu-\ntion" -> "resolution"
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
# Running headers/footers emitted by PDF page renderers, e.g. "page 12 of 300",
# or bare page numbers on their own line.
_PAGINATION_LINE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|\d{1,4})\s*$", re.IGNORECASE)
# Common PDF ligature artifacts
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
              "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}


def clean_text(raw: str) -> str:
    """Normalize extracted text: strip markup/control noise, fix PDF artifacts."""
    t = _CONTROL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    t = _SCRIPT_STYLE.sub(" ", t)
    t = _TAGS.sub(" ", t)
    for src, dst in _LIGATURES.items():
        t = t.replace(src, dst)
    t = _SOFT_HYPHEN_RE.sub("", t)
    t = _HYPHEN_BREAK.sub(r"\1\2", t)
    t = _WS_RUN.sub(" ", t)
    lines = [ln for ln in t.split("\n") if not _PAGINATION_LINE.match(ln)]
    t = "\n".join(lines)
    t = _MANY_BLANK.sub("\n\n", t)
    return t.strip()


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_pdf(data: bytes, *, max_pages: int = 400) -> list[RawPage]:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError("PDF extraction requires PyMuPDF (pip install PyMuPDF)") from e
    pages: list[RawPage] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for pno in range(min(doc.page_count, max_pages)):
            page = doc[pno]
            txt = page.get_text("text")
            header = None
            first_line = txt.strip().split("\n", 1)[0].strip() if txt.strip() else ""
            if 0 < len(first_line) <= 90 and not first_line.endswith("."):
                header = first_line  # heuristic running-section title
            pages.append(RawPage(page=pno + 1, section=header, text=txt))
    return pages


def extract_text_file(data: bytes) -> list[RawPage]:
    text = data.decode("utf-8", errors="replace")
    return [RawPage(page=None, section=None, text=text)]


def extract_bytes(filename: str, data: bytes) -> list[RawPage]:
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_pdf(data)
    if name.endswith((".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".csv")):
        return extract_text_file(data)
    raise ValueError(f"Unsupported document type: {filename!r} "
                     "(supported: .pdf, .txt, .md, .rst, .html, .csv)")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Semantic chunking
# --------------------------------------------------------------------------

_SECTION_MARK = re.compile(
    r"^\s*(?:#{1,4}\s+|(?:Chapter|Section|Unit|Appendix|Example|Definition|"
    r"Theorem|Law)\s+[0-9IVXLC]+[.:]?\s+|[A-Z][A-Za-z' ]{3,60}:$)",
    re.MULTILINE,
)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_pages(pages: list[RawPage], *, target_tokens: int = 300,
                overlap_tokens: int = 40, hard_max_tokens: int = 500) -> list[dict]:
    """Group paragraphs into ~target-token chunks, never splitting mid-paragraph
    unless it exceeds hard_max. Returns dicts ready for DocumentChunk rows:
    {chunk_index, text, page, section, token_count}.
    """
    chunks: list[dict] = []
    idx = 0
    for rp in pages:
        body = clean_text(rp.text)
        if not body:
            continue
        paras = _split_paragraphs(body)
        cur: list[str] = []
        cur_tok = 0
        cur_page = rp.page
        cur_section = rp.section

        def flush() -> None:
            nonlocal idx, cur, cur_tok
            if cur:
                text = "\n\n".join(cur)
                chunks.append({"chunk_index": idx, "text": text, "page": cur_page,
                               "section": cur_section, "token_count": _approx_tokens(text)})
                idx += 1
                cur, cur_tok = [], 0

        for p in paras:
            ptok = _approx_tokens(p)
            if ptok > hard_max_tokens:
                flush()
                words = p.split(" ")
                buf: list[str] = []
                btok = 0
                for w in words:
                    buf.append(w)
                    btok += _approx_tokens(w)
                    if btok >= target_tokens:
                        text = " ".join(buf)
                        chunks.append({"chunk_index": idx, "text": text,
                                       "page": cur_page, "section": cur_section,
                                       "token_count": _approx_tokens(text)})
                        idx += 1
                        # keep tail overlap of whole sentences only
                        tail = " ".join(buf[-6:])
                        buf = [tail] if tail else []
                        btok = _approx_tokens(" ".join(buf))
                if buf and " ".join(buf).strip():
                    text = " ".join(buf)
                    chunks.append({"chunk_index": idx, "text": text, "page": cur_page,
                                   "section": cur_section,
                                   "token_count": _approx_tokens(text)})
                    idx += 1
                continue
            if cur_tok + ptok > target_tokens and cur:
                prev_tail = " ".join(cur[-2:])[: overlap_tokens * 4]
                flush()
                if overlap_tokens > 0 and prev_tail:
                    cur = [prev_tail]
                    cur_tok = _approx_tokens(prev_tail)
            cur.append(p)
            cur_tok += ptok
        flush()
    return chunks


def detect_sections(text: str) -> list[str]:
    """Return section-like headings found in cleaned text (used for metadata)."""
    return [m.group(0).strip() for m in _SECTION_MARK.finditer(text)][:200]
