import asyncio
import contextvars
import csv
import hashlib
import html
import io
import json
import logging
import os
import secrets
import statistics
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from . import cache, obs
from .db import *
from .engine import evaluate
from .simulator import generate
from .worker import worker_loop

req_id: contextvars.ContextVar[str] = contextvars.ContextVar("req_id", default="-")
_old_factory = logging.getLogRecordFactory()


def _factory(*a, **k):
    r = _old_factory(*a, **k)
    r.rid = req_id.get()
    return r


logging.setLogRecordFactory(_factory)
logging.basicConfig(
    level=logging.INFO,
    format='{"t":"%(asctime)s","lvl":"%(levelname)s","rid":"%(rid)s","msg":"%(message)s"}',
)
log = logging.getLogger("robotops")
ENV = os.getenv("ROBOTOPS_ENV", "dev")
SECRET = os.getenv("JWT_SECRET") or (
    "dev-only-secret-change-me-0123456789" if ENV == "dev" else None
)
if not SECRET or len(SECRET) < 32:
    raise RuntimeError("JWT_SECRET (>=32 chars) is required when ROBOTOPS_ENV != dev")
MAX_PAYLOAD_BYTES = 8192
MAX_BODY = int(os.getenv("MAX_BODY_BYTES", "1000000"))
LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")
JWT_TTL_MINUTES = int(os.getenv("JWT_TTL_MINUTES", "60"))
if not 1 <= JWT_TTL_MINUTES <= 24 * 60:
    raise RuntimeError("JWT_TTL_MINUTES must be between 1 and 1440")
if ENV != "dev" and DATABASE_URL.startswith("sqlite"):
    raise RuntimeError("DATABASE_URL must point to PostgreSQL when ROBOTOPS_ENV != dev")
EventType = Literal[
    "person_detected",
    "voice_prompt_issued",
    "acknowledgement_received",
    "notification_sent",
    "device_offline",
    "api_request_failed",
]

if ENV == "dev":
    Base.metadata.create_all(engine)  # non-dev environments use `alembic upgrade head`


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = (
        asyncio.create_task(worker_loop(_job_run))
        if os.getenv("ROBOTOPS_WORKER", "1") == "1"
        else None
    )
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="RobotOps API (simulated devices only)", version="0.1.0", lifespan=lifespan
)
bearer = HTTPBearer(auto_error=False)
if os.getenv("CORS_ORIGINS"):
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ["CORS_ORIGINS"].split(",")],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Device-Key"],
        expose_headers=["X-Total-Count", "X-Request-ID"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
    req_id.set(rid)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY:
        return JSONResponse(
            {"error": {"code": 413, "message": "Request body too large"}},
            413,
            headers={"X-Request-ID": rid},
        )
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("unhandled error %s %s", request.method, request.url.path)
        resp = JSONResponse(
            {
                "error": {
                    "code": 500,
                    "message": "Internal server error",
                    "request_id": rid,
                }
            },
            500,
        )
    elapsed = time.perf_counter() - t0
    resp.headers["X-Request-ID"] = rid
    route = request.scope.get("route")
    label = getattr(
        route, "path", "unmatched"
    )  # template, never the raw path (bounded cardinality)
    if label != "/metrics":
        obs.HTTP_REQUESTS.labels(request.method, label, str(resp.status_code)).inc()
        obs.HTTP_LATENCY.labels(request.method, label).observe(elapsed)
    log.info(
        "%s %s -> %s %.1fms",
        request.method,
        request.url.path,
        resp.status_code,
        elapsed * 1000,
    )
    return resp


@app.exception_handler(RequestValidationError)
async def validation_err(_: Request, e: RequestValidationError):
    det = [
        {"field": ".".join(str(x) for x in d["loc"][1:]), "message": d["msg"]}
        for d in e.errors()
    ]
    msg = "Validation failed: " + "; ".join(
        f"{d['field']}: {d['message']}" for d in det
    )
    return JSONResponse({"error": {"code": 422, "message": msg, "details": det}}, 422)


@app.exception_handler(HTTPException)
async def http_err(_: Request, e: HTTPException):
    return JSONResponse(
        {"error": {"code": e.status_code, "message": e.detail}}, e.status_code
    )


def get_db():
    with SessionLocal() as db:
        yield db


def current_user(cred=Depends(bearer), db=Depends(get_db)) -> User:
    if cred is None:
        raise HTTPException(401, "Invalid or missing token")
    try:
        claims = jwt.decode(
            cred.credentials,
            SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired; please log in again") from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or missing token") from None
    u = db.get(User, claims["sub"])
    if not u:
        raise HTTPException(401, "Unknown user")
    return u


def owned(db, model, id_, user):
    o = db.get(model, id_)
    if not o or o.owner_id != user.id:
        raise HTTPException(404, f"{model.__name__} not found")  # no existence leak
    return o


# ---- schemas
class Creds(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=10, max_length=128)


class Step(BaseModel):
    type: EventType
    within_ms: int | None = Field(None, ge=0)
    required: bool = True
    match: dict = {}


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    description: str = Field("", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    steps: list[Step] = Field(min_length=1, max_length=50)


class EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    type: EventType
    ts_ms: int = Field(ge=0)
    correlation_id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=64)
    payload: dict = {}

    @field_validator("payload")
    @classmethod
    def small(cls, v):
        if len(json.dumps(v)) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        return v


class RunIn(BaseModel):
    events: list[EventIn] | None = None
    correlation_id: str | None = None
    device_id: str | None = None
    defer: bool = False  # create a 'running' run that is completed or cancelled later


def audit(db, u, action, target=""):
    db.add(AuditEvent(owner_id=u.id, action=action, target=target))
    db.commit()


# ---- auth
def token(u):
    """Short-lived access token. Expiry is enforced on every request (see current_user)."""
    iat = int(time.time())
    claims = {"sub": u.id, "iat": iat, "exp": iat + JWT_TTL_MINUTES * 60}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def token_response(u):
    return {
        "access_token": token(u),
        "token_type": "bearer",
        "expires_in": JWT_TTL_MINUTES * 60,
    }


@app.post("/api/v1/auth/register", status_code=201)
def register(c: Creds, db=Depends(get_db)):
    if db.scalar(select(User).where(User.email == c.email.lower())):
        raise HTTPException(409, "Email already registered")
    u = User(
        email=c.email.lower(),
        password_hash=bcrypt.hashpw(c.password.encode(), bcrypt.gensalt()).decode(),
    )
    db.add(u)
    db.commit()
    audit(db, u, "user_registered", u.id)
    return token_response(u)


@app.post("/api/v1/auth/login")
def login(c: Creds, db=Depends(get_db)):
    u = db.scalar(select(User).where(User.email == c.email.lower()))
    rk = f"lf:{c.email.lower()}"
    if cache.get_count(rk) >= LOGIN_MAX_FAILS:
        raise HTTPException(429, "Too many failed attempts; try again in 15 minutes")
    if not u or not bcrypt.checkpw(c.password.encode(), u.password_hash.encode()):
        cache.incr_window(rk, 900)
        log.warning("login_failed")
        raise HTTPException(401, "Invalid credentials")
    cache.clear(rk)
    audit(db, u, "login_ok")
    return token_response(u)


# ---- projects / scenarios
@app.post("/api/v1/projects", status_code=201)
def create_project(body: dict, u=Depends(current_user), db=Depends(get_db)):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "name required")
    p = Project(owner_id=u.id, name=name)
    db.add(p)
    db.commit()
    return {"id": p.id, "name": p.name}


