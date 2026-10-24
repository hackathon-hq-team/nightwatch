"""Offline refusal cycles with scripted model replies and genuine local evidence."""
from copy import deepcopy

import pytest

from conftest import REPRO_TEST, ScriptedLLM
from evaluation.refusal_cases import REFUSALS, SCRIPTED_REPLIES, scripted_refusal
from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import Alert, IncidentState
from scripts.run_demo import assess_result, expected_outcome
from verifier import Verifier


class RecordingRunner(LocalRunner):
    """Record executions so a direct refusal cannot silently run a proposed test."""

    def __init__(self, repo):
        super().__init__(repo)
        self.executions = []

    def run(self, commit, test_files, patch=None, select=None):
        self.executions.append({"commit": commit, "patch": patch, "select": select})
        return super().run(commit, test_files, patch=patch, select=select)


def build_pipeline(demo_repo, tmp_path, case, script):
    repo, _, broken = demo_repo
    # Only Alert fields enter the runtime. Evaluation expectations remain separate.
    raw = {key: value for key, value in case.items() if key not in {"slug", "expected"}}
    alert = Alert(source="scenario", commit=broken, **raw)
    settings = Settings(api_key="", triage_model="", escalation_model="",
                        web_search=False, tavily_api_key="", history_dir=tmp_path / "history")
    llm, runner = ScriptedLLM(script), RecordingRunner(repo)
    pipeline = Pipeline(llm, runner, Verifier(), RepoView(repo), HistoryStore(settings.history_dir), settings)
    return pipeline, llm, runner, alert


def test_refusal_fixtures_are_distinct_synthetic_reports_not_execution_claims():
    assert len({case["slug"] for case in REFUSALS}) == 3
    assert len({SCRIPTED_REPLIES[case["slug"]]["reason"] for case in REFUSALS}) == 3
    for case in REFUSALS:
        assert case["logs"].startswith("SYNTHETIC SCENARIO:")
        assert "Traceback (most recent call last)" not in case["logs"]
        assert "stacktrace" not in case and "previous_good_commit" not in case
        assert expected_outcome(case["slug"], case) == IncidentState.reported


@pytest.mark.parametrize("case", REFUSALS, ids=lambda case: case["slug"])
def test_insufficient_evidence_produces_specific_report_without_test_or_patch(demo_repo, tmp_path, case):
    pipeline, llm, runner, alert = build_pipeline(demo_repo, tmp_path, case, scripted_refusal(case["slug"]))
    incident = pipeline.run(alert)
    answer = SCRIPTED_REPLIES[case["slug"]]
    assert incident.state == IncidentState.reported and not incident.error
    assert not incident.attempts and incident.final_patch is None
    assert incident.repro_broken is None and incident.repro_good is None
    assert incident.investigation.repro_test is None
    assert incident.investigation.not_reproducible_reason == answer["reason"]
    assert incident.report.facts == answer["facts"]
    assert incident.report.next_steps == answer["next_steps"]
    assert [step for step, _ in llm.calls] == ["investigate", "report"]
    assert runner.executions == []
    assert assess_result(case["slug"], incident, IncidentState.reported)[0]
    assert pipeline.history.load(incident.id).report == incident.report


@pytest.mark.parametrize("case", REFUSALS, ids=lambda case: case["slug"])
def test_known_local_calculator_bug_cannot_substitute_for_reported_external_issue(demo_repo, tmp_path, case):
    script = scripted_refusal(case["slug"])
    pipeline, llm, runner, alert = build_pipeline(demo_repo, tmp_path, case, script)
    unrelated = {
        "hypotheses": [{"summary": "Incorrect scripted claim that average([]) explains this incident", "confidence": 0.9}],
        "repro_test": {
            "path": "tests/nightwatch/test_unrelated_average.py", "code": REPRO_TEST,
            # A claimed matching signature cannot override the actual executed failure.
            "expected": alert.signature().model_dump(),
        },
    }
    llm.script["investigate"] = [deepcopy(unrelated), deepcopy(unrelated)]
    llm.script["report"][0]["facts"].append(
        "The local calculator raised ZeroDivisionError, which does not match the reported incident."
    )
    incident = pipeline.run(alert)
    assert incident.state == IncidentState.reported and not incident.error
    assert not incident.attempts and incident.final_patch is None
    assert incident.repro_broken.status == "failed" and incident.repro_broken.exit_code == 1
    assert incident.repro_broken.failures[0].exception_type == "ZeroDivisionError"
    assert incident.repro_verdict.ok is False
    assert incident.repro_verdict.checks["same_signature"] is False
    assert len(runner.executions) == 2 and all(run["patch"] is None for run in runner.executions)
    assert [step for step, _ in llm.calls] == ["investigate", "investigate", "report"]
    assert assess_result(case["slug"], incident, IncidentState.reported)[0]
