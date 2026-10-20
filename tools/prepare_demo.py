"""Create a fresh demo Git repository and Alert-compatible incidents, without API calls."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

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


def prepare(output: Path) -> dict:
    output = output.resolve()
    # Never overwrite a teammate's repository or an earlier run's evidence.
    output.mkdir(parents=True, exist_ok=False)
    repo = output / "repo"
    shutil.copytree(ROOT / "demo-app", repo)
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
        "source": "scenario", "title": "Empty measurements crash average()",
        "exception_type": observed["exception_type"], "message": observed["message"],
        "stacktrace": [{"file": "app/calc.py", "function": observed["function"], "line": observed["line"]}],
        "request": {"samples_ms": []}, "logs": observed["logs"],
        "commit": broken, "previous_good_commit": good,
    }
    insufficient = {
        "source": "scenario", "title": "Unconfirmed memory failure under production load",
        "exception_type": "MemoryError", "message": "allocation failed under load",
        "stacktrace": [], "commit": broken,
        # This is synthetic incident text, not a fabricated execution trace.
        "logs": "SYNTHETIC SCENARIO: report of memory pressure; no request, stack trace, load profile or dataset supplied.",
    }
    for name, alert in [("empty-sample", empty), ("insufficient-evidence", insufficient)]:
        write(alerts / f"{name}.json", json.dumps(alert, ensure_ascii=False, indent=2) + "\n")
    manifest = {"mode": "fixture", "repo": str(repo), "good_commit": good, "broken_commit": broken,
                "alerts": str(alerts)}
    write(output / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; must not already exist")
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), indent=2))


if __name__ == "__main__":
    main()
