"""Deterministic simulated event producer: turns a scenario's steps into a pass or fail event sequence (virtual time)."""


def generate(steps: list[dict], fail: bool) -> list[dict]:
    t, out = 0, []
    for i, s in enumerate(steps):
        last = i == len(steps) - 1
        if fail and last:
            if s.get("within_ms") is None:
                continue  # failing variant: required final event never arrives
            t += s["within_ms"] + 1000  # ...or arrives after its deadline
        elif i > 0:
            t += max(1, (s.get("within_ms") or 500) // 2)
        out.append(
            {"type": s["type"], "ts_ms": t, "payload": dict(s.get("match") or {})}
        )  # satisfy payload conditions
    return out
