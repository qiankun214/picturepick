# PicturePick 桌面版（WebView2）启动自检脚本
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1 -Dir "D:\我的照片"
# 步骤：
#   1) 不开窗口自检（--selftest）：检查内置后端/前端/pythonnet/WebView2 桥接是否可用；
#   2) 启动真实窗口，等待页面加载后自动截图 + 采集 DOM 断言 + 自动退出（窗口显示约 7 秒）。
# 最后打印 PASS/FAIL、截图路径、日志，便于把结果反馈给开发者。
param(
    [string]$Exe = "",
    [string]$Dir = "",
    [string]$Screenshot = "",
    [int]$DelayMs = 7000,
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Exe)) { $Exe = Join-Path $root 'release\PicturePick-WebView2\PicturePick.exe' }
if ([string]::IsNullOrWhiteSpace($Dir)) { $Dir = Join-Path $root 'samples\photos' }
if ([string]::IsNullOrWhiteSpace($Screenshot)) { $Screenshot = Join-Path $root '.tmp\desktop-verify\verify.png' }

Write-Host "== PicturePick 桌面版自检 ==" -ForegroundColor Cyan
Write-Host "   exe     : $Exe"
Write-Host "   照片目录: $Dir"
if (-not (Test-Path $Exe)) {
    Write-Host "[FAIL] 找不到 exe：$Exe" -ForegroundColor Red
    Write-Host "       请先运行：powershell -ExecutionPolicy Bypass -File scripts\build.ps1"
    exit 1
}
if (-not (Test-Path $Dir)) {
    Write-Host "[FAIL] 照片目录不存在：$Dir" -ForegroundColor Red
    exit 1
}

$work = Split-Path -Parent $Screenshot
New-Item -ItemType Directory -Force -Path $work | Out-Null

# ---------------------------------------------------------------------------
# 步骤 1：不开窗口自检
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '== 步骤 1/2：不开窗口自检（--selftest）==' -ForegroundColor Cyan
$selfLogDir = Join-Path $work 'selftest-logs'
$selfOut = Join-Path $work 'selftest.stdout.txt'
$selfErr = Join-Path $work 'selftest.stderr.txt'
Remove-Item $selfLogDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $selfOut, $selfErr -Force -ErrorAction SilentlyContinue
$env:PICTUREPICK_DESKTOP_LOG_DIR = $selfLogDir
$selfProc = Start-Process -FilePath $Exe -ArgumentList @('--selftest') -PassThru -RedirectStandardOutput $selfOut -RedirectStandardError $selfErr
$selfExited = $selfProc.WaitForExit(120000)
if (-not $selfExited) {
    Write-Host '[WARN] 自检超时，强制结束' -ForegroundColor Yellow
    taskkill /PID $selfProc.Id /T /F 2>&1 | Out-Null
} else {
    Write-Host "   自检退出码: $($selfProc.ExitCode)"
}
$selfLog = Join-Path $selfLogDir 'desktop.log'
$selfText = if (Test-Path $selfLog) { Get-Content $selfLog -Raw -Encoding utf8 } else { '' }
if ($selfText -match 'SELFTEST RESULT: PASS') {
    Write-Host '[PASS] 内置运行时自检通过（后端/前端/pythonnet/WebView2 桥接）' -ForegroundColor Green
} else {
    Write-Host '[FAIL] 内置运行时自检未通过：' -ForegroundColor Red
    if ($selfText) { Write-Host ($selfText.Trim()) }
    if ((Test-Path $selfErr) -and ((Get-Item $selfErr).Length -gt 0)) { Get-Content $selfErr -Tail 30 | ForEach-Object { Write-Host "   $_" } }
    Write-Host ''
    Write-Host '请把以上输出发给开发者（自检日志：' -NoNewline
    Write-Host "$selfLog)" -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 步骤 2：真实窗口 + 截图
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '== 步骤 2/2：启动真实窗口并截图（窗口会显示约 7 秒）==' -ForegroundColor Cyan
$evalJson = [System.IO.Path]::ChangeExtension($Screenshot, '.json')
$guiOut = Join-Path $work 'exe.stdout.txt'
$guiErr = Join-Path $work 'exe.stderr.txt'
$userData = Join-Path $work 'userdata'
$guiLogDir = Join-Path $work 'logs'
Remove-Item $Screenshot, $evalJson, $guiOut, $guiErr -Force -ErrorAction SilentlyContinue
Remove-Item $userData, $guiLogDir -Recurse -Force -ErrorAction SilentlyContinue

$env:PICTUREPICK_DESKTOP_SCREENSHOT = $Screenshot
$env:PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT = '1'
$env:PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS = "$DelayMs"
$env:PICTUREPICK_DESKTOP_USER_DATA = $userData
$env:PICTUREPICK_DESKTOP_LOG_DIR = $guiLogDir

$startedAt = Get-Date
$proc = Start-Process -FilePath $Exe -ArgumentList @('--dir', $Dir) -PassThru -RedirectStandardOutput $guiOut -RedirectStandardError $guiErr
$exited = $proc.WaitForExit($TimeoutSec * 1000)
$elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
if (-not $exited) {
    Write-Host "[WARN] $TimeoutSec 秒内未退出，强制结束进程" -ForegroundColor Yellow
    taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
} else {
    Write-Host "   进程退出码: $($proc.ExitCode)（耗时 ${elapsed}s）"
}

$ok = $true
Write-Host ''
Write-Host "== 结果 ==" -ForegroundColor Cyan
if ((Test-Path $Screenshot) -and ((Get-Item $Screenshot).Length -gt 0)) {
    Write-Host "[PASS] 窗口截图：$Screenshot（$((Get-Item $Screenshot).Length) 字节）" -ForegroundColor Green
} else {
    Write-Host "[FAIL] 未生成窗口截图：$Screenshot" -ForegroundColor Red
    $ok = $false
}
if (Test-Path $evalJson) {
    $eval = Get-Content $evalJson -Raw -Encoding utf8
    Write-Host "[INFO] 页面断言：$eval"
    if ($eval -match '"pickerView":true') {
        Write-Host "[PASS] 页面进入选片视图" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 页面未进入选片视图（请检查日志）" -ForegroundColor Yellow
    }
} else {
    Write-Host "[WARN] 未生成页面断言 JSON（可能窗口未加载完成）" -ForegroundColor Yellow
}
$guiLog = Join-Path $guiLogDir 'desktop.log'
if (Test-Path $guiLog) {
    Write-Host ''
    Write-Host "== 应用日志（末尾 20 行）==" -ForegroundColor Cyan
    Get-Content $guiLog -Tail 20 | ForEach-Object { Write-Host "   $_" }
} else {
    Write-Host "[WARN] 未生成应用日志：$guiLog" -ForegroundColor Yellow
}
if ((Test-Path $guiErr) -and ((Get-Item $guiErr).Length -gt 0)) {
    Write-Host ''
    Write-Host "== stderr（末尾 20 行）==" -ForegroundColor Yellow
    Get-Content $guiErr -Tail 20 | ForEach-Object { Write-Host "   $_" }
}

if ($ok) {
    Write-Host ''
    Write-Host 'RESULT: PASS —— 桌面窗口可正常启动并加载应用。' -ForegroundColor Green
    exit 0
}
Write-Host ''
Write-Host 'RESULT: FAIL —— 请把上面的输出、截图路径与日志内容发给开发者。' -ForegroundColor Red
exit 1
