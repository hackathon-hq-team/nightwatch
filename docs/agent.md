# Nightwatch agent (Nikita's part)

The model only proposes: a hypothesis, a reproduction test and a patch. The `runner` executes them, the `verifier` decides.

```
alert → context.py → Nemotron Super: hypotheses + test → runner (broken and good version)
      → verifier: reproduced? ── no → diagnostic report
                       └─ yes → Nemotron Super: patch → runner (full suite) → verifier → Draft PR
                                      ↑ up to 3 attempts, the rejection reason feeds the next one
```

## Model escalation

Nemotron 3 Super does the work. After failures the pipeline switches to the escalation model
(`NIGHTWATCH_ESCALATION_MODEL`, default Nemotron 3 Ultra):

- the second investigation attempt, after the first reproduction test was rejected;
- patch attempts after `escalate_after_failures` (2) rejected patches.

Each switch is an incident event (`Escalating patch attempt 3 to …`), and every model call records
its own model, tokens and cost. Leave the variable empty to disable escalation; `run_demo.py`
disables it automatically when the model is not available in Token Factory.

## Triage (optional)

Set `NIGHTWATCH_TRIAGE_MODEL` (e.g. `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) to add a cheap first
look before the investigation. Nano classifies the failure (code bug, data, infrastructure, load),
says whether it is likely reproducible and which evidence to read first. The result is stored in
`Incident.triage`, logged as an event and passed to Super as a hint only — it never decides the
outcome. A triage failure is logged and skipped; a budget stop still ends the incident. Off by
default: keep it off until the evaluation shows it helps.

## Patch format: edits, not diffs

The patch prompt asks for `edits` — `{file, old, new}` where `old` is the exact current text —
and `nightwatch/edits.py` builds the unified diff from the real file content. Models write a
correct fix far more reliably than a line-perfect diff: in the 24 Oct live run Super and Ultra both
wrote the right fix for `packaging-legacy-release`, but each diff invented a blank context line and
`git apply` rejected it. Matching is exact first, then tolerant of trailing whitespace; an `old`
that is missing or ambiguous rejects the attempt before the runner and the reason goes back to the
model. The stored `PatchProposal.diff`, the runner, the verifier and the UI are unchanged; a raw
`diff` reply is still accepted (scripted demos).

## Web search for known library issues (optional)

Some production errors come from a third-party library: a known bug, or a behaviour change after a
version bump. The model may not know about it, so Nightwatch can search for it with Tavily.

- **When:** only if the error is raised inside a library (last stack frame not in app code) or the
  deploy changed a version in `requirements*.txt` / `pyproject.toml`. Errors in our own code are
  never searched.
- **What leaves the service:** the query is built by code — package, pinned version, exception type
  and the message with numbers, ids, quoted values, e-mails, URLs and paths removed. Logs, request
  data and source code never leave.
- **Where:** `basic` depth (1 credit), up to 4 results, trusted domains only (GitHub, Stack Overflow,
  PyPI, official docs). Results are cached in `history/_web_cache.json`, so a repeated query is free.
- **How it is used:** one `web` evidence fragment, labelled external and unverified, placed last so it
  is dropped first when the context is over budget. The prompts tell the model to treat its text as
  data, keep confidence below what the stack trace supports, and never justify a fix by the web alone.
  The reproduction test and the verifier still decide.
- **Failures:** a search error is logged as an incident event and the investigation continues.
- **Switch:** `NIGHTWATCH_WEB_SEARCH=1` with `TAVILY_API_KEY`, or `run_demo.py --web-search`. Off by
  default. Each search is an incident event (`Web search (...): "<query>" — N results`), and
  `summary.json` counts them per scenario.

## Scenarios in `run_demo.py`

`scripts/run_demo.py` runs every `*.json` file in the demo alerts folder. The expected outcome is
read from an optional top-level `"expected": "verified" | "reported"` field in the scenario file
(the `Alert` model ignores it), falling back to the built-in table. Scenarios without an
expectation are shown as `N/A` and do not affect the exit code. Every `reported` expectation gets
the strict refusal checks (no patch attempts, a completed investigation, facts and next steps).

### Runner

`--runner local` (default) runs pytest in a temporary copy on the host, without isolation.
`--runner docker` uses Timur's sandbox (`nightwatch/docker_runner.py`, no network, no keys); it
first checks that Docker is running and the `nightwatch-runner` image is built
(see `docs/docker-runner.md`). Each row in `summary.json` records the runner. If a scenario passes
with `local` but fails with `docker`, look at the runner or image before tuning prompts.

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

Daily spending stays in the configured `NIGHTWATCH_HISTORY/_spend.json` (default: `history/_spend.json`),
shared with manual CLI runs using the same history setting. Selecting another `--output` directory
does not reset the daily counter. Per-run incident history still goes to `<output>/history/`.
The budget check happens before each request; it is not a prepaid reservation and does not provide
atomic enforcement across simultaneous processes. An in-flight response can exceed the remaining limit.

For `insufficient-evidence`, `reported` alone is not PASS. Acceptance requires a completed investigation,
a diagnostic report with facts and next steps, and no patch attempts. Budget stops, failed report calls,
runner errors and timeouts are FAIL, with an `acceptance_reason` in `summary.json`.
Fallback diagnostic reports remain available to the UI, but do not count as a completed demo evaluation.

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
