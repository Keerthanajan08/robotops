import time

from fastapi.testclient import TestClient

from app import worker
from app.main import app

from .conftest import auth

S3 = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
    {"type": "notification_sent", "within_ms": 10000},
]


def setup(c, h):
    pid = c.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    sid = c.post(
        f"/api/v1/projects/{pid}/scenarios", json={"name": "S", "steps": S3}, headers=h
    ).json()["id"]
    return sid, c.post("/api/v1/devices", json={"name": "d"}, headers=h).json()["id"]


def queue(c, h, did, sid, **kw):
    return c.post(
        f"/api/v1/devices/{did}/simulations", json={"scenario_id": sid, **kw}, headers=h
    )


def test_run_logs_replay_and_cancel(client):
    h = auth(client)
    sid, _ = setup(client, h)
    ev = [
        {"event_id": "1", "type": "person_detected", "ts_ms": 0},
        {"event_id": "2", "type": "voice_prompt_issued", "ts_ms": 9000},
    ]
    run = client.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={"events": ev},
        headers={**h, "X-Request-ID": "req-42"},
    ).json()
    errs = [entry for entry in run["logs"] if entry["level"] == "error"]
    assert any("timeout" in entry["msg"] for entry in errs) and all(
        entry["request_id"] == "req-42" for entry in run["logs"]
    )
    assert (
        client.post(f"/api/v1/runs/{run['id']}/replay", headers=h).json()["logs"][0][
            "msg"
        ]
        == f"replay of run {run['id']}"
    )
    r2 = client.post(
        f"/api/v1/scenarios/{sid}/runs", json={"defer": True}, headers=h
    ).json()
    assert (
        client.post(f"/api/v1/runs/{r2['id']}/cancel", headers=h).json()["logs"][-1][
            "level"
        ]
        == "warning"
    )


def test_job_processed_creates_events_and_run(client):
    h = auth(client)
    sid, did = setup(client, h)
    ok, bad = (
        queue(client, h, did, sid).json(),
        queue(client, h, did, sid, fail=True).json(),
    )
    assert ok["status"] == "queued"
    assert (
        worker.process_next(_cb()) == ok["id"]
        and worker.process_next(_cb()) == bad["id"]
        and worker.process_next() is None
    )
    a, b = (
        client.get(f"/api/v1/simulations/{j['id']}", headers=h).json()
        for j in (ok, bad)
    )
    assert a["status"] == "completed" and a["events_emitted"] == a["events_total"] == 3
    assert (
        client.get(f"/api/v1/runs/{a['run_id']}", headers=h).json()["status"]
        == "passed"
    )
    assert (
        client.get(f"/api/v1/runs/{b['run_id']}", headers=h).json()["status"]
        == "failed"
    )


def _cb():
    from app.main import _job_run

    return _job_run


def test_queued_cancel_not_claimed_and_isolation(client):
    h, o = auth(client), auth(client, "o@example.com")
    sid, did = setup(client, h)
    j = queue(client, h, did, sid).json()
    assert (
        client.post(f"/api/v1/simulations/{j['id']}/cancel", headers=o).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/simulations/{j['id']}/cancel", headers=h).json()["status"]
        == "cancelled"
    )
    assert (
        worker.process_next() is None
        and client.post(f"/api/v1/simulations/{j['id']}/cancel", headers=h).status_code
        == 409
    )


def test_cancel_mid_run_stops_between_events(client):
    h = auth(client)
    sid, did = setup(client, h)
    j = queue(client, h, did, sid, delay_ms=10).json()
    worker.process_next(
        sleep=lambda s: client.post(f"/api/v1/simulations/{j['id']}/cancel", headers=h)
    )
    st = client.get(f"/api/v1/simulations/{j['id']}", headers=h).json()
    assert (
        st["status"] == "cancelled"
        and 1 <= st["events_emitted"] < 3
        and st["run_id"] is None
    )


def test_offline_device_fails_job_with_recorded_delivery(client):
    h = auth(client)
    sid, did = setup(client, h)
    j = queue(client, h, did, sid).json()
    client.post(f"/api/v1/devices/{did}/disconnect", headers=h)
    worker.process_next(_cb())
    st = client.get(f"/api/v1/simulations/{j['id']}", headers=h).json()
    assert st["status"] == "failed" and "offline" in st["error"]
    assert (
        len(
            client.get(
                f"/api/v1/devices/{did}/deliveries?status=failed", headers=h
            ).json()
        )
        == 1
    )


def test_recover_requeues_interrupted_job(client):
    from app.db import SessionLocal, SimulationJob

    h = auth(client)
    sid, did = setup(client, h)
    j = queue(client, h, did, sid).json()
    with SessionLocal() as db:
        db.get(SimulationJob, j["id"]).status = "running"
        db.commit()
    assert worker.recover() == 1 and worker.process_next() == j["id"]


def test_worker_loop_runs_inside_app_lifespan(client):
    h = auth(client)
    sid, did = setup(client, h)
    with TestClient(app) as c:
        j = queue(c, h, did, sid).json()
        st = j
        for _ in range(100):
            st = c.get(f"/api/v1/simulations/{j['id']}", headers=h).json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
    assert st["status"] == "completed" and st["run_id"]


def test_concurrent_workers_process_each_job_exactly_once(client):
    import threading

    from sqlalchemy import func, select

    from app.db import Event, SessionLocal

    h = auth(client)
    sid, did = setup(client, h)
    ids = {queue(client, h, did, sid).json()["id"] for _ in range(8)}
    done, errors = [], []

    def work():
        try:
            while (j := worker.process_next(_cb())) is not None:
                done.append(j)
        except Exception as e:
            errors.append(repr(e))

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert not errors and sorted(done) == sorted(
        ids
    )  # every job claimed once, none twice
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(Event)) == 8 * 3
        )  # no duplicate events
    assert all(
        client.get(f"/api/v1/simulations/{i}", headers=h).json()["status"]
        == "completed"
        for i in ids
    )
