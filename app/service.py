"""Background jobs for Streamlit; workers never call Streamlit APIs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.docker_runner import DEFAULT_IMAGE, Runner
from nightwatch.history import HistoryStore
from nightwatch.model_client import ModelClient
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import Alert, Incident
from tools.prepare_demo import prepare
from verifier import Verifier

from app.demo_model import DemoModel
from app.storage import BusyError, JobStore

SCENARIOS = {
    "empty-sample": "Empty sample · reproducible code error",
    "insufficient-evidence": "Memory pressure · insufficient evidence",
}
from evaluation.catalog import CASES
SCENARIOS.update({case["slug"]: case["title"] for case in CASES})

SCENARIO_DESCRIPTIONS = {
    "empty-sample": "A missing empty-input guard causes division by zero.",
    "insufficient-evidence": "Missing input and workload evidence should end in a report.",
    **{case["slug"]: f"Application: {case['app_file']} · independent acceptance checks after verification."
       for case in CASES},
}


def readiness(image: str = DEFAULT_IMAGE) -> tuple[bool, str]:
    for args, description in [(["docker", "info", "--format", "{{.OSType}}"], "Docker Engine"),
                              (["docker", "image", "inspect", image], f"Runner image {image}")]:
        try:
            outcome = subprocess.run(args, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False, f"{description} is unavailable. Start Docker Desktop and refresh."
        if outcome.returncode:
            return False, ("Build the runner image; see docs/docker-runner.md."
                           if description.startswith("Runner") else "Docker Engine is unavailable. Start Docker Desktop.")
        if description == "Docker Engine" and outcome.stdout.strip() != "linux":
            return False, "Switch Docker Desktop to Linux containers."
    return True, "Docker sandbox ready"


@dataclass
class Job:
    id: str
    mode: str
    scenario: str
    running: bool = True
    incident: Incident | None = None
    error: str = ""
    metadata: dict = field(default_factory=dict)


class ObservableHistory(HistoryStore):
    def __init__(self, root, notify):
        super().__init__(root)
        self.notify = notify

    def save(self, incident):
        super().save(incident)
        self.notify(incident)


class ConsoleService:
    """One job at a time across browser sessions in this UI process.

    ModelClient keeps its existing shared daily spend file for live runs. Demo
    incidents use a different store and can never become live memory evidence.
    """
    def __init__(self, root: Path, settings: Settings | None = None):
        self.root = Path(root).resolve()
        self.settings = settings or Settings()
        history = self.settings.history_dir
        self.live_history = history if history.is_absolute() else self.root / history
        self.demo_history = self.root / ".ui-runs" / "demo-history"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nightwatch-ui")
        self._lock = threading.Lock()
        self._job = None
        self.store = JobStore(self.root / ".ui-runs" / "state")

    def snapshot(self) -> Job | None:
        row = self.store.latest()
        with self._lock:
            if self._job is not None and row and row["id"] == self._job.id:
                return replace(self._job, incident=self._job.incident.model_copy(deep=True)
                               if self._job.incident is not None else None, metadata=dict(row["metadata"]))
        if row is None:
            return None
        incident = None
        if row["incident"]:
            history = self.demo_history if row["mode"] == "demo" else self.live_history
            try:
                incident = HistoryStore(history).load(row["incident"])
            except (OSError, ValueError):
                pass
        return Job(row["id"], row["mode"], row["scenario"], row["state"] == "running",
                   incident, row["error"], row["metadata"])

    def metadata(self, incident_id):
        row = self.store.for_incident(incident_id)
        return row["metadata"] if row else {}

    def publish(self, incident_id):
        import re
        from app.github_pr import publish, validate_incident
        if not re.fullmatch(r"inc-[0-9a-f]{8}", incident_id):
            raise ValueError("Invalid incident identifier")
        row = self.store.for_incident(incident_id)
        if row is None or row["mode"] != "live" or row["state"] != "finished":
            raise ValueError("Wait for this live investigation to complete before publishing.")
        metadata = row["metadata"]
        incident = HistoryStore(self.live_history).load(incident_id)
        validate_incident(incident, metadata)
        previous = self.store.publication(incident_id)
        if previous and previous["state"] == "created":
            return previous["result"]
        self.store.claim_publication(incident_id)
        try:
            result = publish(incident, metadata)
            self.store.finish_publication(incident_id, result)
            return result
        except Exception as error:
            self.store.finish_publication(incident_id, error=type(error).__name__)
            raise

    def _submit(self, mode, scenario, metadata, delivery=None):
        row, duplicate = self.store.create(uuid.uuid4().hex, mode, scenario, metadata, delivery)
        if not duplicate:
            job = Job(row["id"], mode, scenario, metadata=metadata)
            with self._lock:
                self._job = job
            try:
                self._executor.submit(self._run, job)
            except Exception:
                self.store.update(job.id, state="failed", error="Worker could not be started")
                with self._lock:
                    job.running = False
                raise
        return row["id"], duplicate

    def records(self) -> list[tuple[str, Incident]]:
        records = [("demo", inc) for inc in HistoryStore(self.demo_history).all()]
        records += [("live", inc) for inc in HistoryStore(self.live_history).all()]
        return sorted(records, key=lambda row: row[1].created_at, reverse=True)

    def submit(self, mode: str, scenario: str) -> str:
        if mode not in ("demo", "live") or scenario not in SCENARIOS:
            raise ValueError("Unknown run mode or scenario")
        if mode == "live" and not self.settings.api_key:
            raise ValueError("Set NEBIUS_API_KEY in .env and restart the console.")
        return self._submit(mode, scenario, {"kind": "fixture", "scenario": scenario})[0]

    def submit_alert(self, alert: Alert, repo: Path, delivery: str):
        if not self.settings.api_key:
            raise ValueError("Live model API key is not configured")
        return self._submit("live", "sentry", {
            "kind": "repository", "repository": str(Path(repo).resolve()),
            "github_repo": os.getenv("NIGHTWATCH_GITHUB_REPO", ""),
            "base_branch": os.getenv("NIGHTWATCH_GITHUB_BASE", "main"),
            "alert": alert.model_dump(mode="json"),
        }, delivery)

    def _run(self, job: Job):
        def notify(incident):
            self.store.update(job.id, incident=incident.id)
            with self._lock:
                job.incident = incident.model_copy(deep=True)
        try:
            case = next((case for case in CASES if case["slug"] == job.scenario), None)
            if job.metadata["kind"] == "fixture":
                output = self.root / ".ui-runs" / job.id
                manifest = prepare(output)
                repo = Path(manifest["repo"])
                alert = Alert.model_validate_json((Path(manifest["alerts"]) / f"{job.scenario}.json").read_text("utf-8"))
                job.metadata["workspace"] = str(output)
            else:
                repo = Path(job.metadata["repository"])
                alert = Alert.model_validate(job.metadata["alert"])
            self.store.update(job.id, metadata=job.metadata)
            settings = replace(self.settings, history_dir=self.live_history)
            if job.mode == "demo":
                settings = replace(settings, triage_model="", escalation_model="")
                source = RepoView(repo).show(alert.commit, case["app_file"]) if case else None
                llm = DemoModel(case, source)
            else:
                llm = ModelClient(settings)
            history = ObservableHistory(self.demo_history if job.mode == "demo" else self.live_history, notify)
            incident = Pipeline(llm, Runner(repo), Verifier(), RepoView(repo), history, settings).run(alert)
            notify(incident)
            if case and incident.final_patch and incident.state.value == "verified":
                # Private answers are used only after the runtime model has finished.
                # They never become Incident evidence or memory/model context.
                from tools.check_scenario_patch import evaluate, load_inputs
                job.metadata["acceptance"] = {"status": "running", "ok": False,
                                               "reason": "Running independent acceptance checks"}
                self.store.update(job.id, metadata=job.metadata)
                trusted_case, trusted_alert, patch, acceptance_repo = load_inputs(output, job.scenario,
                                                                                 history.path(incident.id))
                acceptance = evaluate(trusted_case, trusted_alert, patch, Runner(acceptance_repo))
                acceptance["runner"] = "docker"
                (output / "acceptance.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
                job.metadata["acceptance"] = acceptance
                self.store.update(job.id, metadata=job.metadata)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if self.settings.api_key:
                message = message.replace(self.settings.api_key, "[redacted]")
            with self._lock:
                job.error = message[:1000]
            if job.metadata.get("acceptance", {}).get("status") == "running":
                job.metadata["acceptance"] = {"status": "error", "ok": False, "reason": message[:1000]}
                self.store.update(job.id, metadata=job.metadata)
        finally:
            self.store.update(job.id, state="failed" if job.error else "finished", error=job.error)
            with self._lock:
                job.running = False
