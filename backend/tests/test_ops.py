import fakeredis

from app import cache
from app.simulator import generate

from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def test_request_id_and_413_and_422_shape(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
    assert client.get("/healthz").headers["x-request-id"]
    big = client.post(
        "/api/v1/auth/login",
        content=b"x" * 1_000_001,
        headers={"Content-Type": "application/json"},
    )
    assert big.status_code == 413
    bad = client.post("/api/v1/auth/register", json={"email": "nope", "password": "x"})
    assert (
        bad.status_code == 422
        and bad.json()["error"]["details"]
        and "Validation failed" in bad.json()["error"]["message"]
    )


def test_login_lockout(client):
    auth(client)
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": "a@example.com", "password": "wrong-password-1"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "correct-horse-1"},
        ).status_code
        == 429
    )


def test_metrics_cache_invalidated_on_new_run(client):
    r = fakeredis.FakeRedis(decode_responses=True)
    cache.set_client(r)
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    ev = [
        {"event_id": "1", "type": "person_detected", "ts_ms": 0},
        {"event_id": "2", "type": "voice_prompt_issued", "ts_ms": 5},
    ]
    assert client.get("/api/v1/metrics", headers=h).json()["total_runs"] == 0
    assert any(k.startswith("m:") for k in r.keys())  # cached
    client.post(f"/api/v1/scenarios/{sid}/runs", json={"events": ev}, headers=h)
    assert (
        client.get("/api/v1/metrics", headers=h).json()["total_runs"] == 1
    )  # version bump -> fresh


def test_simulated_producer_end_to_end(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    did = client.post("/api/v1/devices", json={"name": "d"}, headers=h).json()["id"]
    for fail, want in ((False, "passed"), (True, "failed")):
        cid = client.post(
            f"/api/v1/devices/{did}/simulate",
            json={"scenario_id": sid, "fail": fail},
            headers=h,
        ).json()["correlation_id"]
        assert (
            client.post(
                f"/api/v1/scenarios/{sid}/runs", json={"correlation_id": cid}, headers=h
            ).json()["status"]
            == want
        )
    client.post(f"/api/v1/devices/{did}/disconnect", headers=h)
    assert (
        client.post(
            f"/api/v1/devices/{did}/simulate", json={"scenario_id": sid}, headers=h
        ).status_code
        == 503
    )


def test_generator_pure():
    assert [e["ts_ms"] for e in generate(STEPS, False)] == [0, 500]
    assert generate(STEPS, True)[-1]["ts_ms"] == 2000
    assert len(generate([{"type": "person_detected"}], True)) == 0
