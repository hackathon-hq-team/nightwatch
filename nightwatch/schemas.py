"""Shared Nightwatch data models — the contract between team modules.

Nikita:  pipeline.py, context.py, model_client.py — create and fill the Incident.
Timur:   runner.py returns RunResult; the UI reads Incident from history.
Danik:   verifier.py returns Verdict.

Change only by agreement: these models are stored in history/*.json and read by the UI.
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
    """One stack frame. `file` is relative to the repository root."""

    file: str
    function: str
    line: int | None = None
    in_app: bool = True


class ErrorSignature(BaseModel):
    """The verifier compares a test failure with the original alert by this signature."""

    exception_type: str  # e.g. "ZeroDivisionError" or "KeyError"
    message_contains: str = ""  # stable part of the message, no ids or dates
    file: str | None = None  # file where the exception is raised (last in-app frame)
    function: str | None = None


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("alert"))
    source: Literal["sentry", "manual", "scenario"] = "manual"
    title: str
    exception_type: str
    message: str = ""
    stacktrace: list[Frame] = Field(default_factory=list)  # outermost call first, error site last
    request: dict | None = None  # method, url, body from the Sentry event
    logs: str = ""  # log excerpt around the failure, if any
    commit: str  # version where the failure happened
    previous_good_commit: str | None = None  # last known good version, if known
    received_at: datetime = Field(default_factory=utcnow)
    raw: dict | None = None  # original event, for debugging

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
    """A piece of context. The model must cite its id (E1, E2…) in hypotheses."""

    id: str
    kind: EvidenceKind
    source: str  # file path, commit range, "sentry", etc.
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
    would_refute: str = ""  # what observation would refute it


class ReproTest(BaseModel):
    """Reproduction test. Written BEFORE the patch and never changed afterwards."""

    path: str  # only under tests/nightwatch/
    code: str
    expected: ErrorSignature


class Investigation(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=1)
    repro_test: ReproTest | None = None  # None — the model thinks it cannot be reproduced
    not_reproducible_reason: str = ""


class PatchProposal(BaseModel):
    diff: str  # unified diff relative to the repository root
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class Triage(BaseModel):
    """Cheap first look by a small model. A hint for the investigation, never a decision."""

    category: str  # code_bug | data | infrastructure | load | unknown
    likely_reproducible: bool
    summary: str  # one sentence: what failed and where to look first
    focus: list[str] = Field(default_factory=list)  # evidence ids or file paths worth reading first


class DiagnosticReport(BaseModel):
    facts: list[str]
    hypotheses: list[Hypothesis]
    next_steps: list[str]


# --------------------------------------------------------------------------- runner / verifier


class TestFailure(BaseModel):
    __test__ = False  # keep pytest from collecting this class

    nodeid: str
    exception_type: str
    message: str = ""
    file: str | None = None  # last frame inside the repository (not tests)
    function: str | None = None


class RunResult(BaseModel):
    """Returned by runner.py (Timur)."""

    commit: str
    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: int
    passed: list[str] = Field(default_factory=list)
    failures: list[TestFailure] = Field(default_factory=list)
    tests_hash: str = ""  # sha256 of test files AFTER the patch is applied
    patch_applied: bool | None = None  # None — no patch was given
    output_tail: str = ""  # last lines of pytest output
    duration_s: float = 0.0


class Verdict(BaseModel):
    """Returned by verifier.py (Danik)."""

    ok: bool
    reason: str
    checks: dict[str, bool] = Field(default_factory=dict)


# --------------------------------------------------------------------------- incident


class IncidentState(str, Enum):
    received = "received"
    investigating = "investigating"
    reproducing = "reproducing"
    patching = "patching"
    verified = "verified"  # the patch passed every check
    reported = "reported"  # not reproduced or no passing patch — diagnostic report
    failed = "failed"  # Nightwatch itself crashed (not the same as reported)


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
    similar_incidents: list[str] = Field(default_factory=list)  # ids from history
    triage: Triage | None = None  # only when NIGHTWATCH_TRIAGE_MODEL is set
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
