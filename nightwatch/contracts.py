"""Interfaces implemented by Timur (runner) and Danik (verifier).

pipeline.py depends only on these protocols, so everyone can work independently:
until the real modules land, the implementations in nightwatch/dev/ are used
(local pytest without Docker and a reference verifier).
"""
from __future__ import annotations

from typing import Protocol

from .schemas import Alert, ReproTest, RunResult, Verdict


class Runner(Protocol):
    def run(
        self,
        commit: str,
        test_files: dict[str, str],
        patch: str | None = None,
        select: list[str] | None = None,
    ) -> RunResult:
        """Create a copy of the project at `commit`, write `test_files`, apply `patch`
        (unified diff), run pytest. `select` is a list of paths/nodeids; None — the full suite.

        Requirements: no network, no keys, a timeout. Do not raise because tests fail —
        that is a normal RunResult(status="failed").
        A patch that does not apply → status="error", patch_applied=False.
        """
        ...

    def tests_hash(self, commit: str, test_files: dict[str, str]) -> str:
        """sha256 of all test files at `commit` plus `test_files` — the baseline before the patch."""
        ...


class Verifier(Protocol):
    def check_reproduction(
        self,
        alert: Alert,
        test: ReproTest,
        broken: RunResult,
        good: RunResult | None,
    ) -> Verdict:
        """Reproduced only if the test fails on `broken` with the same signature as the alert
        and (when `good` is given) passes on the previous good version."""
        ...

    def check_patch(
        self,
        alert: Alert,
        test: ReproTest,
        after: RunResult,
        tests_hash_before: str,
    ) -> Verdict:
        """Accepted only if the patch applied, the whole suite is green (including the
        reproduction test) and the test-file hash did not change."""
        ...
