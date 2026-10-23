import pytest

from evaluation.oracle import AVERAGE_CASES, expected_average
from evaluation.catalog import CASES
from nightwatch.dev.local_runner import LocalRunner
from verifier import Verifier
from nightwatch.schemas import Alert, ReproTest
from tools.prepare_demo import git, prepare


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    output = tmp_path_factory.mktemp("demo-parent") / "workspace"
    return output, prepare(output)


def load_alert(output, name):
    return Alert.model_validate_json((output / "alerts" / f"{name}.json").read_text(encoding="utf-8"))


def reproducer(alert):
    return ReproTest(path="tests/nightwatch/test_empty.py", code=(
        "from app.calc import average\n\ndef test_empty():\n    assert average([]) == 0.0\n"
    ), expected=alert.signature())


def test_fixed_answers_are_independent():
    for values, answer in AVERAGE_CASES:
        assert expected_average(list(values)) == answer
    with pytest.raises(ValueError):
        expected_average([999])


def test_preparation_is_clean_and_refuses_overwrite(prepared):
    output, manifest = prepared
    repo = output / "repo"
    assert git(repo, "status", "--porcelain") == ""
    assert manifest["mode"] == "fixture"
    assert len(manifest["broken_commit"]) == 40
    assert git(repo, "rev-parse", "HEAD~1") == manifest["good_commit"]
    assert not (repo / "evaluation").exists()
    assert not (repo / "tools").exists()
    with pytest.raises(FileExistsError):
        prepare(output)


def test_real_failure_good_version_and_frozen_acceptance(prepared):
    output, manifest = prepared
    repo = output / "repo"
    runner = LocalRunner(repo)
    alert = load_alert(output, "empty-sample")
    test = reproducer(alert)
    files = {test.path: test.code}
    baseline = runner.run(alert.commit, {})
    assert baseline.status == "passed", baseline.output_tail
    assert len(baseline.passed) == 4 + sum(case["baseline_count"] for case in CASES)
    broken = runner.run(alert.commit, files, select=[test.path])
    good = runner.run(alert.previous_good_commit, files, select=[test.path])
    assert Verifier().check_reproduction(alert, test, broken, good).ok
    assert broken.failures[0].function == "average"
    assert broken.failures[0].file == "app/calc.py"
    assert "ZeroDivisionError" in alert.logs

    # Prepared patches validate the mechanism; never call them model-generated fixes.
    patch = git(repo, "diff", alert.commit, alert.previous_good_commit, "--", "app/calc.py") + "\n"
    # All recorded cases are frozen in a separate acceptance test before either patch.
    acceptance = "from app.calc import average\n\ndef test_recorded_answers():\n"
    for values, expected in AVERAGE_CASES:
        acceptance += f"    assert average({list(values)!r}) == {expected!r}\n"
    files["tests/nightwatch/test_reference.py"] = acceptance
    frozen = runner.tests_hash(alert.commit, files)
    after = runner.run(alert.commit, files, patch=patch)
    assert Verifier().check_patch(alert, test, after, frozen).ok, after.output_tail
    assert len(after.passed) == 6 + sum(case["baseline_count"] for case in CASES)

    # 'Always zero' fixes the empty case but must fail the non-empty answers.
    bad_source = (repo / "app/calc.py").read_text(encoding="utf-8").replace(
        "    return sum(items) / len(items)", "    return 0.0")
    original = (repo / "app/calc.py").read_text(encoding="utf-8")
    import difflib
    bad_patch = "".join(difflib.unified_diff(original.splitlines(True), bad_source.splitlines(True),
                                         fromfile="a/app/calc.py", tofile="b/app/calc.py"))
    bad = runner.run(alert.commit, files, patch=bad_patch)
    assert bad.patch_applied is True
    assert not Verifier().check_patch(alert, test, bad, frozen).ok
    assert bad.tests_hash == frozen
    assert git(repo, "rev-parse", "HEAD") == manifest["broken_commit"]
    assert git(repo, "status", "--porcelain") == ""


def test_different_bug_cannot_confirm_insufficient_evidence(prepared):
    output, _ = prepared
    alert = load_alert(output, "insufficient-evidence")
    assert alert.previous_good_commit is None
    assert "SYNTHETIC" in alert.logs
    test = reproducer(alert)
    broken = LocalRunner(output / "repo").run(alert.commit, {test.path: test.code}, select=[test.path])
    assert not Verifier().check_reproduction(alert, test, broken, None).ok


def test_no_evidence_ends_in_report_without_patch(prepared, tmp_path):
    from conftest import ScriptedLLM
    from nightwatch.config import Settings
    from nightwatch.context import RepoView
    from nightwatch.history import HistoryStore
    from nightwatch.pipeline import Pipeline

    output, _ = prepared
    llm = ScriptedLLM({"investigate": [{
        "hypotheses": [{"summary": "Memory pressure needs production evidence", "confidence": 0.1}],
        "repro_test": None, "not_reproducible_reason": "Missing input, dataset and load profile",
    }], "report": [{"facts": ["No reproducible input supplied"],
                     "hypotheses": [{"summary": "Memory pressure", "confidence": 0.1}],
                     "next_steps": ["Collect stack trace, input and load profile"]}]})
    pipeline = Pipeline(llm, LocalRunner(output / "repo"), Verifier(),
                        RepoView(output / "repo"), HistoryStore(tmp_path), Settings())
    incident = pipeline.run(load_alert(output, "insufficient-evidence"))
    assert incident.state.value == "reported"
    assert not incident.attempts and incident.final_patch is None
    assert [step for step, _ in llm.calls] == ["investigate", "report"]
