"""Auth expiry, device credentials, device-to-cloud ingestion, observability, pagination, worker leases."""

import time
from datetime import timedelta

import jwt
import pytest

from app import main, worker
from app.db import SessionLocal, SimulationJob, now

from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def ev(i, t="person_detected", ts=0, cid="c1"):
    return {"event_id": str(i), "type": t, "ts_ms": ts, "correlation_id": cid}


def make_device(c, h, name="bot"):
    r = c.post("/api/v1/devices", json={"name": name}, headers=h)
    assert r.status_code == 201
    return r.json()


# ---- JWT expiry
def test_token_carries_expiry(client):
    r = client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "correct-horse-1"},
    ).json()
    claims = jwt.decode(r["access_token"], main.SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == main.JWT_TTL_MINUTES * 60 == r["expires_in"]


def test_expired_token_rejected(client):
    h = auth(client)
    uid = client.get("/api/v1/me", headers=h).json()["id"]
    past = int(time.time()) - 3600
    old = jwt.encode(
        {"sub": uid, "iat": past - 60, "exp": past}, main.SECRET, algorithm="HS256"
    )
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]


def test_token_without_expiry_rejected(client):
    h = auth(client)
    uid = client.get("/api/v1/me", headers=h).json()["id"]
    forever = jwt.encode(
        {"sub": uid}, main.SECRET, algorithm="HS256"
    )  # what the old code issued
    assert (
        client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {forever}"}
        ).status_code
        == 401
    )


def test_token_signed_with_other_secret_rejected(client):
    h = auth(client)
    uid = client.get("/api/v1/me", headers=h).json()["id"]
    t = int(time.time())
    forged = jwt.encode(
        {"sub": uid, "iat": t, "exp": t + 60}, "x" * 40, algorithm="HS256"
    )
    assert (
        client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {forged}"}
        ).status_code
        == 401
    )


# ---- device credentials + device-to-cloud ingestion
def test_device_key_shown_once_and_only_hash_stored(client):
    h = auth(client)
    d = make_device(client, h)
    assert (
        d["device_key"].startswith("rok_") and d["key_prefix"] == d["device_key"][:12]
    )
    listed = client.get("/api/v1/devices", headers=h).json()[0]
    detail = client.get(f"/api/v1/devices/{d['id']}", headers=h).json()
    assert "device_key" not in listed and "device_key" not in detail
    from app.db import Device

    with SessionLocal() as db:
        assert d["device_key"] not in (db.get(Device, d["id"]).key_hash or "")


def test_device_batch_ingest_is_idempotent(client):
    h = auth(client)
    d = make_device(client, h)
    k = {"X-Device-Key": d["device_key"]}
    batch = {"events": [ev(1), ev(2, "voice_prompt_issued", 400)]}
    r1 = client.post("/api/v1/ingest/events", json=batch, headers=k).json()
    r2 = client.post(
        "/api/v1/ingest/events", json=batch, headers=k
    ).json()  # agent resends after a timeout
    assert (r1["accepted"], r1["duplicate"]) == (2, 0) and (
        r2["accepted"],
        r2["duplicate"],
    ) == (0, 2)
    sid = client.post(
        f"/api/v1/projects/{client.post('/api/v1/projects', json={'name': 'P'}, headers=h).json()['id']}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    run = client.post(
        f"/api/v1/scenarios/{sid}/runs", json={"correlation_id": "c1"}, headers=h
    ).json()
    assert (
        run["status"] == "passed"
        and run["device_id"] == d["id"]
        and len(run["events"]) == 2
    )


def test_device_key_required_and_user_jwt_not_accepted(client):
    h = auth(client)
    make_device(client, h)
    body = {"events": [ev(1)]}
    assert client.post("/api/v1/ingest/events", json=body).status_code == 401
    assert client.post("/api/v1/ingest/events", json=body, headers=h).status_code == 401
    assert (
        client.post(
            "/api/v1/ingest/events", json=body, headers={"X-Device-Key": "rok_wrong"}
        ).status_code
        == 401
    )


