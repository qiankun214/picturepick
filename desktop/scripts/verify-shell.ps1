<#
.SYNOPSIS
  PicturePick desktop startup verification with automatic window screenshot evidence.

.DESCRIPTION
  IMPORTANT: run this OUTSIDE the DSH sandbox.
  Chromium creates named pipes for its Mojo platform channel; the DSH sandbox forbids
  named pipe creation, so electron.exe dies immediately with EXCEPTION_BREAKPOINT
  (even `electron.exe --version` crashes). Window verification therefore has to run in a
  normal (lead/escalated) PowerShell session.

  Modes:
    -Mode shell  Shell-only: skip backend, load a built-in data URL, prove window/render/screenshot.
    -Mode app    Real app: spawn Python backend + real frontend, wait for UI, screenshot.

  Outputs:
    desktop\out\verify-<mode>.png     window screenshot (evidence)
    .tmp\desktop\logs\main.log        main process log
    .tmp\desktop\logs\backend.log     backend stdout/stderr

  After the app exits, the script checks that the backend port was released, proving the
  backend process tree is killed on quit.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode shell
  powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-shell.ps1 -Mode app
#>
param(
    [ValidateSet('shell', 'app')]
    [string]$Mode = 'app',
    [string]$Screenshot = '',
    [int]$TimeoutSec = 150,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'

$desktop = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $desktop
$electronExe = Join-Path $desktop 'node_modules\electron\dist\electron.exe'
$logDir = Join-Path $root '.tmp\desktop\logs'
$mainLog = Join-Path $logDir 'main.log'
$backendLog = Join-Path $logDir 'backend.log'
if ([string]::IsNullOrWhiteSpace($Screenshot)) {
    $Screenshot = Join-Path $desktop ("out\verify-$Mode.png")
}

function Write-Step([string]$message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

function Stop-ProcessTree([int]$processId) {
    $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $p) { return }
    try { $p.Kill() } catch { }
    Start-Sleep -Milliseconds 500
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
        try {
            $null = Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID', "$processId", '/T', '/F') -PassThru -WindowStyle Hidden -Wait
        }
        catch { }
    }
}

function Test-PortOpen([int]$port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $port)
        return ($task.Wait(800) -and $client.Connected)
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

Write-Step "Environment check"
if (-not (Test-Path $electronExe)) {
    throw "Electron not found: $electronExe (run npm install first)"
}
$electronVersion = (Get-Content (Join-Path $desktop 'node_modules\electron\package.json') -Raw | ConvertFrom-Json).version
Write-Host "    Electron $electronVersion"
Write-Host "    workspace $root"

# Electron 33 runs as plain Node when ELECTRON_RUN_AS_NODE is set; clear it.
Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Screenshot) | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Remove-Item $Screenshot -Force -ErrorAction SilentlyContinue
Remove-Item $mainLog -Force -ErrorAction SilentlyContinue
Remove-Item $backendLog -Force -ErrorAction SilentlyContinue

$env:PICTUREPICK_DESKTOP_USER_DATA = Join-Path $root '.tmp\desktop\userdata'
$env:PICTUREPICK_DESKTOP_LOG_DIR = $logDir
$env:PICTUREPICK_DESKTOP_SCREENSHOT = $Screenshot
$env:PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS = if ($Mode -eq 'shell') { '900' } else { '3500' }
$env:PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT = '1'
$env:PICTUREPICK_DESKTOP_HEADLESS = '1'
$env:PICTUREPICK_DESKTOP_BACKEND_TIMEOUT_MS = '90000'
$env:ELECTRON_ENABLE_LOGGING = '1'

if ($Mode -eq 'shell') {
    $html = '<!doctype html><html><head><meta charset="utf-8"><title>PicturePick</title></head>' +
    '<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0f1115;color:#e6e8eb;font-family:Segoe UI;flex-direction:column">' +
    '<h1 style="color:#ffb74d;font-size:42px;margin:0 0 8px">PicturePick</h1>' +
    '<p style="color:#9aa4b2;margin:0">Electron shell OK</p>' +
    '<p id="ua" style="font-size:12px;color:#5c6672;margin-top:14px"></p>' +
    '<script>document.getElementById("ua").textContent=navigator.userAgent</script></body></html>'
    $env:PICTUREPICK_DESKTOP_TEST_URL = 'data:text/html;charset=utf-8,' + [uri]::EscapeDataString($html)
    Write-Host "    shell mode: backend skipped via PICTUREPICK_DESKTOP_TEST_URL"
}
else {
    Remove-Item Env:\PICTUREPICK_DESKTOP_TEST_URL -ErrorAction SilentlyContinue
    if (-not (Test-Path (Join-Path $root 'backend\picturepick\__init__.py'))) {
        throw "Backend not ready: $root\backend\picturepick\__init__.py missing. Use -Mode shell for now."
    }
    if (-not (Test-Path (Join-Path $root 'backend\picturepick\webui\index.html'))) {
        Write-Host "    WARN: frontend build backend\picturepick\webui\index.html missing; page may show a JSON placeholder" -ForegroundColor Yellow
    }
}

