"""Web search for known library issues: when it runs, what leaves the service, how results are used."""
from __future__ import annotations

import json
import subprocess

import pytest
from conftest import ScriptedLLM

from nightwatch import web_search
from nightwatch.config import Settings
from nightwatch.context import ContextBuilder, RepoView
from nightwatch.dev.local_runner import LocalRunner
from nightwatch.dev.reference_verifier import ReferenceVerifier
from nightwatch.history import HistoryStore
from nightwatch.pipeline import Pipeline
from nightwatch.schemas import Alert, Frame
from nightwatch.web_search import (
    CachedSearch,
    LibraryHint,
    TavilySearch,
    WebResult,
    build_query,
    changed_dependencies,
    detect_library,
    parse_pins,
    scrub_message,
)

RESULT = WebResult(title="ValidationError after upgrading to v2 · Issue #123",
                   url="https://github.com/pydantic/pydantic/issues/123",
                   content="In v2 the field became required. Use model_validate instead of parse_obj.")


class FakeSearch:
    def __init__(self, results=None, error=None):
        self.queries: list[str] = []
        self.results = [RESULT] if results is None else results
        self.error = error

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


def library_alert(commit: str, good: str | None = None, **kw) -> Alert:
    frames = [Frame(file="app/calc.py", function="average", line=3),
              Frame(file="/usr/lib/python3.12/site-packages/pydantic/main.py", function="model_validate",
                    line=626, in_app=False)]
    return Alert(title="ValidationError in POST /items", exception_type="ValidationError",
                 message="1 validation error for Item price Field required [type=missing, input_value={'name': 'x'}]",
                 stacktrace=frames, commit=commit, previous_good_commit=good, **kw)


# --------------------------------------------------------------------------- when to search


@pytest.mark.parametrize("path, package", [
    ("/usr/lib/python3.12/site-packages/fastapi/routing.py", "fastapi"),
    ("C:\\venv\\Lib\\site-packages\\pydantic\\main.py", "pydantic"),
    ("/venv/lib/python3.11/site-packages/yaml/constructor.py", "PyYAML"),
    ("sqlalchemy/engine/base.py", "sqlalchemy"),
    ("/usr/lib/python3.12/json/decoder.py", None),  # standard library
    ("json/decoder.py", None),
    ("<frozen importlib._bootstrap>", None),
])
def test_package_from_library_frame(path, package):
    assert web_search._package_from_path(path) == package


def test_parse_pins_and_changed_dependencies():
    assert parse_pins("fastapi==0.115.0\npydantic>=2.7\n# comment\nhttpx ~= 0.27\n") == \
        {"fastapi": "0.115.0", "pydantic": "2.7", "httpx": "0.27"}
    assert parse_pins('dependencies = [\n  "openai>=1.40",\n  "pydantic[email]>=2.6",\n]') == \
        {"openai": "1.40", "pydantic": "2.6"}
    diff = ("diff --git a/requirements.txt b/requirements.txt\n--- a/requirements.txt\n+++ b/requirements.txt\n"
            "-pydantic==1.10.13\n+pydantic==2.7.0\n fastapi==0.115.0\n"
            "diff --git a/app/x.py b/app/x.py\n+version==1.0\n")
    assert changed_dependencies(diff) == ["pydantic"]


def test_error_in_our_code_is_not_searched():
    alert = Alert(title="t", exception_type="ZeroDivisionError", message="division by zero", commit="c",
                  stacktrace=[Frame(file="app/calc.py", function="average", line=3)])
    assert detect_library(alert, {}, "") is None


def test_library_frame_triggers_search_with_pinned_version():
    hint = detect_library(library_alert("c"), {"pydantic": "2.7.0"}, "")
    assert hint == LibraryHint("pydantic", "2.7.0", "error raised inside pydantic")


def test_dependency_change_triggers_search():
    alert = Alert(title="t", exception_type="TypeError", message="unexpected keyword argument", commit="c",
                  stacktrace=[Frame(file="app/api.py", function="create", line=9)])
    diff = "diff --git a/requirements.txt b/requirements.txt\n+++ b/requirements.txt\n+httpx==0.28.0\n"
    hint = detect_library(alert, {"httpx": "0.28.0"}, diff)
    assert hint.package == "httpx" and hint.version == "0.28.0" and hint.reason.startswith("dependency")
    assert build_query(hint, alert).endswith("breaking change")


