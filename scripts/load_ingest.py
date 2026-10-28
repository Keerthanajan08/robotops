#!/usr/bin/env python3
"""Load test for device-to-cloud ingestion (POST /api/v1/ingest/events).

Registers a user, creates N devices (each with its own key), then runs C concurrent senders for
D seconds. Every request is a batch of B new, unique events from one device, authenticated with
that device's key, exactly like the edge agent. Reports throughput and latency percentiles and
checks that every sent event was accepted (no errors, no duplicates, no rate-limit rejections).

  pip install httpx
  python scripts/load_ingest.py --url http://localhost:8080 --devices 64 --concurrency 32 --batch 50 --duration 60

Always report results together with the machine they were measured on.
"""

import argparse
import asyncio
import os
import platform
import statistics
import time
import uuid

import httpx


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def setup(client, base, n_devices):
    email = f"load-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        f"{base}/api/v1/auth/register",
        json={"email": email, "password": "load-password-1"},
    )
    r.raise_for_status()
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    keys = []
    for i in range(n_devices):
        r = await client.post(
            f"{base}/api/v1/devices", json={"name": f"load-{i}"}, headers=auth
        )
        r.raise_for_status()
        keys.append(r.json()["device_key"])
    return keys


async def sender(wid, client, base, keys, batch, deadline, stats):
    i = 0
    while time.perf_counter() < deadline:
        key = keys[(wid + i * 7) % len(keys)]
        cid = f"w{wid}-{i}-{uuid.uuid4().hex[:6]}"
        events = [
            {
                "event_id": f"{cid}-{k}",
                "type": "person_detected",
                "ts_ms": k * 10,
                "correlation_id": cid,
                "payload": {"zone": "lobby"},
            }
            for k in range(batch)
        ]
        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{base}/api/v1/ingest/events",
                json={"events": events},
                headers={"X-Device-Key": key},
            )
            dt = (time.perf_counter() - t0) * 1000
            stats["lat"].append(dt)
            if r.status_code == 200:
                body = r.json()
                stats["accepted"] += body["accepted"]
                stats["duplicate"] += body["duplicate"]
            else:
                stats["status"][r.status_code] = (
                    stats["status"].get(r.status_code, 0) + 1
                )
        except httpx.HTTPError as e:
            stats["status"][type(e).__name__] = (
                stats["status"].get(type(e).__name__, 0) + 1
            )
        stats["sent"] += batch
        i += 1


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--devices", type=int, default=64)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--batch", type=int, default=50, help="events per request (1-100)")
    ap.add_argument("--duration", type=int, default=60, help="seconds")
    ap.add_argument(
        "--warmup", type=int, default=5, help="seconds of load before measuring"
    )
    a = ap.parse_args()

    limits = httpx.Limits(
        max_connections=a.concurrency, max_keepalive_connections=a.concurrency
    )
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        keys = await setup(client, a.url, a.devices)
        if a.warmup:
            warm = {"lat": [], "accepted": 0, "duplicate": 0, "sent": 0, "status": {}}
            end = time.perf_counter() + a.warmup
            await asyncio.gather(
                *(
                    sender(w, client, a.url, keys, a.batch, end, warm)
                    for w in range(a.concurrency)
                )
            )
        stats = {"lat": [], "accepted": 0, "duplicate": 0, "sent": 0, "status": {}}
        t0 = time.perf_counter()
        deadline = t0 + a.duration
        await asyncio.gather(
            *(
                sender(w, client, a.url, keys, a.batch, deadline, stats)
                for w in range(a.concurrency)
            )
        )
        elapsed = time.perf_counter() - t0

    lat = stats["lat"]
    reqs = len(lat)
    print(
        f"machine      : {platform.processor() or platform.machine()} | {os.cpu_count()} CPUs | {platform.system()}"
    )
    print(
        f"config       : {a.devices} devices, {a.concurrency} concurrent senders, batch={a.batch}, {a.duration}s (+{a.warmup}s warm-up)"
    )
    print(f"requests     : {reqs} in {elapsed:.1f}s = {reqs / elapsed:.0f} req/s")
    print(
        f"events       : {stats['accepted']} accepted = {stats['accepted'] / elapsed:.0f} events/s"
    )
    print(
        f"latency (ms) : p50 {pct(lat, 50):.0f} | p95 {pct(lat, 95):.0f} | p99 {pct(lat, 99):.0f} | mean {statistics.mean(lat):.0f}"
    )
    print(
        f"correctness  : sent {stats['sent']}, accepted {stats['accepted']}, duplicates {stats['duplicate']}, non-200 {stats['status'] or 0}"
    )


if __name__ == "__main__":
    asyncio.run(main())
