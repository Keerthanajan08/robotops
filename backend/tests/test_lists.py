from .conftest import auth


def test_lists_scoped(client):
    a, b = auth(client), auth(client, "b@example.com")
    client.post("/api/v1/devices", json={"name": "x"}, headers=a)
    assert len(client.get("/api/v1/devices", headers=a).json()) == 1
    assert client.get("/api/v1/devices", headers=b).json() == []
    assert client.get("/api/v1/runs", headers=b).json() == []
