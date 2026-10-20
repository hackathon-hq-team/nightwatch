"""Incident orchestrator.

received → investigating → reproducing → patching → verified
                                   └──────────────┴──→ reported (report instead of a patch)

Rules (the verifier checks them, the pipeline only enforces the order):
1. The model only proposes; the runner executes, the verifier decides.
2. No reproduction — no patch.
3. The reproduction test is fixed before the patch; a patch may not touch tests/.
4. At most max_patch_attempts attempts and a per-incident budget.
Every step is saved to history so the UI shows progress live.
"""
from __future__ import annotations

import logging
import re
import traceback

from . import prompts
from .config import Settings
from .context import ContextBuilder, RepoView, render
from .contracts import Runner, Verifier
from .history import HistoryStore
from .model_client import LLM, BudgetExceeded
from .schemas import (
    Alert,
    Attempt,
    DiagnosticReport,
    Hypothesis,
    Incident,
    IncidentState,
    Investigation,
    PatchProposal,
    RunResult,
    Triage,
    Verdict,
)

log = logging.getLogger("nightwatch.pipeline")
REPORT_FALLBACK_PREFIX = "Diagnostic report unavailable:"

REPRO_DIR = "tests/nightwatch/"
_DIFF_FILE = re.compile(r"^(?:---|\+\+\+) (?:a/|b/)?(\S+)", re.M)


def diff_files(diff: str) -> set[str]:
    return {p for p in _DIFF_FILE.findall(diff) if p != "/dev/null"}


def check_diff(diff: str, max_lines: int) -> str | None:
    """Fast rejection before the runner. Returns a reason or None."""
    if not diff.strip() or "@@" not in diff:
        return "empty or malformed diff"
    files = diff_files(diff)
    if not files:
        return "no files found in the diff"
    touched_tests = [f for f in files if f.startswith("tests/") or "/tests/" in f or f.split("/")[-1].startswith("test_")]
    if touched_tests:
        return f"patch modifies tests: {', '.join(sorted(touched_tests))}"
    changed = sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    if changed > max_lines:
        return f"diff too large: {changed} lines > {max_lines}"
    return None


def _tail(run: RunResult | None, n: int = 3000) -> str:
    if not run:
        return ""
    fails = "\n".join(f"FAILED {f.nodeid}: {f.exception_type}: {f.message} ({f.file}:{f.function})"
                      for f in run.failures[:10])
    return (fails + "\n" + run.output_tail[-n:]).strip()


