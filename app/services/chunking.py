"""
Structure-aware chunking (D010, RESEARCH.md). Splits on heading/paragraph boundaries first;
any resulting section longer than max_tokens is further split with overlap. Token counting
uses tiktoken's cl100k_base encoding as a reasonable proxy across most local/hosted models -
not exact for every possible model's tokenizer, but consistent and good enough for chunk-sizing
purposes (an exact per-model tokenizer would need a provider-specific dependency, which isn't
worth the complexity here).
"""
import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

DEFAULT_MAX_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@lru_cache
def _get_encoding():
    """Loads cl100k_base on first use, not at import time.

    tiktoken.get_encoding() downloads the BPE ranks file from the network on a cold cache, so
    doing it in a module-level constant made `import app.main` block on network I/O - a fresh
    container would hang on startup with no error, and this module is imported by the API
    (app.main -> app.workers.app -> app.workers.ingestion -> app.services.chunking), not just
    by the worker that actually chunks. Lazy + memoized keeps startup off the network and
    still pays the one-time cost at most once per process."""
    return tiktoken.get_encoding("cl100k_base")


@dataclass
class ChunkResult:
    content: str
    heading_path: str | None
    order: int
    token_count: int


def _count_tokens(text: str) -> int:
    return len(_get_encoding().encode(text))


def _split_by_markdown_headings(text: str) -> list[tuple[str | None, str]]:
    """Returns [(heading_path, section_text), ...]. If no markdown headings are found, the
    whole text is one section with heading_path=None (plain text / already-extracted PDF/HTML
    text commonly has no markdown headings)."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(None, text)]

    sections: list[tuple[str | None, str]] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)

    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        heading_stack = [h for h in heading_stack if h[0] < level]
        heading_stack.append((level, title))
        heading_path = " > ".join(h[1] for h in heading_stack)

        if body:
            sections.append((heading_path, body))
    return sections


def _split_by_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs or [text]


def _split_oversized_section(
    section_text: str, *, max_tokens: int, overlap_tokens: int
) -> list[str]:
    """Splits a section that's too long, preferring paragraph boundaries; falls back to a
    sliding token window with overlap if a single paragraph alone still exceeds max_tokens."""
    paragraphs = _split_by_paragraphs(section_text)
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush():
        if current:
            pieces.append("\n\n".join(current))

    for para in paragraphs:
        para_tokens = _count_tokens(para)
        if para_tokens > max_tokens:
            flush()
            current, current_tokens = [], 0
            pieces.extend(_sliding_window_split(para, max_tokens=max_tokens, overlap_tokens=overlap_tokens))
            continue

        if current_tokens + para_tokens > max_tokens:
            flush()
            current, current_tokens = [], 0

        current.append(para)
        current_tokens += para_tokens

    flush()
    return pieces


def _sliding_window_split(text: str, *, max_tokens: int, overlap_tokens: int) -> list[str]:
    encoding = _get_encoding()
    tokens = encoding.encode(text)
    pieces = []
    step = max(max_tokens - overlap_tokens, 1)
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        pieces.append(encoding.decode(window))
        if start + max_tokens >= len(tokens):
            break
    return pieces


def chunk_document(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[ChunkResult]:
    if not text or not text.strip():
        return []

    sections = _split_by_markdown_headings(text)
    results: list[ChunkResult] = []
    order = 0

    for heading_path, section_text in sections:
        section_tokens = _count_tokens(section_text)
        if section_tokens <= max_tokens:
            pieces = [section_text]
        else:
            pieces = _split_oversized_section(
                section_text, max_tokens=max_tokens, overlap_tokens=overlap_tokens
            )

        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            results.append(
                ChunkResult(
                    content=piece,
                    heading_path=heading_path,
                    order=order,
                    token_count=_count_tokens(piece),
                )
            )
            order += 1

    return results
