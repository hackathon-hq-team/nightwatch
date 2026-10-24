"""Unit records for saved-run analysis; these fixtures are not live API evidence."""
from copy import deepcopy
import json

import pytest

from tools.memory_pair_report import PAIR, analyze, main


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def saved_pair(tmp_path):
    alert = {"title": "Inventory replay", "exception_type": "InventoryError", "message": "Conflicting event",
             "commit": "a" * 40, "request": {"events": [{"id": "duplicate"}]}, "stacktrace": []}
    calls = [{"step": "patch", "model": "fixture-model", "prompt_tokens": 200, "completion_tokens": 100,
              "cost_usd": 0.003, "duration_s": 1.0},
             {"step": "patch:retry", "model": "fixture-model", "prompt_tokens": 250, "completion_tokens": 150,
              "cost_usd": 0.004, "duration_s": 2.0}]
    first = {"id": "inc-11111111", "alert": deepcopy(alert), "state": "verified", "error": "",
             "created_at": "2026-10-24T10:00:00Z", "updated_at": "2026-10-24T10:00:10Z",
             "usage": {"calls": deepcopy(calls)}, "similar_incidents": [], "evidence": [],
             "attempts": [{"n": 1, "error": "First candidate failed"}],
             "final_patch": {"diff": "fixture patch", "explanation": "Unit fixture only"}}
    repeat = deepcopy(first)
    repeat.update(id="inc-22222222", created_at="2026-10-24T10:01:00Z", updated_at="2026-10-24T10:01:05Z",
                  similar_incidents=[first["id"]], evidence=[{
                      "id": "E6", "kind": "memory", "source": first["id"] + " (similarity 1.0)",
                      "content": "Recorded prior incident", "truncated": False,
                  }], usage={"calls": [deepcopy(calls[0])]})
    summary = [
        {"scenario": PAIR[0], "incident": first["id"], "got": "verified", "model_calls": 2,
         "tokens": 700, "cost_usd": 0.007, "seconds": 10.0, "runner": "docker"},
        {"scenario": PAIR[1], "incident": repeat["id"], "got": "verified", "model_calls": 1,
         "tokens": 300, "cost_usd": 0.003, "seconds": 5.0, "runner": "docker"},
    ]
    write_json(tmp_path / "summary.json", summary)
    for item in (first, repeat):
        write_json(tmp_path / "history" / (item["id"] + ".json"), item)
    return tmp_path, summary, first, repeat


def save_pair(pair):
    run, summary, first, repeat = pair
    write_json(run / "summary.json", summary)
    for item in (first, repeat):
        write_json(run / "history" / (item["id"] + ".json"), item)


def acceptance(run, incident, scenario):
    path = run / (incident["id"] + "-acceptance.json")
    write_json(path, {"scenario": scenario, "incident": "C:\\original-machine\\history\\" + incident["id"] + ".json",
                      "ok": True, "status": "accepted", "runner": "docker", "checks": {"exact_test_set": True},
                      "runs": {"candidate": {"commit": incident["alert"]["commit"], "status": "passed",
                                             "exit_code": 0, "patch_applied": True,
                                             "passed": ["tests/test_oracle.py::test_answer"], "failures": []}}})
    return path


def test_counts_include_retry_and_failed_attempt_costs_without_claiming_live_improvement(saved_pair):
    result = analyze(saved_pair[0])
    assert result["before"] == {"model_calls": 2, "tokens": 700, "cost_usd": 0.007, "seconds": 10.0}
    assert result["after"] == {"model_calls": 1, "tokens": 300, "cost_usd": 0.003, "seconds": 5.0}
    assert result["memory_confirmed_in_records"] is True
    assert result["live_api_proven"] is False and result["scripted_fixture"] is True
    assert result["quality_confirmed_in_records"] is False and result["observed_reduction_percent"] is None


@pytest.mark.parametrize("metric,value", [("seconds", -1), ("seconds", float("inf")),
                                         ("seconds", float("nan")), ("tokens", True), ("model_calls", 3)])