class Pipeline:
    def __init__(self, llm: LLM, runner: Runner, verifier: Verifier, repo: RepoView,
                 history: HistoryStore, settings: Settings | None = None):
        self.llm = llm
        self.runner = runner
        self.verifier = verifier
        self.repo = repo
        self.history = history
        self.s = settings or Settings()
        self.context = ContextBuilder(repo, history, self.s.context_token_budget)

    # ------------------------------------------------------------------ public

    def run(self, alert: Alert) -> Incident:
        inc = Incident(alert=alert)
        inc.move(IncidentState.received, f"Alert: {alert.exception_type}: {alert.message[:120]}")
        self._save(inc)
        try:
            self._investigate_and_reproduce(inc)
            if inc.state == IncidentState.reproducing:
                self._patch(inc)
        except BudgetExceeded as e:
            self._report(inc, f"Stopped by budget: {e}")
        except Exception as e:  # a Nightwatch failure, not an investigation outcome
            log.exception("pipeline failed")
            inc.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"
            inc.move(IncidentState.failed, f"Nightwatch error: {type(e).__name__}: {e}")
        self._save(inc)
        return inc

    # ------------------------------------------------------------------ steps

    def _note(self, inc: Incident, message: str) -> None:
        inc.move(inc.state, message)

    def _escalate(self, inc: Incident, attempt: int, step: str) -> str | None:
        """Model for a retry after failures: the escalation model if configured, else the main one."""
        model = self.s.escalation_model or None
        if model:
            self._note(inc, f"Escalating {step} attempt {attempt} to {model}")
        return model

    def _triage(self, inc: Incident, user: str) -> str:
        """Optional first look by a small model. Returns a hint for the investigation prompt.

        A triage failure never stops the incident: the investigation simply runs without the hint.
        """
        if not self.s.triage_model:
            return ""
        try:
            t = self.llm.complete_json("triage", prompts.TRIAGE_SYSTEM, user, Triage, inc.usage,
                                       max_tokens=1024, model=self.s.triage_model)
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001 — a hint is optional
            self._note(inc, f"Triage skipped: {type(e).__name__}: {str(e)[:120]}")
            return ""
        inc.triage = t
        self._note(inc, f"Triage ({self.s.triage_model}): {t.category}, "
                        f"{'likely' if t.likely_reproducible else 'unlikely'} reproducible — {t.summary[:120]}")
        return prompts.TRIAGE_HINT.format(category=t.category, reproducible=t.likely_reproducible,
                                          summary=t.summary, focus=", ".join(t.focus) or "—")

    def _investigate_and_reproduce(self, inc: Incident) -> None:
        inc.move(IncidentState.investigating, "Collecting context")
        inc.evidence, inc.similar_incidents = self.context.build(inc.alert, exclude_incident=inc.id)
        if inc.similar_incidents:
            self._note(inc, f"Similar past incidents: {', '.join(inc.similar_incidents)}")
        self._save(inc)

        user = f"## Alert\n{inc.alert.title}\n\n## Evidence\n{render(inc.evidence)}"
        user += self._triage(inc, user)
        for n in range(1, self.s.max_repro_attempts + 1):
            model = self._escalate(inc, n, "investigation") if n > 1 else None
            inv = self.llm.complete_json("investigate", prompts.INVESTIGATE_SYSTEM, user, Investigation, inc.usage,
                                         model=model)
            inc.investigation = inv
            if inv.repro_test is None:
                self._report(inc, f"Model considers the failure not reproducible: {inv.not_reproducible_reason}")
                return
            if not inv.repro_test.path.startswith(REPRO_DIR):
                inv.repro_test.path = REPRO_DIR + inv.repro_test.path.split("/")[-1]

            inc.move(IncidentState.reproducing, f"Reproduction attempt {n}: {inv.repro_test.path}")
            self._save(inc)
            files = {inv.repro_test.path: inv.repro_test.code}
            inc.repro_broken = self.runner.run(inc.alert.commit, files, select=[inv.repro_test.path])
            inc.repro_good = None
            if inc.alert.previous_good_commit:
                inc.repro_good = self.runner.run(inc.alert.previous_good_commit, files, select=[inv.repro_test.path])
            verdict = self.verifier.check_reproduction(inc.alert, inv.repro_test, inc.repro_broken, inc.repro_good)
            inc.repro_verdict = verdict
            self._save(inc)
            if verdict.ok:
                self._note(inc, f"Failure reproduced: {verdict.reason}")
                return  # stay in `reproducing` → patching follows
            inc.rejected_hypotheses.append(inv.hypotheses[0].summary)
            user += "\n\n" + prompts.INVESTIGATE_RETRY.format(reason=verdict.reason, output=_tail(inc.repro_broken))

        self._report(inc, f"Failure not reproduced in {self.s.max_repro_attempts} attempts: "
                          f"{inc.repro_verdict.reason}")

    def _patch(self, inc: Incident) -> None:
        inv = inc.investigation
        test = inv.repro_test
        test_files = {test.path: test.code}
        hash_before = self.runner.tests_hash(inc.alert.commit, test_files)
        best = max(inv.hypotheses, key=lambda h: h.confidence)
        previous = ""

        for n in range(1, self.s.max_patch_attempts + 1):
            inc.move(IncidentState.patching, f"Patch attempt {n}")
            self._save(inc)
            user = prompts.PATCH_USER.format(
                evidence=render(inc.evidence), hypothesis=self._fmt_hyp(best), test_path=test.path,
                test_code=test.code, files=self._files_for_patch(inc), previous=previous)
            system = prompts.PATCH_SYSTEM.format(max_lines=self.s.max_diff_lines)
            model = self._escalate(inc, n, "patch") if n > self.s.escalate_after_failures else None
            proposal = self.llm.complete_json("patch", system, user, PatchProposal, inc.usage, model=model)
            attempt = Attempt(n=n, patch=proposal)
            inc.attempts.append(attempt)

            problem = check_diff(proposal.diff, self.s.max_diff_lines)
            if problem:
                attempt.verdict = Verdict(ok=False, reason=problem, checks={"diff_guard": False})
            else:
                attempt.run = self.runner.run(inc.alert.commit, test_files, patch=proposal.diff, select=None)
                attempt.verdict = self.verifier.check_patch(inc.alert, test, attempt.run, hash_before)
            self._save(inc)

            if attempt.verdict.ok:
                inc.final_patch = proposal
                inc.move(IncidentState.verified, f"Patch verified: {attempt.verdict.reason}")
                return
            previous = prompts.PATCH_PREVIOUS.format(n=n, reason=attempt.verdict.reason,
                                                     diff=proposal.diff[:4000], output=_tail(attempt.run))

        self._report(inc, f"No patch passed verification in {self.s.max_patch_attempts} attempts")

    def _report(self, inc: Incident, why: str) -> None:
        """Diagnostic report. Ask the model; if the budget is gone, build it from what we have."""
        report = None
        try:
            user = f"## Why automatic fix stopped\n{why}\n\n## Evidence\n{render(inc.evidence)}"
            if inc.investigation:
                user += "\n\n## Hypotheses so far\n" + "\n".join(self._fmt_hyp(h) for h in inc.investigation.hypotheses)
            if inc.repro_verdict:
                user += f"\n\n## Reproduction verdict\n{inc.repro_verdict.reason}\n{_tail(inc.repro_broken, 1500)}"
            report = self.llm.complete_json("report", prompts.REPORT_SYSTEM, user, DiagnosticReport, inc.usage, 2048)
        except Exception as e:
            log.warning("report via model failed: %s", e)
            # Keep the useful fallback report, but expose the failed report call to acceptance checks.
            self._note(inc, f"{REPORT_FALLBACK_PREFIX} {type(e).__name__}")
        if report is None:
            hyps = inc.investigation.hypotheses if inc.investigation else [
                Hypothesis(summary="Not enough data for a hypothesis", confidence=0.0)]
            report = DiagnosticReport(facts=[why], hypotheses=hyps,
                                      next_steps=["Check logs and recent changes manually"])
        inc.report = report
        inc.move(IncidentState.reported, why)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _fmt_hyp(h: Hypothesis) -> str:
        return f"- ({h.confidence:.0%}) {h.summary} [{', '.join(h.evidence_ids)}]"

    def _files_for_patch(self, inc: Incident, limit: int = 5, max_chars: int = 30000) -> str:
        files = []
        for f in reversed([f for f in inc.alert.stacktrace if f.in_app]):
            if f.file not in files and not f.file.startswith("tests/"):
                files.append(f.file)
        parts = []
        for path in files[:limit]:
            text = self.repo.show(inc.alert.commit, path)
            if text is not None:
                parts.append(f"### {path}\n```python\n{text[:max_chars]}\n```")
        return "\n\n".join(parts) or "(files unavailable — see evidence)"

    def _save(self, inc: Incident) -> None:
        self.history.save(inc)
