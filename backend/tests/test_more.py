from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def ev(i, t="person_detected", ts=0):
    return {"event_id": i, "type": t, "ts_ms": ts, "correlation_id": "k"}


def setup(c, h):
    pid = c.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]
    return sid, c.post("/api/v1/devices", json={"name": "d"}, headers=h).json()["id"]


def test_failed_deliveries_recorded_listed_and_retried_idempotently(client):
    h, b = auth(client), auth(client, "b@example.com")
    _, did = setup(client, h)
    client.post(f"/api/v1/devices/{did}/disconnect", headers=h)
    for _ in range(2):
        assert (
            client.post(
                f"/api/v1/devices/{did}/events", json=ev("e1"), headers=h
            ).status_code
            == 503
        )  # same event twice
    client.post(
        f"/api/v1/devices/{did}/events",
        json=ev("e2", "voice_prompt_issued", 5),
        headers=h,
    )
    fails = client.get(
        f"/api/v1/devices/{did}/deliveries?status=failed", headers=h
    ).json()
    assert len(fails) == 2 and fails[0]["reason"] == "device_offline"
    assert client.get(f"/api/v1/devices/{did}/deliveries", headers=b).status_code == 404
    assert (
        client.post(f"/api/v1/devices/{did}/deliveries/retry", headers=h).status_code
        == 409
    )
    client.post(f"/api/v1/devices/{did}/reconnect", headers=h)
    assert client.post(f"/api/v1/devices/{did}/deliveries/retry", headers=h).json() == {
        "delivered": 2
    }
    assert client.post(f"/api/v1/devices/{did}/deliveries/retry", headers=h).json() == {
        "delivered": 0
    }
    assert (
        len(client.get(f"/api/v1/devices/{did}", headers=h).json()["recent_events"])
        == 2
    )
    assert (
        client.post(f"/api/v1/devices/{did}/events", json=ev("e1"), headers=h).json()[
            "duplicate"
        ]
        is True
    )  # late client retry is ignored


def test_run_captures_device_state_and_replay_keeps_it(client):
    h = auth(client)
    sid, did = setup(client, h)
    client.post(f"/api/v1/devices/{did}/disconnect", headers=h)
    run = client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={"device_id": did, "events": [ev("1")]},
        headers=h,
    ).json()
    assert run["status"] == "failed" and run["device_state"]["online"] is False
    client.post(f"/api/v1/devices/{did}/reconnect", headers=h)
    assert (
        client.post(f"/api/v1/runs/{run['id']}/replay", headers=h).json()[
            "device_state"
        ]["online"]
        is False
    )


def test_pdf_report(client):
    h = auth(client)
    sid, _ = setup(client, h)
    client.post(f"/api/v1/scenarios/{sid}/runs", json={"events": [ev("1")]}, headers=h)
    r = client.get("/api/v1/reports/runs.pdf", headers=h)
    assert (
        r.status_code == 200
        and r.content.startswith(b"%PDF")
        and r.headers["content-type"] == "application/pdf"
    )
