"""Fail-closed checks for the independent saved-patch acceptance command."""
import difflib
import json

import pytest

from nightwatch.schemas import Alert, Incident, IncidentState, PatchProposal, RunResult, TestFailure
from evaluation.catalog import replace_once
from test_demo_scenarios import prepared
from tools.check_scenario_patch import (
    BASELINE_COUNT, CASES, ORACLE_PATH, REPRO_PATH, evaluate, load_inputs, main, validate_patch_scope,
)

CASE = next(case for case in CASES if case["slug"] == "snapshot-pagination")
PATCH = "--- a/app/pagination.py\n+++ b/app/pagination.py\n@@ -1 +1 @@\n-old\n+new\n"
BROKEN = "a" * 40
GOOD = "b" * 40
DIGEST = "c" * 64


@pytest.mark.parametrize("patch", [
    PATCH + "--- a/tests/test_original.py\n+++ b/tests/test_original.py\n@@ -1 +1 @@\n-old\n+new\n",
    "diff --git a/tests/test_original.py b/app/pagination.py\n" + PATCH,
    "diff --git a/app/pagination.py b/app/pagination.py\nrename from tests/test_original.py\n" + PATCH,
    "diff --git a/app/pagination.py b/app/pagination.py\ncopy from tests/test_original.py\n" + PATCH,
    "diff --git a/app/pagination.py b/app/pagination.py\nold mode 100644\nnew mode 100755\n" + PATCH,
    PATCH.replace("--- a/app/pagination.py", "--- /dev/null"),
    PATCH.replace("+++ b/app/pagination.py", "+++ b/app/../conftest.py"),
    "GIT binary patch\nliteral 4\n",
])
def test_patch_scope_rejects_extra_files_and_metadata(patch):
    with pytest.raises(ValueError):
        validate_patch_scope(patch, CASE["app_file"])


def alert():
    return Alert(title=CASE["title"], exception_type=CASE["exception_type"], commit=BROKEN,
                 previous_good_commit=GOOD)


class EvidenceRunner:
    """Return explicit runner evidence to exercise report validation boundaries."""

    def __init__(self, fault):
        self.fault = fault
        self.baseline = [f"tests/test_original.py::test_{index}" for index in range(BASELINE_COUNT)]
        self.all_nodes = self.baseline + [REPRO_PATH + "::test_target"] + [
            ORACLE_PATH + f"::test_oracle_{index}" for index in range(CASE["acceptance_count"])
        ]
        self.candidate_called = False

    def tests_hash(self, commit, files):
        return DIGEST

    def run(self, commit, files, patch=None, select=None):
        if not files:
            nodes = [] if self.fault == "missing-baseline-report" else self.baseline
            return RunResult(commit=commit, status="passed", exit_code=0, passed=nodes, tests_hash=DIGEST)
        if patch is None and commit == BROKEN:
            return RunResult(commit=commit, status="failed", exit_code=1, tests_hash=DIGEST, failures=[
                TestFailure(nodeid=REPRO_PATH + "::test_target", exception_type=CASE["exception_type"])
            ])
        if patch is None:
            nodes = self.all_nodes[:-1] if self.fault == "missing-golden-oracle" else self.all_nodes
            return RunResult(commit=commit, status="passed", exit_code=0, passed=nodes, tests_hash=DIGEST)
        self.candidate_called = True
        nodes = self.all_nodes
        if self.fault == "skipped-candidate-oracle":
            nodes = self.all_nodes[:-1]
        if self.fault == "duplicate-passing-node":
            nodes = self.all_nodes + [self.all_nodes[0]]
        return RunResult(commit=commit, status="passed", exit_code=0, passed=nodes, patch_applied=True,
                         tests_hash="d" * 64 if self.fault == "modified-tests" else DIGEST)


@pytest.mark.parametrize("fault", ["missing-baseline-report", "missing-golden-oracle",
                                  "skipped-candidate-oracle", "duplicate-passing-node", "modified-tests"])
def test_incomplete_or_modified_evidence_never_accepts(fault):
    runner = EvidenceRunner(fault)
    result = evaluate(CASE, alert(), PATCH, runner)
    assert result["ok"] is False
    assert result["status"] != "accepted"
    if fault in ("missing-baseline-report", "missing-golden-oracle"):
        assert runner.candidate_called is False


