"""Celery application — Redis broker, dead-letter queue, beat schedule (§7, §8).

Queue topology:
  * default  — ingestion jobs
  * dead_letter — jobs that exhausted retries or failed permanently; a human
                  reviews these, they are never silently dropped.

acks_late + reject_on_worker_lost: a job a crashed worker was holding is
redelivered (so a half-written ingest gets retried — safe thanks to the
idempotent pipeline), rather than lost.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from sherpa.config import settings

celery_app = Celery(
    "sherpa",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["sherpa.worker.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # heavy jobs: don't hoard the queue
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("dead_letter"),
    ),
    result_expires=3600,
    timezone="UTC",
    # Beat: the abandoned-upload sweep (§8).
    beat_schedule={
        "sweep-abandoned-uploads": {
            "task": "sherpa.worker.tasks.sweep_abandoned_uploads",
            "schedule": float(settings.sweep_interval_s),
        }
    },
)
