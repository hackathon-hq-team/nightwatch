"""First live run of the Nightwatch agent on Danik's demo scenarios.

What it does:
1. checks the API key and that the configured Nemotron model exists in Token Factory;
2. creates a fresh demo repository with tools.prepare_demo (good + broken commit, alerts);
3. runs the full pipeline on every scenario with the real model, LocalRunner and the
   team verifier (verifier.py) — falls back to ReferenceVerifier if it is not merged yet;
4. compares the outcome with the expected one and prints a summary.

Every prompt and raw model reply is saved under <output>/model-calls/ for prompt tuning.

Usage (from the repository root):
    python scripts/run_demo.py                          # all scenarios
    python scripts/run_demo.py --scenario empty-sample  # one scenario
    python scripts/run_demo.py --check-only             # only check key and model id
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nightwatch.config import Settings  # noqa: E402
from nightwatch.context import RepoView  # noqa: E402
from nightwatch.dev.local_runner import LocalRunner  # noqa: E402
from nightwatch.history import HistoryStore  # noqa: E402
from nightwatch.model_client import ModelClient, ModelError  # noqa: E402
from nightwatch.pipeline import Pipeline, REPORT_FALLBACK_PREFIX  # noqa: E402
from nightwatch.schemas import Alert, Incident, IncidentState  # noqa: E402

# Scenario name → the outcome a correct agent must reach. A scenario file can also declare it
# itself with a top-level "expected": "verified" | "reported" field (ignored by the Alert model).
EXPECTED = {
    "empty-sample": IncidentState.verified,  # real, reproducible bug → verified patch
    "insufficient-evidence": IncidentState.reported,  # no evidence → report, never an invented fix
}


def expected_outcome(name: str, scenario: dict) -> IncidentState | None:
    """Expected outcome from the scenario file, else from EXPECTED; None — not declared."""
    declared = scenario.get("expected")
    if declared:
        return IncidentState(declared)
    return EXPECTED.get(name)


def discover(alerts_dir: Path) -> list[str]:
    """All scenario files: known ones first in EXPECTED order, then the rest alphabetically."""
    found = sorted(p.stem for p in alerts_dir.glob("*.json"))
    return [n for n in EXPECTED if n in found] + [n for n in found if n not in EXPECTED]


def assess_result(name: str, incident: Incident, expected: IncidentState | None = None) -> tuple[bool, str]:
    """A report is successful acceptance only when it represents a completed refusal."""
    expected = expected or EXPECTED[name]
    if incident.state != expected:
        return False, f"expected {expected.value}, got {incident.state.value}"
    if incident.error:
        return False, "incident contains a Nightwatch error"
    if expected != IncidentState.reported:
        return True, f"expected {expected.value} outcome"

    # The pipeline also uses reported for budget stops; those are incomplete evaluations.
    if any(event.message.startswith("Stopped by budget:") for event in incident.events):
        return False, "budget exhausted before evaluation completed"
    if any(event.message.startswith(REPORT_FALLBACK_PREFIX) for event in incident.events):
        return False, "diagnostic report call failed; fallback is not a completed evaluation"
    if incident.attempts or incident.final_patch is not None:
        return False, "insufficient evidence must not trigger patch generation"
    if incident.investigation is None:
        return False, "no completed investigation"
    if incident.report is None or not incident.report.facts or not incident.report.next_steps:
        return False, "missing diagnostic facts or next steps"

    investigation = incident.investigation
    if investigation.repro_test is None:
        if not investigation.not_reproducible_reason.strip():
            return False, "missing explanation of insufficient evidence"
    else:
        verdict = incident.repro_verdict
        broken = incident.repro_broken
        if verdict is None or verdict.ok or broken is None:
            return False, "no evidence that the proposed reproduction was rejected"
        # Import failures, timeouts and missing tests are tool failures, not a correct refusal.
        results = [broken] + ([incident.repro_good] if incident.repro_good is not None else [])
        if any((run.status, run.exit_code) not in (("passed", 0), ("failed", 1)) for run in results):
            return False, "runner failure prevented evaluation"
    return True, "completed investigation reported insufficient evidence without requesting a patch"


def check_model(client: ModelClient) -> bool:
    try:
        ids = client.list_models()
    except Exception as e:  # wrong key, network, endpoint
        print(f"FAIL  cannot list models: {type(e).__name__}: {e}")
        return False
    nemotron = sorted(m for m in ids if "nemotron" in m.lower())
    print("Nemotron models available:")
    for m in nemotron:
        print(f"  {m}{'   <- configured' if m == client.model else ''}")
    if client.model not in ids:
        print(f"FAIL  {client.model} is not available. Set NIGHTWATCH_MODEL in .env to one of the ids above.")
        return False
    print(f"OK    using {client.model}")
    escalation = client.s.escalation_model
    if escalation and escalation not in ids:
        print(f"WARN  escalation model {escalation} is not available — escalation disabled. "
              "Set NIGHTWATCH_ESCALATION_MODEL to one of the ids above, or leave it empty.")
        client.s.escalation_model = ""
    elif escalation:
        print(f"OK    escalation to {escalation}")
    triage = client.s.triage_model
    if triage and triage not in ids:
        print(f"WARN  triage model {triage} is not available — triage disabled.")
        client.s.triage_model = ""
    elif triage:
        print(f"OK    triage with {triage}")
    return True


def load_verifier():
    try:
        from verifier import Verifier  # Danik's module (PR #5)
        return Verifier(), "team verifier.py"
    except ImportError:
        from nightwatch.dev.reference_verifier import ReferenceVerifier
        return ReferenceVerifier(), "ReferenceVerifier (verifier.py not merged yet)"


def prepare(output: Path) -> dict:
    try:
        from tools.prepare_demo import prepare as prepare_demo  # Danik's generator (PR #5)
    except ImportError:
        sys.exit("tools/prepare_demo.py not found — merge Danik's PR #5 first.")
    return prepare_demo(output)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", help="run only this scenario (file name in the alerts folder, without .json)")
    ap.add_argument("--output", type=Path, help="new directory for this run (default: .demo-runs/<time>)")
    ap.add_argument("--check-only", action="store_true", help="only check the key and model id")
    args = ap.parse_args()

    output = (args.output or ROOT / ".demo-runs" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    os.environ.setdefault("NIGHTWATCH_DEBUG_DIR", str(output / "model-calls"))
    settings = Settings()
    # Keep the configured shared budget location across runs. Incident artifacts still belong
    # to each output directory, but ModelClient's DailyBudget must not reset with --output.
    settings.history_dir = settings.history_dir.resolve()

    try:
        client = ModelClient(settings)
    except ModelError as e:
        print(f"FAIL  {e}")
        return 2
    if not check_model(client):
        return 2
    if args.check_only:
        return 0

    demo = prepare(output / "demo")
    repo = Path(demo["repo"])
    verifier, verifier_name = load_verifier()
    print(f"\nDemo repository: {repo}\nVerifier: {verifier_name}\nModel calls: {settings.debug_dir}\n")

    pipeline = Pipeline(client, LocalRunner(repo), verifier, RepoView(repo), HistoryStore(output / "history"),
                        settings)
    alerts_dir = Path(demo["alerts"])
    available = discover(alerts_dir)
    if args.scenario and args.scenario not in available:
        print(f"FAIL  unknown scenario {args.scenario!r}; available: {', '.join(available)}")
        return 2
    names = [args.scenario] if args.scenario else available
    results = []
    for name in names:
        raw = (alerts_dir / f"{name}.json").read_text(encoding="utf-8")
        alert = Alert.model_validate_json(raw)
        expected = expected_outcome(name, json.loads(raw))
        print(f"=== {name}: {alert.title}")
        started = time.time()
        inc = pipeline.run(alert)
        for e in inc.events:
            print(f"  {e.at:%H:%M:%S}  {e.state.value:<13} {e.message[:150]}")
        if expected is None:
            ok, acceptance_reason = None, 'no expected outcome declared — add "expected" to the scenario file'
        else:
            ok, acceptance_reason = assess_result(name, inc, expected)
        results.append({"scenario": name, "expected": expected.value if expected else None,
                        "got": inc.state.value, "ok": ok,
                        "acceptance_reason": acceptance_reason,
                        "cost_usd": inc.usage.cost_usd, "tokens": inc.usage.tokens,
                        "model_calls": len(inc.usage.calls), "patch_attempts": len(inc.attempts),
                        "models": sorted({c.model for c in inc.usage.calls}),
                        "seconds": round(time.time() - started, 1), "incident": inc.id})
        if inc.final_patch:
            print("\n  Verified patch:\n" + "\n".join("    " + ln for ln in inc.final_patch.diff.splitlines()))
        if inc.report:
            print("\n  Report next steps: " + "; ".join(inc.report.next_steps[:3]))
        if inc.error:
            print(f"\n  Nightwatch error: {inc.error.splitlines()[0]}")
        print(f"  -> {_verdict(ok)}: {acceptance_reason}\n")

    (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{'Scenario':<24}{'Result':<8}{'State':<11}{'Calls':>6}{'Tokens':>9}{'Cost $':>9}{'Sec':>7}")
    for r in results:
        print(f"{r['scenario']:<24}{_verdict(r['ok']):<8}{r['got']:<11}{r['model_calls']:>6}"
              f"{r['tokens']:>9}{r['cost_usd']:>9.4f}{r['seconds']:>7}")
    print(f"\nSummary: {output / 'summary.json'}")
    # Scenarios without a declared expectation are shown but do not decide the exit code.
    return 0 if all(r["ok"] is not False for r in results) else 1


def _verdict(ok: bool | None) -> str:
    return "N/A" if ok is None else "PASS" if ok else "FAIL"


if __name__ == "__main__":
    sys.exit(main())
