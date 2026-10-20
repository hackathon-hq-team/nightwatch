"""Operator-console checks: mode boundaries, launch guards and rendered outcomes."""
from pathlib import Path
import time

import pytest

from app.demo_model import DemoModel
from app.service import ConsoleService, ObservableHistory
from nightwatch.config import Settings
from nightwatch.schemas import Alert, DiagnosticReport, Incident, IncidentState, Investigation, PatchProposal, Usage


def test_demo_and_live_memory_are_separate(tmp_path):
    console = ConsoleService(tmp_path, Settings(api_key="", history_dir=Path("live-history")))
    assert console.live_history == tmp_path / "live-history"
    assert console.demo_history != console.live_history
    with pytest.raises(ValueError, match="NEBIUS_API_KEY"):
        console.submit("live", "empty-sample")
    with pytest.raises(ValueError, match="Unknown"):
        console.submit("demo", "not-a-scenario")


def test_running_job_cannot_be_submitted_twice(tmp_path, monkeypatch):
    console = ConsoleService(tmp_path, Settings(api_key=""))
    pending = []
    monkeypatch.setattr(console._executor, "submit", lambda *args: pending.append(args))
    console.submit("demo", "empty-sample")
    with pytest.raises(ValueError, match="already running"):
        console.submit("demo", "empty-sample")
    assert len(pending) == 1


def test_observable_history_retries_windows_sharing_error(tmp_path, monkeypatch):
    from nightwatch.history import HistoryStore
    original = HistoryStore.save
    attempts = []
    def save(self, incident):
        attempts.append(incident.id)
        if len(attempts) == 1:
            error = PermissionError("Sharing violation")
            error.winerror = 32
            raise error
        original(self, incident)
    monkeypatch.setattr(HistoryStore, "save", save)
    notified = []
    store = ObservableHistory(tmp_path, notified.append)
    incident = Incident(alert=Alert(title="test", exception_type="Error", commit="abc"))
    store.save(incident)
    assert len(attempts) == 2 and notified == [incident]
    assert store.load(incident.id) == incident


def test_scripted_model_is_explicit_and_has_zero_api_usage():
    usage = Usage()
    result = DemoModel().complete_json("investigate", "", "MemoryError", Investigation, usage)
    assert result.repro_test is None and result.not_reproducible_reason
    assert usage.calls[0].model == "scripted-demo"
    assert usage.cost_usd == usage.tokens == 0


@pytest.fixture
def app_test(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import app.service as module
    console = ConsoleService(tmp_path, Settings(api_key="", history_dir=Path("live-history")))
    monkeypatch.setattr(module, "ConsoleService", lambda root: console)
    monkeypatch.setattr(module, "readiness", lambda image: (True, "Docker sandbox ready"))
    st.cache_resource.clear()
    st.cache_data.clear()
    return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app/streamlit_app.py")), console


def test_empty_console_and_live_key_guard(app_test):
    app, _ = app_test
    app.run()
    assert not app.exception
    assert any("From alert to evidence" in item.value for item in app.markdown)
    app.radio[0].set_value("Live Nemotron").run()
    assert not app.exception
    button = next(button for button in app.button if button.label == "Run live investigation")
    assert button.disabled
    assert any("NEBIUS_API_KEY" in item.value for item in app.warning)


@pytest.mark.parametrize("state", [IncidentState.verified, IncidentState.reported, IncidentState.failed])
def test_saved_outcomes_render_and_export(app_test, state):
    app, console = app_test
    alert = Alert(title="Saved incident", exception_type="ZeroDivisionError", commit="abc123")
    incident = Incident(alert=alert, state=state)
    if state == IncidentState.verified:
        incident.final_patch = PatchProposal(diff="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-bad\n+good\n",
                                             explanation="A prepared verified patch")
    if state == IncidentState.reported:
        incident.report = DiagnosticReport(facts=["No reproducible input"], hypotheses=[], next_steps=["Capture input"])
    if state == IncidentState.failed:
        incident.error = "Execution failed"
    from nightwatch.history import HistoryStore
    HistoryStore(console.demo_history).save(incident)
    app.run()
    assert not app.exception
    assert app.metric[0].value == state.value.capitalize()
    assert any(tab.label == "Evidence" for tab in app.tabs)
    assert any(tab.label == "Tests & cost" for tab in app.tabs)


@pytest.mark.skipif(__import__("os").getenv("NIGHTWATCH_TEST_DOCKER") != "1", reason="Requires runner image and Docker")
@pytest.mark.parametrize("scenario,outcome", [("empty-sample", "verified"), ("insufficient-evidence", "reported")])
def test_ui_worker_with_real_docker(tmp_path, scenario, outcome):
    console = ConsoleService(tmp_path, Settings(api_key=""))
    console.submit("demo", scenario)
    deadline = time.monotonic() + 60
    while console.snapshot().running and time.monotonic() < deadline:
        time.sleep(0.05)
    job = console.snapshot()
    assert not job.running and not job.error, job
    assert job.incident.state.value == outcome, job.incident.error
    assert console.records()[0][0] == "demo"
    assert not list(console.live_history.glob("inc-*.json"))
