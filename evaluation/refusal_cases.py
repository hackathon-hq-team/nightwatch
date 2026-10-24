"""Synthetic incident reports that lack the evidence needed for a code repair.

These reports are fixture data, not captured remote failures or execution logs.
No service is contacted, deployment value invented, or race manufactured locally.
The scripted replies below test control flow; they do not measure model judgment.
"""
from __future__ import annotations


REFUSALS = [
    {
        "slug": "external-timeout-unobserved",
        "title": "A reported external-service timeout has no captured exchange",
        "exception_type": "TimeoutError",
        "message": "An operator reports that a shipment-status request exceeded its deadline",
        "request": {"component": "shipment-status integration", "response_captured": False},
        "logs": (
            "SYNTHETIC SCENARIO: an operator reports an intermittent timeout when contacting an external "
            "shipment-status service. No network trace, request parameters, response, timing measurements, "
            "service status or integration code was supplied. The fixed local demo cannot contact or "
            "replay that service. No external request or timeout was executed to create this report."
        ),
        "expected": "reported",
    },
    {
        "slug": "deployment-config-absent",
        "title": "A deployment is reported to lack its required upstream endpoint",
        "exception_type": "ConfigurationError",
        "message": "The required UPSTREAM_BASE_URL deployment value was not provided",
        "request": {"component": "deployment configuration", "required_setting": "UPSTREAM_BASE_URL"},
        "logs": (
            "SYNTHETIC SCENARIO: the deployment owner reports that the required upstream endpoint value "
            "was not supplied. The deployment manifest and an approved endpoint are absent from the "
            "evidence. This is a configuration prerequisite, with no demonstrated application-code bug. "
            "The fixed demo has no such deployment integration. No actual environment inspection or "
            "startup failure was executed to create this report."
        ),
        "expected": "reported",
    },
    {
        "slug": "race-without-schedule",
        "title": "A suspected concurrent update has no operation trace or interleaving",
        "exception_type": "ConcurrencyError",
        "message": "An operator suspects that concurrent workers applied one inventory change twice",
        "request": {"symptom": "suspected duplicated inventory change", "concurrency_trace_captured": False},
        "logs": (
            "SYNTHETIC SCENARIO: an operator suspects a race between workers after an inventory count "
            "looked incorrect. No event IDs, before/after snapshot, thread or transaction trace, worker "
            "schedule or concurrency implementation was supplied. A deterministic local example cannot "
            "establish the claimed production interleaving. No concurrent run was executed to create "
            "this report; the existence of a race remains unconfirmed."
        ),
        "expected": "reported",
    },
]


# These are evaluation-only answers, excluded when writing Alert-compatible files.
SCRIPTED_REPLIES = {
    "external-timeout-unobserved": {
        "reason": "Missing captured network exchange, timing evidence and integration code; the remote timeout cannot be reproduced locally.",
        "hypothesis": "The reported delay may belong to the external service or network, but its cause is unconfirmed.",
        "facts": [
            "Only a synthetic operator report was supplied; no external failure was observed by this run.",
            "There is no captured request/response, timing trace or integration implementation.",
        ],
        "next_steps": [
            "Obtain a sanitized request/response trace, elapsed timings and the service incident window.",
            "Ask the integration owner for its timeout policy and relevant code before constructing a replay.",
        ],
    },
    "deployment-config-absent": {
        "reason": "The required deployment endpoint is absent and no approved value or manifest is available; an application patch cannot establish that configuration.",
        "hypothesis": "A missing deployment prerequisite explains the report; an application-code defect has not been demonstrated.",
        "facts": [
            "The synthetic report says that UPSTREAM_BASE_URL was not supplied by the deployment owner.",
            "No approved endpoint or deployment manifest was provided, and the demo has no such integration.",
        ],
        "next_steps": [
            "Ask the deployment owner to confirm the required setting in a redacted manifest and supply its approved endpoint through the normal configuration process.",
            "Repeat the deployment's documented startup check after configuration is supplied; do not invent a URL or change application defaults.",
        ],
    },
    "race-without-schedule": {
        "reason": "Missing operation identities, state snapshots and a concurrency schedule; the suspected interleaving cannot be reconstructed.",
        "hypothesis": "A concurrency defect is one possible explanation, but retries or incorrect observations have not been excluded.",
        "facts": [
            "The only evidence is a synthetic report of a possibly duplicated inventory change.",
            "No before/after state, operation trace, transaction boundaries or worker schedule was captured.",
        ],
        "next_steps": [
            "Collect sanitized operation IDs, before/after snapshots and ordered worker or transaction traces.",
            "Have the concurrency owner provide a repeatable interleaving and implementation before proposing synchronization changes.",
        ],
    },
}


def scripted_refusal(slug: str) -> dict:
    """Create fresh explicitly scripted responses for an offline pipeline check."""
    answer = SCRIPTED_REPLIES[slug]
    hypothesis = {"summary": answer["hypothesis"], "confidence": 0.2}
    return {
        "investigate": [{
            "hypotheses": [dict(hypothesis)],
            "repro_test": None,
            "not_reproducible_reason": answer["reason"],
        }],
        "report": [{
            "facts": list(answer["facts"]),
            "hypotheses": [dict(hypothesis)],
            "next_steps": list(answer["next_steps"]),
        }],
    }
