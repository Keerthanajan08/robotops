#!/usr/bin/env python3
"""RobotOps device agent: the device side of device-to-cloud ingestion.

Standard library only, so it can run on a small edge box. It authenticates with the device's own
key (never a user's password or JWT), and every event is written to a local on-disk outbox *before*
any network call. The outbox is then flushed in batches:

  * network error / 5xx / 429 -> keep everything, back off exponentially with jitter, try again
  * 200                       -> drop the events the server accepted or already had (duplicates)
  * 401                       -> stop: the key is wrong or was rotated; retrying cannot help

Because ingestion is idempotent per event_id, resending a whole batch after a timeout is safe: the
server reports already-stored events as duplicates instead of storing them twice.

Examples
  export ROBOTOPS_DEVICE_KEY=rok_...
  python device_agent.py --url http://localhost:8080 demo            # passing sequence
  python device_agent.py --url http://localhost:8080 demo --slow 3000 # voice prompt 3 s late (timeout)
  python device_agent.py --url http://localhost:8080 send person_detected --ts 0 --cid run-1
  python device_agent.py --url http://localhost:8080 flush            # deliver anything still buffered
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BATCH = 100


class Outbox:
    """Append-only JSONL file. Survives agent restarts and power loss (fsync on write)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, event: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def pending(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text().splitlines()
            if line.strip()
        ]

    def remove(self, event_ids: set[str]) -> None:
        keep = [e for e in self.pending() if e["event_id"] not in event_ids]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in keep))
        os.replace(
            tmp, self.path
        )  # atomic: a crash leaves either the old or the new file


class FatalError(Exception):
    pass


def post_batch(url: str, key: str, events: list[dict], timeout: float) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/ingest/events",
        data=json.dumps({"events": events}).encode(),
        headers={"Content-Type": "application/json", "X-Device-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FatalError(
                f"HTTP {e.code}: device key rejected (wrong or rotated)"
            ) from None
        if e.code == 422:
            raise FatalError(
                f"HTTP 422: server rejected the batch as invalid: {e.read()[:300]!r}"
            ) from None
        raise ConnectionError(f"HTTP {e.code}") from None  # 429 / 5xx: retryable


def flush(
    outbox: Outbox, url: str, key: str, max_attempts: int, timeout: float, log=print
) -> int:
    """Deliver the outbox. Returns the number of events still pending."""
    attempt = 0
    while pending := outbox.pending():
        batch = pending[:BATCH]
        try:
            res = post_batch(url, key, batch, timeout)
            sent = {e["event_id"] for e in batch}
            done = sent & {
                r["event_id"]
                for r in res["results"]
                if r["result"] in ("accepted", "duplicate")
            }
            outbox.remove(done)
            log(
                f"delivered batch: {res['accepted']} accepted, {res['duplicate']} duplicate"
            )
            if (
                done != sent
            ):  # some events rejected (device marked offline): back off, keep them
                raise ConnectionError(f"{len(batch) - len(done)} event(s) not accepted")
        except (ConnectionError, OSError) as e:
            attempt += 1
            if attempt >= max_attempts:
                log(
                    f"giving up for now after {attempt} attempts ({e}); {len(pending)} event(s) stay buffered"
                )
                return len(pending)
            delay = min(30.0, 0.5 * 2**attempt) * random.uniform(
                0.5, 1.0
            )  # exponential backoff + jitter
            log(
                f"delivery failed ({e}); retry {attempt}/{max_attempts - 1} in {delay:.1f}s"
            )
            time.sleep(delay)
            continue
        attempt = 0
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", default=os.getenv("ROBOTOPS_URL", "http://localhost:8080"))
    p.add_argument(
        "--key",
        default=os.getenv("ROBOTOPS_DEVICE_KEY"),
        help="or set ROBOTOPS_DEVICE_KEY",
    )
    p.add_argument(
        "--outbox",
        type=Path,
        default=Path(
            os.getenv("ROBOTOPS_OUTBOX", "~/.robotops/outbox.jsonl")
        ).expanduser(),
    )
    p.add_argument(
        "--attempts",
        type=int,
        default=6,
        help="delivery attempts before leaving events buffered",
    )
    p.add_argument("--timeout", type=float, default=5.0)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="buffer one event, then flush")
    s.add_argument("type")
    s.add_argument("--ts", type=int, required=True, help="virtual timestamp in ms")
    s.add_argument(
        "--cid", required=True, help="correlation id that groups one scenario execution"
    )
    s.add_argument("--payload", default="{}", help="JSON object")
    d = sub.add_parser("demo", help="emit person_detected then voice_prompt_issued")
    d.add_argument(
        "--slow",
        type=int,
        default=400,
        help="ms between the two events (scenario limit is 1000)",
    )
    sub.add_parser("flush", help="deliver anything still buffered")
    a = p.parse_args(argv)
    if not a.key:
        p.error("a device key is required (--key or ROBOTOPS_DEVICE_KEY)")

    outbox = Outbox(a.outbox)
    if a.cmd == "send":
        outbox.add(
            {
                "event_id": uuid.uuid4().hex,
                "type": a.type,
                "ts_ms": a.ts,
                "correlation_id": a.cid,
                "payload": json.loads(a.payload),
            }
        )
    elif a.cmd == "demo":
        cid = "agent-" + uuid.uuid4().hex[:12]
        outbox.add(
            {
                "event_id": f"{cid}-0",
                "type": "person_detected",
                "ts_ms": 0,
                "correlation_id": cid,
                "payload": {"zone": "lobby"},
            }
        )
        outbox.add(
            {
                "event_id": f"{cid}-1",
                "type": "voice_prompt_issued",
                "ts_ms": a.slow,
                "correlation_id": cid,
                "payload": {"lang": "ja"},
            }
        )
        print(f"correlation_id={cid}")
    try:
        left = flush(outbox, a.url, a.key, a.attempts, a.timeout)
    except FatalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