@app.get("/api/v1/projects")
def list_projects(u=Depends(current_user), db=Depends(get_db)):
    return [
        {"id": p.id, "name": p.name}
        for p in db.scalars(
            select(Project).where(Project.owner_id == u.id).order_by(Project.name)
        )
    ]


@app.delete("/api/v1/projects/{pid}", status_code=204)
def delete_project(pid: str, u=Depends(current_user), db=Depends(get_db)):
    p = owned(db, Project, pid, u)
    for s in db.scalars(select(Scenario).where(Scenario.project_id == pid)):
        db.delete(s)
    db.delete(p)
    db.commit()
    audit(db, u, "project_deleted", pid)


@app.post("/api/v1/projects/{pid}/scenarios", status_code=201)
def create_scenario(
    pid: str, s: ScenarioIn, u=Depends(current_user), db=Depends(get_db)
):
    owned(db, Project, pid, u)
    sc = Scenario(
        owner_id=u.id,
        project_id=pid,
        name=s.name,
        severity=s.severity,
        description=s.description,
        tags=s.tags,
        steps=[x.model_dump() for x in s.steps],
    )
    db.add(sc)
    db.commit()
    return {"id": sc.id, "version": sc.version}


def _page(
    db,
    q,
    response: Response,
    sort: str,
    allowed: dict,
    default,
    limit: int,
    offset: int,
    cap: int,
):
    """Shared pagination + whitelisted sorting. `sort` is a column name, '-' prefix for descending.
    Total count is returned in the X-Total-Count header so list bodies stay plain arrays."""
    col = allowed.get(sort.lstrip("-"))
    if col is None:
        raise HTTPException(
            422,
            f"sort must be one of: {', '.join(sorted(allowed))} (prefix '-' for descending)",
        )
    response.headers["X-Total-Count"] = str(
        db.scalar(select(func.count()).select_from(q.subquery()))
    )
    order = col.desc() if sort.startswith("-") else col.asc()
    return db.scalars(q.order_by(order, default).limit(min(limit, cap)).offset(offset))


@app.get("/api/v1/projects/{pid}/scenarios")
def list_scenarios(
    pid: str,
    response: Response,
    q: str = "",
    sort: str = "name",
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    u=Depends(current_user),
    db=Depends(get_db),
):
    owned(db, Project, pid, u)
    base = select(Scenario).where(
        Scenario.project_id == pid, Scenario.name.icontains(q)
    )
    allowed = {
        "name": Scenario.name,
        "severity": Scenario.severity,
        "version": Scenario.version,
    }
    return [
        _sc(s)
        for s in _page(
            db, base, response, sort, allowed, Scenario.id, limit, offset, 100
        )
    ]


# ---- devices / ingestion (simulated)
DEVICE_KEY_PREFIX = "rok_"
DEVICE_RATE_PER_MIN = int(os.getenv("DEVICE_RATE_PER_MIN", "600"))


def _hash_key(key: str) -> str:
    # Keys are 256-bit random values, so a fast hash is sufficient (no dictionary attack is possible);
    # lookup by hash also avoids timing side channels from string comparison.
    return hashlib.sha256(key.encode()).hexdigest()


