[CmdletBinding()]
param(
    [ValidateSet('Start', 'Status', 'RestartTunnel', 'ConfirmWebhook')]
    [string]$Action = 'Start',
    [string]$ConfirmedUrl = '',
    [switch]$OpenSettings,
    [ValidateRange(10, 180)]
    [int]$WaitSeconds = 90
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $root '.ui-runs/sentry'
$python = Join-Path $root '.venv/Scripts/python.exe'
$cloudflared = [System.IO.Path]::GetFullPath((Join-Path $stateDir 'cloudflared.exe'))
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare the Nightwatch .venv first.' }
Push-Location $root
try {
    $settings = & $python -c "import json; from dotenv import dotenv_values; v=dotenv_values('.env'); print(json.dumps({k:v.get(k,'') for k in ['NIGHTWATCH_TARGET_REPO','NIGHTWATCH_RUNNER_IMAGE','NIGHTWATCH_SENTRY_PROJECT','NIGHTWATCH_SENTRY_INTEGRATION_URL']}))" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read local configuration.' }
    $integrationUrl = $settings.NIGHTWATCH_SENTRY_INTEGRATION_URL
    function Read-Health([string]$Url) {
        try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { return $null }
    }
    function Start-Hidden([string]$Name, [string]$Executable, [string[]]$CommandArgs) {
        $process = Start-Process -FilePath $Executable -ArgumentList $CommandArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir "$Name.stdout.log") -RedirectStandardError (Join-Path $stateDir "$Name.stderr.log") -PassThru
        $process.Id | Set-Content (Join-Path $stateDir "$Name.pid")
    }
    function Ensure-Service([string]$Name, [int]$Port, [string]$HealthPath, [string[]]$CommandArgs) {
        $healthUrl = "http://127.0.0.1:$Port$HealthPath"
        $health = Read-Health $healthUrl
        if (-not $health) {
            if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
                throw "Port $Port is occupied but $Name health check failed. No process was stopped."
            }
            Start-Hidden $Name $python $CommandArgs
            for ($attempt = 0; $attempt -lt 20; $attempt++) {
                Start-Sleep -Milliseconds 500
                $health = Read-Health $healthUrl
                if ($health) { break }
            }
        }
        if (-not $health) { throw "$Name did not start; inspect .ui-runs/sentry/$Name.stderr.log" }
        if ($Name -eq 'receiver' -and ($health.status -ne 'ok' -or -not $health.receiver_configured)) { throw 'Receiver signing secret is not configured.' }
        if ($Name -eq 'shop' -and $health.service -ne 'demo-shop') { throw 'Port 8001 is not Demo Shop.' }
        if ($Name -eq 'ui' -and $health -ne 'ok') { throw 'Unexpected UI health response.' }
        Write-Host "$Name ready: $healthUrl"
    }
    function Get-TunnelProcess {
        $pidFile = Join-Path $stateDir 'tunnel.pid'
        if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
        $tunnelId = 0
        if (-not [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$tunnelId)) { return $null }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$tunnelId"
        if ($process -and $process.ExecutablePath -eq $cloudflared -and $process.CommandLine -match 'http://127\.0\.0\.1:8000') { return $process }
        return $null
    }
    function Get-TunnelUrl {
        $logFile = Join-Path $stateDir 'tunnel.stderr.log'
        if (-not (Test-Path -LiteralPath $logFile)) { return '' }
        $match = [regex]::Match((Get-Content $logFile -Raw), 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) { return $match.Value }
        return ''
    }
    if ($Action -in @('Start', 'RestartTunnel')) {
        if (-not $settings.NIGHTWATCH_TARGET_REPO -or -not $settings.NIGHTWATCH_SENTRY_PROJECT) { throw 'Configure target repository and numeric Sentry project in .env.' }
        & docker image inspect $settings.NIGHTWATCH_RUNNER_IMAGE --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and build the configured runner image.' }
        if (-not (Test-Path -LiteralPath $cloudflared)) { throw 'Install official cloudflared at .ui-runs/sentry/cloudflared.exe; see docs/local-sentry-demo.md.' }
        Ensure-Service 'receiver' 8000 '/healthz' @('-m', 'uvicorn', 'app.webhook:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1')
        Ensure-Service 'shop' 8001 '/health' @('scripts/run_sentry_demo_shop.py')
        Ensure-Service 'ui' 8501 '/_stcore/health' @('-m', 'streamlit', 'run', 'app/streamlit_app.py', '--server.address', '127.0.0.1', '--server.port', '8501')
        $tunnelProcess = Get-TunnelProcess
        if ($Action -eq 'RestartTunnel' -and $tunnelProcess) {
            # Only the verified executable recorded by this launcher is stopped.
            Stop-Process -Id $tunnelProcess.ProcessId
            $tunnelProcess = $null
        }
        if (-not $tunnelProcess) {
            Start-Hidden 'tunnel' $cloudflared @('tunnel', '--url', 'http://127.0.0.1:8000', '--no-autoupdate')
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                Start-Sleep -Milliseconds 500
                if (Get-TunnelUrl) { break }
            }
        }
    }
    $baseUrl = Get-TunnelUrl
    if (-not (Get-TunnelProcess) -or -not $baseUrl) { throw 'No managed tunnel is running. Run with -Action Start.' }
    $baseUrl | Set-Content (Join-Path $stateDir 'public-url.txt')
    $webhookUrl = "$baseUrl/webhooks/sentry"
    Write-Host "Webhook URL: $webhookUrl"
    $confirmedFile = Join-Path $stateDir 'confirmed-webhook.txt'
    if ($Action -eq 'ConfirmWebhook') {
        if ($ConfirmedUrl -ne $webhookUrl) { throw 'Pass -ConfirmedUrl with the exact URL saved in Sentry.' }
    }
    $publicReady = $false
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        $health = Read-Health "$baseUrl/healthz"
        if ($health -and $health.status -eq 'ok' -and $health.receiver_configured) { $publicReady = $true; break }
        if ($Action -eq 'Status') { break }
        Write-Host 'Waiting for public DNS and tunnel readiness...'
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (-not $publicReady) { throw 'Public receiver is not reachable yet. Inspect tunnel.stderr.log, then retry Start to reuse this tunnel (do not restart it just for DNS propagation).' }
    & $python scripts/check_sentry_receiver.py $baseUrl
    if ($LASTEXITCODE -ne 0) { throw 'Public receiver signing check failed.' }
    if ($Action -eq 'ConfirmWebhook') { $webhookUrl | Set-Content $confirmedFile }
    $confirmed = (Test-Path -LiteralPath $confirmedFile) -and ((Get-Content $confirmedFile -Raw).Trim() -eq $webhookUrl)
    Write-Host "`nUI: http://127.0.0.1:8501/"
    Write-Host "Webhook URL: $webhookUrl"
    if ($confirmed) {
        Write-Host 'Webhook address matches the last operator-confirmed Sentry configuration.' -ForegroundColor Green
    } else {
        Write-Warning 'ACTION REQUIRED: save this Webhook URL in the existing Sentry integration. Keep the Client Secret unchanged.'
        Write-Host "Settings: $integrationUrl"
        Write-Host "After saving: .\scripts\start_sentry_demo.ps1 -Action ConfirmWebhook -ConfirmedUrl '$webhookUrl'"
    }
    if ($OpenSettings -and -not $confirmed) {
        if ($integrationUrl -notmatch '^https://[a-z0-9.-]*sentry\.io/settings/developer-settings/[a-z0-9-]+/$') { throw 'Set a valid NIGHTWATCH_SENTRY_INTEGRATION_URL in .env.' }
        Start-Process $integrationUrl
    }
} finally { Pop-Location }
