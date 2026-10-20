# Verifier: Danik's component

`verifier.py` implements the existing `nightwatch.contracts.Verifier` interface without changing
shared data models. Select it with `--verifier team` in the CLI. The default remains the development
`ReferenceVerifier`; runner integration and model access are configured separately.

## Expected behaviour

- Reproduction requires pytest exit code 1 on the requested broken commit and a failure from the
  exact reproducer file. Exception type, the full alert-signature message, file and function must match.
  The alert message is matched as a substring without truncation or reverse substring matching.
- When a previous good version is known, its result is required: the actual reproducer must pass,
  the exit code must be zero and the failure list empty. Missing evidence is not success.
- A patch must be explicitly applied (`True`), preserve the non-empty baseline test hash, pass the
  reproducer and suite, exit with zero and contain no recorded failures.
- Import errors, timeouts, similar filenames and setup failures do not confirm the target bug.

## Validation

From the repository root: `python -m pytest -q`.
`tests/test_verifier.py` covers valid results and false-success cases. Integration runs real tests
in a small temporary project through the existing `LocalRunner`, with scripted model responses.
This is not an NVIDIA/Nebius API call.

Validated on 20 October 2026, Windows / Python 3.11.9: 39 tests passed.
The integration fix in `nightwatch/dev/local_runner.py` writes patch files with LF so Windows
newline conversion does not produce a corrupt patch for `git apply`.

## Limitations and dependencies

The verifier trusts runner results. The current RunResult does not expose all collected and skipped
tests or whether the full suite ran. This contract alone cannot prove that no tests disappeared or
were skipped. Complete execution and correct test hashes are the runner's responsibility.
Patch scope is checked by the pipeline, not this module.
Messages containing changing identifiers require an agreed stable-signature mechanism; this module
currently uses the signature returned by `Alert.signature()`.

Environment setup exposed an existing `pip install -e .` issue: setuptools discovers multiple
top-level packages (`scenarios`, `nightwatch`). For testing from source, dependencies can be installed
directly: `python -m pip install openai pydantic python-dotenv pytest`.
Packaging fixes and dependency version pinning remain a separate shared task.
