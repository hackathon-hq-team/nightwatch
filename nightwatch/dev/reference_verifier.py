"""Эталонный verifier для разработки. Даник заменяет его своим verifier.py.

Правила — из раздела «Инварианты» плана:
- воспроизведение: тест падает на сломанной версии с той же сигнатурой, что и алерт,
  и проходит на предыдущей исправной версии (если она дана);
- патч: применился, весь набор зелёный, хэш файлов тестов не изменился.
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
            why = "тест не упал на сломанной версии" if broken.status == "passed" else \
                f"тест не запустился ({broken.status}, код {broken.exit_code})"
            return Verdict(ok=False, reason=why, checks=checks)

        best = max(repro, key=lambda f: sum(_matches(f, alert).values()))
        m = _matches(best, alert)
        checks.update(m)
        if not all(m.values()):
            got = f"{best.exception_type}: {best.message[:80]} в {best.file}:{best.function}"
            want = f"{alert.exception_type}: {alert.message[:80]} в {alert.signature().file}:{alert.signature().function}"
            return Verdict(ok=False, reason=f"сигнатура не совпала — получено «{got}», ожидалось «{want}»",
                           checks=checks)

        if good is not None:
            checks["passes_on_good"] = good.status == "passed"
            if not checks["passes_on_good"]:
                return Verdict(ok=False, reason="тест падает и на предыдущей исправной версии — "
                                                "он проверяет не то изменение", checks=checks)
        return Verdict(ok=True, reason="тест падает с той же сигнатурой, что и алерт" +
                       (", и проходит на исправной версии" if good is not None else ""), checks=checks)

    def check_patch(self, alert: Alert, test: ReproTest, after: RunResult, tests_hash_before: str) -> Verdict:
        checks = {
            "patch_applied": after.patch_applied is not False,
            "tests_unchanged": bool(after.tests_hash) and after.tests_hash == tests_hash_before,
            "repro_passes": any(p.startswith(test.path) for p in after.passed),
            "suite_green": after.status == "passed",
        }
        if not checks["patch_applied"]:
            return Verdict(ok=False, reason="патч не применился", checks=checks)
        if not checks["tests_unchanged"]:
            return Verdict(ok=False, reason="файлы тестов изменились после патча", checks=checks)
        if not checks["repro_passes"]:
            return Verdict(ok=False, reason="тест воспроизведения всё ещё не проходит", checks=checks)
        if not checks["suite_green"]:
            broken = ", ".join(f.nodeid for f in after.failures[:5])
            return Verdict(ok=False, reason=f"патч ломает существующие тесты: {broken}", checks=checks)
        return Verdict(ok=True, reason="тест воспроизведения и весь набор зелёные, тесты не изменены", checks=checks)