if (-not $SkipBuild) {
    Write-Step "Compile TypeScript"
    Push-Location $desktop
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    }
    finally {
        Pop-Location
    }
}

Write-Step "Launch Electron ($Mode mode, timeout ${TimeoutSec}s)"
$startedAt = Get-Date
$proc = Start-Process -FilePath $electronExe -ArgumentList @($desktop) -PassThru

# Poll instead of WaitForExit: the app screenshots and quits itself; if the window never paints
# we still bail out after TimeoutSec.
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) { break }
    if ((Test-Path $Screenshot) -and ((Get-Item $Screenshot).Length -gt 0)) {
        Start-Sleep -Milliseconds 800
        break
    }
    Start-Sleep -Milliseconds 400
}

# Give the app a chance to quit gracefully (it kills the backend in will-quit).
if (-not $proc.HasExited) {
    $null = $proc.WaitForExit(12000)
}
if (-not $proc.HasExited) {
    Write-Host "    timeout: killing Electron process tree" -ForegroundColor Yellow
    Stop-ProcessTree $proc.Id
    Start-Sleep -Seconds 2
}

$elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
try {
    $exitCode = $proc.ExitCode
    Write-Host "    Electron exited, exitCode=$exitCode, elapsed=${elapsed}s"
    if ($exitCode -ne 0) {
        Write-Host "    WARN: non-zero exit (-2147483645 means it is still running inside the sandbox)" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "    Electron process state unavailable, elapsed=${elapsed}s"
}

Start-Sleep -Seconds 1

Write-Step "Check evidence"
$ok = $true
if (Test-Path $Screenshot) {
    $size = (Get-Item $Screenshot).Length
    if ($size -gt 0) {
        Write-Host "    [PASS] window screenshot: $Screenshot ($size bytes)" -ForegroundColor Green
    }
    else {
        Write-Host "    [FAIL] screenshot is empty: $Screenshot" -ForegroundColor Red
        $ok = $false
    }
}
else {
    Write-Host "    [FAIL] screenshot not created: $Screenshot" -ForegroundColor Red
    $ok = $false
}

if (Test-Path $mainLog) {
    Write-Host "    [INFO] main log: $mainLog"
    $mainTail = Get-Content $mainLog -Tail 12
    $mainTail | ForEach-Object { Write-Host "           $_" }
    if ($mainTail -match 'screenshot saved') {
        Write-Host "    [PASS] log confirms screenshot was written" -ForegroundColor Green
    }
    else {
        Write-Host "    [WARN] log has no screenshot confirmation line" -ForegroundColor Yellow
    }
}
else {
    Write-Host "    [FAIL] main log not created (Electron probably never started)" -ForegroundColor Red
    $ok = $false
}

if ($Mode -eq 'app') {
    if (Test-Path $backendLog) {
        Write-Host "    [INFO] backend log: $backendLog"
        Get-Content $backendLog -Tail 8 | ForEach-Object { Write-Host "           $_" }
    }
    Write-Step "Check backend port released after quit"
    $port = $null
    if (Test-Path $mainLog) {
        $line = Get-Content $mainLog | Select-String -Pattern 'port=(\d+)' | Select-Object -Last 1
        if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
    }
    if ($port) {
        Start-Sleep -Milliseconds 700
        if (Test-PortOpen $port) {
            Write-Host "    [FAIL] port $port still open: backend child process not killed" -ForegroundColor Red
            $ok = $false
        }
        else {
            Write-Host "    [PASS] port $port released (backend cleaned up on exit)" -ForegroundColor Green
            Get-Process picturepick-backend -ErrorAction SilentlyContinue | ForEach-Object { Stop-ProcessTree $_.Id }
        }
    }
}

Write-Host ""
if ($ok) {
    Write-Host "RESULT: PASS ($Mode)" -ForegroundColor Green
    exit 0
}
Write-Host "RESULT: FAIL ($Mode). Send the logs above plus desktop\out and .tmp\desktop\logs to desktop-dev." -ForegroundColor Red
exit 1
