"""Trusted in-process HTTP scenarios; no remote endpoints or runtime model calls.

The status-code case records a CLIENT contract diagnostic, not a server crash.
Its independent oracle always inspects FastAPI's HTTP responses directly.
"""
from textwrap import dedent


VALIDATION_FIX = ["    quantity: int", "    quantity: int = Field(gt=0, strict=True)"]
DEPENDENCY_ERROR = '        raise AccessDenied("Missing or invalid X-Token")'
DEPENDENCY_HTTP_ERROR = '        raise HTTPException(status_code=401, detail="Missing or invalid X-Token")'
STATUS_FIX = ['@app.post("/widgets", status_code=200)', '@app.post("/widgets", status_code=201)']
STATUS_PROBE = '''    if response.status_code != 201:
        raise HttpContractError(f"Client-side HTTP contract probe: expected 201, received {response.status_code}")
'''

CASES = [
    {
        "slug": "http-body-validation",
        "title": "A zero quantity reaches pricing instead of an HTTP validation response",
        "app_file": "app/http_validation.py",
        "exception_type": "QuantityError",
        "baseline_count": 4,
        "acceptance_count": 10,
        "call": 'from fastapi.testclient import TestClient\nfrom app.http_validation import app\n'
                'with TestClient(app) as client:\n    client.post("/quote", json={"quantity": 0})\n',
        "request": {"method": "POST", "path": "/quote", "json": {"quantity": 0}},
        "good_replacements": [VALIDATION_FIX],
        "reproducer": dedent('''\
            from fastapi.testclient import TestClient
            from app.http_validation import app

            def test_zero_quantity_is_an_http_validation_error():
                with TestClient(app) as client:
                    response = client.post("/quote", json={"quantity": 0})
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["body", "quantity"]
            '''),
        "acceptance": dedent('''\
            import pytest
            from fastapi.testclient import TestClient
            from app.http_validation import app

            @pytest.mark.parametrize("quantity", [0, -1, -4, True, False, "2", 1.5, None])
            def test_invalid_json_quantities_are_rejected_before_pricing(quantity):
                with TestClient(app) as client:
                    response = client.post("/quote", json={"quantity": quantity})
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["body", "quantity"]

            @pytest.mark.parametrize("quantity, total", [(2, 250), (7, 875)])
            def test_valid_integer_quantities_keep_their_price(quantity, total):
                with TestClient(app) as client:
                    response = client.post("/quote", json={"quantity": quantity})
                assert response.status_code == 200
                assert response.json() == {"quantity": quantity, "total_minor": total}
            '''),
        "bad_replacements": [
            {"name": "coerce-noninteger-json-inputs", "replacements": [
                ["    quantity: int", "    quantity: int = Field(gt=0)"],
            ]},
            {"name": "reject-valid-small-quantities-too", "replacements": [
                ["    quantity: int", "    quantity: int = Field(gt=100, strict=True)"],
            ]},
        ],
    },
    {
        "slug": "http-header-dependency",
        "title": "The X-Token dependency leaks an exception instead of returning HTTP 401",
        "app_file": "app/http_dependency.py",
        "exception_type": "AccessDenied",
        "baseline_count": 3,
        "acceptance_count": 5,
        "call": 'from fastapi.testclient import TestClient\nfrom app.http_dependency import app\n'
                'with TestClient(app) as client:\n    client.get("/profile")\n',
        "request": {"method": "GET", "path": "/profile", "headers": {}},
        "good_replacements": [[DEPENDENCY_ERROR, DEPENDENCY_HTTP_ERROR]],
        "reproducer": dedent('''\
            from fastapi.testclient import TestClient
            from app.http_dependency import app

            def test_missing_token_returns_unauthorized_response():
                with TestClient(app) as client:
                    response = client.get("/profile")
                assert response.status_code == 401
                assert response.json() == {"detail": "Missing or invalid X-Token"}
            '''),
        "acceptance": dedent('''\
            import pytest
            from fastapi.testclient import TestClient
            from app.http_dependency import app

            @pytest.mark.parametrize("headers", [{}, {"X-Token": ""}, {"X-Token": "wrong"}, {"X-Token": " demo-token"}])
            def test_every_invalid_token_returns_the_same_http_error(headers):
                with TestClient(app) as client:
                    response = client.get("/profile", headers=headers)
                assert response.status_code == 401
                assert response.json() == {"detail": "Missing or invalid X-Token"}

            def test_valid_token_and_public_health_remain_available():
                with TestClient(app) as client:
                    valid = client.get("/profile", headers={"x-token": "demo-token", "X-Other": "noise"})
                    health = client.get("/health", headers={"X-Token": "wrong"})
                assert valid.status_code == 200
                assert valid.json() == {"user": "demo", "scope": "read"}
                assert health.status_code == 200
                assert health.json() == {"status": "ok"}
            '''),
        "bad_replacements": [
            {"name": "translate-only-missing-header", "replacements": [[
                DEPENDENCY_ERROR,
                '        if x_token is None:\n    ' + DEPENDENCY_HTTP_ERROR + '\n' + DEPENDENCY_ERROR,
            ]]},
            {"name": "remove-authentication-dependency", "replacements": [[
                'token: str = Depends(require_token)', 'token: str | None = None',
            ]]},
        ],
    },
    {
        "slug": "http-created-status",
        "title": "Client-side HTTP contract probe observes 200 instead of 201 on widget creation",
        "app_file": "app/http_status.py",
        "exception_type": "HttpContractError",
        "baseline_count": 3,
        "acceptance_count": 7,
        "call": 'from app.http_status import probe_widget_creation\nprobe_widget_creation("bolt")\n',
        "request": {"method": "POST", "path": "/widgets", "json": {"name": "bolt"}},
        "good_replacements": [STATUS_FIX],
        "reproducer": dedent('''\
            from app.http_status import probe_widget_creation

            def test_client_probe_accepts_created_status():
                # This exception, when present, is a client diagnostic rather than a server crash.
                assert probe_widget_creation("bolt") == {"id": 1, "name": "bolt"}
            '''),
        "acceptance": dedent('''\
            import pytest
            from fastapi.testclient import TestClient
            from app.http_status import app

            @pytest.mark.parametrize("name", ["bolt", "gear", "A B", "cabinet"])
            def test_actual_creation_response_has_created_status_and_original_body(name):
                # Inspect HTTP directly: disabling the fixture probe cannot satisfy this oracle.
                with TestClient(app) as client:
                    response = client.post("/widgets", json={"name": name})
                assert response.status_code == 201
                assert response.json() == {"id": 1, "name": name}

            @pytest.mark.parametrize("body", [{}, {"name": ""}])
            def test_invalid_body_retains_its_validation_status(body):
                with TestClient(app) as client:
                    response = client.post("/widgets", json=body)
                assert response.status_code == 422

            def test_unrelated_health_status_remains_200():
                with TestClient(app) as client:
                    response = client.get("/health")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
            '''),
        "bad_replacements": [
            {"name": "disable-client-probe-status-check", "replacements": [[STATUS_PROBE, ""]]},
            {"name": "created-status-only-for-observed-name", "replacements": [
                ["from fastapi import FastAPI", "from fastapi import FastAPI\nfrom fastapi.responses import JSONResponse"],
                ['    return {"id": 1, "name": body.name}',
                 '    return JSONResponse(status_code=201 if body.name == "bolt" else 200,\n'
                 '                        content={"id": 1, "name": body.name})'],
            ]},
        ],
    },
]
