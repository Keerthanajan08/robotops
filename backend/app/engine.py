"""Deterministic scenario evaluator. Pure function: no I/O, no clock, no sleeps.
Events carry virtual timestamps (ts_ms); a step's within_ms is measured from the
previously matched step. Events are ordered by ts_ms (stable for ties)."""


def _match(step, ev):
    return ev["type"] == step["type"] and all(
        ev.get("payload", {}).get(k) == v for k, v in step.get("match", {}).items()
    )


def evaluate(steps: list[dict], events: list[dict]) -> dict:
    evs = sorted(events, key=lambda e: e["ts_ms"])
    expected = {s["type"] for s in steps}
    findings, used, pos, prev_ts = [], set(), 0, None

    def add(kind, step, msg):
        findings.append({"kind": kind, "step": step, "message": msg})

    for i, s in enumerate(steps):
        j = next((k for k in range(pos, len(evs)) if _match(s, evs[k])), None)
        if j is None:
            early = next(
                (k for k in range(pos) if k not in used and _match(s, evs[k])), None
            )
            if early is not None:
                used.add(early)
                add(
                    "out_of_order",
                    i,
                    f"'{s['type']}' occurred before its expected position (step {i + 1}).",
                )
            elif s.get("required", True):
                add(
                    "missing",
                    i,
                    f"Required event '{s['type']}' (step {i + 1}) never occurred.",
                )
            continue
        for k in range(pos, j):
            if evs[k]["type"] not in expected:
                add(
                    "unexpected",
                    i,
                    f"Unexpected '{evs[k]['type']}' at t={evs[k]['ts_ms']}ms before step {i + 1}.",
                )
        used.add(j)
        pos = j + 1
        lim = s.get("within_ms")
        if lim is not None and prev_ts is not None and evs[j]["ts_ms"] - prev_ts > lim:
            add(
                "timeout",
                i,
                f"'{s['type']}' arrived {evs[j]['ts_ms'] - prev_ts}ms after previous step; limit {lim}ms.",
            )
        prev_ts = evs[j]["ts_ms"]
    for k in range(pos, len(evs)):
        if evs[k]["type"] not in expected:
            add(
                "unexpected",
                None,
                f"Unexpected '{evs[k]['type']}' at t={evs[k]['ts_ms']}ms after final step.",
            )
    return {
        "status": "failed" if findings else "passed",
        "findings": findings,
        "duration_ms": evs[-1]["ts_ms"] - evs[0]["ts_ms"] if evs else 0,
    }