# --------------------------------------------------------------------------- what leaves the service


def test_scrub_removes_data_from_message():
    msg = ("User alice@example.com not found: id=48213 at https://api.example.com/u/48213 "
           "token 'sk-abc123' uuid 123e4567-e89b-12d3-a456-426614174000 in /srv/app/data/users.db")
    out = scrub_message(msg)
    for leaked in ("alice", "48213", "example.com", "sk-abc123", "123e4567", "/srv"):
        assert leaked not in out, out
    assert "not found" in out


def test_query_has_no_request_logs_or_code():
    alert = library_alert("c", request={"body": {"card": "4111111111111111", "email": "bob@corp.io"}},
                          logs="Authorization: Bearer secret-token-xyz")
    query = build_query(LibraryHint("pydantic", "2.7.0", "error raised inside pydantic"), alert)
    assert query.startswith("pydantic 2.7.0 ValidationError")
    for leaked in ("4111", "bob@", "secret-token", "Bearer", "'x'", "name"):
        assert leaked not in query, query
    assert len(query) < 160


def test_tavily_request_shape(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"results": [{"title": "t", "url": "https://github.com/x", "content": "c"},
                                           {"title": "no url"}]}).encode()

    def fake_urlopen(req, timeout):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.headers), json.loads(req.data)
        return Resp()

    monkeypatch.setattr(web_search.urllib.request, "urlopen", fake_urlopen)
    results = TavilySearch("tvly-test").search("pydantic ValidationError")
    assert sent["url"] == "https://api.tavily.com/search"
    assert sent["headers"]["Authorization"] == "Bearer tvly-test"
    body = sent["body"]
    assert body["search_depth"] == "basic" and body["max_results"] == 4 and "github.com" in body["include_domains"]
    assert "tvly-test" not in json.dumps(body)  # the key goes only in the header
    assert results == [WebResult("t", "https://github.com/x", "c")]


def test_cache_spends_one_credit_per_query(tmp_path):
    inner = FakeSearch()
    cached = CachedSearch(inner, tmp_path / "_web_cache.json")
    assert cached.search("q") == [RESULT]
    assert CachedSearch(inner, tmp_path / "_web_cache.json").search("q") == [RESULT]  # new process, same file
    assert inner.queries == ["q"]


def test_from_settings_is_off_by_default(tmp_path):
    s = Settings(history_dir=tmp_path)
    s.web_search, s.tavily_api_key = False, "tvly-x"
    assert web_search.from_settings(s) is None
    s.web_search, s.tavily_api_key = True, ""
    assert web_search.from_settings(s) is None
    s.tavily_api_key = "tvly-x"
    assert isinstance(web_search.from_settings(s), CachedSearch)


# --------------------------------------------------------------------------- context and pipeline


def test_context_adds_web_evidence_for_library_error(demo_repo):
    repo, good, broken = demo_repo
    web = FakeSearch()
    builder = ContextBuilder(RepoView(repo), web=web)
    evidence, _ = builder.build(library_alert(broken, good))
    assert len(web.queries) == 1 and web.queries[0].startswith("pydantic ValidationError")
    item = next(e for e in evidence if e.kind == "web")
    assert "UNVERIFIED" in item.source and RESULT.url in item.content
    assert evidence[-1].kind == "web"  # lowest priority: dropped first when over budget
    assert any(n.startswith("Web search (error raised inside pydantic)") for n in builder.notes)


def test_context_skips_search_for_own_code(demo_repo, alert):
    repo, _, _ = demo_repo
    web = FakeSearch()
    builder = ContextBuilder(RepoView(repo), web=web)
    evidence, _ = builder.build(alert)
    assert web.queries == [] and not [e for e in evidence if e.kind == "web"] and builder.notes == []


def test_search_failure_is_not_fatal(demo_repo):
    repo, good, broken = demo_repo
    builder = ContextBuilder(RepoView(repo), web=FakeSearch(error=TimeoutError("slow")))
    evidence, _ = builder.build(library_alert(broken, good))
    assert not [e for e in evidence if e.kind == "web"]
    assert builder.notes == ["Web search failed (TimeoutError); continuing without it"]


