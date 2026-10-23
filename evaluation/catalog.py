"""Trusted scenario fixtures; never include this catalog in model context."""
from evaluation.finance_cases import CASES as FINANCE_CASES
from evaluation.library_cases import CASES as LIBRARY_CASES
from evaluation.state_cases import CASES as STATE_CASES

CASES = FINANCE_CASES + STATE_CASES + LIBRARY_CASES


def replace_once(source: str, replacements: list) -> str:
    """Fail on source drift instead of silently preparing a different incident."""
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError(f"Scenario replacement must match exactly once: {before!r}")
        source = source.replace(before, after, 1)
    return source
