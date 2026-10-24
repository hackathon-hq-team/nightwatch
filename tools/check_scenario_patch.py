"""Evaluate a saved model patch against independent scenario acceptance tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from evaluation.catalog import ALL_CASES as CASES, SUITES, baseline_count
from nightwatch.schemas import Alert, Incident, IncidentState, ReproTest
from verifier import Verifier

REPRO_PATH = "tests/nightwatch/test_acceptance_reproducer.py"
ORACLE_PATH = "tests/nightwatch/test_acceptance_oracles.py"
BASELINE_COUNT = 4 + sum(case["baseline_count"] for case in SUITES["standard"])


def validate_patch_scope(patch: str, app_file: str) -> None:
    """Accept only text hunks for the exact existing scenario application file.

    This deliberately conservative format excludes renames, modes, binary patches,
    new/deleted files, quoted paths and extra file sections before running anything.
    """
    if not patch or "\x00" in patch or "\r" in patch:
        raise ValueError("Patch must be a nonempty LF-delimited text diff")
    lines = patch.strip("\n").splitlines()
    index = 0
    if lines and lines[0].startswith("diff "):
        if lines[0] != f"diff --git a/{app_file} b/{app_file}":
            raise ValueError("Patch diff header must name only the scenario application file")
        index += 1
    if index < len(lines) and lines[index].startswith("index "):
        if not re.fullmatch(r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: 100644)?", lines[index]):
            raise ValueError("Unsupported patch index metadata")
        index += 1
    if lines[index:index + 2] != [f"--- a/{app_file}", f"+++ b/{app_file}"]:
        raise ValueError("Patch file headers must name only the scenario application file")
    index += 2
    has_hunk = has_change = False
    for line in lines[index:]:
        if line.startswith("@@"):
            if not re.fullmatch(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*", line):
                raise ValueError("Malformed patch hunk header")
            has_hunk = True
        elif not has_hunk or not line or line.startswith(("--- ", "+++ ")):
            raise ValueError("Unexpected patch metadata or additional file headers")
        elif line == "\\ No newline at end of file":
            continue
        elif line[0] not in " +-":
            raise ValueError("Only text hunk content is permitted")
        elif line[0] in "+-":
            has_change = True
    if not has_hunk or not has_change:
        raise ValueError("Patch contains no application change")


def _green(run, commit: str) -> bool:
    return bool(run.commit == commit and run.status == "passed" and run.exit_code == 0
                and not run.failures and run.passed and len(run.passed) == len(set(run.passed)))


def evaluate(case: dict, alert: Alert, patch: str, runner) -> dict:
    """Run trusted oracles separately from the model's claimed verified outcome.

    Callers must use the Docker runner for model output. The CLI only selects the
    local runner after an explicit declaration that the patch is a trusted fixture.
    """
    result = {"scenario": case["slug"], "ok": False, "status": "rejected",
              "reason": "Acceptance checks have not completed", "checks": {}, "runs": {}}
    checks = result["checks"]

    def record(name, run):
        result["runs"][name] = run.model_dump(mode="json")
        return run

    def stop(reason):
        result["reason"] = reason
        return result

    try:
        validate_patch_scope(patch, case["app_file"])
        checks["patch_scope_allowed"] = True
        if not alert.previous_good_commit or alert.exception_type != case["exception_type"]:
            return stop("Scenario requires matching failure type and previous-good evidence")
        test = ReproTest(path=REPRO_PATH, code=case["reproducer"], expected=alert.signature())
        files = {test.path: test.code, ORACLE_PATH: case["acceptance"]}
        baseline_hash = runner.tests_hash(alert.commit, {})
        frozen_hash = runner.tests_hash(alert.commit, files)
        good_hash = runner.tests_hash(alert.previous_good_commit, files)
        checks["frozen_test_hashes"] = bool(
            re.fullmatch(r"[0-9a-f]{64}", baseline_hash)
            and re.fullmatch(r"[0-9a-f]{64}", frozen_hash) and frozen_hash == good_hash
        )
        if not checks["frozen_test_hashes"]:
            return stop("Missing test digests or original tests differ between fixture commits")
        baseline = record("baseline", runner.run(alert.commit, {}, select=["tests"]))
        checks["baseline_green"] = bool(
            _green(baseline, alert.commit) and baseline.tests_hash == baseline_hash
            and len(baseline.passed) == baseline_count(case) and baseline.patch_applied is None
        )
        if not checks["baseline_green"]:
            return stop("Broken-version baseline did not pass every expected original test")
        broken = record("reproduction", runner.run(alert.commit, files, select=[test.path]))
        golden = record("known_good", runner.run(alert.previous_good_commit, files, select=["tests"]))
        verifier = Verifier()
        reproduction = verifier.check_reproduction(alert, test, broken, golden)
        checks["target_reproduced"] = bool(
            reproduction.ok and broken.tests_hash == frozen_hash
            and broken.patch_applied is None and not broken.passed
            and len(broken.failures) == 1
        )
        expected_count = baseline_count(case) + 1 + case["acceptance_count"]
        checks["known_good_oracles_green"] = bool(
            _green(golden, alert.previous_good_commit) and golden.tests_hash == frozen_hash
            and golden.patch_applied is None and len(golden.passed) == expected_count
            and set(baseline.passed).issubset(golden.passed)
            and sum(node.startswith(REPRO_PATH + "::") for node in golden.passed) == 1
            and sum(node.startswith(ORACLE_PATH + "::") for node in golden.passed) == case["acceptance_count"]
        )
        if not checks["target_reproduced"] or not checks["known_good_oracles_green"]:
            return stop("Trusted reproducer or known-good acceptance evidence was not confirmed")
        after = record("candidate", runner.run(alert.commit, files, patch=patch, select=["tests"]))
        verdict = verifier.check_patch(alert, test, after, frozen_hash)
        checks.update(verdict.checks)
        checks["exact_test_set"] = bool(
            len(after.passed) == len(set(after.passed)) and set(after.passed) == set(golden.passed)
        )
        result["ok"] = all(checks.values())
        result["status"] = "accepted" if result["ok"] else "rejected"
        return stop("Independent acceptance passed" if result["ok"] else
                    "Failed acceptance checks: " + ", ".join(name for name, passed in checks.items() if not passed))
    except Exception as error:
        # Runner/setup exceptions are incomplete evidence, never successful acceptance.
        result["status"] = "error"
        return stop(f"{type(error).__name__}: {error}")


def load_inputs(workspace: Path, scenario: str, incident_path: Path):
    """Bind a saved Incident to one generated scenario before executing its patch."""
    case = next((item for item in CASES if item["slug"] == scenario), None)
    if case is None:
        raise ValueError("Unknown complex scenario")
    workspace = workspace.resolve()
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["scenarios"][scenario]
    repo = workspace / "repo"
    if manifest.get("mode") != "fixture" or Path(manifest["repo"]).resolve() != repo:
        raise ValueError("Workspace manifest must reference its own generated fixture repository")
    if not (repo / ".git").is_dir() or entry.get("app_file") != case["app_file"]:
        raise ValueError("Workspace repository or application path does not match the scenario")
    if not all(re.fullmatch(r"[0-9a-f]{40}", entry[key]) for key in ("broken_commit", "good_commit")):
        raise ValueError("Workspace must contain full fixture commit IDs")
    alert = Alert.model_validate_json((workspace / "alerts" / f"{scenario}.json").read_text(encoding="utf-8"))
    incident = Incident.model_validate_json(incident_path.read_text(encoding="utf-8"))
    if (alert.commit != entry["broken_commit"] or alert.previous_good_commit is not None
            or incident.alert.commit != alert.commit or incident.alert.previous_good_commit is not None
            or incident.alert.signature() != alert.signature() or incident.alert.request != alert.request):
        raise ValueError("Saved incident does not match the generated scenario and fixture commits")
    if incident.state != IncidentState.verified or incident.error or incident.final_patch is None:
        raise ValueError("Saved incident must contain a verified final patch and no runtime error")
    # The runtime alert omits the oracle commit so model context cannot reveal its
    # exact repair. Only this independent acceptance gate binds the private oracle.
    acceptance_alert = alert.model_copy(update={"previous_good_commit": entry["good_commit"]})
    return case, acceptance_alert, incident.final_patch.diff, repo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path, help="Directory produced by tools.prepare_demo")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--incident", required=True, type=Path, help="Saved Nightwatch Incident JSON")
    parser.add_argument("--output", required=True, type=Path, help="Separate independent acceptance result JSON")
    parser.add_argument("--trusted-local-fixture", action="store_true",
                        help="Use local pytest only for a trusted scripted patch; model patches require Docker")
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() == args.incident.resolve():
            raise ValueError("Acceptance output must not overwrite the saved incident")
        case, alert, patch, repo = load_inputs(args.workspace, args.scenario, args.incident)
        if args.trusted_local_fixture:
            from nightwatch.dev.local_runner import LocalRunner
            runner = LocalRunner(repo)
        else:
            from nightwatch.docker_runner import Runner
            runner = Runner(repo)
        result = evaluate(case, alert, patch, runner)
        result["runner"] = "trusted-local-fixture" if args.trusted_local_fixture else "docker"
        result["incident"] = str(args.incident.resolve())
    except Exception as error:
        result = {"scenario": args.scenario, "ok": False, "status": "error",
                  "reason": f"{type(error).__name__}: {error}", "checks": {}, "runs": {}}
    try:
        if args.output.resolve() == args.incident.resolve():
            raise ValueError("Acceptance output must not overwrite the saved incident")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        result.update(ok=False, status="error", reason=f"Cannot save acceptance result: {error}")
    except ValueError as error:
        result.update(ok=False, status="error", reason=str(error))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else (2 if result["status"] == "error" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
