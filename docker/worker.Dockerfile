# Worker image — includes Tesseract for OCR of scanned PDFs (§3, §9).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract OCR engine + language data. PyMuPDF ships its own libs (no extra apt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

# Overridden in compose for the beat service.
# Consume only 'default'; 'dead_letter' is a no-consumer parking queue (see compose).
CMD ["celery", "-A", "sherpa.worker.celery_app", "worker", \
     "--loglevel=info", "--queues=default"]
