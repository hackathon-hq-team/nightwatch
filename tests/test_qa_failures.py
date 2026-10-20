"""Failure acceptance checks with real pytest and the team's demo and verifier.

All executable test inputs below are trusted, fixed fixtures, not runtime model output.
"""
import difflib

import pytest

from conftest import ScriptedLLM
from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from test_demo_scenarios import load_alert, prepared, reproducer
from tools.prepare_demo import EMPTY_GUARD, RETURN_LINE
from verifier import Verifier


def patch_for(path, before, after):
    # Explicit file boundaries are required when git apply --recount reads multiple hunks/files.
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                             fromfile="a/" + path, tofile="b/" + path))


@pytest.mark.parametrize("code", [
    "from missing_nightwatch_demo_module import average\n",
    "def test_invalid(:\n    pass\n",
    "def setup_module():\n    raise ZeroDivisionError('division by zero')\n"
    "\ndef test_empty():\n    assert True\n",
    "def test_unrelated_success():\n    assert True\n",
    "import pytest\npytest.skip('fixture: all tests skipped', allow_module_level=True)\n",
])
def test_broken_reproducer_is_not_confirmation(prepared, code):
    output, _ = prepared
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert).model_copy(update={"code": code})
    result = LocalRunner(output / "repo").run(alert.commit, {test.path: code}, select=[test.path])
    # Missing previous-good evidence must not hide an incorrect broken-version result.
    verdict = Verifier().check_reproduction(alert, test, result, None)
    assert not verdict.ok
    assert not verdict.checks["fails_on_broken"]


def test_real_timeout_is_not_confirmation(prepared):
    output, _ = prepared
    alert = load_alert(output, "empty-sample")
    code = "import time\n\ndef test_wait():\n    time.sleep(10)\n"
    test = reproducer(alert).model_copy(update={"code": code})
    result = LocalRunner(output / "repo", timeout_s=1).run(
        alert.commit, {test.path: code}, select=[test.path])
    assert result.status == "timeout"
    assert not Verifier().check_reproduction(alert, test, result, None).ok


def test_changed_tests_rejected_even_when_every_test_passes(prepared):
    output, _ = prepared
    repo = output / "repo"
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert)
    files = {test.path: test.code}
    runner = LocalRunner(repo)
    frozen = runner.tests_hash(alert.commit, files)
    source = (repo / "app/calc.py").read_text(encoding="utf-8")
    good_patch = patch_for("app/calc.py", source, source.replace(RETURN_LINE, EMPTY_GUARD + RETURN_LINE))
    before = (repo / "tests/test_calc.py").read_text(encoding="utf-8")
    tamper = patch_for("tests/test_calc.py", before, before + "\n# Unauthorized test modification.\n")
    # Bypass the pipeline guard deliberately to test the verifier's independent hash check.
    after = runner.run(alert.commit, files, patch=good_patch + tamper)
    assert after.patch_applied is True, after.output_tail
    assert after.status == "passed", after.output_tail
    verdict = Verifier().check_patch(alert, test, after, frozen)
    assert not verdict.ok
    assert verdict.checks["suite_green"]
    assert not verdict.checks["tests_unchanged"]


def test_patch_with_syntax_error_is_rejected(prepared):
    output, _ = prepared
    repo = output / "repo"
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert)
    runner = LocalRunner(repo)
    files = {test.path: test.code}
    frozen = runner.tests_hash(alert.commit, files)
    before = (repo / "app/calc.py").read_text(encoding="utf-8")
    patch = patch_for("app/calc.py", before, before + "\ndef invalid(:\n")
    after = runner.run(alert.commit, files, patch=patch)
    assert after.patch_applied is True
    assert after.status == "error"
    assert not Verifier().check_patch(alert, test, after, frozen).ok


def test_patch_that_cannot_apply_is_rejected(prepared):
    output, _ = prepared
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert)
    runner = LocalRunner(output / "repo")
    files = {test.path: test.code}
    frozen = runner.tests_hash(alert.commit, files)
    patch = patch_for("app/missing.py", "old value\n", "new value\n")
    after = runner.run(alert.commit, files, patch=patch)
    assert after.status == "error"
    assert after.patch_applied is False
    assert not Verifier().check_patch(alert, test, after, frozen).ok


def test_pipeline_verifies_generated_demo_with_team_verifier(prepared, tmp_path):
    output, _ = prepared
    repo = output / "repo"
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert)
    source = (repo / "app/calc.py").read_text(encoding="utf-8")
    patch = patch_for("app/calc.py", source, source.replace(RETURN_LINE, EMPTY_GUARD + RETURN_LINE))
    llm = ScriptedLLM({
        "investigate": [{"hypotheses": [{"summary": "Empty guard is missing", "confidence": 0.9}],
                         "repro_test": test.model_dump()}],
        "patch": [{"diff": patch, "explanation": "Prepared fixture: restore the empty guard"}],
    })
    history = HistoryStore(tmp_path)
    pipeline = Pipeline(llm, LocalRunner(repo), Verifier(), RepoView(repo), history, Settings())
    incident = pipeline.run(alert)
    assert incident.state.value == "verified", incident.error
    assert incident.repro_verdict.ok
    assert incident.attempts[0].verdict.ok
    assert history.load(incident.id).state.value == "verified"
    assert [step for step, _ in llm.calls] == ["investigate", "patch"]


@pytest.mark.parametrize("code,timeout", [
    ("from missing_nightwatch_demo_module import average\n", 30),
    ("import time\n\ndef test_wait():\n    time.sleep(10)\n", 1),
])
def test_pipeline_never_requests_patch_after_runner_failure(prepared, tmp_path, code, timeout):
    output, _ = prepared
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert).model_copy(update={"code": code})
    llm = ScriptedLLM({
        "investigate": [{"hypotheses": [{"summary": "Fixed failure fixture", "confidence": 0.5}],
                         "repro_test": test.model_dump()}],
        "report": [{"facts": ["No confirmed reproduction"],
                    "hypotheses": [{"summary": "More evidence required", "confidence": 0.1}],
                    "next_steps": ["Inspect the runner failure before requesting a patch"]}],
    })
    settings = Settings(max_repro_attempts=1)
    pipeline = Pipeline(llm, LocalRunner(output / "repo", timeout_s=timeout), Verifier(),
                        RepoView(output / "repo"), HistoryStore(tmp_path), settings)
    incident = pipeline.run(alert)
    assert incident.state.value == "reported", incident.error
    assert not incident.repro_verdict.ok
    assert incident.final_patch is None and not incident.attempts
    assert [step for step, _ in llm.calls] == ["investigate", "report"]
