"""Token-based chunking with overlap and page tracking (handoff §3, §9).

Chunk size is the main quality knob: too big = vague results, too small = ideas
split apart. Default ~450 tokens with ~50 overlap. Each chunk keeps the source
page number so /search can link back to the right page.

We tokenize with tiktoken (a fast, model-agnostic BPE) purely for *counting* and
*splitting* into stable windows. The actual embedding tokenizer lives in TEI; we
only need consistent, roughly-450-token windows here.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

from sherpa.config import settings
from sherpa.ingestion.extract import PageText

_ENC = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    index: int  # 0-based position within the document
    text: str
    page: int  # 1-based source page (the page the chunk starts on)


def _flatten(pages: list[PageText]) -> tuple[list[int], list[int]]:
    """Encode the whole document to tokens, recording each token's source page.

    Returns (tokens, token_pages) of equal length. A single space is inserted
    between pages so words don't run together across a page boundary.
    """
    tokens: list[int] = []
    token_pages: list[int] = []
    for pt in pages:
        if not pt.text:
            continue
        page_tokens = _ENC.encode(pt.text + " ")
        tokens.extend(page_tokens)
        token_pages.extend([pt.page] * len(page_tokens))
    return tokens, token_pages


def chunk_pages(pages: list[PageText]) -> list[Chunk]:
    """Split a document's pages into overlapping ~chunk_tokens windows.

    The page assigned to a chunk is the page its first token came from — the
    natural target for a "jump to source" link.
    """
    size = settings.chunk_tokens
    overlap = settings.chunk_overlap_tokens
    stride = max(1, size - overlap)

    tokens, token_pages = _flatten(pages)
    if not tokens:
        return []

    chunks: list[Chunk] = []
    idx = 0
    for start in range(0, len(tokens), stride):
        window = tokens[start : start + size]
        if not window:
            break
        text = _ENC.decode(window).strip()
        if not text:
            continue
        chunks.append(Chunk(index=idx, text=text, page=token_pages[start]))
        idx += 1
        if start + size >= len(tokens):
            break  # last window reached the end; avoid a trailing overlap-only chunk
    return chunks