def _issue_key(d: Device) -> str:
    key = DEVICE_KEY_PREFIX + secrets.token_urlsafe(32)
    d.key_hash, d.key_prefix, d.key_rotated_at = _hash_key(key), key[:12], now()
    return key


def _device_out(d: Device) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "simulated": d.simulated,
        "online": d.online,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "key_prefix": d.key_prefix,
        "key_rotated_at": d.key_rotated_at.isoformat() if d.key_rotated_at else None,
    }


@app.post("/api/v1/devices", status_code=201)
def create_device(body: dict, u=Depends(current_user), db=Depends(get_db)):
    d = Device(owner_id=u.id, name=str(body.get("name", "sim-robot"))[:80])
    key = _issue_key(d)
    db.add(d)
    db.commit()
    audit(db, u, "device_created", d.id)
    # The plaintext key is returned exactly once; only its hash is stored.
    return {**_device_out(d), "device_key": key}


@app.post("/api/v1/devices/{did}/key/rotate")
def rotate_device_key(did: str, u=Depends(current_user), db=Depends(get_db)):
    """Issues a new device key and immediately invalidates the old one."""
    d = owned(db, Device, did, u)
    key = _issue_key(d)
    db.commit()
    audit(db, u, "device_key_rotated", did)
    return {**_device_out(d), "device_key": key}


def _store_event(db, d: Device, e: "EventIn") -> dict:
    """Shared ingestion path (user-authenticated and device-authenticated).
    Idempotent on (device_id, event_id): retries never double-insert, including concurrent retries."""
    if not d.online:
        if not db.scalar(
            select(DeliveryFailure).where(
                DeliveryFailure.device_id == d.id,
                DeliveryFailure.event_id == e.event_id,
                DeliveryFailure.status == "failed",
            )
        ):
            db.add(
                DeliveryFailure(
                    owner_id=d.owner_id,
                    device_id=d.id,
                    event_id=e.event_id,
                    reason="device_offline",
                    payload=e.model_dump(),
                )
            )
            db.commit()
        obs.EVENTS_INGESTED.labels("rejected_offline").inc()
        return {"event_id": e.event_id, "result": "rejected_offline"}
    if db.scalar(
        select(Event).where(Event.device_id == d.id, Event.event_id == e.event_id)
    ):
        obs.EVENTS_INGESTED.labels("duplicate").inc()
        return {"event_id": e.event_id, "result": "duplicate"}
    ev = Event(owner_id=d.owner_id, device_id=d.id, **e.model_dump())
    d.last_seen = now()
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:  # a concurrent retry inserted the same event first
        db.rollback()
        obs.EVENTS_INGESTED.labels("duplicate").inc()
        return {"event_id": e.event_id, "result": "duplicate"}
    obs.EVENTS_INGESTED.labels("accepted").inc()
    return {
        "event_id": e.event_id,
        "result": "accepted",
        "id": ev.id,
        "correlation_id": ev.correlation_id,
    }


@app.post("/api/v1/devices/{did}/events", status_code=201)
def ingest(did: str, e: EventIn, u=Depends(current_user), db=Depends(get_db)):
    """User-authenticated ingestion (used by the UI and tests)."""
    d = owned(db, Device, did, u)
    r = _store_event(db, d, e)
    if r["result"] == "rejected_offline":
        raise HTTPException(
            503,
            "Simulated device is offline; delivery recorded as failed and can be retried",
        )
    if r["result"] == "duplicate":
        return JSONResponse(
            {"duplicate": True}, 200
        )  # retry-safe: no double processing
    return {"id": r["id"], "correlation_id": r["correlation_id"], "duplicate": False}


def current_device(
    x_device_key: str | None = Header(None), db=Depends(get_db)
) -> Device:
    if not x_device_key or not x_device_key.startswith(DEVICE_KEY_PREFIX):
        raise HTTPException(401, "Missing or invalid X-Device-Key")
    d = db.scalar(select(Device).where(Device.key_hash == _hash_key(x_device_key)))
    if d is None:
        raise HTTPException(401, "Missing or invalid X-Device-Key")
    if cache.incr_window(f"dr:{d.id}", 60) > DEVICE_RATE_PER_MIN:
        raise HTTPException(429, "Device rate limit exceeded; back off and retry")
    return d


class BatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=100)


def _store_batch(db, d: Device, events: list["EventIn"]) -> list[dict]:
    """Set-based ingestion for an online device: one INSERT ... ON CONFLICT DO NOTHING RETURNING and
    one commit per batch, instead of a SELECT + COMMIT per event. The unique (device_id, event_id)
    constraint is the single source of truth for duplicates, so concurrent retries stay exactly-once."""
    ins = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    first, rows = {}, []
    for e in events:  # an event_id repeated inside one batch: first occurrence wins
        if e.event_id not in first:
            first[e.event_id] = e
            rows.append(
                {
                    "id": uid(),
                    "owner_id": d.owner_id,
                    "device_id": d.id,
                    **e.model_dump(),
                }
            )
    stmt = (
        ins(Event)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["device_id", "event_id"])
        .returning(Event.event_id, Event.id)
    )
    inserted = dict(db.execute(stmt).all())
    if inserted:
        d.last_seen = now()
    db.commit()
    results, seen = [], set()
    for e in events:
        if e.event_id in inserted and e.event_id not in seen:
            seen.add(e.event_id)
            results.append(
                {
                    "event_id": e.event_id,
                    "result": "accepted",
                    "id": inserted[e.event_id],
                    "correlation_id": e.correlation_id,
                }
            )
        else:
            results.append({"event_id": e.event_id, "result": "duplicate"})
    n_acc = len(seen)
    obs.EVENTS_INGESTED.labels("accepted").inc(n_acc)
    obs.EVENTS_INGESTED.labels("duplicate").inc(len(events) - n_acc)
    return results


