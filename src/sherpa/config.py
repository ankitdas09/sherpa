"""Central configuration. All env in one place (pydantic-settings).

Every service (api, worker, beat, scripts) imports `settings` from here so the
api and ingestion workers share identical knobs — in particular the embedding
dimension and collection name, which MUST match between query and ingestion.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Postgres (system of record) ---
    database_url: str = Field(
        default="postgresql+psycopg://sherpa:sherpa@postgres:5432/sherpa",
        description="SQLAlchemy URL for the system-of-record DB.",
    )

    # --- Redis / Celery ---
    redis_url: str = Field(default="redis://redis:6379/0")

    # --- Qdrant (vector store) ---
    qdrant_url: str = Field(default="http://qdrant:6333")
    collection_name: str = Field(default="chunks")
    embed_dim: int = Field(default=384, description="Must match the TEI model output dim.")

    # --- TEI (embedding service) ---
    tei_url: str = Field(default="http://tei:80", description="Text Embeddings Inference base URL.")
    embed_batch_size: int = Field(default=32)
    embed_timeout_s: float = Field(default=60.0)

    # --- MinIO (raw PDF bytes) ---
    minio_endpoint: str = Field(default="http://minio:9000")
    minio_access_key: str = Field(default="minioadmin")
    minio_secret_key: str = Field(default="minioadmin")
    minio_bucket: str = Field(default="uploads")
    minio_region: str = Field(default="us-east-1")
    presign_expiry_s: int = Field(default=3600)
    max_upload_bytes: int = Field(default=200 * 1024 * 1024)  # 200 MB

    # --- Ingestion / chunking ---
    chunk_tokens: int = Field(default=450)
    chunk_overlap_tokens: int = Field(default=50)
    ocr_enabled: bool = Field(default=True)

    # --- Retries ---
    max_ingest_retries: int = Field(default=3)

    # --- Abandoned-upload sweep ---
    abandoned_after_s: int = Field(default=3600)  # consider pending_upload abandoned after 1h
    sweep_interval_s: int = Field(default=900)  # run sweep every 15 min


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
