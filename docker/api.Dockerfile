# API image — stateless, no model, no Tesseract.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first (better layer caching).
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

# App config + migrations needed at runtime.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "sherpa.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
