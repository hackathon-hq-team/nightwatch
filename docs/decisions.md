# Integration decisions

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
