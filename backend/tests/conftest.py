import os
import tempfile

os.environ["DATABASE_URL"] = (
    os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tempfile.mkdtemp()}/t.db"
)
import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app import cache

    cache.set_client(None)
    if (
        r := cache.client()
    ) is not None:  # real Redis keeps state across runs: clear only this app's keys
        for pat in ("lf:*", "m:*", "ver:*", "dr:*"):
            for k in r.scan_iter(pat):
                r.delete(k)
    return TestClient(app)


def auth(c, email="a@example.com"):
    t = c.post(
        "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {t}"}
