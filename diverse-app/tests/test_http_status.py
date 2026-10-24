"""Original HTTP checks cover representation, input validation and public health."""
from fastapi.testclient import TestClient

from app.http_status import app


def test_created_representation_preserves_the_supplied_name():
    with TestClient(app) as client:
        response = client.post("/widgets", json={"name": "gear"})
    assert response.json() == {"id": 1, "name": "gear"}


def test_empty_name_is_rejected():
    with TestClient(app) as client:
        response = client.post("/widgets", json={"name": ""})
    assert response.status_code == 422


def test_health_stays_successful():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