def test_version_comes_from_dependency_file(demo_repo):
    repo, good, _ = demo_repo
    (repo / "requirements.txt").write_text("pydantic==2.7.0\n")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "deps"],
                   check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    web = FakeSearch()
    ContextBuilder(RepoView(repo), web=web).build(library_alert(head, good))
    assert web.queries[0].startswith("pydantic 2.7.0 ValidationError")


def test_pipeline_logs_search_and_passes_results_to_model(demo_repo, tmp_path):
    repo, good, broken = demo_repo
    s = Settings()
    s.history_dir = tmp_path / "history"
    report = {"facts": ["f"], "hypotheses": [{"summary": "h", "confidence": 0.2}], "next_steps": ["n"]}
    llm = ScriptedLLM({"investigate": [{"hypotheses": [{"summary": "library change", "confidence": 0.3}],
                                        "repro_test": None, "not_reproducible_reason": "needs the library"}],
                       "report": [report]})
    pipe = Pipeline(llm, LocalRunner(repo), ReferenceVerifier(), RepoView(repo), HistoryStore(s.history_dir), s,
                    web=FakeSearch())
    inc = pipe.run(library_alert(broken, good))
    assert any(e.message.startswith("Web search (error raised inside pydantic)") for e in inc.events)
    assert RESULT.url in llm.calls[0][1] and "UNVERIFIED" in llm.calls[0][1]


def _incident_with_web(cited: list[str]):
    from nightwatch.schemas import Evidence, Incident, Investigation, PatchProposal

    inc = Incident(alert=Alert(title="t", exception_type="InvalidVersion", commit="c"))
    inc.evidence = [
        Evidence(id="E1", kind="stacktrace", source="alert", content="x"),
        Evidence(id="E2", kind="web", source="web, EXTERNAL AND UNVERIFIED, may not match this version: Issue A",
                 content="a", url="https://github.com/pypa/setuptools/issues/3772"),
        Evidence(id="E3", kind="web", source="web, EXTERNAL AND UNVERIFIED, may not match this version: Issue B",
                 content="b", url="https://stackoverflow.com/q/1"),
    ]
    inc.investigation = Investigation(hypotheses=[{"summary": "h", "confidence": 0.8, "evidence_ids": ["E1"]}])
    inc.final_patch = PatchProposal(diff="d", explanation="e", evidence_ids=cited)
    return inc


def test_only_cited_web_sources_are_listed():
    from nightwatch.web_search import cited_web_sources, references_markdown

    inc = _incident_with_web(["E1", "E2"])
    assert [e.url for e in cited_web_sources(inc)] == ["https://github.com/pypa/setuptools/issues/3772"]
    md = references_markdown(inc)
    assert "[Issue A](https://github.com/pypa/setuptools/issues/3772)" in md and "stackoverflow" not in md
    assert "still reproduced and verified" in md


def test_no_cited_web_source_means_no_section():
    from nightwatch.web_search import references_markdown

    assert references_markdown(_incident_with_web(["E1"])) == ""


def test_each_web_result_is_its_own_citable_evidence(demo_repo):
    repo, good, broken = demo_repo
    second = WebResult(title="Another report", url="https://github.com/x/y/issues/2", content="c")
    evidence, _ = ContextBuilder(RepoView(repo), web=FakeSearch([RESULT, second])).build(library_alert(broken, good))
    web = [e for e in evidence if e.kind == "web"]
    assert [e.url for e in web] == [RESULT.url, second.url]
    assert web[0].id != web[1].id and all("UNVERIFIED" in e.source for e in web)


def test_draft_pr_body_lists_cited_upstream_reports():
    from types import SimpleNamespace

    from app.github_pr import body_for

    run = SimpleNamespace(passed=["t1", "t2"])
    with_ref = body_for(_incident_with_web(["E2"]), run)
    assert "Known upstream reports" in with_ref and "issues/3772" in with_ref
    assert "Known upstream reports" not in body_for(_incident_with_web(["E1"]), run)
