"""Background worker for simulation jobs (DB-backed queue). Runs in-process as an asyncio task, or as
separate processes/containers via `python -m app.worker_main`.
Semantics: at-least-once with idempotent inserts (event_id = <correlation>-<index>). Each claim takes a
lease (random token + heartbeat updated on every event). A job whose heartbeat is older than LEASE_S is
re-queued by any worker's periodic recovery; the original holder, if it is still alive, sees its lease is
gone at its next heartbeat and stops, so a job never completes twice. Cancellation is checked between events. If the device is
offline when an event is due, the event is recorded as a failed delivery and the job fails (no silent skipping).
All timestamps are virtual (from the scenario); delay_ms only paces real time."""

import asyncio
import logging
import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update

from .db import (
    DeliveryFailure,
    Device,
    Event,
    Scenario,
    SessionLocal,
    SimulationJob,
    now,
)
from .simulator import generate

log = logging.getLogger("robotops")


LEASE_S = int(os.getenv("WORKER_LEASE_SECONDS", "60"))
HEARTBEAT_FILE = os.getenv(
    "WORKER_HEARTBEAT_FILE"
)  # set by worker_main; read by the container healthcheck


def _beat() -> None:
    """Liveness signal: touched on every poll and every job heartbeat, so a stuck worker goes stale."""
    if HEARTBEAT_FILE:
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass


RECOVER_EVERY_S = 30


def recover(lease_s: int = LEASE_S) -> int:
    """Re-queue running jobs whose worker stopped heartbeating (crashed or was killed)."""
    cutoff = now() - timedelta(seconds=lease_s)
    with SessionLocal() as db:
        n = db.execute(
            update(SimulationJob)
            .where(
                SimulationJob.status == "running",
                or_(
                    SimulationJob.heartbeat_at.is_(None),
                    SimulationJob.heartbeat_at < cutoff,
                ),
            )
            .values(status="queued", lease_token=None)
        ).rowcount
        db.commit()
        if n:
            log.warning("recovered %d stale job(s)", n)
        return n


def process_next(on_complete=None, sleep=time.sleep) -> str | None:
    with SessionLocal() as db:
        job = db.scalar(
            select(SimulationJob)
            .where(SimulationJob.status == "queued")
            .order_by(SimulationJob.created_at)
            .limit(1)
        )
        if not job:
            return None
        token = uuid.uuid4().hex
        claimed = (
            db.execute(  # atomic claim: only one worker's conditional UPDATE can match
                update(SimulationJob)
                .where(SimulationJob.id == job.id, SimulationJob.status == "queued")
                .values(status="running", lease_token=token, heartbeat_at=now())
            ).rowcount
        )
        db.commit()
        jid = job.id
    if claimed != 1:
        return None
    _run(jid, token, on_complete, sleep)
    return jid


def _held(db, jid, token, **values) -> bool:
    """Heartbeat (and optionally update fields) only while this worker still holds the lease.
    Returns False if the job was cancelled or recovered by another worker."""
    n = db.execute(
        update(SimulationJob)
        .where(
            SimulationJob.id == jid,
            SimulationJob.lease_token == token,
            SimulationJob.status == "running",
        )
        .values(heartbeat_at=now(), **values)
    ).rowcount
    db.commit()
    _beat()
    return n == 1


def _run(jid, token, on_complete, sleep):
    with SessionLocal() as db:
        job = db.get(SimulationJob, jid)
        d = db.get(Device, job.device_id)
        sc = db.get(Scenario, job.scenario_id)
        if d is None or sc is None:
            _held(
                db,
                jid,
                token,
                status="failed",
                error="device or scenario no longer exists",
            )
            return
        evs = generate(sc.steps, job.fail)
        if not _held(db, jid, token, events_total=len(evs)):
            return
        for i, e in enumerate(evs):
            db.refresh(d)
            if i and not _held(
                db, jid, token
            ):  # cancelled, or lease lost to another worker
                return
            eid = f"{job.correlation_id}-{i}"
            if not d.online:
                if not db.scalar(
                    select(DeliveryFailure).where(
                        DeliveryFailure.device_id == d.id,
                        DeliveryFailure.event_id == eid,
                        DeliveryFailure.status == "failed",
                    )
                ):
                    db.add(
                        DeliveryFailure(
                            owner_id=job.owner_id,
                            device_id=d.id,
                            event_id=eid,
                            reason="device_offline",
                            payload={
                                "event_id": eid,
                                "correlation_id": job.correlation_id,
                                **e,
                            },
                        )
                    )
                    db.commit()
                _held(
                    db,
                    jid,
                    token,
                    status="failed",
                    error=f"device offline before event {i + 1}/{len(evs)}",
                )
                return
            if not db.scalar(
                select(Event).where(Event.device_id == d.id, Event.event_id == eid)
            ):
                db.add(
                    Event(
                        owner_id=job.owner_id,
                        device_id=d.id,
                        event_id=eid,
                        correlation_id=job.correlation_id,
                        **e,
                    )
                )
            d.last_seen = now()
            db.commit()
            if not _held(db, jid, token, events_emitted=i + 1):
                return
            if job.delay_ms and i < len(evs) - 1:
                sleep(job.delay_ms / 1000)
        if not _held(db, jid, token):
            return
        run_id, error = None, None
        if job.auto_run and on_complete:  # create the run BEFORE publishing "completed"
            try:
                run_id = on_complete(job)
            except Exception:
                log.exception("auto-run failed for job %s", jid)
                error = "events delivered but run creation failed"
        _held(db, jid, token, status="completed", run_id=run_id, error=error)


async def worker_loop(on_complete, poll_s: float = 0.5):
    last_recover = 0.0
    while True:
        _beat()
        if (
            time.monotonic() - last_recover > RECOVER_EVERY_S
        ):  # periodic: picks up jobs of crashed peers
            try:
                await asyncio.to_thread(recover)
            except Exception:
                log.exception("recovery error")
            last_recover = time.monotonic()
        try:
            jid = await asyncio.to_thread(process_next, on_complete)
        except Exception:
            log.exception("worker error")
            jid = None
        if jid is None:
            await asyncio.sleep(poll_s)
