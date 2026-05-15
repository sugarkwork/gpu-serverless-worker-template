"""FastAPI worker implementing the gpu.sugar-knight.com standard contract.

Contract (lowercase status strings, per RunPod-style semantics):

  GET  /health
      → 200 {"ready": true, "in_flight": int, "version": "..."}
      → 503 {"ready": false, "reason": "..."} while warming up

  POST /run     body: {"input": {...}, "id": "optional"}
      → {"id": "<worker-job-id>", "status": "queued" | "running"}

  GET  /status/{id}
      → {"id": "...", "status": "queued"|"running"|"completed"|"failed"|"cancelled",
         "progress": {...},
         "output": {...}    # only when completed
         "error":  "..."    # when failed
        }

  POST /cancel/{id}
      → {"id": "...", "status": "cancelled" | "already_terminal"}

This is intentionally minimal — replace `worker.handler.handler` with the
real model code. Concurrency: one in-flight job per worker by default
(MAX_PARALLEL env var to raise).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from . import handler as handler_mod


logging.basicConfig(
    level=os.environ.get("WORKER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("worker")


MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "1"))
VERSION = os.environ.get("WORKER_VERSION", "echo-v1")


class JobRec:
    __slots__ = ("status", "progress", "output", "error", "task", "created_at", "started_at", "completed_at")

    def __init__(self) -> None:
        self.status = "queued"
        self.progress: dict[str, Any] = {}
        self.output: dict[str, Any] | None = None
        self.error: str | None = None
        self.task: asyncio.Task[Any] | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.completed_at: float | None = None


_jobs: dict[str, JobRec] = {}
_semaphore = asyncio.Semaphore(MAX_PARALLEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("worker startup: version=%s max_parallel=%d", VERSION, MAX_PARALLEL)
    yield
    log.info("worker shutdown — cancelling %d jobs", len(_jobs))
    for j in _jobs.values():
        if j.task and not j.task.done():
            j.task.cancel()


app = FastAPI(title="gpu.sugar-knight.com worker template", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    in_flight = sum(1 for j in _jobs.values() if j.status == "running")
    return {
        "ready": True,
        "version": VERSION,
        "max_parallel": MAX_PARALLEL,
        "in_flight": in_flight,
    }


@app.post("/run")
async def run(body: dict[str, Any]) -> dict[str, Any]:
    if "input" not in body:
        raise HTTPException(status_code=400, detail="missing 'input'")
    job_id = body.get("id") or uuid.uuid4().hex
    if job_id in _jobs:
        # Treat as idempotent re-submit; ignore duplicate.
        rec = _jobs[job_id]
        return {"id": job_id, "status": rec.status}
    rec = JobRec()
    _jobs[job_id] = rec
    rec.task = asyncio.create_task(_execute(job_id, body["input"]))
    return {"id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
async def status(job_id: str) -> dict[str, Any]:
    rec = _jobs.get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown job")
    out: dict[str, Any] = {
        "id": job_id,
        "status": rec.status,
        "progress": rec.progress,
        "created_at": rec.created_at,
        "started_at": rec.started_at,
        "completed_at": rec.completed_at,
    }
    if rec.status == "completed":
        out["output"] = rec.output
    if rec.status in ("failed", "cancelled"):
        out["error"] = rec.error
    return out


@app.post("/cancel/{job_id}")
async def cancel(job_id: str) -> dict[str, Any]:
    rec = _jobs.get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if rec.status in ("completed", "failed", "cancelled"):
        return {"id": job_id, "status": "already_terminal"}
    if rec.task and not rec.task.done():
        rec.task.cancel()
    rec.status = "cancelled"
    rec.completed_at = time.time()
    return {"id": job_id, "status": "cancelled"}


@app.get("/info")
async def info() -> dict[str, Any]:
    return {
        "version": VERSION,
        "max_parallel": MAX_PARALLEL,
        "jobs_total": len(_jobs),
    }


def _make_progress_fn(job_id: str):
    rec = _jobs[job_id]

    def update(payload: dict[str, Any]) -> None:
        rec.progress = payload

    return update


async def _execute(job_id: str, input_payload: dict[str, Any]) -> None:
    rec = _jobs[job_id]
    async with _semaphore:
        rec.status = "running"
        rec.started_at = time.time()
        log.info("job %s start", job_id)
        try:
            out = await handler_mod.handler(input_payload, _make_progress_fn(job_id))
            rec.output = out if isinstance(out, dict) else {"value": out}
            rec.status = "completed"
            log.info("job %s ok in %.2fs", job_id, time.time() - (rec.started_at or 0))
        except asyncio.CancelledError:
            rec.status = "cancelled"
            rec.error = "cancelled"
            log.info("job %s cancelled", job_id)
            raise
        except Exception as exc:
            rec.status = "failed"
            rec.error = str(exc)[:2000]
            log.exception("job %s failed", job_id)
        finally:
            rec.completed_at = time.time()
