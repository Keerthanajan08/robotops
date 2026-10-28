"""Standalone worker process: `python -m app.worker_main`.

Runs the same DB-backed job loop as the in-process worker, so the API can be deployed with
ROBOTOPS_WORKER=0 and workers scaled independently (e.g. `docker compose up --scale worker=3`).
Job claiming is a conditional UPDATE (status='queued' -> 'running'), so several workers never
claim the same job. SIGTERM/SIGINT stop the loop between jobs; a job whose worker died is re-queued
once its lease expires and re-emits idempotently (event_id = <correlation>-<index>).
Health: the loop touches WORKER_HEARTBEAT_FILE; `--healthcheck` fails if it is older than 90 s.
"""

import asyncio
import logging
import os
import signal
import sys
import time

log = logging.getLogger("robotops")


async def main() -> None:
    from .main import _job_run  # imported here so the healthcheck stays cheap
    from .worker import worker_loop

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(worker_loop(_job_run))
    log.info("worker started")
    await stop.wait()
    log.info("worker stopping")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def healthcheck(max_age_s: float = 90) -> int:
    """`python -m app.worker_main --healthcheck`: healthy if the loop beat recently."""
    try:
        age = time.time() - os.path.getmtime(os.environ["WORKER_HEARTBEAT_FILE"])
    except (KeyError, OSError):
        return 1
    return 0 if age < max_age_s else 1


if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(healthcheck())
    asyncio.run(main())
