# Results

Live runs of the full Nightwatch pipeline: NVIDIA Nemotron 3 Super on Nebius Token Factory,
escalation to Nemotron 3 Ultra, Tavily web search on, tests in the Docker sandbox.

Two checks are reported separately:

- **Pipeline** — Nightwatch's own verdict: the reproduction test fails with the alert's signature on
  the broken commit, the patched suite is green and the tests are unchanged.
- **Independent acceptance** — hidden tests the model never sees (`tools/check_scenario_patch.py`),
  applied in Docker to the saved patch. This is the stricter measure of a correct fix.

## Final runs, 28 October 2026 — 17 scenarios

Commit `05bfca8`, `python scripts/run_demo.py --runner docker --web-search --suite diverse`.
Budgets $0.10 per incident and $1.00 per day. One execution per configuration; no run or failed
patch was repeated to improve a result.

| Run | Expected outcome | Fixes | Reports | Model calls | Cost | Time |
| --- | --- | --- | --- | --- | --- | --- |
| final-1 | **17 / 17** | 13 | 4 | 47 | $0.198 | 14 min 46 s |
| final-2, same history (memory) | **17 / 17** | 13 | 4 | 44 | $0.192 | 12 min 30 s |
| final-triage, Nano triage on | 16 / 17 | 12 | 5 | 62 | $0.199 | 18 min 33 s |

**Independent acceptance of final-1: 10 / 12 fixes accepted** (`empty-sample` has no hidden tests).

| Scenario | Expected | Pipeline | Hidden tests | Patch attempts | Cost, $ | Time, s |
| --- | --- | --- | --- | --- | --- | --- |
| insufficient-evidence | report | PASS | — | 0 | 0.0022 | 10 |
| deployment-config-absent | report | PASS | — | 0 | 0.0041 | 22 |
| external-timeout-unobserved | report | PASS | — | 0 | 0.0055 | 32 |
| race-without-schedule | report | PASS | — | 0 | 0.0027 | 25 |
| empty-sample | fix | PASS | — | 1 | 0.0048 | 26 |
| http-body-validation | fix | PASS | rejected: partial fix | 1 | 0.0072 | 40 |
| http-created-status | fix | PASS | accepted | 1 | 0.0067 | 35 |
| http-header-dependency | fix | PASS | accepted | 1 | 0.0176 | 78 |
| order-producer-total | fix | PASS | accepted | 1 | 0.0030 | 17 |
| csv-producer-escaping | fix | PASS | accepted | 3 | 0.0292 | 123 |
| unrelated-receipt-diff | fix | PASS | accepted | 1 | 0.0030 | 16 |
| packaging-legacy-release | fix | PASS | rejected: partial fix | 3 | 0.0558 | 218 |
| inventory-replay | fix | PASS | accepted | 1 | 0.0051 | 27 |
| zz-repeat-inventory-replay | fix | PASS | accepted | 1 | 0.0139 | 50 |
| reservation-boundaries | fix | PASS | accepted | 1 | 0.0161 | 70 |
| settlement-rounding | fix | PASS | accepted | 1 | 0.0062 | 28 |
| snapshot-pagination | fix | PASS | accepted | 1 | 0.0152 | 68 |

Average per incident: **$0.012 and 52 s**. Super handled every incident; Ultra stepped in on 3
after failed attempts. Tavily ran once, on the only error raised inside a library.

### What changed since the 24 October run

The same suite on 24 October (local runner) gave 16/17 and 8/11 fixes confirmed by hidden tests.
Between the runs the context gained the modules that the failing code imports, and the prompts
gained "fix where the value is created" and "cover every documented form":

- **Bugs outside the stack trace are fixed now.** `csv-producer-escaping` (failed on 24 Oct) and
  `order-producer-total` (patched in the wrong file on 24 Oct) were both fixed in the producer and
  accepted by the hidden tests.
- **Two partial fixes remain.** `http-body-validation` adds `quantity > 0` but still accepts `true`
  and `"2"`; `packaging-legacy-release` misses upper-case `-FINAL` with a `v` prefix. Both pass the
  reproduction test and the suite, and both are rejected by the hidden tests — the reason
  independent acceptance is reported next to the pipeline verdict.

### Memory and triage

- **Memory (final-2 vs final-1).** In final-2 every scenario saw its own earlier incident. The run
  used 3 fewer model calls, 3% less money and 15% less time, with the same 17/17. This is one
  paired observation, not proof of a reliable gain: two scenarios got cheaper, the repeated
  inventory case got more expensive.
- **Triage (final-triage vs final-1).** Nano triage added 15 model calls and 26% more time for the
  same cost, and `settlement-rounding` ended in a report instead of a fix. Triage stays off by
  default.

## How we got here — the standard suite, 24 October 2026

Seven scenarios, local runner. The failures of each run were traced through the saved prompts and
raw model replies (`.demo-runs/<time>/model-calls/`) and fixed in the pipeline, not in the
scenarios:

| Run | Pipeline | Cost, $ | Main failure |
| --- | --- | --- | --- |
| 1 | 4 / 7 | 0.073 | replies cut off at 4096 tokens |
| 2 | 6 / 7 | 0.083 | a wrong hand-computed expectation in a reproduction test |
| 3 | **7 / 7** | 0.064 | — every patch accepted on the first attempt |
| 4 | 6 / 7 | 0.093 | one Token Factory 503; edits shifted by two spaces |

| Problem seen live | Fix |
| --- | --- |
| Super spent the whole 4096-token budget on reasoning and returned no JSON | Limits 8192, retry with 16384 when cut off; an unusable reply is a failed attempt, not a crash |
| Correct fixes rejected because the model's unified diff invented a context line | The model returns search/replace edits; code builds the exact diff |
| The model hand-computed an expected output wrongly; the correct fix failed its own test | Reproduction tests assert the alert's error is gone plus stated properties |
| Edits shifted by two spaces did not match the file | Edits match under one constant indentation shift and are re-indented |
| One 503 from Token Factory ended an incident | Transient provider errors retried after 5, 15 and 30 s |
| The producer of bad data was never shown to the model | Context adds the modules the failing code imports |

## Limits of these numbers

- The scenarios are realistic fixtures with known answers, not production incidents.
- One execution per configuration; model behaviour varies between runs.
- Cost is computed from recorded tokens and Token Factory prices and excludes Tavily.
- Hidden-test acceptance was run on final-1 only.
