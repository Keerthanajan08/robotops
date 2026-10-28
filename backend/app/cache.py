"""Optional Redis cache + failure counters. Fails open: if Redis is unset or down, falls back to
per-process memory (counters) or no caching (metrics). Documented trade-off: memory counters are not shared across workers."""

import json
import logging
import os
import time

log = logging.getLogger("robotops")
_r = None
_mem: dict[str, tuple[int, float]] = {}


def client():
    global _r
    if _r is None and os.getenv("REDIS_URL"):
        import redis

        _r = redis.Redis.from_url(
            os.environ["REDIS_URL"], socket_timeout=1, decode_responses=True
        )
    return _r


def set_client(c):
    global _r
    _r = c
    _mem.clear()


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        log.warning("redis unavailable: %s", type(e).__name__)
        return default


def version(uid: str) -> int:
    r = client()
    return int(_safe(lambda: r.get(f"ver:{uid}"), 0) or 0) if r else 0


def bump(uid: str):
    r = client()
    if r:
        _safe(lambda: r.incr(f"ver:{uid}"))


def get_json(key):
    r = client()
    v = _safe(lambda: r.get(key)) if r else None
    return json.loads(v) if v else None


def set_json(key, val, ttl):
    r = client()
    if r:
        _safe(lambda: r.set(key, json.dumps(val), ex=ttl))


def incr_window(key: str, window: int) -> int:
    r = client()
    if r:
        n = _safe(lambda: r.incr(key))
        if n is not None:
            if n == 1:
                _safe(lambda: r.expire(key, window))
            return int(n)
    now = time.time()
    c, exp = _mem.get(key, (0, 0.0))
    if now > exp:
        c, exp = 0, now + window
    _mem[key] = (c + 1, exp)
    return c + 1


def get_count(key: str) -> int:
    r = client()
    if r:
        v = _safe(lambda: r.get(key), "unset")
        if v != "unset":
            return int(v or 0)
    c, exp = _mem.get(key, (0, 0.0))
    return c if time.time() <= exp else 0


def clear(key: str):
    r = client()
    if r:
        _safe(lambda: r.delete(key))
    _mem.pop(key, None)
