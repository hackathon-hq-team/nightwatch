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


ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"


def test_patch_escalates_to_stronger_model_after_two_failures(demo_repo, alert, tmp_path):
    noop = {"diff": FIX_DIFF.replace("return 0", "return 1").replace("+    if not items:\n", "+    if items is None:\n"),
            "explanation": "wrong guard"}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [noop, noop, PATCH]})
    pipe.s.escalation_model = ULTRA
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    patch_models = [m for (step, _), m in zip(llm.calls, llm.models, strict=True) if step == "patch"]
    assert patch_models == [None, None, ULTRA]
    assert any(e.message == f"Escalating patch attempt 3 to {ULTRA}" for e in inc.events)


def test_second_investigation_uses_stronger_model(demo_repo, alert, tmp_path):
    passing = {**INVESTIGATION, "repro_test": {**INVESTIGATION["repro_test"], "code": "def test_ok():\n    assert True\n"}}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [passing, INVESTIGATION], "patch": [PATCH]})
    pipe.s.escalation_model = ULTRA
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert llm.models[:2] == [None, ULTRA]


def test_escalation_can_be_disabled(demo_repo, alert, tmp_path):
    noop = {"diff": FIX_DIFF.replace("+    if not items:\n", "+    if items is None:\n"), "explanation": "x"}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [noop, noop, noop],
                                           "report": [REPORT]})
    pipe.s.escalation_model = ""
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert set(llm.models) == {None}
    assert not any(e.message.startswith("Escalating") for e in inc.events)


NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
TRIAGE = {"category": "code_bug", "likely_reproducible": True,
          "summary": "average() divides by len(items) with an empty list", "focus": ["E1", "app/calc.py"]}


def test_triage_is_off_by_default(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [PATCH]})
    pipe.s.triage_model = ""
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert "triage" not in [step for step, _ in llm.calls] and inc.triage is None


def test_triage_hint_goes_to_investigation(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"triage": [TRIAGE], "investigate": [INVESTIGATION], "patch": [PATCH]})
    pipe.s.triage_model = NANO
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert [step for step, _ in llm.calls][:2] == ["triage", "investigate"]
    assert llm.models[0] == NANO and llm.models[1] is None
    assert "a hint only" in llm.calls[1][1] and TRIAGE["summary"] in llm.calls[1][1]
    assert inc.triage.category == "code_bug"
    assert any(e.message.startswith(f"Triage ({NANO})") for e in inc.events)


def test_triage_failure_does_not_stop_incident(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"triage": [RuntimeError("bad json")], "investigate": [INVESTIGATION],
                                           "patch": [PATCH]})
    pipe.s.triage_model = NANO
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified and inc.triage is None
    assert any(e.message.startswith("Triage skipped") for e in inc.events)
    assert "a hint only" not in llm.calls[1][1]


def test_triage_budget_stop_ends_in_report(demo_repo, alert, tmp_path):
    pipe, _ = make(demo_repo, tmp_path, {"triage": [BudgetExceeded("incident budget")], "report": [REPORT]})
    pipe.s.triage_model = NANO
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert any(e.message.startswith("Stopped by budget") for e in inc.events)


def test_failed_reproduction_does_not_reject_a_hypothesis_the_model_keeps(demo_repo, alert, tmp_path):
    """24 Oct live run: the first test was rejected, the second confirmed the same cause."""
    passing = {**INVESTIGATION, "repro_test": {**INVESTIGATION["repro_test"], "code": "def test_ok():\n    assert True\n"}}
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [passing, INVESTIGATION], "patch": [PATCH]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified and inc.rejected_hypotheses == []


def test_hypothesis_the_model_abandons_is_recorded(demo_repo, alert, tmp_path):
    first = {**INVESTIGATION, "hypotheses": [{"summary": "cache returns stale data", "confidence": 0.4}],
             "repro_test": {**INVESTIGATION["repro_test"], "code": "def test_ok():\n    assert True\n"}}
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [first, INVESTIGATION], "patch": [PATCH]})
    inc = pipe.run(alert)
    assert inc.rejected_hypotheses == ["cache returns stale data"]