@app.post("/api/v1/ingest/events")
def device_ingest(
    body: BatchIn, d: Device = Depends(current_device), db=Depends(get_db)
):
    """Device-to-cloud ingestion. Authenticated with the device's own key (not a user JWT), batched,
    and idempotent per event_id so an agent can safely resend its whole buffer after a network failure.
    Returns a per-event result; HTTP 503 only if the device is marked offline (nothing was accepted)."""
    results = (
        _store_batch(db, d, body.events)
        if d.online
        else [_store_event(db, d, e) for e in body.events]
    )
    if all(r["result"] == "rejected_offline" for r in results):
        return JSONResponse(
            {
                "error": {
                    "code": 503,
                    "message": "Device is offline; deliveries recorded as failed",
                },
                "results": results,
            },
            503,
        )
    counts = Counter(r["result"] for r in results)
    return {
        "device_id": d.id,
        "accepted": counts["accepted"],
        "duplicate": counts["duplicate"],
        "results": results,
    }


class SimIn(BaseModel):
    scenario_id: str
    fail: bool = False


@app.post("/api/v1/devices/{did}/simulate", status_code=201)
def simulate(did: str, body: SimIn, u=Depends(current_user), db=Depends(get_db)):
    """Simulated producer: ingests a generated event sequence for a scenario under a fresh correlation ID."""
    d = owned(db, Device, did, u)
    sc = owned(db, Scenario, body.scenario_id, u)
    if not d.online:
        raise HTTPException(503, "Simulated device is offline; delivery rejected")
    cid = uuid.uuid4().hex
    evs = generate(sc.steps, body.fail)
    for i, e in enumerate(evs):
        db.add(
            Event(
                owner_id=u.id,
                device_id=did,
                event_id=f"{cid}-{i}",
                correlation_id=cid,
                **e,
            )
        )
    d.last_seen = now()
    db.commit()
    return {"correlation_id": cid, "events": len(evs), "simulated": True}


class JobIn(BaseModel):
    scenario_id: str
    fail: bool = False
    delay_ms: int = Field(0, ge=0, le=5000)
    auto_run: bool = True


def _job(j):
    return {
        "id": j.id,
        "status": j.status,
        "device_id": j.device_id,
        "scenario_id": j.scenario_id,
        "correlation_id": j.correlation_id,
        "fail": j.fail,
        "events_total": j.events_total,
        "events_emitted": j.events_emitted,
        "error": j.error,
        "run_id": j.run_id,
        "created_at": j.created_at.isoformat(),
    }


@app.post("/api/v1/devices/{did}/simulations", status_code=202)
def queue_simulation(
    did: str, body: JobIn, u=Depends(current_user), db=Depends(get_db)
):
    owned(db, Device, did, u)
    owned(db, Scenario, body.scenario_id, u)
    j = SimulationJob(
        owner_id=u.id,
        device_id=did,
        scenario_id=body.scenario_id,
        fail=body.fail,
        delay_ms=body.delay_ms,
        auto_run=body.auto_run,
        correlation_id=uuid.uuid4().hex,
    )
    db.add(j)
    db.commit()
    return _job(j)


@app.post("/api/v1/devices/{did}/{action}")
def connectivity(
    did: str,
    action: Literal["disconnect", "reconnect"],
    u=Depends(current_user),
    db=Depends(get_db),
):
    d = owned(db, Device, did, u)
    d.online = action == "reconnect"
    db.commit()
    return {"online": d.online}


def _sc(s):
    return {
        "id": s.id,
        "name": s.name,
        "severity": s.severity,
        "description": s.description,
        "tags": s.tags,
        "steps": s.steps,
        "version": s.version,
    }


@app.get("/api/v1/me")
def me(u=Depends(current_user)):
    return {"id": u.id, "email": u.email}


@app.get("/api/v1/projects/{pid}")
def get_project(pid: str, u=Depends(current_user), db=Depends(get_db)):
    p = owned(db, Project, pid, u)
    return {"id": p.id, "name": p.name}


@app.patch("/api/v1/projects/{pid}")
def rename_project(pid: str, body: dict, u=Depends(current_user), db=Depends(get_db)):
    p = owned(db, Project, pid, u)
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "name required")
    p.name = name
    db.commit()
    return {"id": p.id, "name": p.name}


@app.get("/api/v1/scenarios/{sid}")
def get_scenario(sid: str, u=Depends(current_user), db=Depends(get_db)):
    return _sc(owned(db, Scenario, sid, u))


@app.put("/api/v1/scenarios/{sid}")
def update_scenario(
    sid: str, body: ScenarioIn, u=Depends(current_user), db=Depends(get_db)
):
    sc = owned(db, Scenario, sid, u)
    sc.name, sc.severity, sc.description, sc.tags = (
        body.name,
        body.severity,
        body.description,
        body.tags,
    )
    sc.steps = [x.model_dump() for x in body.steps]
    sc.version += 1  # runs keep their own snapshot
    db.commit()
    audit(db, u, "scenario_updated", sid)
    return _sc(sc)


