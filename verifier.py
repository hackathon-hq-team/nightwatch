"""Acceptance rules for runner results; never executes model-provided code."""
from __future__ import annotations

from nightwatch.schemas import Alert, ReproTest, RunResult, Verdict


def _belongs(nodeid: str, path: str) -> bool:
    # A prefix alone would also accept test_repro.py_backup or another filename.
    return nodeid.startswith(path + "::") and not nodeid.endswith((" [setup]", " [teardown]"))


def _verdict(checks: dict[str, bool], success: str) -> Verdict:
    failed = [name for name, passed in checks.items() if not passed]
    return Verdict(ok=not failed, reason="Failed checks: " + ", ".join(failed) if failed else success,
                   checks=checks)


class Verifier:
    def check_reproduction(self, alert: Alert, test: ReproTest, broken: RunResult,
                           good: RunResult | None) -> Verdict:
        signature = alert.signature()
        failures = [f for f in broken.failures if _belongs(f.nodeid, test.path)]
        matches = [f for f in failures if (
            f.exception_type == signature.exception_type
            and (not signature.message_contains or signature.message_contains in f.message)
            and (signature.file is None or f.file == signature.file)
            and (signature.function is None or f.function == signature.function)
        )]
        checks = {
            "broken_commit_matches": broken.commit == alert.commit,
            "fails_on_broken": broken.status == "failed" and broken.exit_code == 1 and bool(failures),
            "same_signature": bool(matches),
        }
        if alert.previous_good_commit is not None or good is not None:
            checks["passes_on_good"] = bool(
                good is not None and good.status == "passed" and good.exit_code == 0
                and not good.failures and any(_belongs(p, test.path) for p in good.passed)
            )
            if alert.previous_good_commit is not None:
                checks["good_commit_matches"] = good is not None and good.commit == alert.previous_good_commit
        return _verdict(checks, "Reproduction matches the alert and available previous-good evidence")

    def check_patch(self, alert: Alert, test: ReproTest, after: RunResult,
                    tests_hash_before: str) -> Verdict:
        checks = {
            "commit_matches": after.commit == alert.commit,
            "patch_applied": after.patch_applied is True,
            "tests_unchanged": bool(tests_hash_before) and after.tests_hash == tests_hash_before,
            "repro_passes": any(_belongs(p, test.path) for p in after.passed),
            "suite_green": after.status == "passed" and after.exit_code == 0 and not after.failures,
        }
        return _verdict(checks, "Patch applied; reproduction and suite passed; test hashes unchanged")
