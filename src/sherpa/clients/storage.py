"""MinIO object storage client (S3-compatible, via boto3).

System of record for the raw PDF bytes. The browser uploads *directly* here via
a presigned PUT — bytes never flow through the API (handoff §8). The worker reads
bytes back here during ingestion.
"""

from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from sherpa.config import settings


def object_key(doc_id: str) -> str:
    """All three stores point at the same doc via this key (§5, §8)."""
    return f"uploads/{doc_id}.pdf"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
        config=Config(signature_version="s3v4"),
    )


def presigned_put_url(key: str, content_type: str) -> str:
    """A scoped, temporary URL the browser PUTs one specific object to.

    Content-type is pinned into the signature; the frontend must send the same
    Content-Type header on the PUT.
    """
    return _client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.minio_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.presign_expiry_s,
    )


def object_exists(key: str) -> bool:
    """HEAD the object — used by the abandoned-upload sweep (§8)."""
    try:
        _client().head_object(Bucket=settings.minio_bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def get_bytes(key: str) -> bytes:
    """Download an object's bytes (worker reads the PDF for ingestion)."""
    obj = _client().get_object(Bucket=settings.minio_bucket, Key=key)
    return obj["Body"].read()


def ensure_bucket() -> None:
    """Create the bucket if missing (fallback to the minio-init compose job)."""
    client = _client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.minio_bucket)
