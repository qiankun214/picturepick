# PicturePick Electron 一键打包（给用户直接执行的入口）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -SkipFrontend   # 只重打包桌面端
#   powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -SkipBuild      # 跳过构建，只重跑自检/生成启动器
#   powershell -ExecutionPolicy Bypass -File scripts\build-electron.ps1 -RunDebug       # 打包后自动打开调试启动器
# 也可以直接双击根目录的 build-electron.cmd（参数同样可用）。
param(
    [switch]$SkipFrontend,
    [switch]$RunDebug,
    [switch]$SkipBuild,
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '============================================' -ForegroundColor Cyan
Write-Host ' PicturePick Electron 一键打包' -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
Write-Host ''

# 复用主构建脚本（前端 → tsc → 后端 sidecar → Electron 绿色版 + zip + 启动器）
if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1') -SkipFrontend:$SkipFrontend
} else {
    Write-Host '[build-electron] -SkipBuild：跳过构建，直接检查已有产物' -ForegroundColor Yellow
}

$exePath = Join-Path $root 'release\win-unpacked\PicturePick.exe'
if (-not (Test-Path $exePath)) {
    throw "打包失败：未生成 $exePath（去掉 -SkipBuild 重新构建）"
}

# ---- Electron 运行时自检（不经 app，先确认 Electron 二进制本身能启动）----
Write-Host ''
Write-Host '== Electron 运行时检查 ==' -ForegroundColor Cyan
try {
    $electronExe = Join-Path $root 'desktop\node_modules\electron\dist\electron.exe'
    if (-not (Test-Path $electronExe)) {
        Write-Host '[WARN] 未找到 electron.exe，跳过运行时检查' -ForegroundColor Yellow
    } else {
        Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
        $verOut = Join-Path $root '.tmp\electron-version.stdout.txt'
        $verErr = Join-Path $root '.tmp\electron-version.stderr.txt'
        Remove-Item $verOut, $verErr -Force -ErrorAction SilentlyContinue
        $ep = Start-Process -FilePath $electronExe -ArgumentList '--version' -PassThru `
            -RedirectStandardOutput $verOut -RedirectStandardError $verErr
        $done = $ep.WaitForExit(15000)
        if (-not $done) { try { Stop-Process -Id $ep.Id -Force -ErrorAction SilentlyContinue } catch { } }
        $ver = ''
        if (Test-Path $verOut) {
            $raw = Get-Content $verOut -Raw -ErrorAction SilentlyContinue
            if ($null -ne $raw) { $ver = ([string]$raw).Trim() }
        }
        $verErrText = ''
        if (Test-Path $verErr) {
            $rawErr = Get-Content $verErr -Raw -ErrorAction SilentlyContinue
            if ($null -ne $rawErr) { $verErrText = ([string]$rawErr).Trim() }
        }
        $exitCode = $null
        try { $exitCode = $ep.ExitCode } catch { $exitCode = $null }
        Write-Host "   electron.exe --version: exit=$exitCode output=$ver"
        if ($exitCode -ne 0 -or -not $ver) {
            Write-Host '[WARN] Electron 二进制自身无法启动：可能被杀毒/安全策略拦截，或缺少 VC++ 运行库。' -ForegroundColor Yellow
            if ($verErrText) {
                $short = $verErrText.Substring(0, [Math]::Min(800, $verErrText.Length))
                Write-Host "   stderr: $short" -ForegroundColor DarkGray
            }
        } else {
            Write-Host '[PASS] Electron 运行时可用' -ForegroundColor Green
        }
    }
} catch {
    Write-Host "[WARN] Electron 运行时检查本身出错（不影响构建产物）：$($_.Exception.Message)" -ForegroundColor Yellow
}

# ---- 浏览器版兜底包（Electron/WebView 都不可用时仍可使用）----
$browserDir = Join-Path $root 'release\browser'
$backendBuild = Join-Path $root 'build\backend'
if (Test-Path (Join-Path $backendBuild 'picturepick-backend.exe')) {
    if (Test-Path $browserDir) { Remove-Item $browserDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $browserDir | Out-Null
    Copy-Item (Join-Path $backendBuild '*') $browserDir -Recurse -Force
    $browserLauncher = @'
@echo off
setlocal
cd /d "%~dp0"
set "ELECTRON_RUN_AS_NODE="
start "PicturePick backend" /min "%~dp0picturepick-backend.exe" serve --no-open --port 8111
timeout /t 2 /nobreak >nul
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (
  start "" "%EDGE%" --app=http://127.0.0.1:8111/
) else (
  start "" http://127.0.0.1:8111/
)
'@
    $browserText = ($browserLauncher -replace "`r`n", "`n") -replace "`n", "`r`n"
    if (-not $browserText.EndsWith("`r`n")) { $browserText += "`r`n" }
    [System.IO.File]::WriteAllText((Join-Path $browserDir 'Start-PicturePick-Browser.cmd'), $browserText, (New-Object System.Text.ASCIIEncoding))
    Write-Host '浏览器版兜底包：release\browser\Start-PicturePick-Browser.cmd' -ForegroundColor Green
} else {
    Write-Host '[WARN] 未找到 build\backend\picturepick-backend.exe，跳过浏览器版兜底包' -ForegroundColor Yellow
}

# ---- 部署到桌面（DSH 工作区 ACL 会阻止 Electron，必须复制到普通目录运行）----
$deployDir = $null
if ($Deploy) {
    $deployDir = Join-Path ([Environment]::GetFolderPath('Desktop')) 'PicturePick'
    Write-Host ''
    Write-Host "== 部署到 $deployDir ==" -ForegroundColor Cyan
    if (Test-Path $deployDir) {
        try { Remove-Item $deployDir -Recurse -Force -ErrorAction Stop }
        catch { throw "无法清理旧目录 ${deployDir}：$($_.Exception.Message)（可能正在运行，请先关闭 PicturePick）" }
    }
    Copy-Item (Join-Path $root 'release\win-unpacked') $deployDir -Recurse -Force
    Remove-Item (Join-Path $deployDir 'logs') -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[PASS] 已部署：$deployDir\PicturePick.exe" -ForegroundColor Green
    try {
        $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'PicturePick.lnk'
        $shellCom = New-Object -ComObject WScript.Shell
        $shortcut = $shellCom.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $deployDir 'PicturePick.exe'
        $shortcut.WorkingDirectory = $deployDir
        $iconPath = Join-Path $deployDir 'resources\icon.ico'
        if (Test-Path $iconPath) { $shortcut.IconLocation = $iconPath }
        $shortcut.Description = 'PicturePick 选片'
        $shortcut.Save()
        Write-Host "[PASS] 桌面快捷方式：$shortcutPath" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] 无法创建桌面快捷方式：$($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '==== 打包完成 ====' -ForegroundColor Green
Write-Host "绿色版目录：release\win-unpacked"
Write-Host "压缩包    ：release\PicturePick-1.0.0-win-x64-portable.zip"
Write-Host ''
Write-Host '重要：不要直接在 E:\picturePick 目录里双击运行（DSH 工作区 ACL 会让 Electron 静默崩溃）。' -ForegroundColor Yellow
Write-Host '      请把 portable zip 复制到桌面等普通目录解压，或用本脚本 -Deploy 部署到桌面。'
Write-Host ''
Write-Host '启动验证：' -ForegroundColor Yellow
if ($deployDir) {
    Write-Host "  1) 直接双击 $deployDir\PicturePick.exe（或桌面 PicturePick 快捷方式）"
    Write-Host "  2) 没反应/排错：双击 $deployDir\Start-PicturePick-Debug.cmd，把 logs 目录发给开发者"
} else {
    Write-Host '  1) 复制 release\PicturePick-1.0.0-win-x64-portable.zip 到桌面并解压'
    Write-Host '  2) 直接双击解压目录里的 PicturePick.exe'
    Write-Host '  3) 或重新执行：build-electron.cmd -Deploy（自动部署到桌面并创建快捷方式）'
}
Write-Host ''
Write-Host '浏览器版兜底包：release\browser\Start-PicturePick-Browser.cmd（可直接双击）'

if ($RunDebug) {
    $debugCmd = Join-Path $root 'release\win-unpacked\Start-PicturePick-Debug.cmd'
    if (Test-Path $debugCmd) {
        Write-Host '正在打开调试启动器...' -ForegroundColor Cyan
        Start-Process -FilePath $debugCmd
    }
}
