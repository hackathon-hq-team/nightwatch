"""Varied checkpoint cases, verified offline with trusted scripted responses."""
import json
from importlib.util import find_spec

import pytest

from conftest import ScriptedLLM
from evaluation.context_cases import CASES as CONTEXT_CASES
from evaluation.diverse_catalog import NEW_CASES, REPEAT_OF, REPEAT_SLUG, baseline_count, get_case
from evaluation.refusal_cases import REFUSALS
from nightwatch.config import Settings
from nightwatch.context import ContextBuilder, RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import Alert, IncidentState
from scripts.run_demo import discover
from test_complex_scenarios import candidate_patch, case_inputs
from tools.check_scenario_patch import evaluate, load_inputs, validate_patch_scope
from tools.prepare_demo import git, prepare, require_diverse_dependencies
from verifier import Verifier

pytestmark = pytest.mark.skipif(not find_spec("fastapi") or not find_spec("httpx"),
                                reason="Install diverse-app/requirements.txt for HTTP fixture validation")


@pytest.fixture(scope="module")
def diverse(tmp_path_factory):
    output = tmp_path_factory.mktemp("diverse-parent") / "workspace"
    return output, prepare(output, suite="diverse")


def test_seventeen_cases_are_discovered_with_explicit_outcomes(diverse, tmp_path):
    output, manifest = diverse
    names = discover(output / "alerts")
    assert len(names) == len(set(names)) == 17
    assert manifest["suite"] == "diverse"
    assert names.index(REPEAT_OF) < names.index(REPEAT_SLUG)
    reports = []
    for name in names:
        raw = json.loads((output / "alerts" / f"{name}.json").read_text())
        Alert.model_validate(raw)
        if raw["expected"] == "reported":
            reports.append(name)
            assert raw["logs"].startswith("SYNTHETIC SCENARIO")
        else:
            assert raw["expected"] == "verified"
    assert set(reports) == {"insufficient-evidence", *(case["slug"] for case in REFUSALS)}
    assert not (output / "repo/evaluation").exists()
    assert git(output / "repo", "status", "--porcelain") == ""
    with pytest.raises(FileExistsError):
        prepare(output, suite="diverse")
    with pytest.raises(ValueError, match="Unknown scenario suite"):
        prepare(tmp_path / "invalid", suite="typo")
    assert not (tmp_path / "invalid").exists()


def test_missing_optional_dependency_is_explicit(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="diverse-app/requirements.txt"):
        require_diverse_dependencies()


@pytest.mark.parametrize("case", NEW_CASES, ids=lambda case: case["slug"])
def test_real_failure_reference_and_frozen_acceptance(diverse, case):
    repo, alert, _ = case_inputs(diverse, case)
    scoped = {**case, "suite": "diverse"}
    patch = candidate_patch(repo, case, case["good_replacements"])
    result = evaluate(scoped, alert, patch, LocalRunner(repo))
    assert result["ok"], result
    broken = result["runs"]["reproduction"]
    assert broken["failures"][0]["file"] == case.get("failure_file", case["app_file"])
    assert len(result["runs"]["baseline"]["passed"]) == baseline_count("diverse")


@pytest.mark.parametrize("case", NEW_CASES, ids=lambda case: case["slug"])
def test_overfitted_repairs_do_not_pass_independent_answers(diverse, case):
    repo, alert, repro = case_inputs(diverse, case)
    runner = LocalRunner(repo)
    files = {repro.path: repro.code, "tests/nightwatch/test_oracles.py": case["acceptance"]}
    frozen = runner.tests_hash(alert.commit, files)
    overfitted = []
    for bad in case["bad_replacements"]:
        patch = candidate_patch(repo, case, bad["replacements"])
        primary = runner.run(alert.commit, {repro.path: repro.code}, patch=patch, select=[repro.path])
        if primary.status == "passed":
            overfitted.append(bad["name"])
        after = runner.run(alert.commit, files, patch=patch)
        assert after.patch_applied is True, (bad["name"], after.output_tail)
        assert after.status == "failed" and after.exit_code == 1, (bad["name"], after.output_tail)
        assert after.tests_hash == frozen
        assert not Verifier().check_patch(alert, repro, after, frozen).ok
    assert overfitted, case["slug"]