def test_rotation_invalidates_old_key(client):
    h = auth(client)
    d = make_device(client, h)
    new = client.post(f"/api/v1/devices/{d['id']}/key/rotate", headers=h).json()
    assert new["device_key"] != d["device_key"]
    body = {"events": [ev(1)]}
    assert (
        client.post(
            "/api/v1/ingest/events",
            json=body,
            headers={"X-Device-Key": d["device_key"]},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/ingest/events",
            json=body,
            headers={"X-Device-Key": new["device_key"]},
        ).status_code
        == 200
    )
    actions = [a["action"] for a in client.get("/api/v1/audit", headers=h).json()]
    assert "device_key_rotated" in actions


def test_other_user_cannot_rotate_key(client):
    a, b = auth(client), auth(client, "b@example.com")
    d = make_device(client, a)
    assert (
        client.post(f"/api/v1/devices/{d['id']}/key/rotate", headers=b).status_code
        == 404
    )


def test_offline_device_ingest_records_failures(client):
    h = auth(client)
    d = make_device(client, h)
    client.post(f"/api/v1/devices/{d['id']}/disconnect", headers=h)
    r = client.post(
        "/api/v1/ingest/events",
        json={"events": [ev(1), ev(2)]},
        headers={"X-Device-Key": d["device_key"]},
    )
    assert r.status_code == 503
    assert (
        len(client.get(f"/api/v1/devices/{d['id']}/deliveries", headers=h).json()) == 2
    )


