# Local application source template

`app/calc.py` contains the intentional empty-average bug. Original tests cover
non-empty averages and sums and must pass before investigation.
Supported inputs are lists of finite, non-negative numbers. Expected mean of `[]`: `0.0`.
Other examples: `[10, 20, 30] -> 20`, `[7] -> 7`, `[0, 0] -> 0`.

Additional modules expose these contracts in their public function docstrings:

- `settlement.allocate_settlements`: fee-adjusted weighted payouts in exact currency units.
- `inventory.reconcile_inventory`: tenant-scoped, ordered and idempotent stock-event replay.
- `pagination.export_events`: complete tenant exports at a fixed snapshot with composite cursors.
- `reservations.reservation_peaks`: timezone-aware, half-open bookings with weighted capacity.
- `plugin_binding.select_plugin_release`: registry aliases and PEP 440-compatible release selection.

The source template also holds three opt-in advanced modules: `refunds.refund_ledger`,
`event_projection.project_entities`, and `dependency_resolver.resolve_dependencies`.
Use `python -m tools.prepare_demo --suite advanced --output .qa-results/advanced-workspace`
to select them. Preparation copies only the selected suite's modules and tests plus
the calculator baseline; the default suite remains unchanged. Full contracts and
acceptance instructions are in the main repository's `docs/advanced-scenarios.md`.

Original tests cover ordinary paths and pass before the reported regressions are investigated.
The plugin module uses the real packaging dependency already required by pytest.

This folder is a source template, not a separate Git repository and not a deployed API.
From the Nightwatch root run `python -m tools.prepare_demo --output .demo-workspace`.
Use the generated `.demo-workspace/repo` as the runner's repository.
The generated repository contains only the application, original tests and this README;
acceptance answers and evaluation tools stay outside the model's source context.
