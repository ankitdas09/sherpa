# Sherpa — Self-Hosted PDF Semantic Search

Retrieval-only semantic search over private PDFs. A user asks a question and gets
back the most relevant **passages** (file + page + snippet + score) — no LLM, no
generation, **nothing leaves our infrastructure**.

This repo is the full **scalable-by-default skeleton**: every service from the
architecture is wired and the upload → ingest → search path works end-to-end.

## Architecture

```
browser ──metadata──► API (FastAPI, stateless, no model)
browser ──bytes─────► MinIO            (presigned PUT; bytes skip the API)
                       │
   API ──enqueue──► Redis ──► Celery worker(s)
                                  │ read PDF ◄── MinIO
                                  │ extract (PyMuPDF + Tesseract OCR)
                                  │ chunk → embed ──► TEI (the only model)
                                  └ delete-then-write ──► Qdrant
   API (search) ──► Postgres (resolve allowed KBs) ──► TEI (embed query)
                ──► Qdrant (nearest neighbours WHERE kb_id ∈ allowed)
```

**Three stores, three jobs:** MinIO holds the *files*, Postgres holds the *facts*
about them (status, ownership, permissions), Qdrant holds the searchable *chunks*
(derived, rebuildable from MinIO).

## Services (`docker-compose.yml`)

| Service | Role |
| --- | --- |
| `api` | FastAPI, stateless, no model. Runs migrations + Qdrant bootstrap on startup. |
| `worker` | Celery: parse + chunk + embed + write. Scale with `--scale worker=N`. |
| `beat` | Celery beat: abandoned-upload sweep. |
| `tei` | Text Embeddings Inference serving `BAAI/bge-small-en-v1.5` (384-dim). |
| `qdrant` | Vector store (single `chunks` collection, payload indexes on `doc_id`/`kb_id`). |
| `postgres` | System of record (documents, KBs, users/groups, permissions). |
| `redis` | Celery broker + result backend. |
| `minio` | S3-compatible object storage for raw PDF bytes (+ `minio-init` makes the bucket). |

## Run

```bash
cp .env.example .env            # defaults match the compose service names
docker compose up --build       # first boot downloads the TEI model (be patient)
```

When `api` is up: open <http://localhost:8000/docs>. MinIO console:
<http://localhost:9001> (minioadmin / minioadmin).

> **Apple Silicon (arm64):** the official TEI CPU image is amd64-only and is flaky
> under emulation. `docker-compose.override.yml` (auto-loaded) swaps the `tei`
> service for a small arm64-native sidecar (`embed_server/`, fastembed) serving the
> **same model** behind the **same `/embed` contract**. For production / amd64, use
> real TEI: `docker compose -f docker-compose.yml up` (ignores the override).

## End-to-end verification

```bash
# 1. Health
curl -s localhost:8000/healthz

# 2. Seed a KB + users (alice has access, bob does not). Note the printed IDs.
docker compose exec api python scripts/seed.py
KB=<kb_id from output>
ALICE=<alice user_id>
BOB=<bob user_id>

# 3. Register a document -> get a presigned upload URL.
RESP=$(curl -s -X POST localhost:8000/documents \
  -H "X-User-Id: $ALICE" -H 'Content-Type: application/json' \
  -d "{\"filename\":\"sample.pdf\",\"size\":12345,\"kb_ids\":[\"$KB\"]}")
echo "$RESP"
DOC=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["doc_id"])')
URL=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])')

# 4. Upload the bytes straight to MinIO (must match Content-Type).
curl -s -X PUT --upload-file sample.pdf -H 'Content-Type: application/pdf' "$URL"

# 5. Confirm completion -> enqueues ingestion.
curl -s -X POST "localhost:8000/documents/$DOC/complete" -H "X-User-Id: $ALICE"

# 6. Poll until status=done.
curl -s "localhost:8000/documents/$DOC" -H "X-User-Id: $ALICE"

# 7. Search as alice (sees results) ...
curl -s -X POST localhost:8000/search -H "X-User-Id: $ALICE" \
  -H 'Content-Type: application/json' \
  -d '{"query":"your question here","top_k":5}'

# 8. ... and as bob (no access -> empty hits, proving the access filter).
curl -s -X POST localhost:8000/search -H "X-User-Id: $BOB" \
  -H 'Content-Type: application/json' -d '{"query":"your question here"}'
```

**Other checks**

- **Idempotency:** re-run ingestion for the same doc and confirm the point count is
  unchanged: `docker compose exec api python -c "from sherpa.clients.qdrant import count_document; print(count_document('$DOC'))"`.
- **Failure path:** upload a non-PDF / password-protected file; the document lands in
  `status=failed` with a reason and a message on the `dead_letter` queue.
- **OCR:** upload a scanned (image-only) PDF and confirm it still produces chunks.
- **Persistence:** `docker compose restart qdrant postgres` and confirm data survives.

## Offline smoke test (no services)

```bash
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python scripts/smoke_test.py
```

## Configuration

All knobs live in `sherpa/config.py` and are overridable via env (`.env`). The
important ones: `EMBED_DIM` (must equal the TEI model's output dim),
`CHUNK_TOKENS` / `CHUNK_OVERLAP_TOKENS` (the main quality knob), `MAX_INGEST_RETRIES`,
and the MinIO public endpoint used for presigned URLs.

## Notes / deferred (by design)

- **Auth** is a dev `X-User-Id` header; real auth swaps `api/deps.py:current_user` only.
- **Trigger** is browser-confirmation + an abandoned-upload sweep; MinIO bucket
  notifications are the event-driven upgrade if orphans become a problem.
- **Scale** levers (GPU on TEI, more workers, API replicas, Qdrant sharding) don't
  touch the payload shape or pipeline.
- **No frontend** — the `/search` endpoint is the retrieval backend a UI or future
  "summarize results" agent consumes.
