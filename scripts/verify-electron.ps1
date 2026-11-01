# PicturePick Electron 桌面版自检脚本
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\verify-electron.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\verify-electron.ps1 -Exe "release\win-unpacked\PicturePick.exe"
# 步骤：
#   1) 用包内后端 sidecar 做无界面健康检查（/api/health + 首页）；
#   2) 启动真实 Electron 窗口，等待页面加载后自动截图并退出（需要可交互桌面会话）。
# 最后打印 PASS/FAIL、截图路径与日志，便于把结果反馈给开发者。
param(
    [string]$Exe = "",
    [string]$Screenshot = "",
    [int]$DelayMs = 4000,
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Exe)) { $Exe = Join-Path $root 'release\win-unpacked\PicturePick.exe' }
if ([string]::IsNullOrWhiteSpace($Screenshot)) {
    $shotPath = Join-Path $root '.tmp\electron-verify\verify.png'
} else {
    $shotPath = [System.IO.Path]::GetFullPath($Screenshot)
}
$work = Split-Path -Parent $shotPath
New-Item -ItemType Directory -Force -Path $work | Out-Null
$exeDir = Split-Path -Parent $Exe
$backendExe = Join-Path $exeDir 'resources\backend\picturepick-backend.exe'
$port = 18236
$ok = $true

Write-Host '== PicturePick Electron 自检 ==' -ForegroundColor Cyan
Write-Host "   exe     : $Exe"
Write-Host "   截图     : $shotPath"
if (-not (Test-Path $Exe)) {
    Write-Host "[FAIL] 找不到 exe：$Exe" -ForegroundColor Red
    Write-Host '       请先运行：powershell -ExecutionPolicy Bypass -File scripts\build.ps1'
    exit 1
}