def test_invalid_or_inconsistent_summary_metrics_are_rejected(saved_pair, metric, value):
    saved_pair[1][0][metric] = value
    save_pair(saved_pair)
    with pytest.raises(ValueError):
        analyze(saved_pair[0])


@pytest.mark.parametrize("change", ["commit", "request", "signature", "no-memory", "empty-memory", "no-similar-id", "warm-first", "time-order"])
def test_pair_identity_and_memory_evidence_are_required(saved_pair, change):
    _, _, first, repeat = saved_pair
    if change == "commit":
        repeat["alert"]["commit"] = "b" * 40
    elif change == "request":
        repeat["alert"]["request"] = {}
    elif change == "signature":
        repeat["alert"]["message"] = "Different failure"
    elif change == "no-memory":
        repeat["evidence"] = []
    elif change == "empty-memory":
        repeat["evidence"][0]["content"] = "  "
    elif change == "no-similar-id":
        repeat["similar_incidents"] = []
    elif change == "warm-first":
        first["similar_incidents"] = ["inc-33333333"]
    else:
        repeat["created_at"] = "2026-10-24T09:00:00Z"
    save_pair(saved_pair)
    with pytest.raises(ValueError):
        analyze(saved_pair[0])


def test_missing_history_and_summary_path_traversal_are_rejected(saved_pair):
    run, summary, _, repeat = saved_pair
    (run / "history" / (repeat["id"] + ".json")).unlink()
    with pytest.raises(FileNotFoundError):
        analyze(run)
    summary[0]["incident"] = "../outside"
    save_pair(saved_pair)
    with pytest.raises(ValueError, match="not a path"):
        analyze(run)


def test_independent_acceptance_required_before_percentage_comparison(saved_pair):
    run, _, first, repeat = saved_pair
    for item in (first, repeat):
        for call in item["usage"]["calls"]:
            call["model"] = "recorded-provider-model"
    save_pair(saved_pair)
    assert analyze(run)["quality_confirmed_in_records"] is False
    first_path = acceptance(run, first, PAIR[0])
    repeat_path = acceptance(run, repeat, PAIR[1])
    result = analyze(run, first_path, repeat_path)
    assert result["quality_confirmed_in_records"] is True
    assert result["observed_reduction_percent"]["model_calls"] == 50.0
    assert result["observed_reduction_percent"]["tokens"] == 57.14
    assert result["live_api_proven"] is False
    with pytest.raises(ValueError, match="another incident"):
        analyze(run, repeat_path, first_path)


@pytest.mark.parametrize("state", ["reported", "failed"])
def test_faster_unverified_repeat_is_not_quality_confirmed(saved_pair, state):
    _, summary, _, repeat = saved_pair
    repeat["state"] = state
    repeat["final_patch"] = None
    summary[1]["got"] = state
    save_pair(saved_pair)
    result = analyze(saved_pair[0])
    assert result["quality_confirmed_in_records"] is False
    assert result["observed_reduction_percent"] is None


def test_fake_model_never_becomes_live_proof_even_with_accepted_records(saved_pair):
    run, _, first, repeat = saved_pair
    for item in (first, repeat):
        for call in item["usage"]["calls"]:
            call["model"] = "fake"
    save_pair(saved_pair)
    result = analyze(run, acceptance(run, first, PAIR[0]), acceptance(run, repeat, PAIR[1]))
    assert result["scripted_fixture"] is True
    assert result["quality_confirmed_in_records"] is False
    assert result["observed_reduction_percent"] is None and result["live_api_proven"] is False


def test_cli_prints_read_only_and_never_overwrites_output(saved_pair, capsys):
    run = saved_pair[0]
    assert main(["--run", str(run)]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "saved-record analysis"
    output = run / "comparison.json"
    assert main(["--run", str(run), "--output", str(output)]) == 0
    capsys.readouterr()
    before = output.read_bytes()
    assert main(["--run", str(run), "--output", str(output)]) == 2
    assert output.read_bytes() == before
