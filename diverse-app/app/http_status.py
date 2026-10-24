"""HTTP fixture: POST /widgets returns 201, preserving its deterministic JSON body."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


class HttpContractError(RuntimeError):
    """A client-side fixture probe observed an unexpected HTTP response status."""


class WidgetRequest(BaseModel):
    name: str = Field(min_length=1)


app = FastAPI()


@app.post("/widgets", status_code=200)
def create_widget(body: WidgetRequest) -> dict:
    """Return a stateless demo representation; no persistent resource is created."""
    return {"id": 1, "name": body.name}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def probe_widget_creation(name: str) -> dict:
    """Client-side contract diagnostic, not a server exception or network request.

    TestClient dispatches a real in-process ASGI HTTP request through FastAPI.
    The probe reports HTTP status mismatches explicitly as client observations.
    """
    with TestClient(app) as client:
        response = client.post("/widgets", json={"name": name})
    if response.status_code != 201:
        raise HttpContractError(f"Client-side HTTP contract probe: expected 201, received {response.status_code}")
    return response.json()
