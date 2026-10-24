"""Signed ingress, durable coordination and publication guards; no external writes."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from app.service import ConsoleService, SCENARIOS
from app.storage import BusyError, JobStore
from nightwatch.config import Settings
from nightwatch.schemas import Incident, IncidentState, Investigation, PatchProposal, RunResult, TestFailure, Verdict
from conftest import FIX_DIFF, REPRO_TEST, ScriptedLLM


def test_history_does_not_retry_unrelated_permission_failure(tmp_path, monkeypatch):
    from nightwatch.history import HistoryStore
    from nightwatch.schemas import Alert
    calls = []
    def denied(self, target):
        calls.append(target)
        raise PermissionError("Not a Windows sharing failure")
    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError):
        HistoryStore(tmp_path).save(Incident(alert=Alert(title="test", exception_type="Error", commit="a" * 40)))
    assert len(calls) == 1


def test_sentry_credentials_are_redacted_in_header_pairs_and_urls():
    pytest.importorskip("fastapi")
    from app.webhook import redact
    value = {"headers": [["Authorization", "Bearer private"], ["X-Api-Key", "private"]],
             "apiKey": "private", "url": "https://app.example/?token=private&sample=1"}
    cleaned = redact(value)
    assert "private" not in json.dumps(cleaned)
    assert cleaned["url"].endswith("&sample=1")


def test_all_scenarios_are_visible_and_cross_process_jobs_are_exclusive(tmp_path):
    assert len(SCENARIOS) == 7
    first, second = JobStore(tmp_path), JobStore(tmp_path)
    job, duplicate = first.create("one", "live", "sentry", {}, delivery="event-one")
    assert not duplicate
    same, duplicate = second.create("two", "live", "sentry", {}, delivery="event-one")
    assert same["id"] == job["id"] and duplicate
    with pytest.raises(BusyError):
        second.create("three", "demo", "empty-sample", {})
    first.abandon("one")
    second.create("four", "demo", "empty-sample", {})
    assert first.latest()["id"] == "four"
    second.claim_publication("inc-12345678")
    with pytest.raises(BusyError):
        first.claim_publication("inc-12345678")


@pytest.fixture
def receiver(tmp_path, demo_repo, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import app.webhook as module
    repo, good, broken = demo_repo
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "signing-secret")
    monkeypatch.setenv("NIGHTWATCH_TARGET_REPO", str(repo))
    monkeypatch.setenv("NIGHTWATCH_SENTRY_PROJECT", "42")
    monkeypatch.setenv("NIGHTWATCH_GITHUB_REPO", "owner/application")
    monkeypatch.setattr(module, "readiness", lambda image: (True, "ready"))
    console = ConsoleService(tmp_path, Settings(api_key="test-api-key", triage_model="", escalation_model=""))
    pending = []
    monkeypatch.setattr(console._executor, "submit", lambda *args: pending.append(args))
    payload = {"action": "triggered", "data": {"event": {
        "event_id": "event-one", "project": 42, "release": broken,
        "tags": {"previous_release": good}, "request": {"data": {"password": "private"}},
        "exception": {"values": [{"type": "ZeroDivisionError", "value": "division by zero", "stacktrace": {
            "frames": [{"filename": "app/calc.py", "function": "average", "lineno": 3, "in_app": True}]}}]},
    }}}
    return TestClient(module.create_app(console)), console, payload, pending


def deliver(client, payload, *, signature=None, resource="event_alert"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    signed = signature if signature is not None else hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/sentry", content=body,
                       headers={"Sentry-Hook-Signature": signed, "Sentry-Hook-Resource": resource})


def test_signed_alert_is_queued_once_redacted_and_visible_after_restart(receiver):
    client, console, payload, pending = receiver
    result = deliver(client, payload)
    assert result.status_code == 202
    job = result.json()["job"]
    assert len(pending) == 1
    payload["delivery_metadata"] = "changed on retry"
    duplicate = deliver(client, payload)
    assert duplicate.status_code == 200 and duplicate.json()["job"] == job
    assert len(pending) == 1
    row = console.store.get(job)
    assert row["metadata"]["alert"]["request"]["data"]["password"] == "[redacted]"
    restarted = ConsoleService(console.root, console.settings)
    assert restarted.snapshot().id == job
    assert client.get(f"/jobs/{job}").status_code == 401
    status = client.get(f"/jobs/{job}", headers={"Authorization": "Bearer signing-secret"})
    assert status.status_code == 200 and "metadata" not in status.json()


@pytest.mark.parametrize("change,status", [("signature", 401), ("project", 403), ("release", 422),
                                          ("invalid-json", 400), ("huge", 413), ("event-list", 422)])
def test_invalid_ingress_never_starts_work(receiver, change, status):
    client, console, payload, pending = receiver
    signature = None
    if change == "signature":
        signature = "invalid"
    elif change == "project":
        payload["data"]["event"]["project"] = 99
    elif change == "release":
        payload["data"]["event"]["release"] = "--upload-pack=bad"
    elif change == "invalid-json":
        payload = b"{invalid"
    elif change == "huge":
        payload = b"x" * (1024 * 1024 + 1)
    elif change == "event-list":
        payload["data"]["event"] = []
    assert deliver(client, payload, signature=signature).status_code == status
    assert not pending and console.store.latest() is None


def test_unrelated_event_ignored_and_busy_delivery_is_retriable(receiver):
    client, _, payload, pending = receiver
    assert deliver(client, payload, resource="installation").json()["ignored"]
    assert not pending
    assert deliver(client, payload).status_code == 202
    payload["data"]["event"]["event_id"] = "another-event"
    response = deliver(client, payload)
    assert response.status_code == 503 and response.headers["Retry-After"] == "30"
    assert len(pending) == 1


def verified_incident(alert):
    test = {"path": "tests/nightwatch/test_repro.py", "code": REPRO_TEST, "expected": alert.signature()}
    return Incident(alert=alert, state=IncidentState.verified,
                    investigation=Investigation(hypotheses=[{"summary": "Missing guard", "confidence": 0.9}],
                                                repro_test=test),
                    repro_verdict=Verdict(ok=True, reason="matched"),
                    final_patch=PatchProposal(diff=FIX_DIFF, explanation="Restore empty guard"))


class FakeGitHub:
    def __init__(self, commit, *, moved=False, existing=False):
        self.commit = commit
        self.moved = moved
        self.existing = existing
        self.writes = []

    def request(self, endpoint, payload=None):
        if payload is not None:
            self.writes.append((endpoint, payload))
            if endpoint.endswith("/git/trees"):
                return {"sha": "tree"}
            if endpoint.endswith("/git/commits"):
                return {"sha": "new-commit"}
            if endpoint.endswith("/pulls"):
                return {"html_url": "https://github.com/owner/application/pull/1", "number": 1}
            return {}
        if "/pulls?" in endpoint:
            return [{"html_url": "https://github.com/owner/application/pull/1", "number": 1}] if self.existing else []
        if "/commits/" in endpoint:
            return {"sha": "wrong" if self.moved else self.commit, "commit": {"tree": {"sha": "base-tree"}}}
        if "/git/ref/" in endpoint:
            raise RuntimeError("No existing branch")
        raise AssertionError(endpoint)


class PublicationRunner:
    def __init__(self, repo, alert, *, fail=False, missing=False):
        from nightwatch.docker_runner import Runner
        self.snapshot = Runner(repo)._snapshot
        self.alert = alert
        self.last_files = None
        self.fail = fail
        self.missing = missing

    def _snapshot(self, commit, files):
        return self.snapshot(commit, files)

    def tests_hash(self, commit, files):
        return "frozen"

    def run(self, commit, files, patch=None, select=None):
        original = ["tests/test_calc.py::test_average", "tests/test_calc.py::test_total"]
        if not files:
            return RunResult(commit=commit, status="passed", exit_code=0, tests_hash="frozen", passed=original)
        path = next(iter(files))
        if patch:
            self.last_files = self.snapshot(commit, files)
            self.last_files["app/calc.py"] = self.last_files["app/calc.py"].replace(
                b"    return sum(items) / len(items)", b"    if not items:\n        return 0\n    return sum(items) / len(items)")
            return RunResult(commit=commit, status="passed", exit_code=0, tests_hash="bad" if self.fail else "frozen",
                             patch_applied=True, passed=(original[1:] if self.missing else original) + [path + "::test_repro"])
        return RunResult(commit=commit, status="failed", exit_code=1, tests_hash="frozen", failures=[
            TestFailure(nodeid=path + "::test_repro", exception_type=self.alert.exception_type,
                        message=self.alert.message, file="app/calc.py", function="average")])


def test_draft_pr_contains_tested_files_and_never_updates_base(demo_repo, alert):
    from app.github_pr import publish
    repo, _, broken = demo_repo
    alert = alert.model_copy(update={"previous_good_commit": None})
    incident = verified_incident(alert)
    metadata = {"kind": "repository", "repository": str(repo), "github_repo": "owner/application", "base_branch": "main"}
    api = FakeGitHub(broken)
    created = publish(incident, metadata, api, PublicationRunner(repo, alert))
    assert created["url"].endswith("/pull/1")
    tree = next(body for endpoint, body in api.writes if endpoint.endswith("/git/trees"))
    assert tree["base_tree"] == "base-tree"
    paths = {entry["path"] for entry in tree["tree"]}
    assert paths == {"app/calc.py", "tests/nightwatch/test_repro.py", "tests/nightwatch/__init__.py"}
    pull = api.writes[-1][1]
    assert pull["draft"] is True and pull["base"] == "main"
    assert pull["head"].startswith("codex/nightwatch-inc-")
    assert not any("/merges" in endpoint or "/git/refs/heads/main" in endpoint for endpoint, _ in api.writes)


@pytest.mark.parametrize("fault", ["fixture", "reported", "acceptance", "moved-base", "failed-recheck", "missing-original"])
def test_publication_fails_closed_without_any_remote_write(demo_repo, alert, fault):
    from app.github_pr import publish
    repo, _, broken = demo_repo
    alert = alert.model_copy(update={"previous_good_commit": None})
    incident = verified_incident(alert)
    metadata = {"kind": "fixture" if fault == "fixture" else "repository", "repository": str(repo),
                "github_repo": "owner/application", "base_branch": "main"}
    if fault == "reported":
        incident.state = IncidentState.reported
    if fault == "acceptance":
        metadata["acceptance"] = {"ok": False}
    api = FakeGitHub(broken, moved=fault == "moved-base")
    with pytest.raises(ValueError):
        publish(incident, metadata, api, PublicationRunner(repo, alert, fail=fault == "failed-recheck",
                                                          missing=fault == "missing-original"))
    assert not api.writes


def test_retry_reuses_existing_pr_without_writes(demo_repo, alert):
    from app.github_pr import publish
    repo, _, broken = demo_repo
    api = FakeGitHub(broken, existing=True)
    metadata = {"kind": "repository", "repository": str(repo), "github_repo": "owner/application"}
    assert publish(verified_incident(alert), metadata, api)["existing"]
    assert not api.writes


docker = pytest.mark.skipif(os.getenv("NIGHTWATCH_TEST_DOCKER") != "1", reason="Requires Docker runner image")


@docker
@pytest.mark.parametrize("scenario", ["settlement-rounding", "inventory-replay", "snapshot-pagination",
                                      "reservation-boundaries", "packaging-legacy-release"])
def test_complex_scenarios_pipeline_and_independent_acceptance_in_docker(tmp_path, scenario):
    console = ConsoleService(tmp_path, Settings(api_key="", triage_model="", escalation_model=""))
    console.submit("demo", scenario)
    deadline = time.monotonic() + 100
    while console.snapshot().running and time.monotonic() < deadline:
        time.sleep(0.1)
    job = console.snapshot()
    assert not job.running and not job.error, job
    assert job.incident.state.value == "verified", job.incident.model_dump_json()
    assert job.metadata["acceptance"]["ok"], job.metadata["acceptance"]
    assert all("acceptance" not in item.source for item in job.incident.evidence)


@docker
def test_signed_webhook_runs_pipeline_with_real_docker(receiver, monkeypatch):
    client, console, payload, pending = receiver
    import app.service as module
    from concurrent.futures import ThreadPoolExecutor
    console._executor = ThreadPoolExecutor(max_workers=1)
    event = payload["data"]["event"]
    from nightwatch.sentry import alert_from_sentry
    alert = alert_from_sentry(payload)
    incident = verified_incident(alert)
    scripted = ScriptedLLM({"investigate": [incident.investigation.model_dump()],
                           "patch": [incident.final_patch.model_dump()]})
    monkeypatch.setattr(module, "ModelClient", lambda settings: scripted)
    response = deliver(client, payload)
    assert response.status_code == 202
    deadline = time.monotonic() + 60
    while console.snapshot().running and time.monotonic() < deadline:
        time.sleep(0.1)
    job = console.snapshot()
    assert not job.error and job.incident.state.value == "verified", job
    assert job.incident.alert.source == "sentry"
    assert console.records()[0][0] == "live"


@docker
def test_draft_pr_artifacts_are_really_verified_in_docker(demo_repo, alert):
    from app.github_pr import publish
    from nightwatch.docker_runner import Runner
    repo, _, broken = demo_repo
    metadata = {"kind": "repository", "repository": str(repo), "github_repo": "owner/application"}
    api = FakeGitHub(broken)
    publish(verified_incident(alert), metadata, api, Runner(repo))
    tree = next(body for endpoint, body in api.writes if endpoint.endswith("/git/trees"))
    entries = {item["path"]: item for item in tree["tree"]}
    assert entries["tests/nightwatch/test_repro.py"]["content"] == REPRO_TEST
    assert "if not items:" in entries["app/calc.py"]["content"]
    assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]).strip()
