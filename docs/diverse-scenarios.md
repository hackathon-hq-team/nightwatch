# Seventeen varied checkpoint scenarios

NW-SCENARIOS-04 implements Nikita's revised request: cover different failure modes,
not increasingly elaborate algorithms. The initial fixture baseline was `main` at `11d275e`.
It is independent of the three advanced scenarios in draft PR #18; those are not
included in these counts. Use `--suite diverse` for 17 cases; the default `standard`
suite remains the existing seven. All runtime outcomes and provider limits stay unchanged.

## Scenario inventory

| Group | Cases | Expected outcome |
| --- | --- | --- |
| Existing suite | `empty-sample`, `insufficient-evidence`, `inventory-replay`, `packaging-legacy-release`, `reservation-boundaries`, `settlement-rounding`, `snapshot-pagination` | Six verified fixes; one report |
| HTTP body validation | `http-body-validation`: POST /quote must reject nonpositive/noninteger JSON quantities with 422 before pricing | Verified fix |
| HTTP dependency | `http-header-dependency`: GET /profile must translate a missing/invalid demo X-Token into 401 | Verified fix |
| HTTP response status | `http-created-status`: POST /widgets must return 201 and preserve its JSON body | Verified fix |
| Cause outside the traceback | `order-producer-total`, `csv-producer-escaping`: producer returns invalid data, then a separate consumer fails | Verified fixes; current context limitation is exposed |
| Misleading deployment diff | `unrelated-receipt-diff`: invoice arithmetic fails after unrelated receipt presentation changes | Verified fix without reverting the presentation change |
| Insufficient external evidence | `external-timeout-unobserved`, `deployment-config-absent`, `race-without-schedule` | Report without a patch |
| Repeated incident | `zz-repeat-inventory-replay`: same broken snapshot and input as inventory-replay, with an actual earlier incident in this run's history | Reproduce and verify again; measure usage separately |

That is **17 scheduled cases: 13 expected verified results and four expected reports**.
One case deliberately repeats an earlier incident; this is not a claim of 17 distinct bugs.
The three new refusal records are clearly marked synthetic. They contain no invented
traceback or claim that a remote timeout, deployment failure or concurrent run happened here.

## HTTP prerequisites and scope

Install the separately pinned fixture dependencies in the team's environment:

```bash
python -m pip install -r diverse-app/requirements.txt
```

