#!/usr/bin/env python3
"""Worker scale + crash check against a running stack (docker compose up --scale worker=3).

Phase 1: queue N quick jobs; every job must complete exactly once with exactly one run.
Phase 2: queue slow jobs, SIGKILL some workers mid-job, and check the survivors recover the
         orphaned jobs after the lease expires (WORKER_LEASE_SECONDS, default 60s).

  python scripts/worker_chaos.py --url http://localhost:8080 --kill robotops-worker-1 robotops-worker-2
"""

import argparse
import json
import subprocess
import time
import urllib.request
import uuid


def call(base, path, method="GET", body=None, token=None):
    req = urllib.request.Request(
        base + "/api/v1" + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or "null")


def wait_done(base, tok, ids, timeout_s):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        jobs = [call(base, f"/simulations/{j}", token=tok) for j in ids]
        if all(j["status"] in ("completed", "failed", "cancelled") for j in jobs):
            return jobs, time.time() - t0
        time.sleep(1)
    raise SystemExit(f"timeout: {[j['status'] for j in jobs]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument(
        "--kill",
        nargs="*",
        default=[],
        help="worker container names to SIGKILL in phase 2",
    )
    a = ap.parse_args()
    b = a.url
    tok = call(
        b,
        "/auth/register",
        "POST",
        {
            "email": f"chaos-{uuid.uuid4().hex[:8]}@example.com",
            "password": "chaos-password-1",
        },
    )["access_token"]
    pid = call(b, "/projects", "POST", {"name": "chaos"}, tok)["id"]
    steps = [
        {"type": "person_detected"},
        {"type": "voice_prompt_issued", "within_ms": 1000},
        {"type": "notification_sent", "within_ms": 5000},
    ]
    sid = call(
        b, f"/projects/{pid}/scenarios", "POST", {"name": "chaos", "steps": steps}, tok
    )["id"]
    did = call(b, "/devices", "POST", {"name": "chaos-bot"}, tok)["id"]

    ids = [
        call(
            b,
            f"/devices/{did}/simulations",
            "POST",
            {"scenario_id": sid, "delay_ms": 200},
            tok,
        )["id"]
        for _ in range(a.jobs)
    ]
    jobs, took = wait_done(b, tok, ids, 120)
    runs = {j["run_id"] for j in jobs}
    ok = (
        all(j["status"] == "completed" and j["events_emitted"] == 3 for j in jobs)
        and len(runs) == a.jobs
        and None not in runs
    )
    total_runs = len(call(b, f"/runs?device_id={did}&limit=200", token=tok))
    print(
        f"phase 1: {a.jobs} jobs completed in {took:.1f}s; distinct runs={len(runs)}; runs stored for device={total_runs} -> {'OK' if ok and total_runs == a.jobs else 'FAIL'}"
    )

    if not (ok and total_runs == a.jobs):
        raise SystemExit(1)
    if not a.kill:
        return
    slow = [
        call(
            b,
            f"/devices/{did}/simulations",
            "POST",
            {"scenario_id": sid, "delay_ms": 4000},
            tok,
        )["id"]
        for _ in range(3)
    ]
    time.sleep(2)  # every worker is now mid-job
    for name in a.kill:
        subprocess.run(["docker", "kill", name], check=True, capture_output=True)
    print(f"phase 2: killed {a.kill} mid-job; waiting for lease expiry + recovery...")
    jobs, took = wait_done(b, tok, slow, 240)
    runs = [j["run_id"] for j in jobs]
    ok = (
        all(j["status"] == "completed" and j["events_emitted"] == 3 for j in jobs)
        and len(set(runs)) == 3
        and None not in runs
    )
    total_runs = len(call(b, f"/runs?device_id={did}&limit=200", token=tok))
    print(
        f"phase 2: all 3 slow jobs completed after {took:.0f}s; runs stored for device={total_runs} (expected {a.jobs + 3}) -> {'OK' if ok and total_runs == a.jobs + 3 else 'FAIL'}"
    )
    if not (ok and total_runs == a.jobs + 3):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
