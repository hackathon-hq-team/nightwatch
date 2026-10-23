# Integration decisions

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