@app.delete("/api/v1/scenarios/{sid}", status_code=204)
def delete_scenario(sid: str, u=Depends(current_user), db=Depends(get_db)):
    db.delete(owned(db, Scenario, sid, u))
    db.commit()
    audit(db, u, "scenario_deleted", sid)


@app.post("/api/v1/scenarios/{sid}/duplicate", status_code=201)
def duplicate_scenario(sid: str, u=Depends(current_user), db=Depends(get_db)):
    o = owned(db, Scenario, sid, u)
    c = Scenario(
        owner_id=u.id,
        project_id=o.project_id,
        name=f"{o.name} (copy)"[:120],
        severity=o.severity,
        description=o.description,
        tags=list(o.tags),
        steps=list(o.steps),
    )
    db.add(c)
    db.commit()
    return _sc(c)


# ---- runs / replay / reports / metrics
def _out(r):
    return {
        "id": r.id,
        "status": r.status,
        "findings": r.findings,
        "duration_ms": r.duration_ms,
        "events": r.events,
        "scenario": r.scenario_snapshot,
        "replay_of": r.replay_of,
        "correlation_id": r.correlation_id,
        "device_id": r.device_id,
        "project_id": r.project_id,
        "seeded": r.seeded,
        "device_state": r.device_state,
        "logs": r.logs or [],
        "created_at": r.created_at.isoformat(),
    }


def _entry(level, msg):
    return {
        "at": now().isoformat(),
        "level": level,
        "msg": msg,
        "request_id": req_id.get(),
    }


def _eval_logs(events, res):
    out = [_entry("info", f"{len(events)} event(s) evaluated")]
    out += [
        _entry("info", f"event {e['type']} at t={e['ts_ms']}ms")
        for e in sorted(events, key=lambda e: e["ts_ms"])
    ]
    out += [_entry("error", f"{f['kind']}: {f['message']}") for f in res["findings"]]
    out.append(
        _entry(
            "info" if res["status"] == "passed" else "error",
            f"run {res['status']} in {res['duration_ms']}ms (virtual time)",
        )
    )
    return out


def _job_run(
    job,
):  # callback used by the worker to create the run once a job's events are delivered
    with SessionLocal() as db:
        sc, u = db.get(Scenario, job.scenario_id), db.get(User, job.owner_id)
        events, dev = _corr_events(db, u, job.correlation_id)
        return _save(
            db,
            u,
            sc.id,
            _snap(sc),
            events,
            project_id=sc.project_id,
            correlation_id=job.correlation_id,
            device_id=dev or job.device_id,
        ).id


def _snap(sc):
    return {"name": sc.name, "version": sc.version, "steps": sc.steps}


def _save(db, u, sc_id, snap, events, **kw):
    res = evaluate(snap["steps"], events)
    kw.setdefault("device_state", _dev_state(db, kw.get("device_id")))
    logs = [*kw.pop("pre_logs", []), *_eval_logs(events, res)]
    run = Run(
        owner_id=u.id,
        scenario_id=sc_id,
        scenario_snapshot=snap,
        events=events,
        status=res["status"],
        findings=res["findings"],
        duration_ms=res["duration_ms"],
        logs=logs,
        **kw,
    )
    db.add(run)
    db.commit()
    cache.bump(u.id)
    obs.RUNS_RECORDED.labels(run.status).inc()
    log.info("run %s status=%s", run.id, run.status)
    return run


def _corr_events(db, u, cid):
    rows = list(
        db.scalars(
            select(Event)
            .where(Event.owner_id == u.id, Event.correlation_id == cid)
            .order_by(Event.ts_ms)
        )
    )
    return [{"type": r.type, "ts_ms": r.ts_ms, "payload": r.payload} for r in rows], (
        rows[0].device_id if rows else None
    )


@app.post("/api/v1/scenarios/{sid}/runs", status_code=201)
def run_scenario(sid: str, body: RunIn, u=Depends(current_user), db=Depends(get_db)):
    sc = owned(db, Scenario, sid, u)
    kw = dict(
        project_id=sc.project_id,
        correlation_id=body.correlation_id or uuid.uuid4().hex,
        device_id=body.device_id,
    )
    if body.device_id:
        owned(db, Device, body.device_id, u)
    if body.defer:
        run = Run(
            owner_id=u.id,
            scenario_id=sc.id,
            scenario_snapshot=_snap(sc),
            events=[],
            status="running",
            findings=[],
            duration_ms=0,
            logs=[
                _entry(
                    "info",
                    "run created; awaiting events for correlation_id "
                    + kw["correlation_id"],
                )
            ],
            **kw,
        )
        db.add(run)
        db.commit()
        return _out(run)
    if body.events is not None:
        events = [e.model_dump() for e in body.events]
    elif body.correlation_id:
        events, dev = _corr_events(db, u, body.correlation_id)
        kw["device_id"] = kw["device_id"] or dev
    else:
        raise HTTPException(422, "Provide events or correlation_id")
    return _out(_save(db, u, sc.id, _snap(sc), events, **kw))


@app.post("/api/v1/runs/{rid}/complete")
def complete_run(rid: str, u=Depends(current_user), db=Depends(get_db)):
    r = owned(db, Run, rid, u)
    if r.status != "running":
        raise HTTPException(409, f"Run is {r.status}, not running")
    events, dev = _corr_events(db, u, r.correlation_id)
    res = evaluate(r.scenario_snapshot["steps"], events)
    r.events, r.status, r.findings, r.duration_ms, r.device_id = (
        events,
        res["status"],
        res["findings"],
        res["duration_ms"],
        r.device_id or dev,
    )
    r.logs = [
        *(r.logs or []),
        _entry("info", "run completed on request"),
        *_eval_logs(events, res),
    ]
    r.device_state = _dev_state(db, r.device_id)
    db.commit()
    cache.bump(u.id)
    return _out(r)


