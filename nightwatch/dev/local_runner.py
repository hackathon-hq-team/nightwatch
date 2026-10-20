"""Temporary runner for development: pytest locally, without Docker.

Does NOT isolate network or files — only for debugging prompts on the demo project.
The real runner.py (Timur) does the same in a throwaway container with
--network none. Same interface (contracts.Runner).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..schemas import RunResult, TestFailure

PLUGIN_ROOT = str(Path(__file__).resolve().parents[2])  # so pytest can find nightwatch.pytest_plugin


def apply_patch(workdir: Path, diff: str) -> tuple[bool, str]:
    """git apply with line recount; fallback — patch with fuzz."""
    patch_file = workdir / ".nightwatch.patch"
    patch_file.write_text(diff if diff.endswith("\n") else diff + "\n", newline="\n")
    tries = [
        ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_file)],
        ["patch", "-p1", "--forward", "--fuzz=3", "-i", str(patch_file)],
    ]
    errors = []
    for cmd in tries:
        if shutil.which(cmd[0]) is None:
            continue
        res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
        if res.returncode == 0:
            patch_file.unlink(missing_ok=True)
            return True, ""
        errors.append(f"{' '.join(cmd[:2])}: {res.stderr.strip() or res.stdout.strip()}")
    patch_file.unlink(missing_ok=True)
    return False, "\n".join(errors)


def hash_tests(root: Path) -> str:
    h = hashlib.sha256()
    tests = root / "tests"
    if tests.exists():
        for p in sorted(tests.rglob("*.py")):
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class LocalRunner:
    def __init__(self, repo_path: Path | str, timeout_s: int = 120, python: str = sys.executable):
        self.repo = Path(repo_path).resolve()
        self.timeout = timeout_s
        self.python = python

    def _checkout(self, commit: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="nightwatch-"))
        archive = subprocess.run(["git", "-C", str(self.repo), "archive", commit], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive.stdout, check=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)  # needed for git apply
        return tmp

    @staticmethod
    def _write(root: Path, files: dict[str, str]) -> None:
        for rel, code in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(code)
            init = p.parent / "__init__.py"
            if p.parent.name == "nightwatch" and not init.exists():
                init.write_text("")

    def tests_hash(self, commit: str, test_files: dict[str, str]) -> str:
        root = self._checkout(commit)
        try:
            self._write(root, test_files)
            return hash_tests(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def run(self, commit: str, test_files: dict[str, str], patch: str | None = None,
            select: list[str] | None = None) -> RunResult:
        started = time.time()
        root = self._checkout(commit)
        try:
            self._write(root, test_files)
            applied = None
            if patch is not None:
                applied, err = apply_patch(root, patch)
                if not applied:
                    return RunResult(commit=commit, status="error", exit_code=-1, patch_applied=False,
                                     output_tail=f"Patch did not apply:\n{err}"[-3000:],
                                     duration_s=round(time.time() - started, 2))
            report = root / ".nightwatch-report.json"
            env = {**os.environ, "NIGHTWATCH_REPORT": str(report),
                   "PYTHONPATH": os.pathsep.join([str(root), PLUGIN_ROOT]), "PYTHONDONTWRITEBYTECODE": "1"}
            env.pop("NEBIUS_API_KEY", None)  # tests must not see keys
            cmd = [self.python, "-m", "pytest", "-q", "-p", "nightwatch.pytest_plugin", "-p", "no:cacheprovider",
                   *(select or [])]
            try:
                res = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                return RunResult(commit=commit, status="timeout", exit_code=-1, patch_applied=applied,
                                 output_tail=str(e.stdout or "")[-3000:], duration_s=self.timeout)
            data = json.loads(report.read_text()) if report.exists() else {"passed": [], "failures": []}
            failures = [TestFailure(**f) for f in data["failures"]]
            if res.returncode == 0:
                status = "passed"
            elif res.returncode == 1:
                status = "failed"
            else:  # 2–5: collection error, no tests, etc.
                status = "error"
            return RunResult(commit=commit, status=status, exit_code=res.returncode, passed=data["passed"],
                             failures=failures, tests_hash=hash_tests(root), patch_applied=applied,
                             output_tail=(res.stdout + res.stderr)[-3000:],
                             duration_s=round(time.time() - started, 2))
        finally:
            shutil.rmtree(root, ignore_errors=True)
