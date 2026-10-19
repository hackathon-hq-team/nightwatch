"""Общие модели данных Nightwatch — контракт между модулями команды.

Никита:  pipeline.py, context.py, model_client.py — создают и заполняют Incident.
Тимур:   runner.py возвращает RunResult; UI читает Incident из истории.
Даник:   verifier.py возвращает Verdict.

Меняем только по согласованию: эти модели сохраняются в history/*.json и читаются UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "inc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- alert


class Frame(BaseModel):
    """Один кадр стектрейса. file — путь относительно корня репозитория."""

    file: str
    function: str
    line: int | None = None
    in_app: bool = True


class ErrorSignature(BaseModel):
    """По сигнатуре verifier сравнивает падение теста с исходным алертом."""

    exception_type: str  # например "ZeroDivisionError" или "KeyError"
    message_contains: str = ""  # устойчивая часть сообщения, без id и дат
    file: str | None = None  # файл, где брошено исключение (последний in_app кадр)
    function: str | None = None


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("alert"))
    source: Literal["sentry", "manual", "scenario"] = "manual"
    title: str
    exception_type: str
    message: str = ""
    stacktrace: list[Frame] = Field(default_factory=list)  # от внешнего вызова к месту ошибки
    request: dict | None = None  # метод, url, тело запроса из события Sentry
    logs: str = ""  # фрагмент логов вокруг сбоя, если есть
    commit: str  # версия, на которой случился сбой
    previous_good_commit: str | None = None  # последняя версия без сбоя, если известна
    received_at: datetime = Field(default_factory=utcnow)
    raw: dict | None = None  # исходное событие для отладки

    def signature(self) -> ErrorSignature:
        app_frames = [f for f in self.stacktrace if f.in_app] or self.stacktrace
        last = app_frames[-1] if app_frames else None
        return ErrorSignature(
            exception_type=self.exception_type,
            message_contains=self.message[:120],
            file=last.file if last else None,
            function=last.function if last else None,
        )


# --------------------------------------------------------------------------- evidence


EvidenceKind = Literal["stacktrace", "file", "diff", "log", "request", "memory", "web", "test_output"]


class Evidence(BaseModel):
    """Фрагмент контекста. Модель обязана ссылаться на id (E1, E2…) в гипотезах."""

    id: str
    kind: EvidenceKind
    source: str  # путь к файлу, диапазон коммитов, "sentry" и т.п.
    content: str
    truncated: bool = False

    @property
    def approx_tokens(self) -> int:
        return len(self.content) // 4 + 16


# --------------------------------------------------------------------------- model outputs


class Hypothesis(BaseModel):
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    would_refute: str = ""  # что опровергло бы гипотезу


class ReproTest(BaseModel):
    """Тест воспроизведения. Пишется ДО патча и потом не меняется."""

    path: str  # только внутри tests/nightwatch/
    code: str
    expected: ErrorSignature


class Investigation(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=1)
    repro_test: ReproTest | None = None  # None — модель считает, что воспроизвести нельзя
    not_reproducible_reason: str = ""


class PatchProposal(BaseModel):
    diff: str  # unified diff относительно корня репозитория
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class DiagnosticReport(BaseModel):
    facts: list[str]
    hypotheses: list[Hypothesis]
    next_steps: list[str]


# --------------------------------------------------------------------------- runner / verifier


class TestFailure(BaseModel):
    __test__ = False  # чтобы pytest не принимал класс за тест

    nodeid: str
    exception_type: str
    message: str = ""
    file: str | None = None  # последний кадр внутри репозитория (не тестов)
    function: str | None = None


class RunResult(BaseModel):
    """Возвращает runner.py (Тимур)."""

    commit: str
    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: int
    passed: list[str] = Field(default_factory=list)
    failures: list[TestFailure] = Field(default_factory=list)
    tests_hash: str = ""  # sha256 файлов тестов ПОСЛЕ применения патча
    patch_applied: bool | None = None  # None — патча не было
    output_tail: str = ""  # последние строки вывода pytest
    duration_s: float = 0.0


class Verdict(BaseModel):
    """Возвращает verifier.py (Даник)."""

    ok: bool
    reason: str
    checks: dict[str, bool] = Field(default_factory=dict)


# --------------------------------------------------------------------------- incident


class IncidentState(str, Enum):
    received = "received"
    investigating = "investigating"
    reproducing = "reproducing"
    patching = "patching"
    verified = "verified"  # патч прошёл все проверки
    reported = "reported"  # сбой не воспроизведён или патч не прошёл — отчёт
    failed = "failed"  # ошибка самой системы (не путать с reported)


class ModelCall(BaseModel):
    step: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_s: float


class Usage(BaseModel):
    calls: list[ModelCall] = Field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)

    @property
    def tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)


class Attempt(BaseModel):
    n: int
    patch: PatchProposal | None = None
    run: RunResult | None = None
    verdict: Verdict | None = None
    error: str = ""


class Event(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    state: IncidentState
    message: str


class Incident(BaseModel):
    id: str = Field(default_factory=new_id)
    alert: Alert
    state: IncidentState = IncidentState.received
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    evidence: list[Evidence] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)  # id из истории
    investigation: Investigation | None = None
    repro_broken: RunResult | None = None
    repro_good: RunResult | None = None
    repro_verdict: Verdict | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    final_patch: PatchProposal | None = None
    report: DiagnosticReport | None = None
    rejected_hypotheses: list[str] = Field(default_factory=list)

    usage: Usage = Field(default_factory=Usage)
    events: list[Event] = Field(default_factory=list)
    error: str = ""

    def move(self, state: IncidentState, message: str) -> None:
        self.state = state
        self.updated_at = utcnow()
        self.events.append(Event(state=state, message=message))
