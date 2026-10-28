from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def setup(c, h):
    pid = c.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    did = c.post("/api/v1/devices", json={"name": "bot"}, headers=h).json()["id"]
    return pid, sid, did


def E(i, t, ts, cid="c1"):
    return {"event_id": i, "type": t, "ts_ms": ts, "correlation_id": cid}


def test_requires_auth(client):
    assert client.get("/api/v1/projects").status_code == 401


def test_malformed_scenario_rejected(client):
    h = auth(client)
    pid = client.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    bad = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "x", "steps": [{"type": "bogus"}]},
        headers=h,
    )
    assert bad.status_code == 422


def test_workspace_isolation(client):
    a, b = auth(client), auth(client, "b@example.com")
    pid, sid, did = setup(client, a)
    assert client.get(f"/api/v1/projects/{pid}/scenarios", headers=b).status_code == 404
    assert (
        client.post(
            f"/api/v1/scenarios/{sid}/runs", json={"events": []}, headers=b
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/devices/{did}/events",
            json=E("1", "person_detected", 0),
            headers=b,
        ).status_code
        == 404
    )


def test_ingest_idempotent_and_offline(client):
    h = auth(client)
    _, _, did = setup(client, h)
    assert (
        client.post(
            f"/api/v1/devices/{did}/events",
            json=E("1", "person_detected", 0),
            headers=h,
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/devices/{did}/events",
            json=E("1", "person_detected", 0),
            headers=h,
        ).json()["duplicate"]
        is True
    )
    client.post(f"/api/v1/devices/{did}/disconnect", headers=h)
    assert (
        client.post(
            f"/api/v1/devices/{did}/events",
            json=E("2", "person_detected", 1),
            headers=h,
        ).status_code
        == 503
    )


def test_failing_run_replay_and_metrics(client):
    h = auth(client)
    _, sid, did = setup(client, h)
    for i, (t, ts) in enumerate(
        [("person_detected", 0), ("voice_prompt_issued", 3000)]
    ):
        client.post(
            f"/api/v1/devices/{did}/events", json=E(str(i), t, ts, "fail1"), headers=h
        )
    run = client.post(
        f"/api/v1/scenarios/{sid}/runs", json={"correlation_id": "fail1"}, headers=h
    ).json()
    assert run["status"] == "failed" and run["findings"][0]["kind"] == "timeout"
    rp = client.post(f"/api/v1/runs/{run['id']}/replay", headers=h).json()
    assert rp["findings"] == run["findings"] and rp["replay_of"] == run["id"]
    m = client.get("/api/v1/metrics", headers=h).json()
    assert (
        m["total_runs"] == 2
        and m["pass_rate"] == 0
        and m["failure_categories"] == [["timeout", 2]]
    )
