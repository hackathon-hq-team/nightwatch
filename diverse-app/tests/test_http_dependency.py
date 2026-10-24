"""Original HTTP checks for valid credentials and public health requests."""
from fastapi.testclient import TestClient
import pytest

from app.http_dependency import app


@pytest.mark.parametrize("header", ["X-Token", "x-token"])
def test_valid_token_passes_through_dependency(header):
    with TestClient(app) as client:
        response = client.get("/profile", headers={header: "demo-token"})
    assert response.status_code == 200
    assert response.json() == {"user": "demo", "scope": "read"}


def test_health_does_not_require_authentication():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
