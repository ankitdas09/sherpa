"""Local embedding sidecar — arm64-native drop-in for TEI (dev only).

Serves the SAME model (BAAI/bge-small-en-v1.5, 384-dim) behind the SAME HTTP
contract the app's TEI client expects:

    POST /embed   {"inputs": ["text", ...]}  -> [[...384 floats...], ...]
    GET  /health  -> 200

TEI remains the production embedding service (see docker-compose.yml); this exists
only because the official TEI CPU image ships amd64-only and is flaky under
emulation on Apple Silicon. Uses fastembed (ONNX) so there's no torch dependency.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastembed import TextEmbedding
from pydantic import BaseModel

MODEL_ID = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

app = FastAPI(title="sherpa-embed (local TEI-compatible)")
_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=MODEL_ID)
    return _model


class EmbedRequest(BaseModel):
    inputs: list[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/embed")
def embed(req: EmbedRequest) -> list[list[float]]:
    vectors = _get_model().embed(req.inputs)
    return [v.tolist() for v in vectors]