@app.post("/api/v1/runs/{rid}/cancel")
def cancel_run(rid: str, u=Depends(current_user), db=Depends(get_db)):
    r = owned(db, Run, rid, u)
    if r.status != "running":
        raise HTTPException(
            409, f"Only running runs can be cancelled (run is {r.status})"
        )
    r.status = "cancelled"
    r.logs = [*(r.logs or []), _entry("warning", "run cancelled by user")]
    db.commit()
    cache.bump(u.id)
    audit(db, u, "run_cancelled", rid)
    return _out(r)


@app.get("/api/v1/runs/{rid}")
def get_run(rid: str, u=Depends(current_user), db=Depends(get_db)):
    return _out(owned(db, Run, rid, u))


@app.post("/api/v1/runs/{rid}/replay", status_code=201)
def replay(rid: str, u=Depends(current_user), db=Depends(get_db)):
    """Re-evaluates the recorded events against the ORIGINAL scenario snapshot (reproduces the failure)."""
    r = owned(db, Run, rid, u)
    if r.status not in ("passed", "failed"):
        raise HTTPException(409, f"Cannot replay a {r.status} run")
    return _out(
        _save(
            db,
            u,
            r.scenario_id,
            r.scenario_snapshot,
            r.events,
            replay_of=r.id,
            project_id=r.project_id,
            device_id=r.device_id,
            correlation_id=r.correlation_id,
            device_state=r.device_state,
            pre_logs=[_entry("info", f"replay of run {r.id}")],
        )
    )


def _filtered(u, status, project_id, device_id, date_from, date_to):
    q = select(Run).where(Run.owner_id == u.id)
    for col, val in (
        (Run.status, status),
        (Run.project_id, project_id),
        (Run.device_id, device_id),
    ):
        if val:
            q = q.where(col == val)
    if date_from:
        q = q.where(Run.created_at >= date_from)
    if date_to:
        q = q.where(Run.created_at <= date_to)
    return q


@app.get("/api/v1/runs")
def list_runs(
    response: Response,
    status: str | None = None,
    project_id: str | None = None,
    device_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "-created_at",
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    u=Depends(current_user),
    db=Depends(get_db),
):
    base = _filtered(u, status, project_id, device_id, date_from, date_to)
    allowed = {
        "created_at": Run.created_at,
        "duration_ms": Run.duration_ms,
        "status": Run.status,
    }
    rows = _page(db, base, response, sort, allowed, Run.id, limit, offset, 200)
    return [
        {
            "id": r.id,
            "scenario_id": r.scenario_id,
            "scenario": r.scenario_snapshot["name"],
            "status": r.status,
            "duration_ms": r.duration_ms,
            "seeded": r.seeded,
            "created_at": r.created_at.isoformat(),
            "failure_kinds": sorted({f["kind"] for f in r.findings}),
        }
        for r in rows
    ]


@app.get("/api/v1/metrics")
def metrics(
    status: str | None = None,
    project_id: str | None = None,
    device_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    u=Depends(current_user),
    db=Depends(get_db),
):
    key = f"m:{u.id}:{cache.version(u.id)}:{status}:{project_id}:{device_id}:{date_from}:{date_to}"
    if (hit := cache.get_json(key)) is not None:
        return hit
    runs = [
        r
        for r in db.scalars(
            _filtered(u, status, project_id, device_id, date_from, date_to)
        )
        if r.status in ("passed", "failed")
    ]
    n, failed = len(runs), [r for r in runs if r.status == "failed"]
    out = {
        "total_runs": n,
        "failed_runs": len(failed),
        "pass_rate": (n - len(failed)) / n if n else None,
        "median_duration_ms": statistics.median(r.duration_ms for r in runs)
        if n
        else None,
        "failure_categories": Counter(
            f["kind"] for r in failed for f in r.findings
        ).most_common(),
        "seeded_runs_included": sum(r.seeded for r in runs),
    }
    cache.set_json(key, out, 30)
    return out


def _cell(v):  # neutralise spreadsheet formula injection
    v = str(v)
    return "'" + v if v[:1] in "=+-@" else v


@app.get("/api/v1/reports/runs.csv")
def report_csv(
    status: str | None = None,
    project_id: str | None = None,
    device_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    u=Depends(current_user),
    db=Depends(get_db),
):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "run_id",
            "scenario",
            "status",
            "duration_ms",
            "created_at",
            "failures",
            "seeded",
        ]
    )
    for r in db.scalars(
        _filtered(u, status, project_id, device_id, date_from, date_to).order_by(
            Run.created_at
        )
    ):
        w.writerow(
            [
                r.id,
                _cell(r.scenario_snapshot["name"]),
                r.status,
                r.duration_ms,
                r.created_at.isoformat(),
                _cell("; ".join(f["message"] for f in r.findings)),
                r.seeded,
            ]
        )
    audit(db, u, "report_exported", "csv")
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=robotops-runs.csv"},
    )


