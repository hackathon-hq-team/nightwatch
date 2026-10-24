"""Unusable model replies: cut-off answers get more room, and a bad reply is a failed attempt, not a crash."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_pipeline import INVESTIGATION, PATCH, REPORT, ULTRA, make

from nightwatch.config import Settings
from nightwatch.model_client import ModelClient, ModelError
from nightwatch.schemas import IncidentState, PatchProposal, Usage


class FakeOpenAI:
    def __init__(self, replies):
        self.replies = list(replies)  # (text, finish_reason)
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        text, finish = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100))


def client(tmp_path, replies):
    s = Settings()
    s.history_dir = tmp_path
    return ModelClient(s, client=FakeOpenAI(replies))


def test_cut_off_reply_is_retried_with_more_tokens(tmp_path):
    c = client(tmp_path, [("<think>long reasoning that never ends", "length"),
                          ('{"diff": "d", "explanation": "e"}', "stop")])
    out = c.complete_json("patch", "s", "u", PatchProposal, Usage(), max_tokens=4096)
    assert out.diff == "d"
    first, second = c._client.sent
    assert first["max_tokens"] == 4096 and second["max_tokens"] == 8192
    assert len(second["messages"]) == 3  # the cut-off text is not sent back
    assert "cut off" in second["messages"][-1]["content"]


def test_cut_off_twice_explains_why(tmp_path):
    c = client(tmp_path, [("<think>a", "length"), ("<think>b", "length")])
    with pytest.raises(ModelError, match="cut off at max_tokens=16384"):
        c.complete_json("patch", "s", "u", PatchProposal, Usage(), max_tokens=8192)
    assert c._client.sent[1]["max_tokens"] == 16384


def test_unusable_patch_reply_is_a_failed_attempt_then_escalates(demo_repo, alert, tmp_path):
    bad = ModelError("patch: model did not return valid JSON: no JSON object in the reply")
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [bad, bad, PATCH]})
    pipe.s.escalation_model = ULTRA
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified, inc.events
    assert [a.patch is None for a in inc.attempts] == [True, True, False]
    assert inc.attempts[0].error and not inc.attempts[0].verdict.ok
    assert llm.models[-1] == ULTRA
    assert sum(e.message.endswith("unusable model reply") for e in inc.events) == 2
    assert not inc.error


def test_all_patch_replies_unusable_ends_in_report(demo_repo, alert, tmp_path):
    bad = ModelError("patch: no JSON")
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [bad, bad, bad], "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported and not inc.error
    assert inc.final_patch is None and len(inc.attempts) == 3


def test_unusable_investigation_reply_retries_with_escalation(demo_repo, alert, tmp_path):
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [ModelError("investigate: no JSON"), INVESTIGATION],
                                           "patch": [PATCH]})
    pipe.s.escalation_model = ULTRA
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert llm.models[:2] == [None, ULTRA]


def test_no_usable_investigation_ends_in_report(demo_repo, alert, tmp_path):
    bad = ModelError("investigate: no JSON")
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [bad, bad], "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported and not inc.error
    assert any("no usable model reply" in e.message for e in inc.events)



class Unavailable(Exception):
    status_code = 503


def test_transient_provider_error_is_retried(tmp_path, monkeypatch):
    """24 Oct live run: Token Factory answered 503 once and the scenario ended as a Nightwatch error."""
    import nightwatch.model_client as mc

    sleeps = []
    monkeypatch.setattr(mc.time, "sleep", sleeps.append)
    c = client(tmp_path, [('{"diff": "d", "explanation": "e"}', "stop")])
    real = c._client.chat.completions.create
    failures = [Unavailable("503 Service Temporarily Unavailable")] * 2

    def flaky(**kw):
        if failures:
            raise failures.pop()
        return real(**kw)

    c._client.chat.completions.create = flaky
    assert c.complete_json("patch", "s", "u", PatchProposal, Usage()).diff == "d"
    assert sleeps == [5, 15]


def test_permanent_error_is_not_retried(tmp_path, monkeypatch):
    import nightwatch.model_client as mc

    class BadRequest(Exception):
        status_code = 400

    monkeypatch.setattr(mc.time, "sleep", lambda s: pytest.fail("must not wait"))
    c = client(tmp_path, [])

    def bad(**kw):
        raise BadRequest("invalid model")

    c._client.chat.completions.create = bad
    with pytest.raises(BadRequest):
        c.complete_json("patch", "s", "u", PatchProposal, Usage())


def test_retries_give_up_after_the_last_delay(tmp_path, monkeypatch):
    import nightwatch.model_client as mc

    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    c = client(tmp_path, [])
    calls = []

    def down(**kw):
        calls.append(1)
        raise Unavailable("503")

    c._client.chat.completions.create = down
    with pytest.raises(Unavailable):
        c.complete_json("patch", "s", "u", PatchProposal, Usage())
    assert len(calls) == 4  # first try + 3 retries
