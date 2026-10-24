"""Runner choice in scripts/run_demo.py (no Docker needed)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightwatch.config import Settings
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.docker_runner import Runner as DockerRunner
from nightwatch.schemas import Alert, Incident, IncidentState

_spec = importlib.util.spec_from_file_location("run_demo_runner", Path(__file__).parents[1] / "scripts" / "run_demo.py")
run_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_demo)

ALERT = Alert(source="scenario", title="t", exception_type="ZeroDivisionError", message="division by zero",
              commit="HEAD")


def test_make_runner(tmp_path):
    assert isinstance(run_demo.make_runner("local", tmp_path), LocalRunner)
    assert isinstance(run_demo.make_runner("docker", tmp_path), DockerRunner)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    runners = []
    monkeypatch.setenv("NIGHTWATCH_DEBUG_DIR", str(tmp_path / "debug"))
    monkeypatch.setattr(run_demo, "Settings", lambda: Settings(api_key="x", history_dir=tmp_path / "h"))
    monkeypatch.setattr(run_demo, "ModelClient", lambda s: SimpleNamespace(s=s))
    monkeypatch.setattr(run_demo, "check_model", lambda client: True)
    monkeypatch.setattr(run_demo, "load_verifier", lambda: (None, "stub"))

    def prepare(output, suite="standard"):
        alerts = output / "alerts"
        alerts.mkdir(parents=True)
        (alerts / "new-bug.json").write_text(json.dumps({**json.loads(ALERT.model_dump_json()),
                                                         "expected": "verified"}), encoding="utf-8")
        return {"repo": str(output / "repo"), "alerts": str(alerts)}

    def pipeline(client, runner, verifier, repo, history, settings):
        runners.append(runner)
        return SimpleNamespace(run=lambda alert: Incident(alert=alert, state=IncidentState.verified))

    monkeypatch.setattr(run_demo, "prepare", prepare)
    monkeypatch.setattr(run_demo, "Pipeline", pipeline)
    return runners


def run(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(sys, "argv", ["run_demo", "--output", str(tmp_path / "out"), *args])
    return run_demo.main()


def test_local_is_default_and_recorded(monkeypatch, tmp_path, wired):
    monkeypatch.setattr(run_demo, "docker_readiness", lambda: pytest.fail("Docker must not be checked"))
    assert run(monkeypatch, tmp_path) == 0
    assert isinstance(wired[0], LocalRunner)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary[0]["runner"] == "local" and summary[0]["ok"] is True


def test_diverse_suite_is_forwarded_without_changing_model_settings(monkeypatch, tmp_path, wired):
    from tools import prepare_demo

    monkeypatch.setattr(prepare_demo, "require_diverse_dependencies", lambda: None)
    original = run_demo.prepare
    selected = []

    def prepare(output, suite="standard"):
        selected.append(suite)
        return original(output, suite=suite)

    monkeypatch.setattr(run_demo, "prepare", prepare)
    assert run(monkeypatch, tmp_path, "--suite", "diverse") == 0
    assert selected == ["diverse"]


def test_memory_pair_runs_in_order_with_shared_history(monkeypatch, tmp_path, wired):
    from tools import prepare_demo
    from evaluation.diverse_catalog import REPEAT_OF, REPEAT_SLUG

    monkeypatch.setattr(prepare_demo, "require_diverse_dependencies", lambda: None)
    calls, stores = [], []

    def prepare(output, suite="standard"):
        assert suite == "diverse"
        alerts = output / "alerts"
        alerts.mkdir(parents=True)
        for name in (REPEAT_SLUG, REPEAT_OF):
            data = {**json.loads(ALERT.model_dump_json()), "title": name, "expected": "verified"}
            (alerts / f"{name}.json").write_text(json.dumps(data))
        return {"repo": str(output / "repo"), "alerts": str(alerts)}

    def pipeline(client, runner, verifier, repo, history, settings):
        stores.append(history)

        def run_alert(alert):
            calls.append(alert.title)
            return Incident(alert=alert, state=IncidentState.verified)

        return SimpleNamespace(run=run_alert)

    monkeypatch.setattr(run_demo, "prepare", prepare)
    monkeypatch.setattr(run_demo, "Pipeline", pipeline)
    assert run(monkeypatch, tmp_path, "--suite", "diverse", "--memory-pair") == 0
    assert calls == [REPEAT_OF, REPEAT_SLUG] and len(stores) == 1


def test_repeat_alone_is_rejected_before_any_provider_call(monkeypatch, tmp_path):
    from tools import prepare_demo
    from evaluation.diverse_catalog import REPEAT_SLUG

    monkeypatch.setattr(prepare_demo, "require_diverse_dependencies", lambda: None)
    monkeypatch.setattr(run_demo, "ModelClient", lambda *args: pytest.fail("Provider must not be called"))
    with pytest.raises(SystemExit) as error:
        run(monkeypatch, tmp_path, "--suite", "diverse", "--scenario", REPEAT_SLUG)
    assert error.value.code == 2


def test_docker_runner_used_when_ready(monkeypatch, tmp_path, wired):
    monkeypatch.setattr(run_demo, "docker_readiness", lambda: (True, "Docker sandbox ready"))
    assert run(monkeypatch, tmp_path, "--runner", "docker") == 0
    assert isinstance(wired[0], DockerRunner)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary[0]["runner"] == "docker"


def test_docker_not_ready_stops_before_any_run(monkeypatch, tmp_path, wired, capsys):
    monkeypatch.setattr(run_demo, "docker_readiness", lambda: (False, "Build the runner image"))
    assert run(monkeypatch, tmp_path, "--runner", "docker") == 2
    assert wired == [] and "Build the runner image" in capsys.readouterr().out


def test_check_only_also_checks_docker(monkeypatch, tmp_path, wired):
    monkeypatch.setattr(run_demo, "docker_readiness", lambda: (False, "Docker Engine is unavailable"))
    assert run(monkeypatch, tmp_path, "--runner", "docker", "--check-only") == 2


def test_web_search_without_key_stops(monkeypatch, tmp_path, wired, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert run(monkeypatch, tmp_path, "--web-search") == 2
    assert wired == [] and "TAVILY_API_KEY is not set" in capsys.readouterr().out


def test_web_searches_counted_in_summary(monkeypatch, tmp_path, wired):
    from nightwatch.schemas import Event

    def pipeline(client, runner, verifier, repo, history, settings):
        def run_alert(alert):
            inc = Incident(alert=alert, state=IncidentState.verified)
            inc.events.append(Event(state=IncidentState.investigating, message='Web search (x): "q" — 2 results'))
            return inc
        return SimpleNamespace(run=run_alert)

    monkeypatch.setattr(run_demo, "Pipeline", pipeline)
    monkeypatch.setattr(run_demo, "Settings", lambda: Settings(api_key="x", history_dir=tmp_path / "h",
                                                               tavily_api_key="tvly-x"))
    assert run(monkeypatch, tmp_path, "--web-search") == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary[0]["web_searches"] == 1
