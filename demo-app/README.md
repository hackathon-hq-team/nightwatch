# Statistics demo source template

`app/calc.py` contains the intentional empty-average bug. Original tests cover
non-empty averages and sums and must pass before investigation.
Supported inputs are lists of finite, non-negative numbers. Expected mean of `[]`: `0.0`.
Other examples: `[10, 20, 30] -> 20`, `[7] -> 7`, `[0, 0] -> 0`.

This folder is a source template, not a separate Git repository and not a deployed API.
From the Nightwatch root run `python -m tools.prepare_demo --output .demo-workspace`.
Use the generated `.demo-workspace/repo` as the runner's repository.
The generated repository contains only the application, original tests and this README;
acceptance answers and evaluation tools stay outside the model's source context.
