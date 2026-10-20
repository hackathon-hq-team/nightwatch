# Nightwatch agent (Nikita's part)

The model only proposes: a hypothesis, a reproduction test and a patch. The `runner` executes them, the `verifier` decides.

```
alert → context.py → Nemotron Super: hypotheses + test → runner (broken and good version)
      → verifier: reproduced? ── no → diagnostic report
                       └─ yes → Nemotron Super: patch → runner (full suite) → verifier → Draft PR
                                      ↑ up to 3 attempts, the rejection reason feeds the next one
```

## Modules

| File | What it does |
| --- | --- |
| `nightwatch/schemas.py` | Shared models: `Alert`, `Evidence`, `Investigation`, `PatchProposal`, `RunResult`, `Verdict`, `Incident` |
| `nightwatch/contracts.py` | `Runner` (Timur) and `Verifier` (Danik) interfaces |
| `nightwatch/model_client.py` | Nemotron via Token Factory: schema-validated JSON, retry, token accounting, budgets |
| `nightwatch/context.py` | Evidence with ids `E1…`: stack trace, request, logs, diff, files, similar incidents |
| `nightwatch/history.py` | `history/inc-*.json` and similar-incident search (memory) |
| `nightwatch/prompts.py` | Investigation, patch and report prompts |
| `nightwatch/pipeline.py` | Orchestrator and incident states |
| `nightwatch/sentry.py` | Sentry event → `Alert` |
| `nightwatch/pytest_plugin.py` | Writes pytest results with the exception signature to JSON |
| `nightwatch/dev/` | Temporary `LocalRunner` (pytest without Docker) and `ReferenceVerifier` until Timur's and Danik's modules land |

## Contract for Timur (runner)

Implement `contracts.Runner`: `run(commit, test_files, patch, select) -> RunResult` and `tests_hash(...)`.

- copy of the project at `commit`, write `test_files`, then the patch: `git apply --recount`, fallback `patch -p1 --fuzz=3`;
- pytest with the plugin: `NIGHTWATCH_REPORT=/tmp/r.json pytest -p nightwatch.pytest_plugin` — the plugin reports `passed` and `failures` with exception type, message, file and function;
- `--network none`, no keys in the environment, a timeout;
- a patch that does not apply → `status="error", patch_applied=False`; `tests_hash` is sha256 of all `tests/**/*.py` after the patch.

Reference behaviour: `nightwatch/dev/local_runner.py`.

## Contract for Danik (verifier)

Implement `contracts.Verifier`: `check_reproduction(...)` and `check_patch(...)` → `Verdict(ok, reason, checks)`. Reference: `nightwatch/dev/reference_verifier.py`; the tests in `tests/test_pipeline.py` show which cases it must catch.

## Running

First live run on Danik's demo scenarios (needs his PR #5 merged and a key in `.env`):

```bash
python scripts/run_demo.py --check-only   # key works, Nemotron model id is valid
python scripts/run_demo.py                # empty-sample must end verified, insufficient-evidence reported
```

Each run goes to `.demo-runs/<time>/`: `summary.json`, the incident history and every prompt with the raw
model reply in `model-calls/` (set `NIGHTWATCH_DEBUG_DIR` to get the same from the CLI).

Manual runs:

```bash
pip install -e .
cp .env.example .env            # set NEBIUS_API_KEY
python -m nightwatch.cli models # exact Super model id in Token Factory
python -m nightwatch.cli run --repo ../demo-app --alert scenarios/bug01.json
pytest -q                       # tests, no network or key needed
```

## Open questions

- Exact Nemotron 3 Super id and `response_format` support in Token Factory (the client falls back to plain mode on its own).
- The Sentry SDK in the demo project must set `release=<git sha>` and a `previous_release` tag, otherwise `sentry.py` cannot tell the version.
