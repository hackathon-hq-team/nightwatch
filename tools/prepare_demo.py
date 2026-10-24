"""Create a fresh demo Git repository and Alert-compatible incidents, without API calls."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from evaluation.catalog import CASES, replace_once

ROOT = Path(__file__).resolve().parents[1]
RETURN_LINE = "    return sum(items) / len(items)\n"
EMPTY_GUARD = "    if not items:\n        return 0.0\n"


def git(repo: Path, *args: str) -> str:
    # Argument lists, never shell strings: scenario contents cannot become commands.
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    return result.stdout.strip()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def observe(repo: Path, call: str, exception_type: str) -> dict:
    """Capture a real failure from a fixed, trusted fixture (never model code)."""
    capture = '''import json, traceback
from pathlib import Path
import sys
root = Path.cwd()
try:
    exec(compile(CALL, "<scenario>", "exec"))
except Exception as error:
    if type(error).__name__ != EXPECTED_EXCEPTION:
        raise
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        if frame.filename.startswith("<"):
            continue
        path = Path(frame.filename).resolve()
        in_app = path.is_relative_to(root)
        name = path.relative_to(root).as_posix() if in_app else path.as_posix()
        if not in_app and "/site-packages/" in name:
            name = "site-packages/" + name.split("/site-packages/", 1)[1]
        frames.append({"file": name, "function": frame.name, "line": frame.lineno, "in_app": in_app})
    logs = "".join(traceback.format_exception(error))
    logs = logs.replace(str(root), ".").replace(str(Path(sys.prefix)), "[environment]")
    print(json.dumps({"exception_type": type(error).__name__, "message": str(error),
                      "stacktrace": frames, "logs": logs}))
else:
    raise SystemExit("Expected scenario failure was not observed")
'''
    script = f"CALL = {call!r}\nEXPECTED_EXCEPTION = {exception_type!r}\n" + capture
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=repo, check=True,
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    return json.loads(result.stdout)


def prepare_complex(repo: Path, alerts: Path, base: str, cases=None) -> dict:
    """Keep reference repairs off the runtime commit's ancestry and prompt diff."""
    scenarios = {}
    for case in CASES if cases is None else cases:
        slug, app_file = case["slug"], case["app_file"]
        git(repo, "checkout", "-qb", f"fixture/{slug}", base)
        app = repo / app_file
        broken_source = app.read_text(encoding="utf-8")
        # Only explicitly declared presentation changes enter the decoy's deployment
        # diff. Reference repairs never become runtime ancestry or prompt evidence.
        for path, replacements in case.get("decoy_changes", {}).items():
            target = repo / path
            write(target, replace_once(target.read_text(encoding="utf-8"), replacements))
            git(repo, "add", path)
        git(repo, "commit", "--allow-empty", "-qm", f"fixture: capture {slug} incident")
        broken = git(repo, "rev-parse", "HEAD")
        observed = observe(repo, case["call"], case["exception_type"])
        git(repo, "checkout", "-qb", f"fixture/{slug}-oracle")
        write(app, replace_once(broken_source, case["good_replacements"]))
        git(repo, "add", app_file)
        git(repo, "commit", "-qm", f"fixture: correct {slug} behavior")
        good = git(repo, "rev-parse", "HEAD")
        alert = {
            "source": "scenario", "title": case["title"], "expected": "verified",
            **observed, "request": case["request"],
            # The golden reference is evaluation metadata, not runtime evidence.
            "commit": broken,
        }
        write(alerts / f"{slug}.json", json.dumps(alert, indent=2) + "\n")
        scenarios[slug] = {"good_commit": good, "broken_commit": broken, "app_file": app_file}
    git(repo, "checkout", "-q", "main")
    return scenarios


def require_diverse_dependencies() -> None:
    """Fail before creating a partial workspace when optional HTTP tools are absent."""
    from importlib.util import find_spec

    missing = [name for name in ("fastapi", "httpx") if find_spec(name) is None]
    if missing:
        raise RuntimeError("The diverse suite requires " + ", ".join(missing)
                           + "; install with python -m pip install -r diverse-app/requirements.txt")


