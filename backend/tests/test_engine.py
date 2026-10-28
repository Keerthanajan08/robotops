from app.engine import evaluate

S = [
    {"type": "person_detected"},
    {"type": "voice_prompt_issued", "within_ms": 1000},
    {"type": "acknowledgement_received", "within_ms": 5000, "required": False},
    {"type": "notification_sent", "within_ms": 10000},
]


def ev(t, ts):
    return {"type": t, "ts_ms": ts, "payload": {}}


def kinds(r):
    return [f["kind"] for f in r["findings"]]


def test_pass():
    r = evaluate(
        S,
        [
            ev("person_detected", 0),
            ev("voice_prompt_issued", 500),
            ev("notification_sent", 6000),
        ],
    )
    assert r["status"] == "passed" and r["duration_ms"] == 6000


def test_missing_required():
    assert kinds(
        evaluate(S, [ev("person_detected", 0), ev("voice_prompt_issued", 1)])
    ) == ["missing"]


def test_timeout():
    r = evaluate(
        S,
        [
            ev("person_detected", 0),
            ev("voice_prompt_issued", 1500),
            ev("notification_sent", 2000),
        ],
    )
    assert kinds(r) == ["timeout"]


def test_out_of_order():
    r = evaluate(
        S,
        [
            ev("voice_prompt_issued", 0),
            ev("person_detected", 100),
            ev("notification_sent", 200),
        ],
    )
    assert "out_of_order" in kinds(r) and r["status"] == "failed"


def test_unexpected():
    r = evaluate(
        S,
        [
            ev("person_detected", 0),
            ev("api_request_failed", 10),
            ev("voice_prompt_issued", 20),
            ev("notification_sent", 30),
        ],
    )
    assert kinds(r) == ["unexpected"]


def test_deterministic():
    e = [ev("person_detected", 0), ev("voice_prompt_issued", 5000)]
    assert evaluate(S, e) == evaluate(S, list(reversed(e)))
