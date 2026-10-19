"""Reference verifier for development. Danik replaces it with verifier.py.

Rules from the "Invariants" section of the plan:
- reproduction: the test fails on the broken version with the same signature as the alert
  and passes on the previous good version (when given);
- patch: it applied, the whole suite is green, the test-file hash did not change.
"""
from __future__ import annotations

from ..schemas import Alert, ReproTest, RunResult, TestFailure, Verdict


def _matches(f: TestFailure, alert: Alert) -> dict[str, bool]:
    sig = alert.signature()
    msg_key = sig.message_contains.strip()[:60]
    return {
        "same_exception": f.exception_type == sig.exception_type,
        "same_message": not msg_key or msg_key.lower() in f.message.lower() or f.message.lower() in msg_key.lower(),
        "same_location": not sig.file or f.file == sig.file,
    }


class ReferenceVerifier:
    def check_reproduction(self, alert: Alert, test: ReproTest, broken: RunResult,
                           good: RunResult | None) -> Verdict:
        checks: dict[str, bool] = {}
        repro = [f for f in broken.failures if f.nodeid.startswith(test.path)]
        checks["fails_on_broken"] = broken.status == "failed" and bool(repro)
        if not checks["fails_on_broken"]:
            why = "test did not fail on the broken version" if broken.status == "passed" else \
                f"test did not run ({broken.status}, exit code {broken.exit_code})"
            return Verdict(ok=False, reason=why, checks=checks)

        best = max(repro, key=lambda f: sum(_matches(f, alert).values()))
        m = _matches(best, alert)
        checks.update(m)
        if not all(m.values()):
            got = f"{best.exception_type}: {best.message[:80]} in {best.file}:{best.function}"
            sig = alert.signature()
            want = f"{alert.exception_type}: {alert.message[:80]} in {sig.file}:{sig.function}"
            return Verdict(ok=False, reason=f"signature mismatch — got '{got}', expected '{want}'", checks=checks)

        if good is not None:
            checks["passes_on_good"] = good.status == "passed"
            if not checks["passes_on_good"]:
                return Verdict(ok=False, reason="test also fails on the previous good version — "
                                                "it does not test the change", checks=checks)
        return Verdict(ok=True, reason="test fails with the same signature as the alert" +
                       (" and passes on the good version" if good is not None else ""), checks=checks)

    def check_patch(self, alert: Alert, test: ReproTest, after: RunResult, tests_hash_before: str) -> Verdict:
        checks = {
            "patch_applied": after.patch_applied is not False,
            "tests_unchanged": bool(after.tests_hash) and after.tests_hash == tests_hash_before,
            "repro_passes": any(p.startswith(test.path) for p in after.passed),
            "suite_green": after.status == "passed",
        }
        if not checks["patch_applied"]:
            return Verdict(ok=False, reason="patch did not apply", checks=checks)
        if not checks["tests_unchanged"]:
            return Verdict(ok=False, reason="test files changed after the patch", checks=checks)
        if not checks["repro_passes"]:
            return Verdict(ok=False, reason="reproduction test still fails", checks=checks)
        if not checks["suite_green"]:
            broken = ", ".join(f.nodeid for f in after.failures[:5])
            return Verdict(ok=False, reason=f"patch breaks existing tests: {broken}", checks=checks)
        return Verdict(ok=True, reason="reproduction test and full suite green, tests unchanged", checks=checks)
