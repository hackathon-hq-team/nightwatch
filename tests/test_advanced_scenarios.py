"""Independent offline checks for the opt-in, compact advanced suite."""
import json

import pytest

from conftest import ScriptedLLM
from evaluation.catalog import ADVANCED_CASES, CASES, baseline_count
from nightwatch.config import Settings
from nightwatch.context import ContextBuilder, RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import IncidentState
from scripts.run_demo import discover
from test_complex_scenarios import candidate_patch, case_inputs
from tools.check_scenario_patch import evaluate, load_inputs
from tools.prepare_demo import git, prepare
from verifier import Verifier


@pytest.fixture(scope="module")
def advanced(tmp_path_factory):
    output = tmp_path_factory.mktemp("advanced-parent") / "workspace"
    return output, prepare(output, suite="advanced")


def test_opt_in_preparation_keeps_suites_separate_and_validates_before_writing(advanced, tmp_path):
    output, manifest = advanced
    assert manifest["suite"] == "advanced"
    assert discover(output / "alerts") == sorted(case["slug"] for case in ADVANCED_CASES)
    assert len(manifest["scenarios"]) == 3
    assert not any((output / "repo" / case["app_file"]).exists() for case in CASES)
    assert git(output / "repo", "status", "--porcelain") == ""
    with pytest.raises(ValueError, match="Unknown scenario suite"):
        prepare(tmp_path / "invalid", suite="typo")
    assert not (tmp_path / "invalid").exists()
    with pytest.raises(FileExistsError):
        prepare(output, suite="advanced")


@pytest.mark.parametrize("case", ADVANCED_CASES, ids=lambda case: case["slug"])
def test_compact_context_is_complete_without_reference_repairs(advanced, case):
    repo, alert, _ = case_inputs(advanced, case, reference=False)
    evidence, _ = ContextBuilder(RepoView(repo)).build(alert)
    # Character limits bound input size; they are NOT a tokenizer or a live usage claim.
    assert len(json.dumps(case["request"], indent=2)) < 4000
    assert sum(len(item.content) for item in evidence) <= 12000
    source = (repo / case["app_file"]).read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 120  # ContextBuilder includes this entire file.
    assert all(not item.truncated for item in evidence)
    assert {item.source.split("@")[0] for item in evidence if item.kind == "file"} == {case["app_file"]}
    assert all("@@" not in item.content for item in evidence if item.kind == "diff")
    assert all("evaluation/" not in item.source for item in evidence)
    assert alert.previous_good_commit is None
    assert not (repo / "evaluation").exists()
    assert not (repo / "tools").exists()


@pytest.mark.parametrize("case", ADVANCED_CASES, ids=lambda case: case["slug"])
def test_real_failure_reference_and_independent_acceptance(advanced, case):
    repo, alert, _ = case_inputs(advanced, case)
    patch = candidate_patch(repo, case, case["good_replacements"])
    result = evaluate(case, alert, patch, LocalRunner(repo))
    assert result["ok"], result
    assert all(result["checks"].values())
    assert len(result["runs"]["baseline"]["passed"]) == baseline_count(case)
    assert len(result["runs"]["candidate"]["passed"]) == baseline_count(case) + 1 + case["acceptance_count"]


@pytest.mark.parametrize("case", ADVANCED_CASES, ids=lambda case: case["slug"])
def test_partial_and_overfitted_repairs_are_rejected(advanced, case):
    repo, alert, repro = case_inputs(advanced, case)
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
    assert overfitted, "Every scenario must reject a patch that satisfies only its incident input"


@pytest.mark.parametrize("case", ADVANCED_CASES, ids=lambda case: case["slug"])
def test_scripted_cycle_and_saved_patch_acceptance(advanced, case, tmp_path):
    repo, alert, repro = case_inputs(advanced, case, reference=False)
    # Exercise the current search/replace reply contract, not a hand-formatted model diff.
    edits = [{"file": case["app_file"], "old": old, "new": new}
             for old, new in case["good_replacements"]]
    llm = ScriptedLLM({
        "investigate": [{"hypotheses": [{"summary": "Trusted offline fixture", "confidence": 0.9}],
                         "repro_test": repro.model_dump()}],
        "patch": [{"edits": edits, "explanation": "Prepared fixture repair, not a live model result"}],
    })
    history = HistoryStore(tmp_path / "history")
    incident = Pipeline(llm, LocalRunner(repo), Verifier(), RepoView(repo), history,
                        Settings(api_key="", triage_model="", escalation_model="")).run(alert)
    assert incident.state == IncidentState.verified, incident.model_dump_json()
    assert [step for step, _ in llm.calls] == ["investigate", "patch"]
    assert incident.investigation.repro_test == repro and incident.repro_good is None
    saved = tmp_path / "saved.json"
    saved.write_text(incident.model_dump_json(), encoding="utf-8")
    loaded_case, reference_alert, patch, loaded_repo = load_inputs(advanced[0], case["slug"], saved)
    assert loaded_repo == repo and reference_alert.previous_good_commit
    result = evaluate(loaded_case, reference_alert, patch, LocalRunner(repo))
    assert result["ok"], result
