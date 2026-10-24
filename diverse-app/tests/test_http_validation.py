"""Original HTTP checks for successful prices and structurally invalid requests."""
from fastapi.testclient import TestClient
import pytest

from app.http_validation import app


@pytest.mark.parametrize("quantity, total", [(1, 125), (4, 500)])
def test_valid_quote(quantity, total):
    with TestClient(app) as client:
        response = client.post("/quote", json={"quantity": quantity})
    assert response.status_code == 200
    assert response.json() == {"quantity": quantity, "total_minor": total}


@pytest.mark.parametrize("body", [{}, {"quantity": "not-a-number"}])
def test_missing_or_unparseable_quantity_returns_validation_error(body):
    with TestClient(app) as client:
        response = client.post("/quote", json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "quantity"]
