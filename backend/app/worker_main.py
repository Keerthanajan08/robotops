"""Standalone worker process: `python -m app.worker_main`.

Runs the same DB-backed job loop as the in-process worker, so the API can be deployed with
ROBOTOPS_WORKER=0 and workers scaled independently (e.g. `docker compose up --scale worker=3`).
Job claiming is a conditional UPDATE (status='queued' -> 'running'), so several workers never
claim the same job. SIGTERM/SIGINT stop the loop between jobs; an interrupted job is re-queued by
the next worker's startup recovery and re-emits idempotently (event_id = <correlation>-<index>).
"""

import asyncio
import logging
import signal

from .main import _job_run
from .worker import worker_loop

log = logging.getLogger("robotops")


async def main() -> None:
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


if __name__ == "__main__":
    asyncio.run(main())
