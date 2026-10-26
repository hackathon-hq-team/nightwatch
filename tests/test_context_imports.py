"""Context includes modules the failing code imports (the producer of bad data is often one)."""
from __future__ import annotations

import subprocess

from conftest import ScriptedLLM

from nightwatch.config import Settings
from nightwatch.context import ContextBuilder, RepoView, local_imports
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.dev.reference_verifier import ReferenceVerifier
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import Alert, Frame, IncidentState

FILES = {"app/__init__.py", "app/payload.py", "app/util.py", "app/sub/__init__.py", "app/sub/helper.py",
         "app/consumer.py", "app/sub/inner.py"}


def test_local_imports_resolves_repository_modules_only():
    src = ("import csv\nimport app.util\nfrom fastapi import FastAPI\nfrom app.payload import build\n"
           "from app.sub import helper\nfrom .sub.inner import thing\n")
    assert local_imports(src, "app/consumer.py", FILES.__contains__) == [
        "app/util.py", "app/payload.py", "app/sub/__init__.py", "app/sub/helper.py", "app/sub/inner.py"]


def test_relative_parent_import_and_syntax_errors():
    assert local_imports("from .. import util\n", "app/sub/inner.py", FILES.__contains__) == \
        ["app/__init__.py", "app/util.py"]
    assert local_imports("def broken(:\n", "app/x.py", FILES.__contains__) == []


PRODUCER = '''def build_payload(rows):
    """CSV transport; SKUs may contain commas and must be quoted."""
    return {"content": "sku,quantity\\n" + "\\n".join(f"{r['sku']},{r['quantity']}" for r in rows)}
'''
CONSUMER = '''from app.payload import build_payload


class PayloadError(RuntimeError):
    pass


def read(rows):
    payload = build_payload(rows)
    for line in payload["content"].splitlines()[1:]:
        if line.count(",") != 1:
            raise PayloadError("unexpected CSV fields")
    return payload
'''


def _repo(tmp_path):
    repo = tmp_path / "r"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "payload.py").write_text(PRODUCER)
    (repo / "app" / "consumer.py").write_text(CONSUMER)
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    alert = Alert(title="PayloadError", exception_type="PayloadError", message="unexpected CSV fields", commit=head,
                  stacktrace=[Frame(file="app/consumer.py", function="read", line=12)])
    return repo, alert


def test_producer_file_is_in_the_context(tmp_path):
    repo, alert = _repo(tmp_path)
    builder = ContextBuilder(RepoView(repo))
    evidence, _ = builder.build(alert)
    files = [e for e in evidence if e.kind == "file"]
    assert files[0].source.startswith("app/consumer.py@")
    assert files[1].source.startswith("app/payload.py@") and "imported by app/consumer.py" in files[1].source
    assert "must be quoted" in files[1].content
    assert builder.notes == ["Related files imported by the failing code: app/payload.py"]


def test_patch_step_sees_the_producer(tmp_path):
    repo, alert = _repo(tmp_path)
    s = Settings()
    s.history_dir = tmp_path / "h"
    investigation = {"hypotheses": [{"summary": "producer does not quote SKUs", "confidence": 0.8}],
                     "repro_test": {"path": "tests/nightwatch/test_repro.py",
                                    "code": "from app.consumer import read\n\ndef test_r():\n"
                                            "    read([{'sku': 'a,b', 'quantity': 1}])\n",
                                    "expected": {"exception_type": "PayloadError",
                                                 "message_contains": "unexpected CSV fields",
                                                 "file": "app/consumer.py", "function": "read"}}}
    fix = {"edits": [{"file": "app/payload.py",
                      "old": "f\"{r['sku']},{r['quantity']}\"",
                      "new": "f\"{r['sku'].replace(',', ';')},{r['quantity']}\""}],
           "explanation": "escape commas in the producer"}
    llm = ScriptedLLM({"investigate": [investigation], "patch": [fix]})
    inc = Pipeline(llm, LocalRunner(repo), ReferenceVerifier(), RepoView(repo), HistoryStore(s.history_dir), s).run(alert)
    patch_prompt = llm.calls[1][1]
    assert "### app/payload.py" in patch_prompt  # full current content offered for editing
    assert inc.state == IncidentState.verified, inc.events
    assert "app/payload.py" in inc.final_patch.diff


def test_real_cross_file_scenario_shows_its_producer(tmp_path):
    """24 Oct live run: csv-producer-escaping failed because export_payload.py was never shown."""
    import json

    import pytest

    pytest.importorskip("fastapi")
    from tools.prepare_demo import prepare

    manifest = prepare(tmp_path / "ws", suite="diverse")
    alert = Alert.model_validate_json((tmp_path / "ws" / "alerts" / "csv-producer-escaping.json").read_text())
    evidence, _ = ContextBuilder(RepoView(manifest["repo"])).build(alert)
    sources = [e.source for e in evidence if e.kind == "file"]
    assert any(s.startswith("app/export_payload.py@") and "imported by" in s for s in sources), sources
    assert json.dumps(sources)  # serialisable for the incident history
