# Scenario acceptance, Sentry receiver and Draft PRs

## Install and run

From a source checkout (the console and integration tools are source-tree apps):

```powershell
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -e ".[ui,webhook,test-integrations]"
docker build -t nightwatch-runner:dev -f docker/runner/Dockerfile .
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

All seven scenarios appear in the sidebar. Scripted demo uses prepared responses;
live mode uses the real configured model. Both execute tests only through Docker.
For the five complex scenarios, pipeline `verified` is followed by the independent
acceptance tool: frozen private tests, known-good oracle commit, exact passing-node
set and constrained patch scope. The UI shows acceptance separately, including failure
reasons, individual checks, run output and a downloadable report. Reference answers
and acceptance runs never enter the model's evidence, history or memory.

## Receive Sentry alerts

Set these server-side values in `.env` (never send keys in chat or browser widgets):

```dotenv
NEBIUS_API_KEY=<your Nebius key>
NIGHTWATCH_TARGET_REPO=C:/path/to/your/application
NIGHTWATCH_SENTRY_PROJECT=<numeric Sentry project ID>
SENTRY_WEBHOOK_SECRET=<Sentry internal integration client secret>
NIGHTWATCH_GITHUB_REPO=owner/application
NIGHTWATCH_GITHUB_BASE=main
```

Start a second terminal from the same checkout:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.webhook:app --host 127.0.0.1 --port 8000 --workers 1
```

Configure a Sentry internal integration issue-alert action to send `event_alert`
deliveries to `/webhooks/sentry`. See [Sentry webhook documentation](https://docs.sentry.io/organization/integrations/integration-platform/webhooks/).
The handler checks HMAC-SHA256 over the exact body against `Sentry-Hook-Signature`,
uses a constant-time comparison, limits bodies to 1 MiB and rejects events from other
projects. Unsupported resources and resolution/update actions are ignored. An unsigned
request is never accepted. Supported alert envelope: `{action: "triggered", data: {event: {...}}}`.

The Sentry SDK must send `release=<full lowercase Git SHA>` and optionally a
`previous_release` tag. Commits must already exist in the configured checkout. The
receiver never fetches arbitrary URLs, chooses a repo from payload data or maps a
human release name to an unverified commit. Sync the trusted checkout operationally.
Common structured credential fields and configured secrets are redacted before
conversion to Alert; do not send production secrets in unstructured logs.

New delivery: `202 {job, duplicate: false}`. Retries for the same project/event identity
return the original job with `200`, including after a process restart. Bad signature:
401; wrong project: 403; bad event/commit: 422; unavailable configuration/runner or busy
worker: 503. Busy replies include Retry-After so a new delivery is not silently lost.
`GET /jobs/<job>` requires `Authorization: Bearer <SENTRY_WEBHOOK_SECRET>` and returns
only state and incident ID. `/healthz` contains no payload or credentials.

Creating a Sentry integration and exposing this localhost endpoint over HTTPS are
deployment tasks. No public tunnel or account change is made by this implementation.

## Shared execution and recovery

UI and receiver must run from the same source root with the same history settings.
They share `.ui-runs/state/jobs.sqlite3`, which atomically allows only one investigation
across processes. The receiver does not block its HTTP response while a model works.
Its durable job/incident mapping lets the UI discover webhook incidents, progress
and failures. Scripted histories remain separate from live histories. Do not run
multiple UI/receiver copies against different roots expecting a shared lock.

Jobs do not resume automatically after a process crash. Stop the original worker
before explicitly releasing its stale slot:

```powershell
.\.venv\Scripts\python.exe -m app.storage --abandon <job-id>
```

This marks the old job failed and preserves delivery deduplication. Replay requires
a genuinely new event; it never causes an implicit second billable model call.

## Create a Draft PR

Webhook incidents from the configured application can publish a verified patch.
In the UI, review the patch and target, acknowledge publication and click **Create
Draft PR**. Acknowledgement is local product UX, not a new authentication flow.
Configure `gh auth login` on the server with write access to Contents and Pull requests
for the target repository. Only github.com is currently supported.

Before writing, Nightwatch requires a real-repository incident, a verified final patch,
matching target branch/release SHA and a fresh Docker reproduction/full-suite check.
Every previously passing original test must still appear in the final passing-node
set; missing or newly skipped original tests reject publication.
It publishes the actual tested application files and the frozen reproduction test.
Only Python application changes are supported automatically; protected/configuration
paths, modified original tests and unexpected runtime files are rejected.

GitHub's [tree API](https://docs.github.com/en/rest/git/trees) preserves all unchanged
base files; a new commit points at that tree. A deterministic `codex/nightwatch-inc-*`
branch and a draft pull request are created. The title/body include the incident,
test results and model cost, without attaching the raw event or logs. No merge/deploy/base
branch update occurs. An existing PR is reused on retry. A branch with different
content is never overwritten. A per-incident SQLite claim prevents duplicate clicks
across browser sessions. Network failures retain enough identity for safe retries;
an interrupted publication process must be stopped before its publishing claim is reset.

```powershell
.\.venv\Scripts\python.exe -m app.storage --reset-publication <incident-id>
```

Simulated alerts (including live models on generated demo repos) cannot publish to
the team's real repository: their private fixture commits do not exist on GitHub.
Their patch and reproducer remain downloadable. Prepare a dedicated remote demo
application checkout if a real GitHub PR is needed for a presentation.

The UI's localhost binding is preserved. Authentication, TLS and infrastructure
deployment remain separate tasks before remote hosting.

## Validation on 24 October 2026

- All five complex scripted scenarios completed pipeline and independent acceptance
  in real Docker containers; the signed HTTP receiver also drove a scripted alert
  through real Docker reproduction and verification.
- PR tests exercised actual Docker-generated publication artifacts with an injected
  GitHub API. They checked draft-only creation, tested-file content, stable retry
  identity, moved-base refusal, acceptance failure, test-hash failure and missing
  original tests. No actual GitHub branch or pull request was created.
- Final full regression after the shared Windows history retry fix:
  `NIGHTWATCH_TEST_DOCKER=1 python -m pytest -q` — **187 passed in 261.52s**.
  Report: `.qa-results/all-integrations-verified.xml`. Additional targeted checks
  cover credential-header redaction and the last publication guard.
- Browser check: `snapshot-pagination` reached `verified` and displayed a separate
  successful independent acceptance result with downloadable details.
- No Nebius API key, Sentry signing secret or target application is configured on
  this host. Live provider calls, delivery from a real Sentry account and actual
  GitHub publication require those settings and remain unvalidated externally.
