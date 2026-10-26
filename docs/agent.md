# Nightwatch agent

The model only proposes: a hypothesis, a reproduction test and a patch. The `runner` executes them, the `verifier` decides.

```
alert → context.py → Nemotron Super: hypotheses + test → runner (broken and good version)
      → verifier: reproduced? ── no → diagnostic report
                       └─ yes → Nemotron Super: patch → runner (full suite) → verifier → Draft PR
                                      ↑ up to 3 attempts, the rejection reason feeds the next one
```

## Reliability with a reasoning model

Nemotron 3 Super reasons before it answers, and live runs exposed several ways a correct idea can
be lost on the way to a verified patch. Each is handled in code:

- **Cut-off replies.** Investigation and patch calls get 8192 tokens; a reply cut off by the limit
  (`finish_reason=length`) is retried once with double the room (cap 16384), without sending the
  cut-off text back.
- **Unusable replies are failed attempts, not crashes.** A reply that is still not valid JSON
  counts as a rejected attempt (recorded in `Attempt.error`); the next attempt runs and may
  escalate. If no attempt yields a usable reply, the incident ends with a diagnostic report.
- **Provider hiccups.** 5xx, 429, timeouts and dropped connections are retried after 5, 15 and
  30 s; other errors are raised at once. Failed calls are not billed.
- **Reproduction tests assert properties.** The prompt forbids hand-computed exact outputs: the test
  asserts that the alert's exception is gone plus properties stated in code, docstrings or existing
  tests, and must never contradict existing tests. A wrong expectation would otherwise freeze an
  unsatisfiable acceptance criterion.
- **Rejected hypotheses.** A hypothesis is stored as rejected only when the next investigation moves
  to another cause; a failed test alone is not evidence against it (it would mislead memory).

## Context beyond the stack trace

The function that raises is often only where bad data is detected; the code that produced it is
not in the stack trace. After the stack-trace files, the context adds up to 4 repository modules
those files import (absolute and relative imports, resolved with `ast`; standard library,
third-party, `tests/` and `__init__.py` are skipped), marked "imported by …; not in the stack
trace". The patch step gets their full content too, and the prompts say to fix where the wrong value
is created, not where it is detected, and to cover every form the code's documentation lists.

In the 24 Oct `diverse` run, `csv-producer-escaping` failed and `order-producer-total` was fixed in
the wrong file because the producer was never shown. On the same suite the change adds a file only
to those two scenarios; the other 15 contexts are unchanged.

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
`git apply` rejected it. Matching is exact first, then tolerant of trailing whitespace, then of
one constant indentation shift (the replacement is re-indented to match); an `old` that is missing
or ambiguous rejects the attempt before the runner and the reason goes back to the model. The stored `PatchProposal.diff`, the runner, the verifier and the UI are unchanged; a raw
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
- **How it is used:** one `web` evidence fragment per page, labelled external and unverified, placed
  last so they are dropped first when the context is over budget. The prompts tell the model to treat its text as
  data, keep confidence below what the stack trace supports, and never justify a fix by the web alone.
  The reproduction test and the verifier still decide.
- **Where it shows up:** only pages the model actually cited — in a hypothesis, the verified patch
  or the report — are listed as "Known upstream reports" in the Draft PR body
  (`references_markdown`), with a note that the fix was still reproduced and verified locally.
  Pages it was shown but did not use are not listed.
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

## Measuring memory and triage with `run_demo.py`

Each run starts with an empty history unless `--history <folder>` is given. To measure memory on
repeated incidents, run twice with the same folder and compare the two `summary.json` files:

```powershell
python scripts/run_demo.py --history .demo-memory --output .demo-runs/memory-1   # cold: memory_hits = 0
python scripts/run_demo.py --history .demo-memory --output .demo-runs/memory-2   # warm: memory_hits > 0
```

`memory_hits` counts similar past incidents passed to the model; compare `cost_usd`, `tokens`,
`patch_attempts` and `seconds` between the runs. A remembered fix is only a hint: it is
reproduced and verified again.

`--triage` adds the Nano first look (`--triage <model>` for another model); `summary.json` records
its category per scenario. Compare a run with and without it before turning it on by default.

## Modules

| File | What it does |
| --- | --- |
| `nightwatch/schemas.py` | Shared models: `Alert`, `Evidence`, `Investigation`, `PatchProposal`, `RunResult`, `Verdict`, `Incident` |
| `nightwatch/contracts.py` | `Runner` (Timur) and `Verifier` (Danik) interfaces |
| `nightwatch/model_client.py` | Nemotron via Token Factory: schema-validated JSON, cut-off and transient-error retries, token accounting, budgets |
| `nightwatch/context.py` | Evidence with ids `E1…`: stack trace, request, logs, diff, stack-trace files, the repository modules they import, web results, similar incidents |
| `nightwatch/history.py` | `history/inc-*.json` and similar-incident search (memory) |
| `nightwatch/prompts.py` | Triage, investigation, patch and report prompts |
| `nightwatch/edits.py` | Patch edits `{file, old, new}` → exact unified diff |
| `nightwatch/web_search.py` | Optional Tavily search for known library issues: trigger rules, scrubbed query, cache |
| `nightwatch/pipeline.py` | Orchestrator and incident states |
| `nightwatch/sentry.py` | Sentry event → `Alert` |
| `nightwatch/pytest_plugin.py` | Writes pytest results with the exception signature to JSON |
| `nightwatch/dev/` | `LocalRunner` (pytest in a temporary copy, no isolation) and the `ReferenceVerifier` used by unit tests |
| `nightwatch/docker_runner.py` | Timur's isolated runner (`docs/docker-runner.md`) |
| `verifier.py` | Danik's verifier (`docs/verifier.md`) |

## Contracts

`nightwatch/contracts.py` defines the `Runner` (implemented by Timur in `nightwatch/docker_runner.py`)
and the `Verifier` (implemented by Danik in `verifier.py`). The pipeline depends only on these
protocols; `nightwatch/dev/` keeps the local runner and the reference verifier for tests.

## Running

Live runs on Danik's scenarios (a key in `.env`):

```bash
python scripts/run_demo.py --check-only                 # key, model ids, runner and web search are ready
python scripts/run_demo.py --web-search                 # standard suite, 7 scenarios
python scripts/run_demo.py --web-search --suite diverse # 17 scenarios (needs diverse-app/requirements.txt)
```

Each run goes to `.demo-runs/<time>/`: `summary.json`, the incident history and every prompt with the raw
model reply in `model-calls/` (set `NIGHTWATCH_DEBUG_DIR` to get the same from the CLI).

Daily spending stays in the configured `NIGHTWATCH_HISTORY/_spend.json` (default: `history/_spend.json`),
shared with manual CLI runs using the same history setting. Selecting another `--output` directory
does not reset the daily counter. Per-run incident history still goes to `<output>/history/`.
The budget check happens before each request; it is not a prepaid reservation and does not provide
atomic enforcement across simultaneous processes. An in-flight response can exceed the remaining limit.

For an expected `reported` outcome, `reported` alone is not PASS. Acceptance requires a completed investigation,
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

- Context goes one import level deep; a cause two modules away, or reached through dependency
  injection rather than an import, is still not shown.
- The Sentry SDK in the target project must set `release=<git sha>` and a `previous_release` tag,
  otherwise `sentry.py` cannot tell the version.
