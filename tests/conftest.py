"""Фикстуры: маленький git-репозиторий с багом и LLM со сценарием ответов."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightwatch.schemas import Alert, Frame, Usage

GOOD = '''\
def average(items):
    """Среднее значение; для пустого списка — 0."""
    if not items:
        return 0
    return sum(items) / len(items)


def total(items):
    return sum(items)
'''

BROKEN = '''\
def average(items):
    """Среднее значение; для пустого списка — 0."""
    return sum(items) / len(items)


def total(items):
    return sum(items)
'''

EXISTING_TEST = '''\
from app.calc import average, total


def test_average():
    assert average([1, 2, 3]) == 2


def test_total():
    assert total([1, 2]) == 3
'''

REPRO_TEST = '''\
from app.calc import average


def test_average_of_empty_list_is_zero():
    assert average([]) == 0
'''

FIX_DIFF = '''\
--- a/app/calc.py
+++ b/app/calc.py
@@ -1,5 +1,7 @@
 def average(items):
     """Среднее значение; для пустого списка — 0."""
+    if not items:
+        return 0
     return sum(items) / len(items)


'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def demo_repo(tmp_path) -> tuple[Path, str, str]:
    repo = tmp_path / "demo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "app" / "calc.py").write_text(GOOD)
    (repo / "tests" / "test_calc.py").write_text(EXISTING_TEST)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "good")
    good = _git(repo, "rev-parse", "HEAD")
    (repo / "app" / "calc.py").write_text(BROKEN)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "simplify average")
    broken = _git(repo, "rev-parse", "HEAD")
    return repo, good, broken


@pytest.fixture
def alert(demo_repo) -> Alert:
    _, good, broken = demo_repo
    return Alert(
        title="ZeroDivisionError: division by zero in GET /stats",
        exception_type="ZeroDivisionError",
        message="division by zero",
        stacktrace=[Frame(file="app/calc.py", function="average", line=3)],
        request={"method": "GET", "url": "/stats?ids="},
        commit=broken,
        previous_good_commit=good,
    )


class ScriptedLLM:
    """Отдаёт заранее заданные ответы по шагам: {"investigate": [..], "patch": [..], "report": [..]}."""

    def __init__(self, script: dict[str, list]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, step, system, user, schema, usage: Usage, max_tokens=4096):
        self.calls.append((step, user))
        queue = self.script.get(step) or []
        if not queue:
            raise AssertionError(f"нет ответа для шага {step}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        from nightwatch.schemas import ModelCall
        usage.calls.append(ModelCall(step=step, model="fake", prompt_tokens=1000, completion_tokens=200,
                                     cost_usd=0.001, duration_s=0.0))
        return schema.model_validate(item)
