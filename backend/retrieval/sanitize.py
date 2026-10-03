"""HTML content sanitizer (Phase 4, §35).

Retrieved web pages are UNTRUSTED DATA. This module converts HTML into clean
text and quarantines injection vectors BEFORE anything is stored or prompted:
  * removes script/style/noscript/template/svg/iframe entirely;
  * drops comments and CDATA;
  * strips zero-width & bidi-override characters (hidden-invisible prompts);
  * neutralizes obvious instruction-injection markers (recorded as a flag so
    the caller can lower source confidence / require review);
  * collapses whitespace to normal prose.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from bs4 import Comment, CData, ProcessingInstruction

# Elements removed wholesale (content + subtree).
_DROP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas",
              "iframe", "frame", "frameset", "object", "embed", "form", "nav",
              "footer", "header", "aside"}

# Invisible/bidi characters commonly abused for hidden prompt injection.
_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff\u00ad\u202a-\u202e\u2066-\u2069]")

_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b", re.I),
    re.compile(r"\bdisregard\s+(the\s+)?(system|previous)\s+(prompt|instructions)\b", re.I),
    re.compile(r"\byou\s+are\s+now\s+(a|an|in)\b", re.I),
    re.compile(r"\bnew\s+system\s+prompt\b", re.I),
    re.compile(r"\b(reveal|print|show)\s+(your|the)\s+(system\s+)?prompt\b", re.I),
    re.compile(r"<\s*/?\s*(system|assistant)\s*>", re.I),
    re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<\|im_start\|>|<\|endoftext\|>", re.I),
]


@dataclass
class SanitizedDoc:
    text: str
    title: str | None
    injection_suspect: bool = False
    dropped_elements: list[str] = field(default_factory=list)
    original_bytes: int = 0
    normalized_chars: int = 0


def sanitize_html(html: bytes | str) -> SanitizedDoc:
    from bs4 import BeautifulSoup

    raw = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    soup = BeautifulSoup(raw, "lxml")

    dropped: list[str] = []
    for tag in soup.find_all(True):
        name = (tag.name or "").lower()
        if name in _DROP_TAGS:
            dropped.append(name)
            tag.decompose()

    # Any remaining comments/CDATA/PIs are noise or hidden payloads.
    for node in list(soup.find_all(string=lambda s: isinstance(
            s, (Comment, CData, ProcessingInstruction)))):
        dropped.append(type(node).__name__.lower())
        node.extract()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True)[:512] if title_tag else None

    body = soup.body or soup
    text = body.get_text(separator="\n")

    text = _INVISIBLE_RE.sub("", text)
    suspects = any(p.search(text) for p in _INJECTION_PATTERNS)
    if suspects:
        # Neutralize rather than silently keep: bracket the phrase so no model
        # can read it as an imperative, and flag for quality downgrade.
        for p in _INJECTION_PATTERNS:
            text = p.sub(lambda m: "[REDACTED-INJECTION]", text)

    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    return SanitizedDoc(text=text, title=title, injection_suspect=suspects,
                        dropped_elements=sorted(set(dropped)),
                        original_bytes=len(raw), normalized_chars=len(text))