@pytest.mark.parametrize("case", CONTEXT_CASES, ids=lambda case: case["slug"])
def test_cross_file_case_shows_the_producer_only_through_imports(diverse, case):
    repo, alert, _ = case_inputs(diverse, case, reference=False)
    assert {frame.file for frame in alert.stacktrace if frame.in_app} == {case["failure_file"]}
    evidence, _ = ContextBuilder(RepoView(repo)).build(alert)
    shown = {item.source.split("@")[0]: item.source for item in evidence if item.kind == "file"}
    # The traceback names only the consumer; the producer reaches the model as an imported module.
    assert set(shown) == {case["failure_file"], case["app_file"]}
    assert f"imported by {case['failure_file']}" in shown[case["app_file"]]
    assert case["app_file"] not in alert.logs
    # A consumer-only bypass must not become an accepted producer repair.
    bypass = f"--- a/{case['failure_file']}\n+++ b/{case['failure_file']}\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(ValueError):
        validate_patch_scope(bypass, case["app_file"])


def test_unrelated_deployment_diff_is_visible_but_the_repair_is_not(diverse):
    case = get_case("unrelated-receipt-diff")
    repo, alert, _ = case_inputs(diverse, case, reference=False)
    parent = git(repo, "rev-parse", alert.commit + "~1")
    assert git(repo, "diff", "--name-only", parent, alert.commit) == "app/receipt_view.py"
    assert git(repo, "show", f"{parent}:app/invoice.py") == git(repo, "show", f"{alert.commit}:app/invoice.py")
    evidence, _ = ContextBuilder(RepoView(repo)).build(alert)
    diff = "\n".join(item.content for item in evidence if item.kind == "diff")
    assert "New checkout receipt" in diff and "@@" in diff
    assert "total = subtotal - discount" not in diff
    assert alert.previous_good_commit is None


def scripted(case, repro):
    return {
        "investigate": [{"hypotheses": [{"summary": "Trusted fixture diagnosis", "confidence": 0.9}],
                         "repro_test": repro.model_dump()}],
        "patch": [{"edits": [{"file": case["app_file"], "old": old, "new": new}
                              for old, new in case["good_replacements"]],
                   "explanation": "Trusted fixture repair; not a live model result"}],
    }


@pytest.mark.parametrize("case", NEW_CASES, ids=lambda case: case["slug"])
def test_scripted_cycle_and_saved_patch_acceptance(diverse, case, tmp_path):
    repo, alert, repro = case_inputs(diverse, case, reference=False)
    llm = ScriptedLLM(scripted(case, repro))
    history = HistoryStore(tmp_path / "history")
    incident = Pipeline(llm, LocalRunner(repo), Verifier(), RepoView(repo), history,
                        Settings(api_key="", triage_model="", escalation_model="")).run(alert)
    assert incident.state == IncidentState.verified, incident.events
    assert [step for step, _ in llm.calls] == ["investigate", "patch"]
    saved = tmp_path / "saved.json"
    saved.write_text(incident.model_dump_json(), encoding="utf-8")
    bound_case, bound_alert, patch, bound_repo = load_inputs(diverse[0], case["slug"], saved)
    assert bound_case["suite"] == "diverse" and bound_repo == repo
    assert evaluate(bound_case, bound_alert, patch, LocalRunner(repo))["ok"]


def test_repeat_uses_an_actual_first_incident_and_reverifies(diverse, tmp_path):
    first_case, repeat_case = get_case(REPEAT_OF), get_case(REPEAT_SLUG)
    repo, first_alert, first_repro = case_inputs(diverse, first_case, reference=False)
    _, repeat_alert, repeat_repro = case_inputs(diverse, repeat_case, reference=False)
    assert first_alert.commit == repeat_alert.commit and first_alert.request == repeat_alert.request
    assert first_alert.signature() == repeat_alert.signature()
    script = scripted(first_case, first_repro)
    second_script = scripted(repeat_case, repeat_repro)
    for step in script:
        script[step].extend(second_script[step])
    llm = ScriptedLLM(script)
    history = HistoryStore(tmp_path / "history")
    pipeline = Pipeline(llm, LocalRunner(repo), Verifier(), RepoView(repo), history,
                        Settings(api_key="", triage_model="", escalation_model=""))
    first, repeat = pipeline.run(first_alert), pipeline.run(repeat_alert)
    assert first.state == repeat.state == IncidentState.verified
    assert not first.similar_incidents
    assert repeat.similar_incidents == [first.id]
    assert any(item.kind == "memory" and item.source.startswith(first.id) for item in repeat.evidence)
    assert first.id != repeat.id
    assert first.repro_verdict.ok and repeat.repro_verdict.ok
    assert first.attempts[0].verdict.ok and repeat.attempts[0].verdict.ok
    assert [step for step, _ in llm.calls] == ["investigate", "patch", "investigate", "patch"]
    saved = tmp_path / "repeat.json"
    saved.write_text(repeat.model_dump_json(), encoding="utf-8")
    bound, accepted_alert, patch, _ = load_inputs(diverse[0], REPEAT_SLUG, saved)
    assert evaluate(bound, accepted_alert, patch, LocalRunner(repo))["ok"]
