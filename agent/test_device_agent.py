"""Unit tests for the device agent's outbox and retry logic (no network)."""

import pytest

import device_agent as da


def ev(i):
    return {
        "event_id": str(i),
        "type": "person_detected",
        "ts_ms": i,
        "correlation_id": "c",
    }


def ok(events, dup=()):
    res = [
        {
            "event_id": e["event_id"],
            "result": "duplicate" if e["event_id"] in dup else "accepted",
        }
        for e in events
    ]
    return {
        "accepted": sum(r["result"] == "accepted" for r in res),
        "duplicate": len(dup),
        "results": res,
    }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(da.time, "sleep", lambda s: None)


def test_outbox_survives_reopen(tmp_path):
    da.Outbox(tmp_path / "o.jsonl").add(ev(1))
    assert da.Outbox(tmp_path / "o.jsonl").pending() == [ev(1)]


def test_network_failure_keeps_events_then_retry_delivers(tmp_path, monkeypatch):
    box = da.Outbox(tmp_path / "o.jsonl")
    box.add(ev(1))
    box.add(ev(2))
    calls = []

    def flaky(url, key, events, timeout):
        calls.append(len(events))
        if len(calls) < 3:
            raise ConnectionError("connection refused")
        return ok(events)

    monkeypatch.setattr(da, "post_batch", flaky)
    assert da.flush(box, "u", "k", max_attempts=5, timeout=1, log=lambda m: None) == 0
    assert calls == [2, 2, 2] and box.pending() == []


def test_gives_up_but_keeps_buffer(tmp_path, monkeypatch):
    box = da.Outbox(tmp_path / "o.jsonl")
    box.add(ev(1))
    monkeypatch.setattr(
        da, "post_batch", lambda *a: (_ for _ in ()).throw(ConnectionError("down"))
    )
    assert da.flush(box, "u", "k", max_attempts=3, timeout=1, log=lambda m: None) == 1
    assert box.pending() == [ev(1)]


def test_duplicates_are_cleared(tmp_path, monkeypatch):
    box = da.Outbox(tmp_path / "o.jsonl")
    box.add(ev(1))
    monkeypatch.setattr(da, "post_batch", lambda u, k, events, t: ok(events, dup={"1"}))
    assert da.flush(box, "u", "k", 3, 1, log=lambda m: None) == 0


def test_partial_rejection_backs_off_instead_of_spinning(tmp_path, monkeypatch):
    box = da.Outbox(tmp_path / "o.jsonl")
    box.add(ev(1))
    box.add(ev(2))

    def half(u, k, events, t):  # server accepts event 1 but rejects event 2 every time
        res = [
            {
                "event_id": e["event_id"],
                "result": "accepted" if e["event_id"] == "1" else "rejected_offline",
            }
            for e in events
        ]
        return {
            "accepted": sum(r["result"] == "accepted" for r in res),
            "duplicate": 0,
            "results": res,
        }

    monkeypatch.setattr(da, "post_batch", half)
    assert da.flush(box, "u", "k", max_attempts=3, timeout=1, log=lambda m: None) == 1
    assert [e["event_id"] for e in box.pending()] == ["2"]


def test_rejected_key_is_fatal(tmp_path, monkeypatch):
    box = da.Outbox(tmp_path / "o.jsonl")
    box.add(ev(1))

    def reject(*a):
        raise da.FatalError("HTTP 401")

    monkeypatch.setattr(da, "post_batch", reject)
    with pytest.raises(da.FatalError):
        da.flush(box, "u", "k", 3, 1, log=lambda m: None)
    assert box.pending() == [ev(1)]  # nothing lost
