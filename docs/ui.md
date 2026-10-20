# Nightwatch operator console

Run from the repository root with Docker's Linux Engine and the runner image ready:

```powershell
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -e ".[ui]"
docker build -t nightwatch-runner:dev -f docker/runner/Dockerfile .
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

Open http://localhost:8501. The committed Streamlit configuration binds to loopback
and disables usage telemetry. The console has no authentication; remote hosting
and webhook integration require a separate deployment/access-control step.

## Run an incident

Select one of two scenarios and click **Run investigation**. Scripted demo is the
default: prepared model responses, real Git snapshots, Docker execution and the team
verifier. It needs no API key and incurs no API cost. It demonstrates the mechanism,
not model quality. The calculator template intentionally contains the bug.

For **Live Nemotron**, configure `NEBIUS_API_KEY` and model settings in `.env`, restart
the server, acknowledge API charges and click **Run live investigation**. This runs
the actual model pipeline on the selected generated demo repository. No API request
is made just by opening the console or choosing live mode. Existing incident/daily
budgets apply; they are not atomic hard spending caps across separate processes.

Both modes require the local runner image, execute through Docker only and permit
one job at a time in this UI process. Reruns and browser polling never resubmit jobs.
The worker continues if the browser disconnects while the server stays alive.
There is no job resume after a server restart; unfinished saved incidents are labelled
incomplete. A separate CLI/server process is outside the UI's single-job guard.

## Inspect results

- Investigation: status, hypotheses, timeline, verified diff or diagnostic report.
- Evidence: the actual E1… context fragments and memory references.
- Tests & cost: broken/good runs, verifier checks, patch attempts, model calls,
  token counts and estimated cost using the configured price table.
- Downloads: verified patch, reproduction test and complete incident JSON.

No GitHub PR is created from this screen yet. A verified patch is not a deployment.

## Data

Each simulated alert gets a fresh Git fixture in `.ui-runs/<job-id>/`.
Scripted incidents are stored in `.ui-runs/demo-history/`; live incidents and live
memory use `NIGHTWATCH_HISTORY` (default `history/`). Demo history is never supplied
to live model calls. History from CLI runs in the same configured directory also
appears in the console. API keys are read server-side, never entered in browser
widgets. The configured key is redacted from displayed errors and JSON exports.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ui.py
$env:NIGHTWATCH_TEST_DOCKER = "1"
.\.venv\Scripts\python.exe -m pytest -q tests/test_ui.py
```

The tests exercise rendered terminal outcomes and live-key guards with Streamlit
AppTest, memory separation, duplicate-launch prevention, Windows sharing retries
and both background-worker outcomes with scripted models and real Docker execution.

Validated on 20 October 2026: `tests/test_ui.py` plus `tests/test_docker_runner.py`
with `NIGHTWATCH_TEST_DOCKER=1` — **28 passed in 35.22s**. Both scenarios were also
started through the browser: empty sample reached `verified`, memory pressure
reached `reported` with zero patch attempts. Live model calls were not made.