The HTTP cases use actual in-process ASGI requests through FastAPI's TestClient.
They require no running server, remote service, credentials or database. They demonstrate
FastAPI behavior; they do not demonstrate PostgreSQL integration or real authentication.
The `demo-token` constant is explicitly fixture data. See the official
[FastAPI testing guide](https://fastapi.tiangolo.com/tutorial/testing/).

**Timur's integration prerequisite:** install `diverse-app/requirements.txt` in the
test image, rebuild it, then repeat the HTTP checks inside Docker. This PR deliberately
does not edit the Dockerfile or claim that the current image already has these packages.
The current image only installs pytest. Missing imports are setup failures, never successful
reproduction. The host also needs these packages because preparation captures real failures.

The wrong-status case records a **client-side contract probe**, not a fabricated server
exception. Its alert says so explicitly. Independent acceptance checks the actual HTTP
status and body directly; removing the probe's assertion cannot produce an accepted fix.

## Context and diff cases

The cross-file producers finish before the consumers raise. Captured tracebacks therefore
contain the real consumer frames and no producer frame. The consumer imports the producer,
but the current context builder only sends traceback files. Tests confirm that the actual
buggy producer source is absent from the model's file evidence.

The trusted reference repair is executable and independently verified, but a scripted
cycle is not proof that the live model can repair an unseen file. A live failure here is
useful benchmark evidence. Nikita owns any decision to extend context collection or add
file retrieval. This PR does not inject the answer or a fabricated frame to hide that limit.
Only the producer is permitted in independent patch acceptance; weakening consumer
validation is rejected.

The receipt scenario has an actual deployment diff in `app/receipt_view.py`; the invoice
bug predates that deployment. Its parent has the same broken invoice function. Correct
repair changes only `app/invoice.py` and preserves the newly deployed receipt settings.
The reference repair remains on a separate oracle branch, outside runtime ancestry.

## Prepare and check offline

Use fresh output directories to preserve evidence:

```bash
python -m tools.prepare_demo --suite diverse --output .qa-results/diverse-workspace
python -m pytest -q tests/test_diverse_scenarios.py tests/test_diverse_refusals.py tests/test_memory_pair_report.py
python -m pytest -q --junitxml=.qa-results/diverse-final.xml
```

Expected answers and prepared repairs stay in `evaluation/`, outside the generated
application. The generated repository contains only the selected application fixtures,
original tests and their requirements/instructions. Preparation captures the real
repairable failures and labels synthetic reports separately. Scripted offline runs
verify control flow, frozen hashes and independent answers, not model judgment or spending.

## Nikita's live run

After Timur's image dependency update, retain the team's current provider settings and limits:

```bash
python -m scripts.run_demo --suite diverse --runner docker --check-only
python -m scripts.run_demo --suite diverse --runner docker --output .demo-runs/diverse-01
```

Use `--scenario <name>` to run a single ordinary case. Refusal success requires a completed
investigation and useful report, with no patch, no budget interruption and no runner failure.
This reuses the current acceptance rules; no new successful status is invented.
More cases naturally mean more total model calls. No live calls are made by fixture preparation.

For a repairable result, take the actual incident ID from `summary.json` and run:

```bash
python -m tools.check_scenario_patch --workspace .demo-runs/diverse-01/demo --scenario order-producer-total --incident .demo-runs/diverse-01/history/inc-EXAMPLE.json --output .qa-results/order-acceptance.json
```

Independent acceptance defaults to Docker and binds the scenario, commit, permitted file,
baseline count, trusted reproducer and all additional tests. Keep the complete generated
repository with Git metadata; a summary screenshot alone is insufficient. Do not use the
local trusted-fixture override for arbitrary model patches.

## Memory measurement

The full 17-case run orders the repeat after the original inventory incident and uses
one history store. For an uncontaminated pair, run just those two with a fresh output:

```bash
python -m scripts.run_demo --suite diverse --runner docker --memory-pair --output .demo-runs/memory-pair-01
python -m tools.memory_pair_report --run .demo-runs/memory-pair-01
```

For this comparison, omit `--history`: the report reads the pair's records from
`<output>/history` and requires the first incident to have no earlier matching memory.
The separate shared-history option remains available for other experiments. The
`--triage` option from main can also be used; its calls remain part of recorded usage.

Running the repeat alone is rejected before a provider call. The pair still performs
reproduction and patch verification twice. It does not reuse a successful outcome or
skip tests. The comparison must include all calls, retries, reporting, tokens, cost and
elapsed time; a failed run is not removed to improve the result.

The report binds the saved histories to the summary and checks that the second incident
actually received the first as memory. It does not manufacture usage numbers for scripted
responses. For quality-confirmed comparison, run independent acceptance on both incidents
and provide those two output files with `--first-acceptance` and `--repeat-acceptance`.
A single pair is observational: variance, caching and model behavior prevent attributing
every difference to memory. Lower cost or latency is measured, not guaranteed.

If the first incident ends in `failed`, the current history store does not expose it as
memory. The report then rejects the pair with exit code 2 instead of claiming a memory
comparison. Preserve both original summary rows and their costs; a missing memory link
is an incomplete experiment, not evidence that a repeat became cheaper.

## Validation recorded on 24 October 2026

- Python 3.11.9 on Windows, pytest 9.1.1 and the pinned HTTP fixture requirements.
- Initial full suite: **245 passed, 12 skipped, one failed**. All **54 newly added checks**
  passed, covering discovery, real failures, reference repairs, ten incorrect repairs,
  independent acceptance, refusals, repeat history, CLI selection and usage reporting.
- The remaining failure is the existing
  `tests/test_edits.py::test_no_newline_at_end_of_file` (`git apply` rejects its patch
  on this Windows setup). It also fails in the unchanged `main` snapshot at `11d275e`;
  the shared edit implementation is outside this task and was not changed.
- Eight skips require Docker/the runner image; four require Streamlit. No Docker/UI
  validation is claimed here.
- The documented preparation command completed and produced 17 valid alerts:
  13 expected verified results and four expected reports. This validates fixture
  generation, not 17 live model successes. Local scripted execution used no provider API.
- Integration with `main` at `89dd7b7`: **256 passed, 12 skipped, one failed** using
  `python -m pytest -q --tb=short --junitxml=.qa-results/diverse-merge-main.xml`.
  The only failure remains the same Windows patch test above. Both the diverse-suite
  options and main's shared-history/triage options are preserved.

## Ownership and stop point

Danik delivers scenarios, expected behavior, negative checks and instructions. Nikita
owns live model runs, context/memory behavior and report-generation changes. Timur owns
Docker dependency installation and UI integration. No runtime model, prompt, verifier,
storage, UI or Docker implementation is changed by this scenario task.
