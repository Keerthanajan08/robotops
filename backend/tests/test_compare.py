from .conftest import auth

STEPS = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
]


def ev(i, t, ts):
    return {"event_id": i, "type": t, "ts_ms": ts}


def mk(c, h):
    pid = c.post("/api/v1/projects", json={"name": "P"}, headers=h).json()["id"]
    return c.post(
        f"/api/v1/projects/{pid}/scenarios",
        json={"name": "S", "steps": STEPS},
        headers=h,
    ).json()["id"]


def run(c, h, sid, late):
    return c.post(
        f"/api/v1/scenarios/{sid}/runs",
        json={
            "events": [
                ev("1", "person_detected", 0),
                ev("2", "voice_prompt_issued", 5000 if late else 100),
            ]
        },
        headers=h,
    ).json()


def test_replay_is_identical_and_fix_is_improvement(client):
    h = auth(client)
    sid = mk(client, h)
    bad = run(client, h, sid, True)
    rp = client.post(f"/api/v1/runs/{bad['id']}/replay", headers=h).json()
    same = client.get(f"/api/v1/runs/{bad['id']}/compare/{rp['id']}", headers=h).json()
    assert (
        same["outcome"] == "identical"
        and same["events_identical"]
        and len(same["findings"]["unchanged"]) == 1
    )
    good = run(client, h, sid, False)
    imp = client.get(f"/api/v1/runs/{bad['id']}/compare/{good['id']}", headers=h).json()
    assert (
        imp["outcome"] == "improved"
        and imp["findings"]["resolved"][0]["kind"] == "timeout"
        and imp["findings"]["new"] == []
    )
    assert imp["duration_delta_ms"] == -4900 and not imp["events_identical"]
    assert (
        client.get(f"/api/v1/runs/{good['id']}/compare/{bad['id']}", headers=h).json()[
            "outcome"
        ]
        == "regressed"
    )


def test_compare_is_workspace_isolated(client):
    a, b = auth(client), auth(client, "b@example.com")
    r = run(client, a, mk(client, a), True)
    assert (
        client.get(f"/api/v1/runs/{r['id']}/compare/{r['id']}", headers=b).status_code
        == 404
    )
