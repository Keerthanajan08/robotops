from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def mk(c, h):
    pid = c.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "=S1", "tags": ["x"], "steps": STEPS},
        headers=h,
    ).json()["id"]
    return pid, sid


def ev(i, t, ts, cid="c"):
    return {"event_id": i, "type": t, "ts_ms": ts, "correlation_id": cid}


def test_update_bumps_version_but_old_run_keeps_snapshot(client):
    h = auth(client)
    pid, sid = mk(client, h)
    run = client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={
            "events": [
                ev("1", "person_detected", 0),
                ev("2", "voice_prompt_issued", 5000),
            ]
        },
        headers=h,
    ).json()
    assert run["status"] == "failed"
    up = client.put(
        f"/api/v1/scenarios/{sid}",
        json={"name": "S", "steps": [{"type": "person_detected"}]},
        headers=h,
    ).json()
    assert up["version"] == 2
    rp = client.post(f"/api/v1/runs/{run['id']}/replay", headers=h).json()
    assert rp["status"] == "failed" and rp["scenario"]["version"] == 1


def test_duplicate_and_isolation(client):
    a, b = auth(client), auth(client, "b@example.com")
    _, sid = mk(client, a)
    d = client.post(f"/api/v1/scenarios/{sid}/duplicate", headers=a).json()
    assert d["name"].endswith("(copy)") and d["id"] != sid
    assert (
        client.post(f"/api/v1/scenarios/{sid}/duplicate", headers=b).status_code == 404
    )


def test_deferred_run_complete_and_cancel(client):
    h = auth(client)
    _, sid = mk(client, h)
    did = client.post("/api/v1/devices", json={"name": "d"}, headers=h).json()["id"]
    r = client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={"defer": True, "correlation_id": "live1"},
        headers=h,
    ).json()
    assert r["status"] == "running"
    for i, (t, ts) in enumerate([("person_detected", 0), ("voice_prompt_issued", 200)]):
        client.post(
            f"/api/v1/devices/{did}/events", json=ev(str(i), t, ts, "live1"), headers=h
        )
    done = client.post(f"/api/v1/runs/{r['id']}/complete", headers=h).json()
    assert done["status"] == "passed" and done["device_id"] == did
    assert client.post(f"/api/v1/runs/{r['id']}/cancel", headers=h).status_code == 409
    r2 = client.post(
        f"/api/v1/scenarios/{sid}/runs", json={"defer": True}, headers=h
    ).json()
    assert (
        client.post(f"/api/v1/runs/{r2['id']}/cancel", headers=h).json()["status"]
        == "cancelled"
    )
    assert client.post(f"/api/v1/runs/{r2['id']}/replay", headers=h).status_code == 409
    assert (
        client.get("/api/v1/metrics", headers=h).json()["total_runs"] == 1
    )  # cancelled excluded


def test_filters_reports_audit(client):
    h = auth(client)
    pid, sid = mk(client, h)
    client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={
            "events": [
                ev("1", "person_detected", 0),
                ev("2", "voice_prompt_issued", 9000),
            ]
        },
        headers=h,
    )
    client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={
            "events": [
                ev("1", "person_detected", 0),
                ev("2", "voice_prompt_issued", 10),
            ]
        },
        headers=h,
    )
    assert len(client.get("/api/v1/runs?status=failed", headers=h).json()) == 1
    assert len(client.get(f"/api/v1/runs?project_id={pid}", headers=h).json()) == 2
    assert (
        client.get("/api/v1/runs?date_from=2999-01-01T00:00:00", headers=h).json() == []
    )
    csv_ = client.get("/api/v1/reports/runs.csv", headers=h)
    assert (
        csv_.headers["content-type"].startswith("text/csv") and "'=S1" in csv_.text
    )  # formula-injection neutralised
    assert "<table>" in client.get("/api/v1/reports/runs.html", headers=h).text
    acts = {a["action"] for a in client.get("/api/v1/audit", headers=h).json()}
    assert {"user_registered", "report_exported"} <= acts


def test_seed_flagged(client):
    from app.seed import seed

    seed("demo@example.com", "demo-password-1")
    t = client.post(
        "/api/v1/auth/login",
        json={"email": "demo@example.com", "password": "demo-password-1"},
    ).json()["access_token"]
    m = client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {t}"}).json()
    assert m["total_runs"] == 3 and m["seeded_runs_included"] == 3
