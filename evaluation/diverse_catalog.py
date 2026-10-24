"""Diverse checkpoint cases; independent from the advanced-scenarios draft PR."""
from evaluation.catalog import CASES as STANDARD_CASES
from evaluation.context_cases import CASES as CONTEXT_CASES
from evaluation.decoy_cases import CASES as DECOY_CASES
from evaluation.http_cases import CASES as HTTP_CASES

NEW_CASES = HTTP_CASES + CONTEXT_CASES + DECOY_CASES
CASES = STANDARD_CASES + NEW_CASES
REPEAT_SLUG = "zz-repeat-inventory-replay"
REPEAT_OF = "inventory-replay"


def get_case(slug: str) -> dict | None:
    original = REPEAT_OF if slug == REPEAT_SLUG else slug
    case = next((case for case in CASES if case["slug"] == original), None)
    return {**case, "slug": slug} if case is not None else None


def baseline_count(suite: str = "standard") -> int:
    if suite not in {"standard", "diverse"}:
        raise ValueError("Unknown scenario suite")
    cases = CASES if suite == "diverse" else STANDARD_CASES
    return 4 + sum(case["baseline_count"] for case in cases)
