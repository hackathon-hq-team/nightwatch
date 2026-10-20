# Docker runner (Timur)

`nightwatch.docker_runner.Runner` implements `contracts.Runner`; `runner.py` keeps
the original team import working. CLI selection: `--runner team --verifier team`.
The local/reference CLI defaults remain development options.

## Setup

Start Docker Desktop with Linux containers on Windows, or Docker Engine on Linux.
Check `docker version`: both Client and Server must be present. From the project root:

```powershell
docker build -t nightwatch-runner:dev -f docker/runner/Dockerfile .
$env:NIGHTWATCH_TEST_DOCKER = "1"
.\.venv\Scripts\python.exe -m pytest -q tests/test_docker_runner.py
```

Linux equivalent:

```bash
docker build -t nightwatch-runner:dev -f docker/runner/Dockerfile .
NIGHTWATCH_TEST_DOCKER=1 python -m pytest -q tests/test_docker_runner.py
```

Build requires network access. Execution never pulls images or installs dependencies.
The initial image contains Python, git and pytest for the current calculator demo.
For FastAPI/PostgreSQL projects, prepare a trusted image with their dependencies
before execution and set `NIGHTWATCH_RUNNER_IMAGE` to its local tag. No image is built
from model-provided Dockerfiles. PostgreSQL service integration is a separate step.

## Execution

Each call resolves the requested commit, reads `git archive` without host extraction,
rejects links/special files and traversal paths, filters common credential filenames,
then adds reproduction files under `tests/nightwatch/`. Existing reproduction files
cannot be overwritten. Uncommitted files and Git metadata are never copied.
Filename filtering cannot identify secrets committed inside arbitrary source files.

The container runs as UID 10001 with network disabled, a read-only root filesystem,
all capabilities dropped, no privilege escalation, 512 MiB RAM/swap combined,
one CPU and 128 PIDs. Only `/workspace` and `/tmp` are writable, bounded tmpfs volumes.
No bind mounts, socket mounts or host environment variables are forwarded.
Docker's default seccomp profile remains enabled. The Docker daemon is trusted.

Files enter the writable tmpfs through a tar stream and `docker exec`, as Docker's
archive-copy endpoint may reject writes with a read-only root filesystem.
The patch is applied with `git apply --recount --ignore-space-change` inside the
container; context matching tolerates Windows CRLF without rewriting test bytes. Pytest uses the
bundled reporting plugin. The host reads its JSON and computes test hashes from the
container snapshot after execution. Baseline and final hashes use sorted POSIX paths
and raw bytes of `tests/**/*.py`, including the reproducer. The runner returns actual
failure signatures, nodeids, exit status and output tail using the shared RunResult.

Each Docker operation has a control timeout; pytest has a separate execution timeout
(120 seconds by default). `docker rm -f` runs on success, error and timeout to stop
remaining processes and discard tmpfs. If the daemon becomes unavailable, cleanup
may need operator intervention: inspect `docker ps -a --filter name=nightwatch-`.

The verifier and test hashes provide acceptance checks for cooperative generated
code, not tamper-proof attestation against malicious tests that forge their report.
The existing RunResult contract also lacks a complete collected/skipped inventory.

## Demo

```powershell
python -m tools.prepare_demo --output .demo-workspace
python -m nightwatch.cli run --repo .demo-workspace/repo --alert .demo-workspace/alerts/empty-sample.json --runner team --verifier team
```

The CLI demo uses a live, billable model API. Container integration tests instead use
fixed trusted fixtures and require no API key. Do not infer live model quality from them.

## Validation on 20 October 2026

Image `nightwatch-runner:dev` built successfully on Docker Desktop 4.46.0 / Linux
Engine 28.4.0. Host Python: 3.14; container Python: 3.11.14, pytest: 8.4.2.

All **18 runner tests** passed in the full suite, including six container-backed
checks: reproduction on broken/good commits plus verified patch, sandbox isolation,
timeout cleanup, malformed patch rejection and both pipeline outcomes with the team
verifier (verified fix and report without patch). Model responses are scripted.
No live model API was called. No runner containers remained after the run.

Full command:

```powershell
$env:NIGHTWATCH_TEST_DOCKER = "1"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.qa-results/docker-full-final --junitxml=.qa-results/docker-full-final.xml
```

Result: **105 passed, 1 failed** in 81.65 seconds. The failure was the existing
`test_triage_hint_goes_to_investigation`: Windows denied HistoryStore's atomic file
replacement (`WinError 5`). That test passed on isolated retry (1 passed in 2.40s).
This intermittent history-write issue remains separate from the Docker runner.
