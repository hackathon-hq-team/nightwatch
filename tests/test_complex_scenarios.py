"""Run trusted fixtures through real Git, pytest, pipeline and team verifier."""
import difflib
import json

import pytest

from conftest import ScriptedLLM
from evaluation.catalog import ADVANCED_CASES, CASES, replace_once
from nightwatch.config import Settings
from nightwatch.context import ContextBuilder, RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline, diff_files
from nightwatch.schemas import Alert, IncidentState, ReproTest
from scripts.run_demo import assess_result, discover, expected_outcome
from test_demo_scenarios import prepared  # Shared preparation, not a model service.
from tools.prepare_demo import git, observe
from verifier import Verifier

BASELINE_COUNT = 4 + sum(case["baseline_count"] for case in CASES)


def case_inputs(prepared, case, *, reference=True):
    output, manifest = prepared
    raw = json.loads((output / "alerts" / f"{case['slug']}.json").read_text(encoding="utf-8"))
    alert = Alert.model_validate(raw)
    if reference:
        alert = alert.model_copy(update={"previous_good_commit": manifest["scenarios"][case["slug"]]["good_commit"]})
    test = ReproTest(path=f"tests/nightwatch/test_{case['slug'].replace('-', '_')}.py",
                     code=case["reproducer"], expected=alert.signature())
    return output / "repo", alert, test


def candidate_patch(repo, case, replacements):
    source = (repo / case["app_file"]).read_text(encoding="utf-8")
    fixed = replace_once(source, replacements)
    return "".join(difflib.unified_diff(source.splitlines(True), fixed.splitlines(True),
                                     fromfile="a/" + case["app_file"], tofile="b/" + case["app_file"]))


def test_all_seven_alerts_are_discovered_without_runtime_registration(prepared):
    output, manifest = prepared
    names = discover(output / "alerts")
    assert names == ["empty-sample", "insufficient-evidence"] + sorted(case["slug"] for case in CASES)
    repo = output / "repo"
    for case in CASES:
        raw = json.loads((output / "alerts" / f"{case['slug']}.json").read_text(encoding="utf-8"))
        assert expected_outcome(case["slug"], raw) == IncidentState.verified
        commits = manifest["scenarios"][case["slug"]]
        assert raw["commit"] == commits["broken_commit"]
        assert raw.get("previous_good_commit") is None
        parent = git(repo, "rev-parse", raw["commit"] + "~1")
        assert parent != commits["good_commit"]
        assert git(repo, "diff", "--name-only", parent, raw["commit"]) == ""
        assert git(repo, "diff", "--name-only", raw["commit"], commits["good_commit"]) == case["app_file"]
        evidence, _ = ContextBuilder(RepoView(repo)).build(Alert.model_validate(raw))
        # No repair hunks or evaluation files reach the model through its history context.
        assert all("@@" not in item.content for item in evidence if item.kind == "diff")
        assert all("evaluation/" not in item.source for item in evidence)
    assert not (repo / "evaluation").exists()
    assert not (repo / "tools").exists()
    assert not any((repo / case["app_file"]).exists() for case in ADVANCED_CASES)
    assert not list(repo.rglob("*.pyc"))
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == manifest["broken_commit"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["slug"])
def test_real_reproduction_frozen_answers_and_good_patch(prepared, case):
    repo, alert, test = case_inputs(prepared, case)
    runner, verifier = LocalRunner(repo), Verifier()
    baseline = runner.run(alert.commit, {})
    assert baseline.status == "passed", baseline.output_tail
    assert len(baseline.passed) == BASELINE_COUNT
    files = {test.path: test.code}
    broken = runner.run(alert.commit, files, select=[test.path])
    good = runner.run(alert.previous_good_commit, files, select=[test.path])
    assert verifier.check_reproduction(alert, test, broken, good).ok, broken.output_tail
    assert broken.failures[0].exception_type == case["exception_type"]
    assert broken.failures[0].file == case["app_file"]
    assert case["exception_type"] in alert.logs

    files["tests/nightwatch/test_acceptance.py"] = case["acceptance"]
    frozen = runner.tests_hash(alert.commit, files)
    patch = candidate_patch(repo, case, case["good_replacements"])
    assert diff_files(patch) == {case["app_file"]}
    after = runner.run(alert.commit, files, patch=patch)
    assert verifier.check_patch(alert, test, after, frozen).ok, after.output_tail
    assert len(after.passed) == BASELINE_COUNT + 1 + case["acceptance_count"]
    assert set(baseline.passed) < set(after.passed)
    assert after.tests_hash == frozen


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["slug"])
def test_superficial_repairs_cannot_satisfy_independent_answers(prepared, case):
    repo, alert, test = case_inputs(prepared, case)
    runner = LocalRunner(repo)
    files = {test.path: test.code, "tests/nightwatch/test_acceptance.py": case["acceptance"]}
    frozen = runner.tests_hash(alert.commit, files)
    superficially_passing = []
    for bad in case["bad_replacements"]:
        patch = candidate_patch(repo, case, bad["replacements"])
        repro_only = runner.run(alert.commit, {test.path: test.code}, patch=patch, select=[test.path])
        if repro_only.status == "passed":
            superficially_passing.append(bad["name"])
        after = runner.run(alert.commit, files, patch=patch)
        assert after.patch_applied is True, (bad["name"], after.output_tail)
        assert after.status == "failed" and after.exit_code == 1, (bad["name"], after.output_tail)
        assert after.tests_hash == frozen
        assert not Verifier().check_patch(alert, test, after, frozen).ok, bad["name"]
    # Each scenario has a plausible patch that passes the observed case, yet is wrong.
    assert superficially_passing, case["slug"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["slug"])
