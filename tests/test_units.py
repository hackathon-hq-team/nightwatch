from types import SimpleNamespace

import pytest

from nightwatch.config import Settings
from nightwatch.model_client import BudgetExceeded, ModelClient, ModelError, extract_json
from nightwatch.schemas import PatchProposal, Usage
from nightwatch.sentry import alert_from_sentry


def test_extract_json_strips_think_and_fences():
    text = '<think>hmm {"no": 1}</think>\nHere:\n```json\n{"a": "b{c}", "n": [1, 2]}\n```'
    assert extract_json(text) == {"a": "b{c}", "n": [1, 2]}


def test_extract_json_plain():
    assert extract_json('ok {"x": {"y": "}"}} trailing') == {"x": {"y": "}"}}


class FakeOpenAI:
    """Minimal stand-in for openai.OpenAI to test retries and cost accounting."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        text = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                               usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=100_000))


def _client(tmp_path, replies, **settings):
    s = Settings()
    s.history_dir = tmp_path
    for k, v in settings.items():
        setattr(s, k, v)
    return ModelClient(s, client=FakeOpenAI(replies))


def test_complete_json_retries_on_invalid_schema_and_counts_cost(tmp_path):
    c = _client(tmp_path, ['{"explanation": "no diff"}', '{"diff": "d", "explanation": "e"}'], incident_budget_usd=10)
    usage = Usage()
    out = c.complete_json("patch", "sys", "user", PatchProposal, usage)
    assert out.diff == "d"
    assert [x.step for x in usage.calls] == ["patch", "patch:retry"]
    # 1M input × $0.30 + 0.1M output × $0.90 = $0.39 per call
    assert usage.cost_usd == pytest.approx(0.78)
    assert c.daily.spent_today() == pytest.approx(0.78)
    second_messages = c._client.sent[1]["messages"]
    assert "failed schema validation" in second_messages[-1]["content"]


def test_complete_json_gives_up_after_two_bad_answers(tmp_path):
    c = _client(tmp_path, ["no json", "still none"], incident_budget_usd=10)
    with pytest.raises(ModelError):
        c.complete_json("patch", "s", "u", PatchProposal, Usage())


def test_incident_budget_blocks_next_call(tmp_path):
    c = _client(tmp_path, ['{"diff": "d", "explanation": "e"}'] * 2, incident_budget_usd=0.30)
    usage = Usage()
    c.complete_json("patch", "s", "u", PatchProposal, usage)  # $0.39 > $0.30
    with pytest.raises(BudgetExceeded):
        c.complete_json("patch", "s", "u", PatchProposal, usage)


def test_alert_from_sentry_webhook():
    payload = {"data": {"event": {
        "title": "ZeroDivisionError: division by zero",
        "release": "abc123",
        "tags": [["previous_release", "def456"]],
        "request": {"method": "GET", "url": "http://x/stats", "headers": {"Cookie": "secret"}},
        "exception": {"values": [{"type": "ZeroDivisionError", "value": "division by zero", "stacktrace": {"frames": [
            {"filename": "/usr/lib/python3/starlette/routing.py", "function": "handle", "lineno": 10, "in_app": False},
            {"filename": "/srv/app/app/calc.py", "function": "average", "lineno": 3, "in_app": True}]}}]},
    }}}
    a = alert_from_sentry(payload)
    assert a.commit == "abc123" and a.previous_good_commit == "def456"
    assert a.signature().file == "app/calc.py" and a.signature().function == "average"
    assert "headers" not in a.request  # headers (cookies, tokens) never reach the model


def test_alert_from_sentry_requires_release():
    with pytest.raises(ValueError):
        alert_from_sentry({"exception": {"values": [{"type": "E", "value": "v"}]}})


def test_debug_dir_saves_prompt_and_raw_reply(tmp_path):
    c = _client(tmp_path, ['<think>x</think>{"diff": "d", "explanation": "e"}'], incident_budget_usd=10,
                debug_dir=tmp_path / "calls")
    c.complete_json("patch", "sys", "user", PatchProposal, Usage())
    files = list((tmp_path / "calls").glob("*-patch.json"))
    assert len(files) == 1
    saved = __import__("json").loads(files[0].read_text())
    assert saved["reply"].startswith("<think>") and saved["messages"][1]["content"] == "user"
