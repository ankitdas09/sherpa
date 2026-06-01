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


def _client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.minio_endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
        config=Config(signature_version="s3v4"),
    )


def presigned_put_url(key: str, content_type: str) -> str:
    """A scoped, temporary URL the browser PUTs one specific object to.

    Signed against the *public* endpoint so the host/browser can reach it (the
    internal `minio:9000` hostname is unreachable from outside the docker network).
    Content-type is pinned into the signature; the client must send the same
    Content-Type header on the PUT.
    """
    return _client(settings.minio_public_endpoint).generate_presigned_url(
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


def put_bytes(key: str, data: bytes, content_type: str = "application/pdf") -> None:
    """Write bytes via the internal endpoint.

    Used by the /demo visualizer to stand in for the browser's direct presigned
    PUT (the presigned URL targets the *public* endpoint, unreachable from inside
    the docker network).
    """
    _client().put_object(
        Bucket=settings.minio_bucket, Key=key, Body=data, ContentType=content_type
    )


def clear_uploads() -> int:
    """Delete every object under the bucket. Returns the count removed (demo reset)."""
    client = _client()
    resp = client.list_objects_v2(Bucket=settings.minio_bucket)
    objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
    if objs:
        client.delete_objects(Bucket=settings.minio_bucket, Delete={"Objects": objs})
    return len(objs)
