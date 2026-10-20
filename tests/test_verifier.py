import pytest

from nightwatch.schemas import Alert, ErrorSignature, Frame, ReproTest, RunResult, TestFailure
from verifier import Verifier


@pytest.fixture
def evidence():
    alert = Alert(title="empty input", exception_type="ZeroDivisionError", message="division by zero",
                  commit="broken", stacktrace=[Frame(file="app/stats.py", function="average")])
    test = ReproTest(path="tests/nightwatch/test_repro.py", code="", expected=ErrorSignature(
        exception_type="ZeroDivisionError"))
    failure = TestFailure(nodeid=test.path + "::test_empty", exception_type="ZeroDivisionError",
                          message="division by zero", file="app/stats.py", function="average")
    broken = RunResult(commit="broken", status="failed", exit_code=1, failures=[failure])
    after = RunResult(commit="broken", status="passed", exit_code=0, passed=[failure.nodeid],
                      patch_applied=True, tests_hash="frozen")
    return alert, test, broken, after


def test_valid_reproduction_and_patch(evidence):
    alert, test, broken, after = evidence
    assert Verifier().check_reproduction(alert, test, broken, None).ok
    assert Verifier().check_patch(alert, test, after, "frozen").ok


@pytest.mark.parametrize("field,value", [
    ("exception_type", "ImportError"), ("message", ""), ("message", "division"),
    ("file", "app/other.py"), ("function", "other"),
    ("nodeid", "tests/nightwatch/test_repro.py_backup::test_empty"),
    ("nodeid", "tests/nightwatch/test_repro.py::test_empty [setup]"),
])
def test_wrong_failure_is_rejected(evidence, field, value):
    alert, test, broken, _ = evidence
    setattr(broken.failures[0], field, value)
    assert not Verifier().check_reproduction(alert, test, broken, None).ok


@pytest.mark.parametrize("status,code", [("passed", 0), ("error", 2), ("timeout", 124), ("failed", 2)])
def test_execution_failure_is_not_reproduction(evidence, status, code):
    alert, test, broken, _ = evidence
    broken.status, broken.exit_code = status, code
    assert not Verifier().check_reproduction(alert, test, broken, None).ok


def test_known_good_requires_actual_reproducer_and_right_commit(evidence):
    alert, test, broken, after = evidence
    alert.previous_good_commit = "good"
    verifier = Verifier()
    assert not verifier.check_reproduction(alert, test, broken, None).ok
    good = after.model_copy(update={"commit": "good", "passed": []})
    assert not verifier.check_reproduction(alert, test, broken, good).ok
    good.passed = after.passed
    assert verifier.check_reproduction(alert, test, broken, good).ok
    good.commit = "wrong"
    assert not verifier.check_reproduction(alert, test, broken, good).ok


@pytest.mark.parametrize("changes", [
    {"patch_applied": None}, {"patch_applied": False}, {"tests_hash": "changed"},
    {"passed": []}, {"passed": ["tests/nightwatch/test_repro.py_other::test_empty"]},
    {"exit_code": 1}, {"status": "error"}, {"status": "timeout"}, {"commit": "wrong"},
])
def test_invalid_patch_results_are_rejected(evidence, changes):
    alert, test, _, after = evidence
    assert not Verifier().check_patch(alert, test, after.model_copy(update=changes), "frozen").ok


def test_inconsistent_success_and_missing_hash_are_rejected(evidence):
    alert, test, broken, after = evidence
    after.failures = broken.failures
    assert not Verifier().check_patch(alert, test, after, "frozen").ok
    after.failures = []
    assert not Verifier().check_patch(alert, test, after, "").ok


def test_team_verifier_with_real_pytest_pipeline(demo_repo, alert, tmp_path):
    from test_pipeline import INVESTIGATION, PATCH, make
    from nightwatch.schemas import IncidentState

    pipeline, _ = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [PATCH]})
    pipeline.verifier = Verifier()
    incident = pipeline.run(alert)
    assert incident.state == IncidentState.verified, incident.events
    assert incident.repro_verdict.ok
    assert incident.attempts[0].verdict.ok
