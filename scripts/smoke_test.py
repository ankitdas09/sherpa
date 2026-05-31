"""Offline smoke test — no external services required.

Verifies: every module imports, deterministic IDs behave, object keys are stable,
schemas validate, and config loads with sane defaults.
"""

from __future__ import annotations

import uuid

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"{GREEN}PASS{RESET} {name}")
    else:
        _failed += 1
        print(f"{RED}FAIL{RESET} {name}")


# 1. Imports -----------------------------------------------------------------
import sherpa.config as config  # noqa: E402
import sherpa.schemas as schemas  # noqa: E402
from sherpa.clients import qdrant, storage, tei  # noqa: E402
from sherpa.db import models, session  # noqa: E402

check("all modules import", True)

# 2. Config defaults ---------------------------------------------------------
s = config.settings
check("embed_dim default is 384", s.embed_dim == 384)
check("collection name is 'chunks'", s.collection_name == "chunks")
check("chunk_tokens > chunk_overlap", s.chunk_tokens > s.chunk_overlap_tokens)

# 3. Deterministic point IDs (idempotency, §7 Fix 1) -------------------------
doc = str(uuid.uuid4())
id_a = qdrant.point_id(doc, 0)
id_a2 = qdrant.point_id(doc, 0)
id_b = qdrant.point_id(doc, 1)
check("point_id is deterministic (same doc+idx -> same id)", id_a == id_a2)
check("point_id differs by chunk index", id_a != id_b)
check("point_id is a valid UUID", uuid.UUID(id_a).version == 5)

# 4. Object key ties stores together (§5, §8) --------------------------------
check("object_key format", storage.object_key(doc) == f"uploads/{doc}.pdf")

# 5. Schemas validate --------------------------------------------------------
kb = uuid.uuid4()
req = schemas.CreateDocumentRequest(filename="a.pdf", size=10, kb_ids=[kb])
check("CreateDocumentRequest requires >=1 kb_id", req.kb_ids == [kb])

try:
    schemas.CreateDocumentRequest(filename="a.pdf", size=10, kb_ids=[])
    check("empty kb_ids rejected", False)
except Exception:
    check("empty kb_ids rejected", True)

sr = schemas.SearchRequest(query="hello")
check("SearchRequest default top_k=10", sr.top_k == 10)

try:
    schemas.SearchRequest(query="x", top_k=0)
    check("top_k=0 rejected", False)
except Exception:
    check("top_k=0 rejected", True)

# 6. ORM metadata has all 7 tables ------------------------------------------
tables = set(models.Base.metadata.tables)
expected = {
    "documents",
    "knowledge_bases",
    "document_kb",
    "users",
    "groups",
    "user_groups",
    "kb_access",
}
check(f"all 7 tables in metadata (got {len(tables)})", expected <= tables)
check("DocStatus has 6 states", len(list(models.DocStatus)) == 6)

# session module exposes the expected API
check("session has session_scope + get_db", hasattr(session, "session_scope") and hasattr(session, "get_db"))

# tei module exposes embed funcs
check("tei exposes embed_texts + embed_query", hasattr(tei, "embed_texts") and hasattr(tei, "embed_query"))

# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
