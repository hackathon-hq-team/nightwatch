# Restart the local Sentry demo on Windows

Run from the Nightwatch checkout, with Docker Desktop running:

```powershell
.\scripts\start_sentry_demo.ps1
```

The launcher checks the runner image, starts missing receiver, Demo Shop and UI
processes in the background, and reuses an existing managed Cloudflare tunnel.
It does not fetch, merge, publish, trigger an incident or make model calls.
It checks the public endpoint with a signed ignored event and an invalid signature.
Secrets are read from ignored `.env` files and never passed on command lines.

## First-time prerequisites

- Install Nightwatch `.[ui,webhook]` into its `.venv`.
- Prepare a separate Demo Shop checkout, its `.venv` and pinned dependencies.
- Build its runner image as described in the application's handoff.
- Configure Nightwatch `.env`: `NEBIUS_API_KEY`, `NIGHTWATCH_TARGET_REPO`,
  `NIGHTWATCH_RUNNER_IMAGE`, `NIGHTWATCH_SENTRY_PROJECT`, `SENTRY_WEBHOOK_SECRET`
  and the GitHub target. Use an absolute path for the target repository.
- Set `SENTRY_DSN` in the Demo Shop checkout's ignored `.env`. The launcher passes
  only the application's own Sentry settings to it, with its actual Git HEAD as
  `SENTRY_RELEASE` and the `demo-good` commit as `SENTRY_PREVIOUS_RELEASE`.
- Set `NIGHTWATCH_SENTRY_INTEGRATION_URL` to the edit page of the existing internal
  integration, for example `https://your-org.sentry.io/settings/developer-settings/your-integration/`.
- Download the official Windows AMD64 `cloudflared.exe` from
  [Cloudflare's download page](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)
  into `.ui-runs/sentry/cloudflared.exe`.

All processes bind to localhost. Only the signed receiver on port 8000 is exposed
by the tunnel; UI stays at `http://127.0.0.1:8501/` and Demo Shop at port 8001.
Keep one instance of each service. An occupied unhealthy port causes an error;
the launcher never kills an unrelated application.

## When the tunnel address changes

Quick Tunnels have temporary addresses. The script prints the current full
Webhook URL and warns until that exact address has been confirmed as saved.
No additional Sentry API token or broader account permissions are needed.
New addresses can take time to resolve through the computer's DNS. Startup waits
up to 90 seconds (`-WaitSeconds` can change this). If it still times out, retry
Start to reuse the same tunnel; repeatedly restarting it only creates more new
addresses. Certificate validation and system DNS settings are not changed.

1. Open the existing internal integration (optionally use `-OpenSettings`).
2. Replace **Webhook URL** with the printed `https://…trycloudflare.com/webhooks/sentry`.
3. Save Changes. Keep **Client Secret**, permissions and the alert rule unchanged.
4. Record the address you just saved:

```powershell
.\scripts\start_sentry_demo.ps1 -Action ConfirmWebhook -ConfirmedUrl 'https://CURRENT.trycloudflare.com/webhooks/sentry'
```

Confirmation records an operator acknowledgement locally; it is not an API read
of the Sentry account. The signing probe verifies reachability and secret agreement,
not that Sentry has saved the address. Actual Sentry deliveries remain visible in
the alert history and receiver logs. Repeating an existing issue may not trigger a
new-issue rule; the probe intentionally avoids new incidents and paid model calls.

To deliberately replace a tunnel and exercise this workflow:

```powershell
.\scripts\start_sentry_demo.ps1 -Action RestartTunnel -OpenSettings
```

This stops only the recorded cloudflared process after checking its executable
and target port. The receiver, UI and current investigations remain running.
Without `RestartTunnel`, repeated starts preserve the running tunnel's address.

```powershell
.\scripts\start_sentry_demo.ps1 -Action Status
```

Logs, PIDs, current URL and last confirmed webhook are under `.ui-runs/sentry/`,
excluded from Git. After changing `.env` or updating application source, restart
the affected service explicitly; Start reuses healthy processes and does not
claim to reload their configuration. No scheduled task or automatic boot service
is installed. A permanent address requires a separately configured named tunnel
and domain; this workflow deliberately supports temporary URLs with explicit updates.

## Manual validation on 24 October 2026

- Repeated Start reused healthy services and the existing managed tunnel.
- RestartTunnel created a new HTTPS address. Public DNS became available before
  the local resolver; retrying Start reused that same tunnel successfully.
- The new URL was saved and re-opened in the existing Sentry integration, then
  recorded with ConfirmWebhook. Secret and permissions were retained.
- Start restored a stopped Demo Shop through the new isolated launcher and
  preserved the confirmed tunnel address.
- Valid signatures were accepted as ignored events; invalid signatures returned
  401. These startup checks created no incidents or model calls.