# ---------------------------------------------------------------------------
# 步骤 1：后端 sidecar 健康检查（不需要 GUI）
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '== 步骤 1/2：包内后端 sidecar 健康检查 ==' -ForegroundColor Cyan
if (-not (Test-Path $backendExe)) {
    Write-Host "[FAIL] 包内缺少后端 sidecar：$backendExe" -ForegroundColor Red
    Write-Host '       请确认 release\win-unpacked\resources\backend 存在（重新运行 build.ps1）。'
    $ok = $false
}
else {
    $beOut = Join-Path $work 'backend.stdout.txt'
    $beErr = Join-Path $work 'backend.stderr.txt'
    Remove-Item $beOut, $beErr -Force -ErrorAction SilentlyContinue
    $be = Start-Process -FilePath $backendExe -ArgumentList @('serve', '--host', '127.0.0.1', '--port', "$port", '--no-open') `
        -PassThru -RedirectStandardOutput $beOut -RedirectStandardError $beErr
    try {
        $ready = $false
        for ($i = 0; $i -lt 100; $i++) {
            try {
                $health = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/api/health"
                if ($health.StatusCode -eq 200 -and $health.Content -match '"status"\s*:\s*"ok"') { $ready = $true; break }
            } catch { Start-Sleep -Milliseconds 200 }
        }
        if ($ready) {
            Write-Host "[PASS] 后端 /api/health 正常：$($health.Content)" -ForegroundColor Green
            try {
                $index = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$port/"
                if ($index.StatusCode -eq 200 -and $index.Content.Length -gt 100) {
                    Write-Host "[PASS] 内置前端可由后端提供（$($index.Content.Length) chars）" -ForegroundColor Green
                } else {
                    Write-Host '[FAIL] 后端首页响应异常' -ForegroundColor Red
                    $ok = $false
                }
            } catch {
                Write-Host "[FAIL] 后端首页请求失败：$($_.Exception.Message)" -ForegroundColor Red
                $ok = $false
            }
        } else {
            Write-Host '[FAIL] 后端在 20s 内未就绪' -ForegroundColor Red
            if ((Test-Path $beErr) -and ((Get-Item $beErr).Length -gt 0)) { Get-Content $beErr -Tail 20 | ForEach-Object { Write-Host "   $_" } }
            $ok = $false
        }
    } finally {
        if (-not $be.HasExited) {
            try { & taskkill /PID $be.Id /T /F 2>$null | Out-Null }
            catch { try { Stop-Process -Id $be.Id -Force -ErrorAction SilentlyContinue } catch { } }
        }
    }
}

# ---------------------------------------------------------------------------
# 步骤 2：真实窗口 + 截图（需要可交互桌面）
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host "== 步骤 2/2：启动 Electron 窗口并截图（窗口会显示约 $([math]::Round($DelayMs / 1000, 1)) 秒）==" -ForegroundColor Cyan
$userData = Join-Path $work 'userdata'
$guiLogDir = Join-Path $work 'logs'
$guiOut = Join-Path $work 'electron.stdout.txt'
$guiErr = Join-Path $work 'electron.stderr.txt'
Remove-Item $shotPath, $guiOut, $guiErr -Force -ErrorAction SilentlyContinue
Remove-Item $userData, $guiLogDir -Recurse -Force -ErrorAction SilentlyContinue

$env:PICTUREPICK_DESKTOP_SCREENSHOT = $shotPath
$env:PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT = '1'
$env:PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS = "$DelayMs"
$env:PICTUREPICK_DESKTOP_USER_DATA = $userData
$env:PICTUREPICK_DESKTOP_LOG_DIR = $guiLogDir
$env:PICTUREPICK_DESKTOP_DEBUG = '1'
# 某些开发/沙箱环境会把 ELECTRON_RUN_AS_NODE=1 传给子进程，Electron 会以 Node 模式静默退出
Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

try {
    $proc = Start-Process -FilePath $Exe -PassThru -RedirectStandardOutput $guiOut -RedirectStandardError $guiErr
    $exited = $proc.WaitForExit($TimeoutSec * 1000)
    if (-not $exited) {
        Write-Host "[WARN] $TimeoutSec 秒内未退出，强制结束" -ForegroundColor Yellow
        try { & taskkill /PID $proc.Id /T /F 2>$null | Out-Null }
        catch { try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { } }
    } else {
        Write-Host "   窗口退出码: $($proc.ExitCode)"
    }
} finally {
    foreach ($name in 'PICTUREPICK_DESKTOP_SCREENSHOT', 'PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT',
        'PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS', 'PICTUREPICK_DESKTOP_USER_DATA', 'PICTUREPICK_DESKTOP_LOG_DIR',
        'PICTUREPICK_DESKTOP_DEBUG') {
        Remove-Item ("Env:\" + $name) -ErrorAction SilentlyContinue
    }
}

Write-Host ''
Write-Host '== 结果 ==' -ForegroundColor Cyan
if ((Test-Path $shotPath) -and ((Get-Item $shotPath).Length -gt 0)) {
    Write-Host "[PASS] 窗口截图：$shotPath（$((Get-Item $shotPath).Length) 字节）" -ForegroundColor Green
} else {
    Write-Host "[FAIL] 未生成窗口截图：$shotPath" -ForegroundColor Red
    $ok = $false
    if ((Test-Path $guiErr) -and ((Get-Item $guiErr).Length -gt 0)) {
        Write-Host '== Electron stderr（末尾 20 行）==' -ForegroundColor Yellow
        Get-Content $guiErr -Tail 20 | ForEach-Object { Write-Host "   $_" }
    }
}

$mainLog = Join-Path $guiLogDir 'main.log'
$backendLog = Join-Path $guiLogDir 'backend.log'
if (Test-Path $mainLog) {
    Write-Host ''
    Write-Host '== main.log（末尾 15 行）==' -ForegroundColor Cyan
    Get-Content $mainLog -Encoding UTF8 -Tail 15 | ForEach-Object { Write-Host "   $_" }
} else {
    Write-Host "[WARN] 未生成 main.log：$mainLog" -ForegroundColor Yellow
}
if (Test-Path $backendLog) {
    Write-Host ''
    Write-Host '== backend.log（末尾 10 行）==' -ForegroundColor Cyan
    Get-Content $backendLog -Encoding UTF8 -Tail 10 | ForEach-Object { Write-Host "   $_" }
}

Write-Host ''
if ($ok) {
    Write-Host 'RESULT: PASS —— Electron 窗口可正常启动并加载应用。' -ForegroundColor Green
    exit 0
}
Write-Host 'RESULT: FAIL —— 请把上面的输出、截图路径与日志内容发给开发者。' -ForegroundColor Red
exit 1
