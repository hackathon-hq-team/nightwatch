"""Offline regressions for live-demo acceptance and shared daily spending."""
import json
import sys
from types import SimpleNamespace

import pytest

from conftest import ScriptedLLM
from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.model_client import BudgetExceeded, ModelClient, ModelError
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import (
    Alert, DiagnosticReport, Event, Hypothesis, Incident, IncidentState, Investigation,
    PatchProposal, ReproTest, RunResult, Usage, Verdict,
)
from scripts import run_demo
from verifier import Verifier


@pytest.fixture
def refusal():
    alert = Alert(title="Missing evidence", exception_type="MemoryError", commit="broken")
    hypothesis = Hypothesis(summary="Load profile is required", confidence=0.1)
    return Incident(alert=alert, state=IncidentState.reported,
                    investigation=Investigation(hypotheses=[hypothesis],
                                                not_reproducible_reason="No input or load profile"),
                    report=DiagnosticReport(facts=["No reproducer input"], hypotheses=[hypothesis],
                                            next_steps=["Collect the input and load profile"]))


def test_completed_refusal_passes(refusal):
    assert run_demo.assess_result("insufficient-evidence", refusal)[0]


@pytest.mark.parametrize("change", ["missing_investigation", "missing_report", "missing_reason", "error",
                                      "budget_stop", "fallback_report", "patch"])
def test_reported_alone_does_not_pass(refusal, change):
    if change == "missing_investigation":
        refusal.investigation = None
    elif change == "missing_report":
        refusal.report = None
    elif change == "missing_reason":
        refusal.investigation.not_reproducible_reason = " "
    elif change == "error":
        refusal.error = "Runner unavailable"
    elif change == "budget_stop":
        refusal.events.append(Event(state=IncidentState.reported, message="Stopped by budget: exhausted"))
    elif change == "fallback_report":
        refusal.events.append(Event(state=IncidentState.reported,
                                    message="Diagnostic report unavailable: ModelError"))
    else:
        refusal.final_patch = PatchProposal(diff="fixture", explanation="Must not propose a patch")
    assert not run_demo.assess_result("insufficient-evidence", refusal)[0]


@pytest.mark.parametrize("status,code,accepted", [
    ("passed", 0, True), ("failed", 1, True), ("error", 2, False), ("error", 5, False),
    ("timeout", -1, False), ("passed", 1, False),
])
def test_rejected_candidate_requires_completed_execution(refusal, status, code, accepted):
    refusal.investigation.repro_test = ReproTest(path="tests/nightwatch/test_repro.py", code="fixture",
                                               expected=refusal.alert.signature())
    refusal.repro_verdict = Verdict(ok=False, reason="Different failure")
    refusal.repro_broken = RunResult(commit="broken", status=status, exit_code=code)
    assert run_demo.assess_result("insufficient-evidence", refusal)[0] is accepted


@pytest.mark.parametrize("failure", [BudgetExceeded("offline budget stop"), ModelError("offline report outage")])
def test_actual_pipeline_stop_or_report_outage_is_not_pass(demo_repo, alert, tmp_path, failure):
    repo, _, _ = demo_repo
    investigation = {"hypotheses": [{"summary": "Not enough evidence", "confidence": 0.1}],
                     "not_reproducible_reason": "Missing load profile"}
    script = {"investigate": [failure if isinstance(failure, BudgetExceeded) else investigation],
              "report": [failure]}
    pipeline = Pipeline(ScriptedLLM(script), LocalRunner(repo), Verifier(), RepoView(repo),
                        HistoryStore(tmp_path), Settings())
    incident = pipeline.run(alert)
    assert incident.state == IncidentState.reported
    assert incident.report is not None  # Useful fallback remains available to the UI.
    assert not run_demo.assess_result("insufficient-evidence", incident)[0]


def wire_main(monkeypatch, tmp_path, refusal):
    """Only stub external calls and pipeline work; exercise real main output and client budgets."""
    clients = []
    histories = []
    monkeypatch.setenv("NIGHTWATCH_DEBUG_DIR", str(tmp_path / "debug"))
    settings = lambda: Settings(history_dir=tmp_path / "shared-history", daily_budget_usd=0.5)
    monkeypatch.setattr(run_demo, "Settings", settings)
    monkeypatch.setattr(run_demo, "check_model", lambda client: True)
    monkeypatch.setattr(run_demo, "load_verifier", lambda: (Verifier(), "team fixture"))

    def client_factory(configuration):
        client = ModelClient(configuration, client=SimpleNamespace())
        clients.append(client)
        return client

    def prepare(output):
        alerts = output / "alerts"
        alerts.mkdir(parents=True)
        (alerts / "insufficient-evidence.json").write_text(refusal.alert.model_dump_json(), encoding="utf-8")
        return {"repo": str(output / "repo"), "alerts": str(alerts)}

    def pipeline_factory(client, runner, verifier, repo, history, configuration):
        histories.append(history.root)
        return SimpleNamespace(run=lambda alert: refusal)

    monkeypatch.setattr(run_demo, "ModelClient", client_factory)
    monkeypatch.setattr(run_demo, "prepare", prepare)
    monkeypatch.setattr(run_demo, "Pipeline", pipeline_factory)
    return clients, histories


def test_main_shares_daily_budget_across_output_directories(monkeypatch, tmp_path, refusal):
    clients, histories = wire_main(monkeypatch, tmp_path, refusal)
    outputs = [tmp_path / "first-run", tmp_path / "second-run"]
    for index, output in enumerate(outputs):
        monkeypatch.setattr(sys, "argv", ["run_demo", "--scenario", "insufficient-evidence", "--output", str(output)])
        assert run_demo.main() == 0
        if index == 0:
            clients[0].daily.add(0.5)
    assert clients[0].daily.path == clients[1].daily.path
    assert clients[1].daily.spent_today() == 0.5
    with pytest.raises(BudgetExceeded):
        clients[1]._check_budget(Usage())
    assert histories == [output / "history" for output in outputs]


def test_main_fails_and_records_reason_for_budget_stop(monkeypatch, tmp_path, refusal):
    refusal.events.append(Event(state=IncidentState.reported, message="Stopped by budget: exhausted"))
    wire_main(monkeypatch, tmp_path, refusal)
    output = tmp_path / "budget-stop"
    monkeypatch.setattr(sys, "argv", ["run_demo", "--scenario", "insufficient-evidence", "--output", str(output)])
    assert run_demo.main() == 1
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))[0]
    assert result["got"] == "reported" and result["ok"] is False
    assert "budget" in result["acceptance_reason"]
