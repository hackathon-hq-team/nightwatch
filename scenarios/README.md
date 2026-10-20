# Two initial acceptance scenarios

Generate actual `Alert` JSON with real Git commit IDs using
`python -m tools.prepare_demo --output .demo-workspace`.
`example_alert.json` is the older illustrative placeholder, not a runnable generated alert.

| Scenario | Input evidence | Expected behaviour |
| --- | --- | --- |
| `empty-sample` | `average([])` raises `ZeroDivisionError`; real traceback and broken/good commit IDs | Confirm this signature on broken, pass the reproducer on good, then request a patch. Accept only after unchanged tests and full suite pass. |
| `insufficient-evidence` | Synthetic report of memory pressure, no stack trace, input, traffic or dataset | Diagnostic report (`reported`); ask for missing evidence. Empty-input division by zero is a different failure and must not confirm this incident. No patch without confirmed reproduction. |

These are two deterministic starting scenarios, not the planned 20–30-scenario evaluation.
No claims about real API availability, production failures or model quality follow from them.
Known answers live in `evaluation/`, outside the generated application repository.
