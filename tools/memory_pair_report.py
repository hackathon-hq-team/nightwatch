"""Compare saved cold/repeated inventory incidents; never execute saved model output."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

from nightwatch.schemas import Incident, IncidentState

PAIR = ("inventory-replay", "zz-repeat-inventory-replay")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def contained(run, relative):
    path = (run / relative).resolve()
    if not path.is_relative_to(run):
        raise ValueError("Saved-run paths must stay inside the selected run directory")
    return path


def number(value, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (integer and type(value) is not int)):
        raise ValueError("Metrics must be finite nonnegative numbers; counters must be integers")
    return value


def load_entry(run, row):
    identifier = row.get("incident", "")
    if not isinstance(identifier, str) or not re.fullmatch(r"inc-[0-9a-f]{8}", identifier):
        raise ValueError("Summary incident must be a generated incident ID, not a path")
    path = contained(run, Path("history") / f"{identifier}.json")
    raw = read_json(path)
    incident = Incident.model_validate(raw)
    if incident.id != identifier or row.get("got") != incident.state.value:
        raise ValueError("Summary identity/outcome does not match its history record")
    calls = raw.get("usage", {}).get("calls", [])
    if not calls:
        raise ValueError("A comparison requires recorded model calls for both incidents")
    totals = {"model_calls": len(calls), "tokens": 0, "cost_usd": 0, "seconds": number(row["seconds"])}
    for call in calls:
        totals["tokens"] += number(call["prompt_tokens"], integer=True) + number(call["completion_tokens"], integer=True)
        totals["cost_usd"] += number(call["cost_usd"])
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    for key in ("model_calls", "tokens", "cost_usd"):
        expected = number(row[key], integer=key != "cost_usd")
        if not math.isclose(expected, totals[key], rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"Summary {key} disagrees with recorded usage")
    scripted = any(re.search(r"fake|scripted|fixture", call.model, re.I) for call in incident.usage.calls)
    return incident, totals, scripted


def accepted(path, incident, scenario):
    if path is None:
        return False
    result = read_json(path)
    # Match the portable incident filename; archived runs may move between computers.
    bound_name = str(result.get("incident", "")).replace("\\", "/").rsplit("/", 1)[-1]
    allowed = {scenario, PAIR[0]}
    if bound_name != f"{incident.id}.json" or result.get("scenario") not in allowed:
        raise ValueError("Independent acceptance is bound to another incident or scenario")
    checks, candidate = result.get("checks", {}), result.get("runs", {}).get("candidate", {})
    return bool(result.get("ok") is True and result.get("status") == "accepted"
                and result.get("runner") == "docker" and checks and all(value is True for value in checks.values())
                and candidate.get("commit") == incident.alert.commit and candidate.get("status") == "passed"
                and candidate.get("exit_code") == 0 and candidate.get("patch_applied") is True
                and candidate.get("passed") and not candidate.get("failures"))


def analyze(run: Path, first_acceptance=None, repeat_acceptance=None) -> dict:
    run = Path(run).resolve()
    summary = read_json(contained(run, "summary.json"))
    if not isinstance(summary, list) or any(not isinstance(row, dict) for row in summary):
        raise ValueError("Saved summary must be an array of scenario records")
    rows = []
    for scenario in PAIR:
        matches = [row for row in summary if row.get("scenario") == scenario]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one summary row for {scenario}")
        rows.append(matches[0])
    first, before, first_scripted = load_entry(run, rows[0])
    repeat, after, repeat_scripted = load_entry(run, rows[1])
    if first.id == repeat.id or (first.alert.commit, first.alert.request, first.alert.signature()) != (
            repeat.alert.commit, repeat.alert.request, repeat.alert.signature()):
        raise ValueError("The pair must have distinct IDs and identical commit, request and failure signature")
    if first.similar_incidents or any(item.kind == "memory" for item in first.evidence):
        raise ValueError("The first incident is not a cold run")
    if (first.id not in repeat.similar_incidents or not any(
            item.kind == "memory" and item.content.strip() and item.source.split(" ", 1)[0] == first.id
            for item in repeat.evidence)):
        raise ValueError("The repeated incident has no saved memory evidence from the first incident")
    if repeat.created_at < first.updated_at:
        raise ValueError("The repeated incident predates completion of the first incident")
    independent = [accepted(first_acceptance, first, PAIR[0]), accepted(repeat_acceptance, repeat, PAIR[1])]
    verified = all(item.state == IncidentState.verified and not item.error and item.final_patch is not None
                   for item in (first, repeat))
    scripted = first_scripted or repeat_scripted
    quality = verified and all(independent) and not scripted
    reductions = {key: round(100 * (before[key] - after[key]) / before[key], 2) if before[key] else None
                  for key in before} if quality else None
    return {"source": "saved-record analysis", "first_incident": first.id, "repeat_incident": repeat.id,
            "memory_confirmed_in_records": True, "before": before, "after": after,
            "models": [sorted({call.model for call in item.usage.calls}) for item in (first, repeat)],
            "runners": [row.get("runner") for row in rows],
            "saved_outcomes": [first.state.value, repeat.state.value], "scripted_fixture": scripted,
            "independent_acceptance": independent, "quality_confirmed_in_records": quality,
            "observed_reduction_percent": reductions, "live_api_proven": False,
            "limitation": "All recorded calls include retries and failed attempts. A single saved pair does not establish a causal memory improvement or prove API provenance."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--first-acceptance", type=Path)
    parser.add_argument("--repeat-acceptance", type=Path)
    parser.add_argument("--output", type=Path, help="Optional new JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.run, args.first_acceptance, args.repeat_acceptance)
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(payload)
        print(payload, end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
