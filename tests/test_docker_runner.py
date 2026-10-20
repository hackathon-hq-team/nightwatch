"""Snapshot guards offline; real container checks opt in via NIGHTWATCH_TEST_DOCKER=1."""
import io
import os
import subprocess
import tarfile

import pytest

from conftest import FIX_DIFF, REPRO_TEST, ScriptedLLM
from nightwatch.config import Settings
from nightwatch.context import RepoView
from nightwatch.docker_runner import Runner, pack, safe_path, tests_digest as digest_tests, unpack
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import ReproTest
from verifier import Verifier

FILES = {"tests/nightwatch/test_repro.py": REPRO_TEST}


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "tests/../../escape", "C:/escape", "a\\b", ".git/config"])
def test_snapshot_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_archive_rejects_symlink():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        link = tarfile.TarInfo("tests/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    with pytest.raises(ValueError, match="Links"):
        unpack(stream.getvalue())


def test_archive_filters_credentials():
    files = {".env": b"secret", ".env.production": b"secret", ".aws/credentials": b"secret",
             "private.pem": b"secret", ".env.example": b"KEY=", "app/calc.py": b"pass"}
    assert unpack(pack(files), filter_secrets=True) == {
        ".env.example": b"KEY=", "app/calc.py": b"pass"}


def test_snapshot_uses_commit_and_freezes_tests(demo_repo):
    repo, good, broken = demo_repo
    runner = Runner(repo)
    before = runner.tests_hash(broken, FILES)
    (repo / "app/calc.py").write_text("uncommitted host change")
    assert runner.tests_hash(broken, FILES) == before
    assert b"uncommitted" not in runner._snapshot(broken, FILES)["app/calc.py"]
    assert digest_tests({"tests/a.py": b"old"}) != digest_tests({"tests/a.py": b"new"})
    with pytest.raises(ValueError, match="under tests/nightwatch"):
        runner.tests_hash(good, {"tests/test_calc.py": "pass"})


def test_unavailable_docker_returns_error_and_does_not_execute_tests(demo_repo, monkeypatch):
    repo, _, broken = demo_repo
    original = Runner._command
    def unavailable(args, **kwargs):
        if args[0] == "docker":
            raise FileNotFoundError("fixture: Docker unavailable")
        return original(args, **kwargs)
    monkeypatch.setattr(Runner, "_command", staticmethod(unavailable))
    result = Runner(repo).run(broken, FILES)
    assert result.status == "error" and "Docker unavailable" in result.output_tail


def test_timeout_keeps_isolation_flags_and_removes_sandbox(demo_repo, monkeypatch):
    repo, _, broken = demo_repo
    original = Runner._command
    calls = []
    def command(args, **kwargs):
        if args[0] != "docker":
            return original(args, **kwargs)
        calls.append(args)
        if args[1] == "exec" and "pytest" in args:
            raise subprocess.TimeoutExpired(args, 1)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(Runner, "_command", staticmethod(command))
    assert Runner(repo, timeout_s=1).run(broken, FILES).status == "timeout"
    create = next(args for args in calls if args[1] == "create")
    assert {"--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=10001:10001", "--pids-limit=128", "--memory=512m",
            "--memory-swap=512m", "--cpus=1.0", "--pull=never"}.issubset(create)
    assert not any(arg in ("-v", "--volume", "--mount", "--privileged", "--env-file") for arg in create)
    assert calls[-1][1:3] == ["rm", "-f"]


def test_create_timeout_also_cleans_up(demo_repo, monkeypatch):
    repo, _, broken = demo_repo
    original = Runner._command
    calls = []
    def command(args, **kwargs):
        if args[0] != "docker":
            return original(args, **kwargs)
        calls.append(args)
        if args[1] == "create":
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(Runner, "_command", staticmethod(command))
    assert Runner(repo).run(broken, FILES).status == "timeout"
    assert calls[-1][1:3] == ["rm", "-f"]


docker = pytest.mark.skipif(os.getenv("NIGHTWATCH_TEST_DOCKER") != "1",
                            reason="Set NIGHTWATCH_TEST_DOCKER=1 with Docker Engine and runner image ready")


@docker
def test_container_reproduction_and_verified_patch(demo_repo, alert):
    repo, good, broken = demo_repo
    runner = Runner(repo)
    test = ReproTest(path=next(iter(FILES)), code=REPRO_TEST, expected=alert.signature())
    bad = runner.run(broken, FILES, select=[test.path])
    healthy = runner.run(good, FILES, select=[test.path])
    assert Verifier().check_reproduction(alert, test, bad, healthy).ok, (bad, healthy)
    baseline = runner.tests_hash(broken, FILES)
    fixed = runner.run(broken, FILES, patch=FIX_DIFF)
    assert Verifier().check_patch(alert, test, fixed, baseline).ok, fixed


@docker
def test_container_isolation(demo_repo, monkeypatch):
    repo, good, _ = demo_repo
    monkeypatch.setenv("NEBIUS_API_KEY", "host-only-canary")
    code = '''import os, socket
from pathlib import Path
def test_sandbox():
    assert os.getuid() == 10001
    assert 'NEBIUS_API_KEY' not in os.environ
    assert not Path('/var/run/docker.sock').exists()
    assert not Path('/workspace/.git').exists()
    try:
        Path('/opt/nightwatch/tamper').write_text('bad')
    except OSError:
        pass
    else:
        assert False, 'root filesystem must be read-only'
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(('1.1.1.1', 443)) != 0
'''
    outcome = Runner(repo).run(good, {"tests/nightwatch/test_isolation.py": code})
    assert outcome.status == "passed", outcome


@docker
def test_timeout_removes_container(demo_repo):
    repo, good, _ = demo_repo
    code = "import time\ndef test_wait():\n    time.sleep(60)\n"
    result = Runner(repo, timeout_s=2).run(good, {"tests/nightwatch/test_wait.py": code})
    assert result.status == "timeout", result
    # Every test run removes its sandbox; no live container may keep this process.
    remaining = subprocess.run(["docker", "ps", "-aq", "--filter", "name=nightwatch-"],
                               capture_output=True, text=True, check=True)
    assert not remaining.stdout.strip()


@docker
def test_invalid_patch_is_error(demo_repo):
    repo, _, broken = demo_repo
    result = Runner(repo).run(broken, FILES, patch="not a diff")
    assert result.status == "error" and result.patch_applied is False


@docker
@pytest.mark.parametrize("outcome", ["verified", "reported"])
def test_pipeline_with_docker_and_team_verifier(demo_repo, alert, tmp_path, outcome):
    repo, _, _ = demo_repo
    hypothesis = {"summary": "Prepared fixture hypothesis", "confidence": 0.2, "evidence_ids": ["E1"]}
    if outcome == "verified":
        investigation = {"hypotheses": [hypothesis], "repro_test": {
            "path": next(iter(FILES)), "code": REPRO_TEST, "expected": alert.signature().model_dump()}}
        script = {"investigate": [investigation], "patch": [{"diff": FIX_DIFF, "explanation": "Restore guard"}]}
    else:
        alert = alert.model_copy(update={"exception_type": "MemoryError", "message": "allocation failed under load",
                                         "stacktrace": [], "request": None, "previous_good_commit": None})
        script = {"investigate": [{"hypotheses": [hypothesis], "repro_test": None,
                                   "not_reproducible_reason": "Missing load and input evidence"}],
                  "report": [{"facts": ["No stack trace [E1]"], "hypotheses": [hypothesis],
                              "next_steps": ["Collect input and load profile"]}]}
    llm = ScriptedLLM(script)
    history = HistoryStore(tmp_path / "history")
    incident = Pipeline(llm, Runner(repo), Verifier(), RepoView(repo), history, Settings()).run(alert)
    assert incident.state.value == outcome, (incident.error, incident.events)
    assert history.load(incident.id).state.value == outcome
    if outcome == "reported":
        assert not incident.attempts and incident.final_patch is None
        assert [step for step, _ in llm.calls] == ["investigate", "report"]
