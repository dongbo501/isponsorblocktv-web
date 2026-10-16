import pytest
from fastapi.testclient import TestClient

from webapp.app import create_app


class FakeSupervisor:
    def __init__(self, store):
        self.store = store
        self.logs = []
        self.sequence = 0
        self.applies = 0

    async def start(self):
        pass

    async def close(self):
        pass

    async def apply(self):
        self.applies += 1

    def log(self, message, level="INFO"):
        self.sequence += 1
        self.logs.append({"id": self.sequence, "message": message, "level": level})

    def status(self):
        return {"state": "waiting" if self.store.enabled else "stopped"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, FakeSupervisor)) as client:
        yield client


@pytest.fixture
def admin(client):
    response = client.post("/api/auth/setup", json={"password": "test-password-123"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client