@app.get("/api/v1/reports/runs.html")
def report_html(
    status: str | None = None,
    project_id: str | None = None,
    device_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    u=Depends(current_user),
    db=Depends(get_db),
):
    runs = list(
        db.scalars(
            _filtered(u, status, project_id, device_id, date_from, date_to).order_by(
                Run.created_at.desc()
            )
        )
    )
    m = metrics(status, project_id, device_id, date_from, date_to, u, db)
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(r.scenario_snapshot['name'])}</td><td>{r.status}</td><td>{r.duration_ms} ms</td><td>{e('; '.join(f['message'] for f in r.findings)) or '—'}</td><td>{'seed/demo' if r.seeded else 'recorded'}</td></tr>"
        for r in runs
    )
    pr = "n/a" if m["pass_rate"] is None else f"{m['pass_rate']:.0%}"
    audit(db, u, "report_exported", "html")
    return Response(
        f"<!doctype html><meta charset=utf-8><title>RobotOps report</title><style>body{{font:14px sans-serif;margin:2rem}}td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}table{{border-collapse:collapse}}</style>"
        f"<h1>RobotOps validation report</h1><p>Simulated devices only. Runs: {m['total_runs']} · Pass rate: {pr} · Seed/demo runs included: {m['seeded_runs_included']}</p>"
        f"<table><tr><th>Scenario</th><th>Status</th><th>Duration</th><th>Findings</th><th>Source</th></tr>{rows}</table>",
        media_type="text/html",
    )


def _brief(r):
    return {
        "id": r.id,
        "status": r.status,
        "duration_ms": r.duration_ms,
        "scenario": r.scenario_snapshot["name"],
        "scenario_version": r.scenario_snapshot["version"],
        "created_at": r.created_at.isoformat(),
    }


@app.get("/api/v1/runs/{a}/compare/{b}")
def compare_runs(a: str, b: str, u=Depends(current_user), db=Depends(get_db)):
    """Compare run B (typically newer / a replay) against run A: status, findings added/resolved/unchanged, event sequences."""
    ra, rb = owned(db, Run, a, u), owned(db, Run, b, u)
    key = lambda f: (f["kind"], f.get("step"), f["message"])
    fa, fb = {key(f) for f in ra.findings}, {key(f) for f in rb.findings}
    show = lambda ks: [
        {"kind": k[0], "step": k[1], "message": k[2]} for k in sorted(ks, key=str)
    ]
    ev = lambda r: [
        {"type": e["type"], "ts_ms": e["ts_ms"]}
        for e in sorted(r.events, key=lambda e: e["ts_ms"])
    ]
    ea, eb = ev(ra), ev(rb)
    if ra.status == rb.status and fa == fb and ea == eb:
        outcome = "identical"
    elif (ra.status == "failed" and rb.status == "passed") or (fa - fb and not fb - fa):
        outcome = "improved"
    elif (ra.status == "passed" and rb.status == "failed") or (fb - fa and not fa - fb):
        outcome = "regressed"
    else:
        outcome = "changed"
    return {
        "a": _brief(ra),
        "b": _brief(rb),
        "outcome": outcome,
        "duration_delta_ms": rb.duration_ms - ra.duration_ms,
        "findings": {
            "resolved": show(fa - fb),
            "new": show(fb - fa),
            "unchanged": show(fa & fb),
        },
        "events": {"a": ea, "b": eb},
        "events_identical": ea == eb,
    }