def write_input_workspace(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "alerts").mkdir()
    manifest = {"mode": "fixture", "repo": str(tmp_path / "repo"), "scenarios": {
        CASE["slug"]: {"broken_commit": BROKEN, "good_commit": GOOD, "app_file": CASE["app_file"]}
    }}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    runtime_alert = alert().model_copy(update={"previous_good_commit": None})
    (tmp_path / "alerts" / f"{CASE['slug']}.json").write_text(runtime_alert.model_dump_json())
    incident = Incident(alert=runtime_alert, state=IncidentState.verified,
                        final_patch=PatchProposal(diff=PATCH, explanation="Trusted test fixture"))
    path = tmp_path / "incident.json"
    path.write_text(incident.model_dump_json())
    return incident, path


@pytest.mark.parametrize("change", ["wrong-commit", "reported-state", "no-patch", "runtime-error", "oracle-exposed"])
def test_saved_incident_must_match_completed_scenario(tmp_path, change):
    incident, path = write_input_workspace(tmp_path)
    if change == "wrong-commit":
        incident.alert.commit = "e" * 40
    elif change == "reported-state":
        incident.state = IncidentState.reported
    elif change == "no-patch":
        incident.final_patch = None
    elif change == "oracle-exposed":
        incident.alert.previous_good_commit = GOOD
    else:
        incident.error = "Runner failed"
    path.write_text(incident.model_dump_json())
    with pytest.raises(ValueError):
        load_inputs(tmp_path, CASE["slug"], path)


def test_private_oracle_commit_is_bound_only_for_independent_acceptance(tmp_path):
    incident, path = write_input_workspace(tmp_path)
    _, acceptance_alert, _, _ = load_inputs(tmp_path, CASE["slug"], path)
    assert acceptance_alert.previous_good_commit == GOOD
    assert incident.alert.previous_good_commit is None
    assert Incident.model_validate_json(path.read_text()).alert.previous_good_commit is None


def test_missing_workspace_writes_error_result_and_nonzero_exit(tmp_path, capsys):
    output = tmp_path / "acceptance.json"
    code = main(["--workspace", str(tmp_path / "missing"), "--scenario", CASE["slug"],
                 "--incident", str(tmp_path / "incident.json"), "--output", str(output)])
    assert code == 2
    result = json.loads(output.read_text())
    assert result["ok"] is False and result["status"] == "error"
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("candidate,accepted", [("good", True), ("partial", False)])
def test_saved_trusted_fixture_runs_independent_acceptance(prepared, tmp_path, capsys, candidate, accepted):
    workspace, _ = prepared
    case = CASE
    generated = Alert.model_validate_json(
        (workspace / "alerts" / f"{case['slug']}.json").read_text(encoding="utf-8"))
    source = (workspace / "repo" / case["app_file"]).read_text(encoding="utf-8")
    replacements = case["good_replacements"] if accepted else case["bad_replacements"][0]["replacements"]
    fixed = replace_once(source, replacements)
    diff = "".join(difflib.unified_diff(source.splitlines(True), fixed.splitlines(True),
                                      fromfile="a/" + case["app_file"], tofile="b/" + case["app_file"]))
    # A saved 'verified' claim is deliberately insufficient for the partial repair.
    incident = Incident(alert=generated, state=IncidentState.verified,
                        final_patch=PatchProposal(diff=diff, explanation="Trusted scripted acceptance fixture"))
    incident_path = tmp_path / f"{candidate}-incident.json"
    incident_path.write_text(incident.model_dump_json(), encoding="utf-8")
    output = tmp_path / f"{candidate}-acceptance.json"
    code = main(["--workspace", str(workspace), "--scenario", case["slug"],
                 "--incident", str(incident_path), "--output", str(output), "--trusted-local-fixture"])
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["ok"] is accepted, result["reason"]
    assert code == (0 if accepted else 1)
    assert result["checks"]["target_reproduced"]
    assert result["checks"]["known_good_oracles_green"]
    assert result["runs"]["candidate"]["patch_applied"] is True
    assert json.loads(capsys.readouterr().out)["ok"] is accepted
