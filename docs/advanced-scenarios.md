# Compact advanced scenarios

NW-SCENARIOS-03 is Danik's opt-in benchmark extension, requested on 24 October 2026.
It adds three compound incidents. The existing seven-scenario default stays unchanged.
The goal is harder reasoning within comparable input and generation budgets, not longer
prompts or more retries. These fixtures do not train the model or prove improved live ability.

## Behavior and independent answers

| Scenario | Required behavior | Why a single local fix is insufficient | Allowed application file |
| --- | --- | --- | --- |
| `cumulative-refunds` | Refunds reverse cumulative portions of the original sale allocation; exact retries count once; sale/currency identities remain separate; zero fields remain present | Per-request rounding loses fractions, while replay accounting also changes cumulative state twice | `app/refunds.py` |
| `versioned-projection` | Tenant/entity versions are consecutive regardless of arrival order; deletion preserves version history; recreation resets the value; exact retries count once | Sorting events alone does not preserve the version through deletion; retaining versions alone does not restore arrival order | `app/event_projection.py` |
| `dependency-backtracking` | Resolve all shared dependency constraints deterministically, trying lower versions when necessary and preserving package eligibility rules | Trying another version is insufficient when failed branches leave constraints in subsequent branches | `app/dependency_resolver.py` |

Public docstrings specify the behavior, including ordering, duplicates, zeros and error
conditions. Literal expected answers, reference repairs and extra acceptance tests live
in `evaluation/refund_cases.py`, `evaluation/projection_cases.py` and
`evaluation/resolver_cases.py`. They are never copied into the generated application or
sent as reference patches in runtime context. The reference branch is reserved for
independent acceptance; runtime alerts omit `previous_good_commit` as before.

Each fixture has a passing broken-version baseline, a trusted reproducer that raises
the target application exception, a known-good repair, incomplete repairs, and a
plausible workaround that passes the incident example but fails independent checks.
Expected results are verified before a candidate patch is evaluated. The tests remain
frozen throughout each evaluation. A model's generated assertion is not the oracle.

## Offline verification

From the repository root, with the existing environment:

```bash
python -m pytest -q tests/test_advanced_scenarios.py
python -m tools.prepare_demo --suite advanced --output .qa-results/advanced-workspace
python -m pytest -q --junitxml=.qa-results/advanced-final.xml
```

Use a new output directory each time. Preparation refuses to overwrite old evidence.
The advanced repository includes the calculator's four baseline tests plus the three
advanced application modules and their baselines. Its alerts directory contains only
the three advanced incidents. Standard and advanced baseline files are not mixed.
The manifest records the suite; the acceptance tool determines expected test counts
from trusted suite membership, not a model response or supplied count.

Offline pipeline checks use explicitly scripted replies through the current
search/replace edit format. They execute trusted local source with Git and pytest.
They are not NVIDIA/Nebius calls and do not establish Docker execution or model quality.

### Recorded verification: 24 October 2026

- Windows, Python 3.11.9, pytest 9.1.1. The full command above produced **205 passed,
  12 skipped, 1 failed** in 159.54 seconds. All 13 advanced integration checks passed.
- The single failure is the existing `tests/test_edits.py::test_no_newline_at_end_of_file`
  Windows Git application check. It also fails in an untouched export of base commit
  `11d275e`; neither that test nor `nightwatch/edits.py` was changed here.
- Eight skips require Docker; four require optional Streamlit. They are unverified,
  not successful executions. No live model or Tavily calls were made.
- Advanced baseline: **35 tests** (four calculator plus 31 new originals). Independent
  acceptance: **24 tests**, including 88 refund partitions and 24 event permutations.
  All **16 deliberately incorrect candidate patches** were rejected.
- Three complete scripted pipeline cycles used structured edits, then their saved
  patches passed the separate acceptance gate. The previous seven-scenario suite and
  its 37-test baseline passed the existing scenario checks.
- Preparation CLI and `run_demo --help` succeeded; `git diff --check` passed.

Measured context contains 4,847 characters for refunds, 5,059 for projection, and
4,503 for dependency resolution, with no truncated evidence. Sources are respectively
63, 59 and 61 lines. The older five complex scenarios measured 3,600–4,619 characters
without web results. These measurements exclude prompt framing, schemas, generated
tests and model output; they establish comparable source size, not equal paid tokens.

The local execution environment initially denied Git writes in its restricted system
temporary directory. Final verification used normal Windows temporary directories.
No project runner or verifier workaround was added.

## Live handoff and token comparison

Nikita reviews the PR and runs the model in the team's configured environment. Timur
checks Docker readiness using the existing instructions. Keep the current model,
escalation model, maximum attempts, completion limits and monetary limits unchanged.
No provider settings or budget controls are changed by this task.

```bash
python -m scripts.run_demo --suite advanced --runner docker --check-only
python -m scripts.run_demo --suite advanced --runner docker --scenario cumulative-refunds --output .demo-runs/advanced-refunds-01
```

Repeat with `versioned-projection` and `dependency-backtracking`, using fresh output
directories. Omit `--scenario` to run all three in one invocation. The default invocation
without `--suite advanced` still runs only the original seven scenarios. The suite
argument is only fixture selection; the core pipeline, provider, prompts and UI are unchanged.
No web search is necessary for these application-logic incidents; dependency resolution
uses the already installed `packaging` library, without downloading project dependencies.

After a live `verified` result, use its actual incident ID from `summary.json`:

```bash
python -m tools.check_scenario_patch --workspace .demo-runs/advanced-refunds-01/demo --scenario cumulative-refunds --incident .demo-runs/advanced-refunds-01/history/inc-EXAMPLE.json --output .qa-results/advanced-refunds-acceptance.json
```

This independent check defaults to Docker. It requires the original generated repository
including its Git metadata. Keep the full run folder; a summary screenshot alone cannot
establish acceptance. Never use `--trusted-local-fixture` for an unreviewed model patch.

Record the following per scenario: model IDs, total calls, total prompt plus completion
tokens (including retries and reporting), cost, elapsed time, pipeline outcome and
independent acceptance outcome. Compare with the same configuration on the existing
suite. The natural comparison pairs are refunds/settlement, projection/inventory and
dependency resolution/legacy packaging. Use saved run JSON for a repeatable baseline.
Do not discard failed runs from the cost comparison or retry indefinitely until one passes.

Success at the desired budget requires both independent acceptance and no more total
tokens than the agreed baseline. A harder task may require more reasoning tokens even
with a short prompt. Offline checks bound evidence to 12,000 characters and require the
entire application source to fit the current context window; these are input-size checks,
not an exact tokenizer or a promise about provider consumption. Live token parity and
empirical difficulty remain unmeasured until Nikita's run.

## Stop point

Danik supplies scenarios, trusted expected behavior, acceptance and instructions.
Nikita owns the model run and changes to runtime test generation or provider behavior;
Timur owns Docker/UI integration. Do not weaken original tests, rewrite a frozen
reproducer, or alter outcome rules to make a candidate pass. If a model-generated test
contradicts the contract, retain that failed run and coordinate a new run with Nikita.
