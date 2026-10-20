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
from nightwatch.pipeline import Pipeline  # noqa: E402
from nightwatch.schemas import Alert, IncidentState  # noqa: E402

# Scenario name → the outcome a correct agent must reach.
EXPECTED = {
    "empty-sample": IncidentState.verified,  # real, reproducible bug → verified patch
    "insufficient-evidence": IncidentState.reported,  # no evidence → report, never an invented fix
}


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
    ap.add_argument("--scenario", choices=sorted(EXPECTED), help="run only this scenario")
    ap.add_argument("--output", type=Path, help="new directory for this run (default: .demo-runs/<time>)")
    ap.add_argument("--check-only", action="store_true", help="only check the key and model id")
    args = ap.parse_args()

    output = (args.output or ROOT / ".demo-runs" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    os.environ.setdefault("NIGHTWATCH_DEBUG_DIR", str(output / "model-calls"))
    settings = Settings()
    settings.history_dir = output / "history"

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

    pipeline = Pipeline(client, LocalRunner(repo), verifier, RepoView(repo), HistoryStore(settings.history_dir),
                        settings)
    names = [args.scenario] if args.scenario else list(EXPECTED)
    results = []
    for name in names:
        alert = Alert.model_validate_json((Path(demo["alerts"]) / f"{name}.json").read_text(encoding="utf-8"))
        print(f"=== {name}: {alert.title}")
        started = time.time()
        inc = pipeline.run(alert)
        for e in inc.events:
            print(f"  {e.at:%H:%M:%S}  {e.state.value:<13} {e.message[:150]}")
        ok = inc.state == EXPECTED[name]
        results.append({"scenario": name, "expected": EXPECTED[name].value, "got": inc.state.value, "ok": ok,
                        "cost_usd": inc.usage.cost_usd, "tokens": inc.usage.tokens,
                        "model_calls": len(inc.usage.calls), "patch_attempts": len(inc.attempts),
                        "seconds": round(time.time() - started, 1), "incident": inc.id})
        if inc.final_patch:
            print("\n  Verified patch:\n" + "\n".join("    " + ln for ln in inc.final_patch.diff.splitlines()))
        if inc.report:
            print("\n  Report next steps: " + "; ".join(inc.report.next_steps[:3]))
        if inc.error:
            print(f"\n  Nightwatch error: {inc.error.splitlines()[0]}")
        print(f"  -> {'PASS' if ok else 'FAIL'}: expected {EXPECTED[name].value}, got {inc.state.value}\n")

    (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{'Scenario':<24}{'Result':<8}{'State':<11}{'Calls':>6}{'Tokens':>9}{'Cost $':>9}{'Sec':>7}")
    for r in results:
        print(f"{r['scenario']:<24}{'PASS' if r['ok'] else 'FAIL':<8}{r['got']:<11}{r['model_calls']:>6}"
              f"{r['tokens']:>9}{r['cost_usd']:>9.4f}{r['seconds']:>7}")
    print(f"\nSummary: {output / 'summary.json'}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
