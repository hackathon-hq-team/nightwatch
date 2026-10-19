"""End-to-end pipeline tests: real pytest on a demo repository, scripted LLM."""
from __future__ import annotations

from conftest import FIX_DIFF, REPRO_TEST, ScriptedLLM

from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.dev.reference_verifier import ReferenceVerifier
from nightwatch.history import HistoryStore
from nightwatch.model_client import BudgetExceeded
from nightwatch.pipeline import Pipeline, check_diff
from nightwatch.schemas import IncidentState

INVESTIGATION = {
    "hypotheses": [{"summary": "average() lost the empty-list guard in the last commit",
                    "evidence_ids": ["E1", "E4"], "confidence": 0.9, "would_refute": "test passes on broken"}],
    "repro_test": {"path": "tests/nightwatch/test_repro_average.py", "code": REPRO_TEST,
                   "expected": {"exception_type": "ZeroDivisionError", "message_contains": "division by zero",
                                "file": "app/calc.py", "function": "average"}},
}
PATCH = {"diff": FIX_DIFF, "explanation": "restore empty-list guard", "evidence_ids": ["E4"]}
REPORT = {"facts": ["x"], "hypotheses": INVESTIGATION["hypotheses"], "next_steps": ["y"]}


def make(demo_repo, tmp_path, script):
    repo, _, _ = demo_repo
    s = Settings()
    s.history_dir = tmp_path / "history"
    llm = ScriptedLLM(script)
    pipe = Pipeline(llm, LocalRunner(repo), ReferenceVerifier(), RepoView(repo), HistoryStore(s.history_dir), s)
    return pipe, llm


def test_happy_path_verified(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [PATCH]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified, inc.events
    assert inc.repro_verdict.ok and inc.repro_verdict.checks["passes_on_good"]
    assert inc.final_patch.diff == FIX_DIFF
    assert len(inc.attempts) == 1 and inc.attempts[0].verdict.checks["suite_green"]
    # context: stack trace, request, diff, file
    kinds = [e.kind for e in inc.evidence]
    assert kinds[:2] == ["stacktrace", "request"] and "diff" in kinds and "file" in kinds
    # history saved
    assert pipe.history.load(inc.id).state == IncidentState.verified


def test_repro_that_does_not_fail_goes_to_report(demo_repo, alert, tmp_path):
    passing = {**INVESTIGATION, "repro_test": {**INVESTIGATION["repro_test"],
                                               "code": "def test_ok():\n    assert True\n"}}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [passing, passing], "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert not inc.attempts  # no patch was even requested
    assert [c[0] for c in llm.calls] == ["investigate", "investigate", "report"]
    assert "did not fail" in llm.calls[1][1]  # the rejection reason reaches the second attempt


def test_wrong_exception_is_not_a_reproduction(demo_repo, alert, tmp_path):
    wrong = {**INVESTIGATION, "repro_test": {**INVESTIGATION["repro_test"], "code":
             "from app.calc import average\n\ndef test_x():\n    average(None)\n"}}  # TypeError, not ZeroDivisionError
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [wrong, wrong], "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert inc.repro_verdict.checks["same_exception"] is False


def test_patch_touching_tests_is_rejected_then_fixed(demo_repo, alert, tmp_path):
    cheat = {"diff": "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1 +1 @@\n-x\n+y\n",
             "explanation": "cheat"}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [cheat, PATCH]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert inc.attempts[0].verdict.checks == {"diff_guard": False}
    assert inc.attempts[0].run is None  # never reached the runner
    assert "modifies tests" in llm.calls[-1][1]  # the reason reaches the next attempt


def test_patch_that_does_not_fix_is_retried_then_reported(demo_repo, alert, tmp_path):
    noop = {"diff": FIX_DIFF.replace("return 0", "return 1").replace("+    if not items:\n", "+    if items is None:\n"),
            "explanation": "wrong guard"}
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [noop, noop, noop],
                                         "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert len(inc.attempts) == 3
    assert all(not a.verdict.ok for a in inc.attempts)


def test_memory_finds_previous_verified_incident(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION, INVESTIGATION], "patch": [PATCH, PATCH]})
    first = pipe.run(alert)
    second = pipe.run(alert.model_copy(update={"id": "alert-2"}))
    assert second.similar_incidents == [first.id]
    memory = [e for e in second.evidence if e.kind == "memory"]
    assert memory and "Verified fix" in memory[0].content


def test_budget_exceeded_ends_in_report(demo_repo, alert, tmp_path):
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [BudgetExceeded("limit")], "report": [BudgetExceeded("x")]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert "budget" in inc.events[-1].message


def test_check_diff():
    assert check_diff(FIX_DIFF, 200) is None
    assert "tests" in check_diff("--- a/tests/x.py\n+++ b/tests/x.py\n@@ -1 +1 @@\n-a\n+b\n", 200)
    assert check_diff("nonsense", 200)
    big = "--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n" + "+a\n" * 300
    assert "too large" in check_diff(big, 200)
