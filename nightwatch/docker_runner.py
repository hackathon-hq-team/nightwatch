"""Disposable Linux containers implementing contracts.Runner.

Only regular files from git archive enter the container. No host directory,
environment, credentials or Docker socket is mounted into the sandbox.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time
import uuid

from .schemas import RunResult, TestFailure

DEFAULT_IMAGE = "nightwatch-runner:dev"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or any(p in ("..", ".git") for p in path.parts)):
        raise ValueError(f"Unsafe repository path: {value!r}")
    return path.as_posix()


def unpack(data: bytes, *, filter_secrets: bool = False) -> dict[str, bytes]:
    """Read an archive without ever extracting it on the host."""
    files = {}
    size = 0
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            name = safe_path(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"Links and special files are unsupported: {name}")
            size += member.size
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError("Repository snapshot exceeds 64 MiB")
            if filter_secrets:
                parts = PurePosixPath(name).parts
                if any(p in (".aws", ".ssh", ".git") for p in parts):
                    continue
                base = parts[-1].lower()
                if ((base == ".env" or base.startswith(".env.")) and base != ".env.example"
                        or base.endswith((".pem", ".key")) or base in ("id_rsa", "id_ed25519")):
                    continue
            files[name] = archive.extractfile(member).read()
    return files


def pack(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(safe_path(name))
            member.size = len(content)
            member.uid = member.gid = 10001
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def tests_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        if name.startswith("tests/") and name.endswith(".py"):
            digest.update(name.encode("utf-8"))
            digest.update(content)
    return digest.hexdigest()


class Runner:
    def __init__(self, repo_path: Path | str, timeout_s: float = 120,
                 image: str | None = None, memory: str = "512m", cpus: float = 1.0):
        self.repo = Path(repo_path).resolve()
        self.timeout = timeout_s
        self.image = image or os.getenv("NIGHTWATCH_RUNNER_IMAGE", DEFAULT_IMAGE)
        self.memory = memory
        self.cpus = cpus
        self.last_files: dict[str, bytes] | None = None

    @staticmethod
    def _command(args: list[str], *, data: bytes | None = None, timeout: float = 30):
        return subprocess.run(args, input=data, capture_output=True, timeout=timeout)

    @staticmethod
    def _require(result) -> bytes:
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace")[-2000:])
        return result.stdout

    def _snapshot(self, commit: str, test_files: dict[str, str]) -> dict[str, bytes]:
        # Resolve first: an untrusted ref must never become an option to git archive.
        sha = self._require(self._command(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", "--end-of-options", commit + "^{commit}"]
        )).decode().strip()
        files = unpack(self._require(self._command(
            ["git", "-C", str(self.repo), "archive", "--format=tar", sha]
        )), filter_secrets=True)
        for path, code in test_files.items():
            name = safe_path(path)
            if not name.startswith("tests/nightwatch/") or not name.endswith(".py"):
                raise ValueError("Reproduction files must be Python files under tests/nightwatch/")
            if name in files:
                raise ValueError(f"Reproduction would overwrite an existing test: {name}")
            files[name] = code.encode("utf-8")
        if test_files:
            files.setdefault("tests/nightwatch/__init__.py", b"")
        return files

    def tests_hash(self, commit: str, test_files: dict[str, str]) -> str:
        return tests_digest(self._snapshot(commit, test_files))

    def run(self, commit: str, test_files: dict[str, str], patch: str | None = None,
            select: list[str] | None = None) -> RunResult:
        started = time.monotonic()
        self.last_files = None
        name = "nightwatch-" + uuid.uuid4().hex
        created = False
        applied = None
        def result(status, exit_code, **kwargs):
            return RunResult(commit=commit, status=status, exit_code=exit_code, patch_applied=applied,
                             duration_s=round(time.monotonic() - started, 3), **kwargs)
        try:
            files = self._snapshot(commit, test_files)
            for item in select or []:
                if item.startswith("-"):
                    raise ValueError("Test selection must contain paths or nodeids, not pytest options")
                safe_path(item.split("::", 1)[0])
            # tmpfs is discarded with the container; nothing is mounted from the host.
            created = True  # Also clean up if create times out after the daemon accepted it.
            self._require(self._command([
                "docker", "create", "--name", name, "--pull=never", "--network=none",
                "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user=10001:10001", "--memory=" + self.memory, "--memory-swap=" + self.memory,
                "--cpus=" + str(self.cpus), "--pids-limit=128",
                "--tmpfs=/workspace:rw,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700",
                self.image,
            ]))
            self._require(self._command(["docker", "start", name]))
            # Docker's archive-copy endpoint rejects writes to read-only rootfs,
            # including tmpfs on some engines. Extract through the sandbox user
            # instead; the host still never extracts repository content.
            self._require(self._command([
                "docker", "exec", "-i", name, "python", "-c",
                "import sys,tarfile; "
                "tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/workspace',filter='data')",
            ], data=pack(files)))
            if patch is not None:
                patched = self._command(["docker", "exec", "-i", name, "git", "apply", "--recount",
                                         "--ignore-space-change",
                                         "--whitespace=nowarn", "-"], data=(patch.rstrip("\n") + "\n").encode())
                applied = patched.returncode == 0
                if not applied:
                    return result("error", -1, output_tail="Patch did not apply:\n" +
                                  patched.stderr.decode("utf-8", "replace")[-2500:])
            # No shell, no inherited host env, and no target-repository pytest options.
            executed = self._command([
                "docker", "exec", name, "python", "-m", "pytest", "-q", "-o", "addopts=",
                "-p", "nightwatch_pytest_plugin", "-p", "no:cacheprovider", *(select or []),
            ], timeout=self.timeout)
            report = json.loads(self._require(self._command([
                "docker", "exec", name, "python", "-c",
                "from pathlib import Path; print(Path('/tmp/nightwatch-report.json').read_text())",
            ])))
            after = unpack(self._require(self._command([
                "docker", "exec", name, "python", "-c",
                "import sys,tarfile; "
                "archive=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); "
                "archive.add('/workspace',arcname='.'); archive.close()",
            ])))
            status = {0: "passed", 1: "failed"}.get(executed.returncode, "error")
            if status == "passed":
                self.last_files = after
            return result(status, executed.returncode, passed=report["passed"],
                          failures=[TestFailure(**f) for f in report["failures"]], tests_hash=tests_digest(after),
                          output_tail=(executed.stdout + executed.stderr).decode("utf-8", "replace")[-3000:])
        except subprocess.TimeoutExpired:
            return result("timeout", -1, output_tail="Runner command exceeded its timeout")
        except (OSError, RuntimeError, ValueError, tarfile.TarError, KeyError) as error:
            return result("error", -1, output_tail=f"{type(error).__name__}: {error}"[-3000:])
        finally:
            if created:
                # Killing the Docker CLI does not kill container processes. Force-remove
                # the container as well, including after timeout or report errors.
                try:
                    self._command(["docker", "rm", "-f", name], timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    pass
