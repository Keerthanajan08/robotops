"""Demo seed: `python -m app.seed EMAIL PASSWORD`. Seeded runs are flagged seeded=True and labelled in reports."""

import sys

import bcrypt
from sqlalchemy import select

from .db import *
from .engine import evaluate

STEPS = [
    {"type": "person_detected", "within_ms": None, "required": True, "match": {}},
    {"type": "voice_prompt_issued", "within_ms": 1000, "required": True, "match": {}},
    {
        "type": "acknowledgement_received",
        "within_ms": 5000,
        "required": False,
        "match": {},
    },
    {"type": "notification_sent", "within_ms": 10000, "required": True, "match": {}},
]
CASES = {
    "ok": [
        ("person_detected", 0),
        ("voice_prompt_issued", 400),
        ("notification_sent", 6000),
    ],
    "late_prompt": [
        ("person_detected", 0),
        ("voice_prompt_issued", 2500),
        ("notification_sent", 6000),
    ],
    "no_notification": [("person_detected", 0), ("voice_prompt_issued", 400)],
}


def seed(email, password):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            print("already seeded")
            return
        u = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        )
        db.add(u)
        db.flush()
        p = Project(owner_id=u.id, name="Caregiver escalation PoC (DEMO SEED)")
        db.add(p)
        db.flush()
        sc = Scenario(
            owner_id=u.id,
            project_id=p.id,
            name="No-ack escalation",
            severity="high",
            tags=["demo"],
            steps=STEPS,
        )
        db.add(sc)
        d = Device(owner_id=u.id, name="sim-robot-01")
        db.add(d)
        db.flush()
        for name, evs in CASES.items():
            events = [{"type": t, "ts_ms": ts, "payload": {}} for t, ts in evs]
            r = evaluate(STEPS, events)
            db.add(
                Run(
                    owner_id=u.id,
                    scenario_id=sc.id,
                    project_id=p.id,
                    device_id=d.id,
                    correlation_id=f"seed-{name}",
                    seeded=True,
                    scenario_snapshot={"name": sc.name, "version": 1, "steps": STEPS},
                    events=events,
                    status=r["status"],
                    findings=r["findings"],
                    duration_ms=r["duration_ms"],
                )
            )
        db.commit()
        print("seeded demo data for", email)


if __name__ == "__main__":
    seed(sys.argv[1], sys.argv[2])
