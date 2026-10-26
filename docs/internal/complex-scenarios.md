# Harder scenario suite

Task NW-SCENARIOS-02 adds five bug scenarios to the original `empty-sample` and
`insufficient-evidence` smoke cases. The total is seven auto-discovered scenarios:
six expect `verified`, and insufficient evidence expects `reported` without a patch.
The five new cases require reasoning across interacting constraints, not just adding
an empty-input guard. Difficulty has not yet been measured against a live model.

## Expected behavior

| Scenario | Contract and interacting conditions | Observed failure | Allowed application file |
| --- | --- | --- | --- |
| `settlement-rounding` | Gross minus fees; USD/JPY/KWD precision; integer weights; largest remainders; stable account ties; aggregate repeated batches | `SettlementError` on valid mixed-currency payouts | `app/settlement.py` |
| `inventory-replay` | Out-of-order sequence numbers; tenant-scoped IDs; identical retries; shipments and cumulative partial returns; SKU isolation | `InventoryError` during valid multi-tenant replay | `app/inventory.py` |
| `snapshot-pagination` | Descending timestamp and ascending ID; exclusive continuation; tied timestamps; tenant and snapshot isolation; every eligible row exactly once | `IncompleteExport` across page boundaries | `app/pagination.py` |
| `reservation-boundaries` | Half-open intervals; multiple events at one instant; UTC offsets; weighted capacity; cancellations; independent resources | `CapacityExceeded` for a valid handoff | `app/reservations.py` |
| `packaging-legacy-release` | Real packaging parser; narrow registry aliases; numeric post/local version ordering; prerelease policy; bounded compatibility; first equivalent artifact | Actual library `InvalidVersion` when parsing legacy registry labels | `app/plugin_binding.py` |

The function docstrings define behavior visible to the model. Literal expected answers,
good fixture patches and deliberately bad candidates are held separately in
`evaluation/finance_cases.py`, `evaluation/state_cases.py` and `evaluation/library_cases.py`.
The evaluation files and their independent expected answers are never copied into
the generated application repository. Reference implementations exist on separate
oracle branches for independent execution. New runtime alerts omit `previous_good_commit`,
and their parent snapshots contain the same faulty source, so the current context
builder does not send reference repairs through its history diff. An automated
context check verifies this. The original calculator case retains its known-good history.

Each case includes a bad candidate that passes its primary reproducer but fails broader
checks. Examples include distributing all residual money equally, deduplicating whole
event payloads, ignoring the timestamp part of a cursor, suppressing capacity errors,
and sorting hotfix version components as strings. The library case also rejects accepting
arbitrary invalid suffixes. This protects against overfitting to one observed input.

## Offline verification

```bash
python -m pytest -q tests/test_complex_scenarios.py tests/test_demo_scenarios.py tests/test_scenario_patch_gate.py --junitxml=.qa-results/scenarios.xml
python -m tools.prepare_demo --output .demo-workspace
```

These checks execute trusted fixtures with LocalRunner. They prove that original tests
pass on broken snapshots, intended failures reproduce, known-good versions pass, prepared
repairs satisfy independent answers, bad repairs fail and test hashes remain unchanged.
Five additional complete pipeline cycles use scripted investigation/patch responses;
the earlier tests cover the original fix and insufficient-evidence report cycles.
No external model service is called by these tests.

### Recorded validation: 23 October 2026

- Windows / Python 3.11.9 / pytest 9.1.1 / packaging 26.3.
- Full final command: `python -m pytest -q --junitxml=.qa-results/complex-final.xml`.
- Result: **149 passed, 12 skipped in 115.57 seconds**. Eight skips require Docker;
  four require the optional Streamlit installation. These are not successful executions.
- The generated application baseline contains **37 tests**; the five new scenarios add
  **30 independent acceptance cases** and **16 deliberately incorrect patch candidates**.
- Preparation CLI was also run successfully with a fresh
  `.qa-results/complex-final-workspace` output directory: seven discoverable alerts,
  real captured exceptions and separate reference branches.
- `git diff --check` passed. Local reports and generated repositories remain ignored.
- No Docker CLI, Nebius key or Tavily key is available in this validation environment.
  Live model quality, actual web retrieval and Docker/UI integration are unverified here.

Every new alert declares `expected: verified`, uses real commit SHAs and carries the
real exception stack, without identifying the prepared reference implementation.
The generated manifest adds a `scenarios` map for the external acceptance tool. Its legacy
`good_commit` and `broken_commit` still refer to the calculator case. All fixture branches
are kept so `git archive <alert.commit>` works without switching the host checkout.
The reference implementation is not an ancestor of the new runtime commit. Core
pipeline behavior and optional previous-good support remain unchanged.

## Live model run

Nikita configures Nebius and Timur validates Docker using [docker-runner.md](../docker-runner.md).
Run from the project root; the commands below require that integration environment:

```bash
docker build -t nightwatch-runner:dev -f docker/runner/Dockerfile .
python -m scripts.run_demo --runner docker --check-only
python -m scripts.run_demo --runner docker --output .demo-runs/complex-live
```

Use `--scenario <name>` to run one case, and a fresh output directory for each run.
The unfiltered command runs all seven, using the discovery added in PR #10. Model
calls are billable and respect the team's existing configured budgets.

For every new case with a final patch, take its incident ID from `summary.json` and
run the independent check (replace `inc-EXAMPLE` with that actual ID):

```bash
python -m tools.check_scenario_patch --workspace .demo-runs/complex-live/demo --scenario settlement-rounding --incident .demo-runs/complex-live/history/inc-EXAMPLE.json --output .qa-results/settlement-live.json
```

The acceptance tool defaults to Docker. It checks the matching scenario/commit and
single-file patch scope, confirms broken/good reproduction, then evaluates frozen
independent tests and the complete expected passing-node set. Exit code zero means
acceptance passed; inspect the JSON reason on failure. A pipeline `verified` result
alone is not the independent acceptance result. The explicit `--trusted-local-fixture`
option is only for prepared fixture patches, never arbitrary model responses.

Retain the run summary, incident JSON, model calls, acceptance JSON, runner/image
versions and any failures. Report real model success separately from scripted checks.
No automatic merge, provider changes or UI changes are part of this PR.

## Library compatibility and Tavily

The upstream [packaging changelog](https://packaging.pypa.io/en/stable/changelog.html)
records removal of `LegacyVersion` in 22.0. The
[version API](https://packaging.pypa.io/en/stable/version.html) documents `InvalidVersion`
for invalid version strings. The scenario exercises the actual current package, with
registry-specific aliases that need deliberate normalization while preserving PEP 440
ordering. It is a compatibility regression fixture, not a historical package installation
or upgrade run. Require packaging 22+; this PR adds no dependency or image changes.

External traceback frames are preserved with `in_app=false`, including
`site-packages/packaging/version.py`. The detector from Nikita's `feat/tavily` branch
was checked offline against a real traceback and identified `packaging`. That branch
is not merged by this scenario PR. Nikita still needs to connect his changes and verify
an actual Tavily request plus useful retrieved evidence. No Tavily search success is
claimed from the detector-only check.

## Stop point and handoff

Danik supplies the fixtures, independent answers, acceptance checks and instructions.
Nikita reviews/merges the scenario PR when appropriate for his work cycle and runs
the live model/Tavily integration. Timur checks all cases inside Docker and renders
their saved incidents through the existing UI contract. Changes needed in those
components should be coordinated with their owners rather than included silently here.
