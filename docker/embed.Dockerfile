# Local embedding sidecar (arm64-native TEI substitute for dev). See embed_server/main.py.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data

WORKDIR /app
RUN pip install --upgrade pip && \
    pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "fastembed>=0.3" "pydantic>=2.7"

COPY embed_server ./embed_server

EXPOSE 80
CMD ["uvicorn", "embed_server.main:app", "--host", "0.0.0.0", "--port", "80"]
