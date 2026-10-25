<#
.SYNOPSIS
  Verify PicturePick desktop packaging artifacts: PyInstaller sidecar, portable fallback bundle,
  electron-builder output.

.DESCRIPTION
  -Mode backend   verify build\backend\picturepick-backend.exe (version / serve / health / port release)
  -Mode portable  run scripts\pack-portable.mjs, validate release\win-unpacked and the zip
  -Mode dist      run electron-builder --dir (use -FullDist for portable + nsis installers)
  -Mode all       backend -> portable -> dist

  Inside the DSH sandbox, electron-builder can fail with spawn EPERM because it spawns
  app-builder.exe/7za.exe with piped stdio. Use -Mode backend/portable in the sandbox, or ask
  the lead to run -Mode dist in a normal (non-sandboxed) shell.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-package.ps1 -Mode backend
  powershell -ExecutionPolicy Bypass -File E:\picturePick\desktop\scripts\verify-package.ps1 -Mode all -FullDist
#>
param(
    [ValidateSet('backend', 'portable', 'dist', 'all')]
    [string]$Mode = 'backend',
    [switch]$FullDist,
    [switch]$SkipBuild,
    [switch]$RequireWebui
)

$ErrorActionPreference = 'Stop'

$desktop = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $desktop
$backendExe = Join-Path $root 'build\backend\picturepick-backend.exe'
$releaseDir = Join-Path $root 'release'
$zipPath = Join-Path $releaseDir 'PicturePick-1.0.0-win-x64-portable.zip'
$failures = New-Object System.Collections.Generic.List[string]

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

function Stop-ProcessTree([int]$processId) {
    $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $p) { return }
    try { $p.Kill() } catch { }
    Start-Sleep -Milliseconds 500
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
        # taskkill /T also covers grandchildren (uvicorn workers); ignore its stderr/exit code.
        try {
            $null = Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID', "$processId", '/T', '/F') -PassThru -WindowStyle Hidden -Wait
        }
        catch { }
    }
}

function Assert-File([string]$path, [string]$label) {
    if (Test-Path $path) {
        $size = (Get-Item $path).Length
        Write-Host "    [PASS] $label -> $path ($size bytes)" -ForegroundColor Green
        return $true
    }
    Write-Host "    [FAIL] missing $label -> $path" -ForegroundColor Red
    $failures.Add($label) | Out-Null
    return $false
}

function Invoke-BackendChecks {
    Write-Step "Verify PyInstaller backend sidecar"
    if (-not (Assert-File $backendExe 'picturepick-backend.exe')) { return }

    # Start-Process and piped stdio are unreliable inside the sandbox, so the backend checks
    # (version / serve / health / webui / port cleanup) live in scripts/verify-backend.mjs,
    # which uses file-descriptor redirection instead of pipes.
    $jsArgs = @('scripts\verify-backend.mjs')
    if ($RequireWebui) { $jsArgs += '--require-webui' }
    Push-Location $desktop
    try {
        node @jsArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [FAIL] verify-backend.mjs exit code $LASTEXITCODE" -ForegroundColor Red
            $failures.Add('backend checks') | Out-Null
        }
    }
    finally { Pop-Location }
}

function Invoke-PortableChecks {
    Write-Step "Verify portable fallback bundle (pack-portable.mjs)"
    Push-Location $desktop
    try {
        node scripts/pack-portable.mjs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [FAIL] pack-portable.mjs exit code $LASTEXITCODE" -ForegroundColor Red
            $failures.Add('pack-portable') | Out-Null
            return
        }
    }
    finally { Pop-Location }

    Assert-File (Join-Path $releaseDir 'win-unpacked\PicturePick.exe') 'release\win-unpacked\PicturePick.exe' | Out-Null
    Assert-File (Join-Path $releaseDir 'win-unpacked\resources\app\dist\main.js') 'win-unpacked\resources\app\dist\main.js' | Out-Null
    Assert-File (Join-Path $releaseDir 'win-unpacked\resources\backend\picturepick-backend.exe') 'embedded backend sidecar' | Out-Null
    Assert-File $zipPath 'portable zip' | Out-Null
}

function Invoke-DistChecks {
    Write-Step "Verify electron-builder pipeline"
    Push-Location $desktop
    try {
        if (-not $SkipBuild) {
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        }
        if ($FullDist) {
            Write-Host "    electron-builder (portable + nsis) ..."
            npm run dist
        }
        else {
            Write-Host "    electron-builder --dir (proves asar + extraResources pipeline) ..."
            npm run dist:dir
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [FAIL] electron-builder exit code $LASTEXITCODE" -ForegroundColor Red
            $failures.Add('electron-builder') | Out-Null
            return
        }
    }
    finally { Pop-Location }

    Assert-File (Join-Path $releaseDir 'win-unpacked\PicturePick.exe') 'electron-builder PicturePick.exe' | Out-Null
    Assert-File (Join-Path $releaseDir 'win-unpacked\resources\backend\picturepick-backend.exe') 'electron-builder embedded backend' | Out-Null
    Assert-File (Join-Path $releaseDir 'win-unpacked\resources\app.asar') 'app.asar' | Out-Null
    if ($FullDist) {
        Get-ChildItem $releaseDir -Filter 'PicturePick-*.exe' -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    [INFO] installer artifact: $($_.FullName) ($($_.Length) bytes)" }
    }
}

if ($Mode -in @('backend', 'all')) { Invoke-BackendChecks }
if ($Mode -in @('portable', 'all')) { Invoke-PortableChecks }
if ($Mode -in @('dist', 'all')) { Invoke-DistChecks }

Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "RESULT: PASS ($Mode)" -ForegroundColor Green
    exit 0
}
Write-Host "RESULT: FAIL ($Mode): $($failures -join '; ')" -ForegroundColor Red
exit 1
