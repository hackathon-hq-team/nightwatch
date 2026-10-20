"""Background jobs for Streamlit; workers never call Streamlit APIs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import threading
import time
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

SCENARIOS = {
    "empty-sample": "Empty sample · reproducible code error",
    "insufficient-evidence": "Memory pressure · insufficient evidence",
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


class ObservableHistory(HistoryStore):
    def __init__(self, root, notify):
        super().__init__(root)
        self.notify = notify

    def save(self, incident):
        # A history reader or antivirus may briefly hold the destination on Windows.
        # Keep atomic replacement and retry only these transient sharing errors.
        for attempt in range(5):
            try:
                super().save(incident)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (5, 32) or attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
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

    def snapshot(self) -> Job | None:
        with self._lock:
            if self._job is None:
                return None
            return replace(self._job, incident=self._job.incident.model_copy(deep=True)
                           if self._job.incident is not None else None)

    def records(self) -> list[tuple[str, Incident]]:
        records = [("demo", inc) for inc in HistoryStore(self.demo_history).all()]
        records += [("live", inc) for inc in HistoryStore(self.live_history).all()]
        return sorted(records, key=lambda row: row[1].created_at, reverse=True)

    def submit(self, mode: str, scenario: str) -> str:
        if mode not in ("demo", "live") or scenario not in SCENARIOS:
            raise ValueError("Unknown run mode or scenario")
        if mode == "live" and not self.settings.api_key:
            raise ValueError("Set NEBIUS_API_KEY in .env and restart the console.")
        with self._lock:
            if self._job is not None and self._job.running:
                raise ValueError("An investigation is already running.")
            job = Job(uuid.uuid4().hex, mode, scenario)
            self._job = job
        self._executor.submit(self._run, job)
        return job.id

    def _run(self, job: Job):
        def notify(incident):
            with self._lock:
                job.incident = incident.model_copy(deep=True)
        try:
            output = self.root / ".ui-runs" / job.id
            manifest = prepare(output)
            repo = Path(manifest["repo"])
            alert = Alert.model_validate_json((Path(manifest["alerts"]) / f"{job.scenario}.json").read_text("utf-8"))
            settings = replace(self.settings, history_dir=self.live_history)
            if job.mode == "demo":
                settings = replace(settings, triage_model="", escalation_model="")
                llm = DemoModel()
            else:
                llm = ModelClient(settings)
            history = ObservableHistory(self.demo_history if job.mode == "demo" else self.live_history, notify)
            incident = Pipeline(llm, Runner(repo), Verifier(), RepoView(repo), history, settings).run(alert)
            notify(incident)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if self.settings.api_key:
                message = message.replace(self.settings.api_key, "[redacted]")
            with self._lock:
                job.error = message[:1000]
        finally:
            with self._lock:
                job.running = False