def prepare(output: Path, suite: str = "standard") -> dict:
    if suite not in {"standard", "diverse"}:
        raise ValueError(f"Unknown scenario suite: {suite}")
    cases = CASES
    if suite == "diverse":
        require_diverse_dependencies()
        from evaluation.diverse_catalog import CASES as diverse_cases
        cases = diverse_cases
    output = output.resolve()
    # Never overwrite a teammate's repository or an earlier run's evidence.
    output.mkdir(parents=True, exist_ok=False)
    repo = output / "repo"
    shutil.copytree(ROOT / "demo-app", repo,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    if suite == "diverse":
        shutil.copytree(ROOT / "diverse-app", repo, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    calc = repo / "app/calc.py"
    broken_source = calc.read_text(encoding="utf-8")
    if broken_source.count(RETURN_LINE) != 1:
        raise ValueError("Demo source changed: review preparation and expected behaviour together")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "user.name", "Nightwatch demo fixture")
    git(repo, "config", "user.email", "demo@example.invalid")
    # A known-good historical version is deliberate fixture data, not a model fix.
    write(calc, broken_source.replace(RETURN_LINE, EMPTY_GUARD + RETURN_LINE))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture: working empty-input behaviour")
    good = git(repo, "rev-parse", "HEAD")
    write(calc, broken_source)
    git(repo, "add", "app/calc.py")
    git(repo, "commit", "-qm", "fixture: remove empty-input guard")
    broken = git(repo, "rev-parse", "HEAD")

    # Run only our trusted demo source to capture the real exception and traceback.
    capture = '''import json, traceback
from app.calc import average
try:
    average([])
except ZeroDivisionError as error:
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps({"exception_type": type(error).__name__, "message": str(error),
        "line": frame.lineno, "function": frame.name,
        "logs": "".join(traceback.format_exception(error))}))
else:
    raise SystemExit("Expected demo bug was not observed")
'''
    observed = json.loads(subprocess.run(
        [sys.executable, "-B", "-c", capture], cwd=repo, check=True,
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    ).stdout)
    alerts = output / "alerts"
    alerts.mkdir()
    empty = {
        "source": "scenario", "title": "Empty measurements crash average()", "expected": "verified",
        "exception_type": observed["exception_type"], "message": observed["message"],
        "stacktrace": [{"file": "app/calc.py", "function": observed["function"], "line": observed["line"]}],
        "request": {"samples_ms": []}, "logs": observed["logs"],
        "commit": broken, "previous_good_commit": good,
    }
    insufficient = {
        "source": "scenario", "title": "Unconfirmed memory failure under production load", "expected": "reported",
        "exception_type": "MemoryError", "message": "allocation failed under load",
        "stacktrace": [], "commit": broken,
        # This is synthetic incident text, not a fabricated execution trace.
        "logs": "SYNTHETIC SCENARIO: report of memory pressure; no request, stack trace, load profile or dataset supplied.",
    }
    for name, alert in [("empty-sample", empty), ("insufficient-evidence", insufficient)]:
        write(alerts / f"{name}.json", json.dumps(alert, ensure_ascii=False, indent=2) + "\n")
    scenarios = prepare_complex(repo, alerts, broken, cases)
    if suite == "diverse":
        from evaluation.diverse_catalog import REPEAT_OF, REPEAT_SLUG
        from evaluation.refusal_cases import REFUSALS

        for case in REFUSALS:
            alert = {key: value for key, value in case.items() if key != "slug"}
            alert.update(source="scenario", commit=broken, stacktrace=[])
            write(alerts / f"{case['slug']}.json", json.dumps(alert, indent=2) + "\n")
        # Same broken snapshot, fresh incident ID at runtime. Patches execute only
        # in disposable copies, so this is a real recurrence, not an already-fixed app.
        repeat = json.loads((alerts / f"{REPEAT_OF}.json").read_text(encoding="utf-8"))
        repeat["title"] = "Repeated incident: " + repeat["title"]
        write(alerts / f"{REPEAT_SLUG}.json", json.dumps(repeat, indent=2) + "\n")
        scenarios[REPEAT_SLUG] = {**scenarios[REPEAT_OF], "repeat_of": REPEAT_OF}
    manifest = {"mode": "fixture", "suite": suite, "repo": str(repo), "good_commit": good, "broken_commit": broken,
                "alerts": str(alerts), "scenarios": scenarios}
    write(output / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; must not already exist")
    parser.add_argument("--suite", choices=["standard", "diverse"], default="standard",
                        help="standard: existing seven; diverse: seventeen checkpoint scenarios")
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, suite=args.suite), indent=2))


if __name__ == "__main__":
    main()
