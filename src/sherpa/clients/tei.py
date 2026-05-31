"""Embedding client — talks to the self-hosted TEI service.

This is the *only* place the model is reached. Both the API (single query
embedding) and the ingestion workers (bulk chunk embeddings) call through here,
so query and ingestion are structurally guaranteed to use the same model
(handoff §5, §9). Text goes to our own TEI container — never a third party.
"""

from __future__ import annotations

import httpx

from sherpa.config import settings


class TEIError(RuntimeError):
    """Raised on a non-transient TEI failure (caller decides retry policy)."""


def _embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    # TEI's OpenAI-compatible-ish endpoint: POST /embed -> [[...], [...]]
    resp = client.post("/embed", json={"inputs": texts})
    resp.raise_for_status()
    data = resp.json()
    # TEI returns a bare list of vectors for /embed.
    if not isinstance(data, list):
        raise TEIError(f"unexpected TEI response shape: {type(data)!r}")
    return data


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed many texts, batched. Order of output matches order of input.

    Raises httpx.HTTPError on transient/network failure (workers retry those)
    and TEIError on a malformed response.
    """
    if not texts:
        return []

    vectors: list[list[float]] = []
    with httpx.Client(base_url=settings.tei_url, timeout=settings.embed_timeout_s) as client:
        for i in range(0, len(texts), settings.embed_batch_size):
            batch = texts[i : i + settings.embed_batch_size]
            vectors.extend(_embed_batch(client, batch))

    if len(vectors) != len(texts):
        raise TEIError(f"embedding count mismatch: got {len(vectors)} for {len(texts)} inputs")
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Same model as ingestion — by construction."""
    return embed_texts([text])[0]