@app.get("/api/v1/audit")
def list_audit(limit: int = 100, u=Depends(current_user), db=Depends(get_db)):
    q = (
        select(AuditEvent)
        .where(AuditEvent.owner_id == u.id)
        .order_by(AuditEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    return [
        {"action": a.action, "target": a.target, "created_at": a.created_at.isoformat()}
        for a in db.scalars(q)
    ]


@app.get("/api/v1/devices")
def list_devices(u=Depends(current_user), db=Depends(get_db)):
    return [
        _device_out(d)
        for d in db.scalars(
            select(Device).where(Device.owner_id == u.id).order_by(Device.name)
        )
    ]


@app.get("/api/v1/devices/{did}")
def get_device(did: str, u=Depends(current_user), db=Depends(get_db)):
    d = owned(db, Device, did, u)
    ev = db.scalars(
        select(Event)
        .where(Event.device_id == did)
        .order_by(Event.ts_ms.desc())
        .limit(50)
    )
    return {
        **_device_out(d),
        "recent_events": [
            {"type": x.type, "ts_ms": x.ts_ms, "correlation_id": x.correlation_id}
            for x in ev
        ],
    }


def _dev_state(db, did):
    d = db.get(Device, did) if did else None
    return (
        {
            "name": d.name,
            "online": d.online,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "captured_at": now().isoformat(),
        }
        if d
        else None
    )


@app.get("/api/v1/devices/{did}/deliveries")
def list_deliveries(
    did: str, status: str | None = None, u=Depends(current_user), db=Depends(get_db)
):
    owned(db, Device, did, u)
    q = (
        select(DeliveryFailure)
        .where(DeliveryFailure.device_id == did)
        .order_by(DeliveryFailure.created_at.desc())
        .limit(200)
    )
    if status:
        q = q.where(DeliveryFailure.status == status)
    return [
        {
            "id": f.id,
            "event_id": f.event_id,
            "type": f.payload["type"],
            "reason": f.reason,
            "status": f.status,
            "created_at": f.created_at.isoformat(),
        }
        for f in db.scalars(q)
    ]


@app.post("/api/v1/devices/{did}/deliveries/retry")
def retry_deliveries(did: str, u=Depends(current_user), db=Depends(get_db)):
    """Explicit retry: replays failed deliveries once the device is online. Idempotent: an event_id already stored is not inserted twice."""
    d = owned(db, Device, did, u)
    if not d.online:
        raise HTTPException(409, "Device is offline; reconnect it before retrying")
    n = 0
    for f in db.scalars(
        select(DeliveryFailure)
        .where(DeliveryFailure.device_id == did, DeliveryFailure.status == "failed")
        .order_by(DeliveryFailure.created_at)
    ):
        if not db.scalar(
            select(Event).where(Event.device_id == did, Event.event_id == f.event_id)
        ):
            db.add(Event(owner_id=u.id, device_id=did, **f.payload))
        f.status = "delivered"
        n += 1
    d.last_seen = now()
    db.commit()
    audit(db, u, "deliveries_retried", did)
    return {"delivered": n}


@app.get("/api/v1/reports/runs.pdf")
def report_pdf(
    status: str | None = None,
    project_id: str | None = None,
    device_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    u=Depends(current_user),
    db=Depends(get_db),
):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    runs = list(
        db.scalars(
            _filtered(u, status, project_id, device_id, date_from, date_to).order_by(
                Run.created_at.desc()
            )
        )
    )
    m = metrics(status, project_id, device_id, date_from, date_to, u, db)
    st = getSampleStyleSheet()
    P = lambda t: Paragraph(html.escape(str(t)), st["BodyText"])
    pr = "n/a" if m["pass_rate"] is None else f"{m['pass_rate']:.0%}"
    rows = [["Scenario", "Status", "Duration", "Findings", "Source"]] + [
        [
            P(r.scenario_snapshot["name"]),
            r.status,
            f"{r.duration_ms} ms",
            P(" ".join(f["message"] for f in r.findings) or "-"),
            "seed/demo" if r.seeded else "recorded",
        ]
        for r in runs
    ]
    buf = io.BytesIO()
    t = Table(rows, repeatRows=1, colWidths=[92, 44, 50, 208, 57])
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, "#999999"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    SimpleDocTemplate(buf, pagesize=A4).build(
        [
            Paragraph("RobotOps validation report", st["Title"]),
            P(
                f"Simulated devices only. Runs: {m['total_runs']} | Pass rate: {pr} | Seed/demo runs included: {m['seeded_runs_included']}"
            ),
            Spacer(1, 12),
            t,
        ]
    )
    audit(db, u, "report_exported", "pdf")
    return Response(
        buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=robotops-report.pdf"},
    )


@app.get("/api/v1/simulations")
def list_simulations(limit: int = 50, u=Depends(current_user), db=Depends(get_db)):
    return [
        _job(j)
        for j in db.scalars(
            select(SimulationJob)
            .where(SimulationJob.owner_id == u.id)
            .order_by(SimulationJob.created_at.desc())
            .limit(min(limit, 200))
        )
    ]


@app.get("/api/v1/simulations/{jid}")
def get_simulation(jid: str, u=Depends(current_user), db=Depends(get_db)):
    return _job(owned(db, SimulationJob, jid, u))


@app.post("/api/v1/simulations/{jid}/cancel")
def cancel_simulation(jid: str, u=Depends(current_user), db=Depends(get_db)):
    j = owned(db, SimulationJob, jid, u)
    if j.status not in ("queued", "running"):
        raise HTTPException(409, f"Job is {j.status}")
    j.status = "cancelled"
    db.commit()
    audit(db, u, "simulation_cancelled", jid)
    return _job(j)


@app.get("/healthz")
def health():
    """Liveness: the process is up. Deliberately does not touch dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def ready(db=Depends(get_db)):
    """Readiness: can this instance serve traffic? PostgreSQL is required; Redis is optional
    (the app fails open without it), so a Redis outage reports 'degraded' but stays ready."""
    checks, ok = {}, True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"], ok = f"error: {type(e).__name__}", False
    r = cache.client()
    if r is None:
        checks["redis"] = "not configured"
    else:
        try:
            r.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"degraded: {type(e).__name__}"
    body = {"status": "ready" if ok else "not_ready", "checks": checks}
    return JSONResponse(body, 200 if ok else 503)


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request, db=Depends(get_db)):
    if (
        METRICS_TOKEN
        and request.headers.get("authorization") != f"Bearer {METRICS_TOKEN}"
    ):
        raise HTTPException(401, "Metrics require a bearer token")
    counts = dict(
        db.execute(
            select(SimulationJob.status, func.count()).group_by(SimulationJob.status)
        ).all()
    )
    for st in ("queued", "running", "completed", "failed", "cancelled"):
        obs.JOBS.labels(st).set(counts.get(st, 0))
    oldest = db.scalar(
        select(func.min(SimulationJob.created_at)).where(
            SimulationJob.status == "queued"
        )
    )
    if oldest is not None and oldest.tzinfo is None:  # SQLite returns naive datetimes
        oldest = oldest.replace(tzinfo=UTC)
    obs.OLDEST_QUEUED.set((now() - oldest).total_seconds() if oldest else 0)
    online = dict(
        db.execute(select(Device.online, func.count()).group_by(Device.online)).all()
    )
    obs.DEVICES.labels("true").set(online.get(True, 0))
    obs.DEVICES.labels("false").set(online.get(False, 0))
    return Response(obs.render(), media_type=CONTENT_TYPE_LATEST)