def test_device_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "DEVICE_RATE_PER_MIN", 2)
    h = auth(client)
    k = {"X-Device-Key": make_device(client, h)["device_key"]}
    codes = [
        client.post(
            "/api/v1/ingest/events", json={"events": [ev(i)]}, headers=k
        ).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_batch_size_limited(client):
    h = auth(client)
    k = {"X-Device-Key": make_device(client, h)["device_key"]}
    assert (
        client.post(
            "/api/v1/ingest/events",
            json={"events": [ev(i) for i in range(101)]},
            headers=k,
        ).status_code
        == 422
    )


# ---- observability
def test_readyz_reports_dependencies(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


def test_prometheus_metrics_use_route_templates(client):
    h = auth(client)
    d = make_device(client, h)
    client.get(f"/api/v1/devices/{d['id']}", headers=h)
    client.post(
        "/api/v1/ingest/events",
        json={"events": [ev(1)]},
        headers={"X-Device-Key": d["device_key"]},
    )
    body = client.get("/metrics").text
    assert (
        'route="/api/v1/devices/{did}"' in body and d["id"] not in body
    )  # bounded label cardinality
    assert 'robotops_device_events_total{result="accepted"}' in body
    assert (
        'robotops_simulation_jobs{status="queued"}' in body
        and "robotops_queue_oldest_job_age_seconds" in body
    )


def test_metrics_token(client, monkeypatch):
    monkeypatch.setattr(main, "METRICS_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code
        == 200
    )


# ---- pagination + sorting
def test_runs_pagination_and_sorting(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    for gap in (100, 300, 200, 5000, 50):
        events = [ev("a", ts=0), ev("b", "voice_prompt_issued", gap)]
        client.post(f"/api/v1/scenarios/{sid}/runs", json={"events": events}, headers=h)
    r = client.get("/api/v1/runs?sort=duration_ms&limit=2&offset=1", headers=h)
    assert r.headers["X-Total-Count"] == "5" and [
        x["duration_ms"] for x in r.json()
    ] == [100, 200]
    desc = client.get("/api/v1/runs?sort=-duration_ms&limit=1", headers=h).json()
    assert desc[0]["duration_ms"] == 5000
    assert client.get("/api/v1/runs?sort=password_hash", headers=h).status_code == 422
    assert client.get("/api/v1/runs?offset=-1", headers=h).status_code == 422


def test_scenarios_pagination(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    for n in "cab":
        client.post(
            f"/api/v1/projects/{pid}/scenarios",
            json={"name": n, "steps": STEPS},
            headers=h,
        )
    r = client.get(f"/api/v1/projects/{pid}/scenarios?sort=-name&limit=2", headers=h)
    assert r.headers["X-Total-Count"] == "3" and [s["name"] for s in r.json()] == [
        "c",
        "b",
    ]


# ---- worker leases
def _queued_job(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    did = make_device(client, h)["id"]
    return client.post(
        f"/api/v1/devices/{did}/simulations", json={"scenario_id": sid}, headers=h
    ).json()["id"]


@pytest.mark.parametrize("age_s,recovered", [(5, 0), (600, 1)])
def test_recover_only_touches_stale_leases(client, age_s, recovered):
    jid = _queued_job(client)
    with SessionLocal() as db:
        j = db.get(SimulationJob, jid)
        j.status, j.lease_token, j.heartbeat_at = (
            "running",
            "a" * 32,
            now() - timedelta(seconds=age_s),
        )
        db.commit()
    assert worker.recover(lease_s=60) == recovered


def test_worker_that_lost_its_lease_stops(client):
    jid = _queued_job(client)
    calls = []

    def steal_lease(
        _seconds,
    ):  # while the first worker sleeps, another worker reclaims the job
        with SessionLocal() as db:
            db.get(SimulationJob, jid).lease_token = "b" * 32
            db.commit()
        calls.append(1)

    with SessionLocal() as db:
        db.get(SimulationJob, jid).delay_ms = 10
        db.commit()
    assert (
        worker.process_next(
            on_complete=lambda j: pytest.fail("must not create a run"),
            sleep=steal_lease,
        )
        == jid
    )
    with SessionLocal() as db:
        j = db.get(SimulationJob, jid)
        assert (
            calls
            and j.status == "running"
            and j.lease_token == "b" * 32
            and j.events_emitted == 1
        )


@pytest.mark.skipif(
    not main.DATABASE_URL.startswith("postgresql"), reason="needs real concurrent DB"
)
def test_concurrent_duplicate_ingest_never_errors(client):
    import threading

    from sqlalchemy import func, select

    from app.db import Device, Event

    h = auth(client)
    did = make_device(client, h)["id"]
    barrier, results = threading.Barrier(8), []

    def send():
        with SessionLocal() as db:
            d = db.get(Device, did)
            barrier.wait()
            results.append(
                main._store_event(db, d, main.EventIn(**ev("same")))["result"]
            )

    threads = [threading.Thread(target=send) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        stored = db.scalar(
            select(func.count()).select_from(Event).where(Event.device_id == did)
        )
    assert sorted(results).count("accepted") == 1 and len(results) == 8 and stored == 1


def test_worker_passing_simulation_satisfies_payload_conditions(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    steps = [
        {"type": "person_detected", "match": {"zone": "lobby"}},
        {"type": "voice_prompt_issued", "within_ms": 1000, "match": {"lang": "ja"}},
    ]
    sid = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": steps},
        headers=h,
    ).json()["id"]
    did = make_device(client, h)["id"]
    jid = client.post(
        f"/api/v1/devices/{did}/simulations", json={"scenario_id": sid}, headers=h
    ).json()["id"]
    assert worker.process_next(main._job_run) == jid
    run_id = client.get(f"/api/v1/simulations/{jid}", headers=h).json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}", headers=h).json()["status"] == "passed"


def test_batch_with_repeated_event_id_stores_it_once(client):
    h = auth(client)
    k = {"X-Device-Key": make_device(client, h)["device_key"]}
    r = client.post(
        "/api/v1/ingest/events", json={"events": [ev("a"), ev("a"), ev("b")]}, headers=k
    ).json()
    assert (r["accepted"], r["duplicate"]) == (2, 1)
    assert [x["result"] for x in r["results"]] == ["accepted", "duplicate", "accepted"]


@pytest.mark.skipif(
    not main.DATABASE_URL.startswith("postgresql"), reason="needs real concurrent DB"
)
def test_concurrent_identical_batches_store_each_event_once(client):
    import threading

    from sqlalchemy import func, select

    from app.db import Device, Event

    h = auth(client)
    did = make_device(client, h)["id"]
    batch = [main.EventIn(**ev(f"e{i}")) for i in range(20)]
    barrier, accepted = threading.Barrier(8), []

    def send():
        with SessionLocal() as db:
            d = db.get(Device, did)
            barrier.wait()
            accepted.append(
                sum(r["result"] == "accepted" for r in main._store_batch(db, d, batch))
            )

    threads = [threading.Thread(target=send) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        stored = db.scalar(
            select(func.count()).select_from(Event).where(Event.device_id == did)
        )
    assert sum(accepted) == 20 and stored == 20
