"""Trusted scenario fixtures; never include this catalog in model context."""
from evaluation.finance_cases import CASES as FINANCE_CASES
from evaluation.library_cases import CASES as LIBRARY_CASES
from evaluation.state_cases import CASES as STATE_CASES
from evaluation.refund_cases import CASES as REFUND_CASES
from evaluation.projection_cases import CASES as PROJECTION_CASES
from evaluation.resolver_cases import CASES as RESOLVER_CASES

CASES = FINANCE_CASES + STATE_CASES + LIBRARY_CASES
ADVANCED_CASES = REFUND_CASES + PROJECTION_CASES + RESOLVER_CASES
SUITES = {"standard": CASES, "advanced": ADVANCED_CASES}
ALL_CASES = CASES + ADVANCED_CASES


def baseline_count(case: dict) -> int:
    """Use trusted suite membership, never a count supplied by a model or run file."""
    for cases in SUITES.values():
        if any(item["slug"] == case["slug"] for item in cases):
            return 4 + sum(item["baseline_count"] for item in cases)
    raise ValueError("Unknown scenario")


def replace_once(source: str, replacements: list) -> str:
    """Fail on source drift instead of silently preparing a different incident."""
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError(f"Scenario replacement must match exactly once: {before!r}")
        source = source.replace(before, after, 1)
    return source
