# Integration decisions

## NW-SCENARIOS-04: varied seventeen-case checkpoint suite

Danik confirmed Nikita's revised request on 24 October 2026: create a separate PR
with 17 scheduled cases based on main, leaving the advanced-scenarios draft PR #18
independent. Variety takes priority over increasing algorithmic difficulty.

- Keep the existing seven-case suite as the default. An explicit `--suite diverse`
  adds three FastAPI cases, two cross-file causes, one unrelated deployment diff,
  three synthetic insufficient-evidence reports and one repeated inventory incident.
  Expected totals are 13 verified fixes and four reports, including one deliberate repeat.
- Add pinned optional HTTP fixture requirements in `diverse-app/requirements.txt`.
  Timur must install them in the Docker image and validate the rebuilt runner; the
  host also needs them to capture actual failures. No database behavior is claimed.
- Preserve real traceback frames. Cross-file fixtures intentionally expose the current
  context collector's inability to include a producer that has already returned.
  Nikita owns any follow-up context change; this task does not modify core code or prompts.
- Keep independent answers outside the generated application. Acceptance permits only
  the intended application file and checks all 67 original tests in a diverse workspace,
  the frozen reproducer and the scenario's additional answers. No core status is redefined.
- Repeat the same broken commit/input with a new incident ID in one shared run history.
  The optional `--memory-pair` selects just the original and repeat. Saved usage reports
  include all calls and require independent acceptance before comparing quality-confirmed
  costs; scripted tests do not establish live model savings.
- Alert, Incident, runner and verifier contracts stay unchanged. The optional suite
  selector is fixture metadata, not a new product or public arbitrary-code service.
  Nikita owns live model/context validation; Timur owns Docker/UI integration.

See [scenario inventory, commands and stop point](../scenarios.md).

## NW-SCENARIOS-02: harder scenarios for the next checkpoint

Team request relayed by Danik on 23 October 2026: prepare 5–8 runnable scenarios in a
separate PR, including one library compatibility incident for Nikita's Tavily work.
This extends the earlier two-scenario fixture scope. It does not implement a general
repository service or change the shared Alert, Incident, runner or verifier contracts.

- Add five multi-condition bug scenarios alongside the two existing smoke scenarios.
- Use the `expected` JSON field and discovery already merged in PR #10. No runtime
  registration, pipeline rewrite, provider configuration or UI change is required.
- Generate independent broken/reference snapshots on fixture branches in the same
  disposable repository. Preserve the original main branch and legacy manifest fields.
  Add only a scenario-to-commit map to the preparation manifest, which is fixture metadata.
- Keep correct answers, prepared patches and acceptance tests outside the generated
  source repository. Public function contracts and baseline tests remain visible.
  New runtime alerts omit the optional `previous_good_commit`; reference repairs live
  on separate oracle branches, not in the runtime commit's ancestry. The existing
  context builder therefore cannot reveal a ready-made repair through the parent diff.
  Independent acceptance binds the reference SHA from the fixture manifest instead.
  The original calculator scenario keeps its historical-good behavior.
- Evaluate completed candidate patches separately against frozen independent answers.
  This does not reinterpret the core pipeline's `verified` status or silently add
  evaluation answers to model prompts. A live benchmark result needs both checks.
- Use real `packaging` behavior for the library incident. Pytest already depends on
  packaging; no shared dependency or Docker image change is included. Require a
  modern packaging version (22+) for this compatibility fixture. No historical
  installation/upgrade experiment is claimed.
- Nikita owns the live model/Tavily run; Timur owns Docker/UI validation. Stop this
  scenario task before changing those components. See `complex-scenarios.md` for handoff.

## Live-demo acceptance and daily spend continuity

Proposed in the dedicated fix PR for Nikita's review. No shared data schema or verifier rule changes.

- Keep ModelClient's budget store in the configured shared history directory across demo runs.
  Store per-run incidents separately in each output directory. This reuses the existing CLI budget store.
- Treat `reported` as a product outcome, not sufficient evidence that the demo acceptance check passed.
  Require investigation/report evidence and reject budget interruptions and runner failures.
- Preserve fallback diagnostic reports on report-call failure. Add an English event message exposing
  that fallback so the demo evaluator can mark the evaluation incomplete without changing Incident fields.
- Add `acceptance_reason` to the demo's local summary (not the shared Incident contract).
- Validate offline with scripted clients; live API and container checks remain separate.
