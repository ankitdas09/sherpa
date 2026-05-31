"""Idempotent Qdrant bootstrap: create the collection + payload indexes.

Run on API startup and safe to run by hand. Must happen before any bulk
ingestion so access-filtered and delete-by-document operations stay fast (§6, §10).
"""

from __future__ import annotations

from sherpa.clients.qdrant import ensure_collection
from sherpa.config import settings


def main() -> None:
    ensure_collection()
    print(
        f"qdrant ready: collection={settings.collection_name!r} "
        f"dim={settings.embed_dim} indexes=[doc_id, kb_id]"
    )


if __name__ == "__main__":
    main()
