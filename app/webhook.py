"""Signed Sentry issue-alert receiver. Run: uvicorn app.webhook:app --host 127.0.0.1 --port 8000."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.service import ConsoleService, readiness
from app.storage import BusyError
from nightwatch.sentry import alert_from_sentry

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
PRIVATE = re.compile(r"^(?:x[-_]?)?(?:authorization|cookie|cookies|password|passwd|secret|client[-_]?secret|"
                     r"api[-_]?key|access[-_]?token|refresh[-_]?token|token|sentry[-_]hook[-_]signature)$", re.I)


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {key: "[redacted]" if PRIVATE.fullmatch(str(key)) else redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        # Sentry represents request headers as [name, value] pairs in some payloads.
        if len(value) == 2 and isinstance(value[0], str) and PRIVATE.fullmatch(value[0]):
            return [value[0], "[redacted]"]
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)([?&](?:token|api_key|password|secret|access_token)=)[^&#\s]+", r"\1[redacted]", value)
    return value


def validate_release(repo, commit):
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ValueError("Sentry release and previous_release must be full lowercase Git commit SHAs")
    result = subprocess.run(["git", "-C", str(repo), "cat-file", "-t", commit], capture_output=True,
                            text=True, timeout=10)
    if result.returncode or result.stdout.strip() != "commit":
        raise ValueError("Release commit is not available in the configured local repository")


def create_app(console: ConsoleService | None = None):
    app = FastAPI(title="Nightwatch Sentry receiver", docs_url=None, redoc_url=None)
    app.state.console = console or ConsoleService(ROOT)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "receiver_configured": bool(os.getenv("SENTRY_WEBHOOK_SECRET"))}

    def secret():
        configured = os.getenv("SENTRY_WEBHOOK_SECRET", "")
        if not configured:
            raise HTTPException(503, "Sentry signing secret is not configured")
        return configured

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str, request: Request):
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + secret()).encode()):
            raise HTTPException(401, "Authentication required")
        job = app.state.console.store.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        # Do not expose payloads, repository paths, logs or credentials over HTTP.
        return {"job": job_id, "state": job["state"], "incident": job["incident"],
                "failed": bool(job["error"])}

    def register(payload, digest):
        target = os.getenv("NIGHTWATCH_TARGET_REPO", "")
        project = os.getenv("NIGHTWATCH_SENTRY_PROJECT", "")
        if not target or not project:
            raise HTTPException(503, "Repository and Sentry project must be configured")
        data = payload.get("data", {})
        event = data.get("event", {})
        if not isinstance(event, dict):
            raise HTTPException(422, "Expected data.event in an issue-alert payload")
        event_project = event.get("project")
        if isinstance(event_project, dict):
            event_project = event_project.get("id")
        if str(event_project) != project:
            raise HTTPException(403, "Event project is not configured for this receiver")
        console = app.state.console
        safe_payload = redact(payload, (secret(), console.settings.api_key, os.getenv("GH_TOKEN", "")))
        try:
            alert = alert_from_sentry(safe_payload)
            repo = Path(target).resolve()
            validate_release(repo, alert.commit)
            if alert.previous_good_commit:
                validate_release(repo, alert.previous_good_commit)
        except (ValueError, TypeError, AttributeError, OSError, subprocess.TimeoutExpired) as error:
            raise HTTPException(422, "Invalid event, release SHA or unavailable repository commit") from error
        # Sentry may retry an event with different delivery metadata. Prefer the
        # event identity, scoped to this receiver's fixed project and repository.
        event_id = event.get("event_id") or event.get("id") or digest
        delivery = hashlib.sha256(f"{repo}:{project}:{event_id}".encode()).hexdigest()
        with console.store.connect() as db:
            existing = db.execute("SELECT id FROM jobs WHERE delivery=?", (delivery,)).fetchone()
        if existing:
            return JSONResponse({"job": existing["id"], "duplicate": True}, status_code=200)
        if not console.settings.api_key:
            raise HTTPException(503, "Live model API key is not configured")
        ready, _ = readiness(os.getenv("NIGHTWATCH_RUNNER_IMAGE", "nightwatch-runner:dev"))
        if not ready:
            raise HTTPException(503, "Docker runner is not ready", headers={"Retry-After": "30"})
        try:
            job, duplicate = console.submit_alert(alert, repo, delivery)
        except BusyError as error:
            raise HTTPException(503, "An investigation is running; retry later", headers={"Retry-After": "30"}) from error
        return JSONResponse({"job": job, "duplicate": duplicate}, status_code=200 if duplicate else 202)

    @app.post("/webhooks/sentry")
    async def sentry(request: Request):
        signing_secret = secret()
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Payload exceeds 1 MiB")
        signature = request.headers.get("Sentry-Hook-Signature", "")
        expected = hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), signature.encode()):
            raise HTTPException(401, "Invalid Sentry signature")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise HTTPException(400, "Invalid JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise HTTPException(422, "Expected an issue-alert object")
        resource = request.headers.get("Sentry-Hook-Resource", "event_alert")
        if resource not in ("event_alert", "event-alert", "error"):
            return {"ignored": True, "reason": "Unsupported webhook resource"}
        if payload.get("action") not in (None, "triggered", "created"):
            return {"ignored": True, "reason": "Not a new error alert"}
        return await run_in_threadpool(register, payload, hashlib.sha256(body).hexdigest())

    return app


app = create_app()
