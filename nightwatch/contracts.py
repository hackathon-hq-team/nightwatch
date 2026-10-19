"""Интерфейсы, которые реализуют Тимур (runner) и Даник (verifier).

pipeline.py зависит только от этих протоколов, поэтому каждый может работать
независимо: до готовности настоящих модулей используются реализации из
nightwatch/dev/ (локальный запуск pytest без Docker и эталонный verifier).
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
        """Поднять копию проекта на commit, положить test_files, применить patch
        (unified diff), запустить pytest. select — список путей/nodeid; None — весь набор.

        Требования: без сети, без ключей, таймаут. Не бросать исключения из-за
        падения тестов — это нормальный RunResult(status="failed").
        Неприменимый патч → status="error", patch_applied=False.
        """
        ...

    def tests_hash(self, commit: str, test_files: dict[str, str]) -> str:
        """sha256 всех файлов тестов на commit плюс test_files — эталон до патча."""
        ...


class Verifier(Protocol):
    def check_reproduction(
        self,
        alert: Alert,
        test: ReproTest,
        broken: RunResult,
        good: RunResult | None,
    ) -> Verdict:
        """Сбой воспроизведён, только если тест падает на broken с той же сигнатурой,
        что и алерт, и (если good есть) проходит на предыдущей исправной версии."""
        ...

    def check_patch(
        self,
        alert: Alert,
        test: ReproTest,
        after: RunResult,
        tests_hash_before: str,
    ) -> Verdict:
        """Патч принят, только если патч применился, весь набор зелёный (включая тест
        воспроизведения) и хэш файлов тестов не изменился."""
        ...
