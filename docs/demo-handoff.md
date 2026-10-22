# Demo scenario handoff

The source template contains the original calculator example plus five multi-condition
bug scenarios. Preparation creates **seven** Alert-compatible JSON files. Detailed
contracts and acceptance commands are in [complex-scenarios.md](complex-scenarios.md).
All application code is a local educational fixture; it is not a deployed service.

## Local fixture checks

Use the project Python environment from the repository root:

```bash
python -m pytest -q
python -m tools.prepare_demo --output .demo-workspace
```

Preparation requires Git, the project dependencies and packaging 22+ (already a pytest
dependency in the validated environment). It creates a new directory and refuses to
overwrite it. Choose another output directory for another run.

- `.demo-workspace/repo`: separate Git repository, with no remote.
- `.demo-workspace/alerts/*.json`: real failing SHAs and expected outcomes.
- `.demo-workspace/manifest.json`: explicitly labelled fixture metadata and commit map.

Each complex scenario has a broken snapshot under `fixture/<scenario>` and a reference
repair under `fixture/<scenario>-oracle`. Only the external manifest identifies the
reference SHA; new runtime alerts omit the optional previous-good commit to avoid
leaking the repair through the model's history context.
The original main branch still ends at the calculator regression. Always use the
commit from the chosen alert; inspecting main alone does not show every scenario's history.
Real exceptions and tracebacks are captured by executing trusted fixture inputs.
Only `insufficient-evidence` uses explicitly labelled synthetic incident text.

## Nikita: live run

The merged `scripts/run_demo.py` discovers all seven files automatically. Each declares
`expected`, so a new runtime registry is unnecessary. Configure the provider outside
Git and prepare Timur's Docker image, then follow [the live-run steps](complex-scenarios.md#live-model-run).
Use `--runner docker` for model-generated code. Saved pipeline results need the
separate independent acceptance check before being counted as a solved harder scenario.

For a direct CLI run, pass the generated repository and chosen alert:

```bash
python -m nightwatch.cli run --repo .demo-workspace/repo --alert .demo-workspace/alerts/settlement-rounding.json --runner team --verifier team
```

Do not send `evaluation/`, prepared repairs or acceptance instructions to the runtime
model. The existing context builder supplies source/history from the generated repo.

## Timur: runner and UI

Use the generated repo plus the scenario's alert commit, rather than `demo-app` or
the Nightwatch repository. Run the original suite, then the reproducer, then the full
unchanged suite after the candidate patch. The library scenario uses the real installed
packaging package; it does not need network access inside the container.

The result contract and UI are unchanged. New scenario choices in the UI are a separate
integration task if its selector is still fixed to the original pair. Confirm container
execution and that saved incidents display correctly; follow [Docker setup](docker-runner.md).

## Verification boundary

Offline tests use actual Git snapshots, subprocess pytest, the pipeline and team
verifier, with explicitly scripted model replies. They validate the scenarios and
processing mechanism. They do not measure Nemotron's ability to solve the scenarios.
Docker, live NVIDIA/Nebius calls and live Tavily search remain team integration checks.