def test_complete_pipeline_cycle_with_explicit_scripted_responses(prepared, case, tmp_path):
    repo, alert, test = case_inputs(prepared, case, reference=False)
    patch = candidate_patch(repo, case, case["good_replacements"])
    llm = ScriptedLLM({"investigate": [{
        "hypotheses": [{"summary": "Prepared scenario diagnosis", "confidence": 0.9}],
        "repro_test": test.model_dump(),
    }], "patch": [{"diff": patch, "explanation": "Prepared fixture repair; not a model result"}]})
    history = HistoryStore(tmp_path / "history")
    pipeline = Pipeline(llm, LocalRunner(repo), Verifier(), RepoView(repo), history,
                        Settings(api_key="", triage_model="", escalation_model=""))
    incident = pipeline.run(alert)
    assert incident.state == IncidentState.verified, incident.model_dump_json()
    assert assess_result(case["slug"], incident, IncidentState.verified)[0]
    assert [step for step, _ in llm.calls] == ["investigate", "patch"]
    assert incident.repro_verdict.ok and incident.attempts[0].verdict.ok
    assert incident.repro_good is None  # Reference repairs are reserved for independent acceptance.
    assert incident.investigation.repro_test == test
    assert len(incident.attempts[0].run.passed) == BASELINE_COUNT + 1
    assert history.load(incident.id).final_patch.diff == patch
    states = {event.state for event in incident.events}
    assert states == {IncidentState.received, IncidentState.investigating, IncidentState.reproducing,
                      IncidentState.patching, IncidentState.verified}


def test_library_alert_contains_real_external_frames(prepared):
    case = next(case for case in CASES if case["slug"] == "packaging-legacy-release")
    _, alert, _ = case_inputs(prepared, case)
    assert alert.exception_type == "InvalidVersion"
    assert alert.stacktrace[-1].in_app is False
    assert alert.stacktrace[-1].file == "site-packages/packaging/version.py"
    assert alert.signature().file == "app/plugin_binding.py"


def test_capture_does_not_mislabel_setup_errors_as_target_bug(prepared):
    import subprocess

    output, _ = prepared
    with pytest.raises(subprocess.CalledProcessError):
        observe(output / "repo", "import nonexistent_scenario_dependency", "InventoryError")
    with pytest.raises(subprocess.CalledProcessError):
        observe(output / "repo", "pass", "InventoryError")
