# PicturePick 一键构建（Electron 桌面版）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipFrontend        # 只重打包桌面端
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipDesktop         # 只构建前端
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WithInstaller       # 额外尝试 electron-builder 安装包
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WebView2            # 兼容旧方案：同时构建 WebView2 版
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Verify              # 构建后做自检
#
# 产物：
#   release\win-unpacked\PicturePick.exe                        绿色版（双击即用）
#   release\PicturePick-<version>-win-x64-portable.zip          绿色版压缩包
#   （-WithInstaller）release\PicturePick-<version>-win-x64-portable.exe / -setup.exe
param(
    [switch]$SkipInstall,
    [switch]$SkipFrontend,
    [switch]$SkipDesktop,
    [switch]$WithInstaller,
    [switch]$WebView2,
    [switch]$Console,
    [switch]$Verify,
    [switch]$WithElectron  # 兼容旧参数：Electron 现在就是默认方案
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 构建缓存配置（受限环境/离线环境必需；普通环境也无副作用）----
$env:npm_config_cache = Join-Path $root '.cache\npm-build'
$env:npm_config_logs_dir = Join-Path $root '.cache\npm-build\_logs'
$env:npm_config_foreground_scripts = 'true'
$env:electron_config_cache = Join-Path $root '.cache\electron'
$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
$env:ELECTRON_BUILDER_CACHE = Join-Path $root '.cache\electron-builder'
New-Item -ItemType Directory -Force -Path $env:npm_config_cache, $env:electron_config_cache | Out-Null

function Assert-LastExit([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what 失败（exit $LASTEXITCODE）" }
}

# ---- 选择 Python 解释器：优先 .venv，其次 py -3.12，最后 PATH 上的 python ----
# 注意：Microsoft Store 版 Python 会以 WindowsApps 别名的形式抢占 `python` 命令，
# 直接调用 `python` 可能落到没有项目依赖的 Store 版本上（本机就出现过 3.13 抢占）。
function Resolve-PythonExe {
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidate = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate) {
            $path = ([string]$candidate).Trim()
            if (Test-Path $path) { return $path }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    throw '未找到可用的 Python 解释器（需要 Python 3.12；见 docs\PYTHON-UPGRADE.md）'
}

$pythonExe = Resolve-PythonExe
Write-Host "[build] Python: $pythonExe" -ForegroundColor DarkGray
$pyVersionRaw = & $pythonExe -c "import sys; print('%d.%d' % sys.version_info[:2])"
$pyVersion = ([string]$pyVersionRaw).Trim()
Write-Host "[build] Python 版本: $pyVersion"
if ([version]$pyVersion -lt [version]'3.11') {
    Write-Host "[build] 警告：Python $pyVersion 已过时（3.10 已 EOL），建议升级到 3.12，见 docs\PYTHON-UPGRADE.md" -ForegroundColor Yellow
}

# ---- 0. 依赖检查 ----
Write-Host '[build] 检查 Python 依赖（fastapi/uvicorn/Pillow/PyInstaller）...' -ForegroundColor Cyan
& $pythonExe -c "import fastapi, uvicorn, PIL, PyInstaller; print('deps ok (backend)')" 2>&1 | Out-String | Write-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host '[build] 缺少后端打包依赖，请先执行：' -ForegroundColor Yellow
    Write-Host "        `"$pythonExe`" -m pip install -r backend\requirements.txt"
    Write-Host "        `"$pythonExe`" -m pip install pyinstaller"
    throw '后端打包依赖不完整'
}

if ($WebView2) {
    Write-Host '[build] 检查 WebView2 兼容方案的依赖（pywebview/pythonnet）...' -ForegroundColor Cyan
    & $pythonExe -c "import webview, pythonnet; print('deps ok (pywebview)')" 2>&1 | Out-String | Write-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[build] 缺少 pywebview/pythonnet，请执行：' -ForegroundColor Yellow
        Write-Host "        `"$pythonExe`" -m pip install -r desktop_webview\requirements.txt"
        throw 'WebView2 打包依赖不完整'
    }
}

# ---- 1. 前端 ----
if (-not $SkipFrontend) {
    if (-not $SkipInstall -and -not (Test-Path (Join-Path $root 'frontend\node_modules'))) {
        Write-Host '[build] 安装前端依赖...' -ForegroundColor Cyan
        npm --prefix frontend install --foreground-scripts --no-audit --no-fund
        Assert-LastExit '前端依赖安装'
    }
    Write-Host '[build] 构建前端 → backend/picturepick/webui' -ForegroundColor Cyan
    npm --prefix frontend run build
    Assert-LastExit '前端构建'
    if (-not (Test-Path (Join-Path $root 'backend\picturepick\webui\index.html'))) {
        throw '前端构建产物缺失：backend/picturepick/webui/index.html'
    }
}

# ---- 2. Electron 桌面版（默认）----
$electronBuilt = $false
$portableExe = Join-Path $root 'release\win-unpacked\PicturePick.exe'
if (-not $SkipDesktop) {
    if (-not $SkipInstall -and -not (Test-Path (Join-Path $root 'desktop\node_modules'))) {
        Write-Host '[build] 安装 Electron 依赖...' -ForegroundColor Cyan
        npm --prefix desktop install --foreground-scripts --no-audit --no-fund
        Assert-LastExit 'Electron 依赖安装'
    }

    $tsc = Join-Path $root 'desktop\node_modules\typescript\bin\tsc'
    if (-not (Test-Path $tsc)) { throw "缺少 TypeScript：$tsc（请先 npm --prefix desktop install）" }
    Write-Host '[build] 编译 Electron 主进程/preload（tsc）...' -ForegroundColor Cyan
    & node $tsc -p (Join-Path $root 'desktop\tsconfig.json')
    Assert-LastExit 'Electron TypeScript 编译'
    if (-not (Test-Path (Join-Path $root 'desktop\dist\main.js'))) {
        throw 'Electron 编译产物缺失：desktop/dist/main.js'
    }

    Write-Host '[build] PyInstaller 打包后端 sidecar（Python 3.12）...' -ForegroundColor Cyan
    & $pythonExe -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $root 'build') `
        --workpath (Join-Path $root 'build\.pyinstaller-work') `
        (Join-Path $root 'desktop\pyinstaller\picturepick-backend.spec')
    Assert-LastExit '后端 sidecar 打包'
    $backendExe = Join-Path $root 'build\backend\picturepick-backend.exe'
    if (-not (Test-Path $backendExe)) { throw "后端 sidecar 产物缺失：$backendExe" }
    & $backendExe version 2>&1 | Out-String | Write-Host

    Write-Host '[build] 组装 Electron 绿色版（release\win-unpacked + portable zip）...' -ForegroundColor Cyan
    & node (Join-Path $root 'desktop\scripts\pack-portable.mjs')
    Assert-LastExit 'Electron 绿色版打包'
    if (-not (Test-Path $portableExe)) { throw "Electron 绿色版产物缺失：$portableExe" }
    $electronBuilt = $true

    # ---- 生成启动器：清掉 ELECTRON_RUN_AS_NODE 等会干扰 Electron 的环境变量 ----
    $unpacked = Join-Path $root 'release\win-unpacked'
    $normalLauncher = @'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ELECTRON_RUN_AS_NODE="
start "" "%~dp0PicturePick.exe" %*
'@
    $debugLauncher = @'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ELECTRON_RUN_AS_NODE="
set "ELECTRON_ENABLE_LOGGING=1"
set "PICTUREPICK_DESKTOP_DEBUG=1"
set "PICTUREPICK_DESKTOP_LOG_DIR=%~dp0logs"
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo ===== PicturePick debug launch %DATE% %TIME% =====>> "%~dp0logs\console.log"
"%~dp0PicturePick.exe" %* >> "%~dp0logs\console.log" 2>&1
echo exit=%ERRORLEVEL%>> "%~dp0logs\console.log"
echo.
echo Finished. Logs are in the logs folder: console.log / startup.log / main.log / backend.log
pause
'@
    $launcherEncoding = New-Object System.Text.ASCIIEncoding
    foreach ($item in @(
            @{ Path = (Join-Path $unpacked 'Start-PicturePick.cmd'); Text = $normalLauncher },
            @{ Path = (Join-Path $unpacked 'Start-PicturePick-Debug.cmd'); Text = $debugLauncher })) {
        $content = ($item.Text -replace "`r`n", "`n") -replace "`n", "`r`n"
        if (-not $content.EndsWith("`r`n")) { $content += "`r`n" }
        [System.IO.File]::WriteAllText($item.Path, $content, $launcherEncoding)
    }
    Write-Host '       → release\win-unpacked\PicturePick.exe（直接双击即可，不需要 cmd）' -ForegroundColor DarkGray
    Write-Host '       → release\win-unpacked\Start-PicturePick-Debug.cmd（仅排错用：带日志）' -ForegroundColor DarkGray

    if ($WithInstaller) {
        Write-Host '[build] electron-builder 生成安装包（需要联网/缓存，可能失败）...' -ForegroundColor Yellow
        npm --prefix desktop run dist:sandbox
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[build] electron-builder 失败；绿色版仍可用' -ForegroundColor Yellow
        }
    }
}

# ---- 3. WebView2 兼容方案（可选，旧方案；见 docs/PACKAGING-RESEARCH.md）----
if ($WebView2) {
    if ($Console) {
        $env:PICTUREPICK_SPEC_CONSOLE = '1'
        $distPath = Join-Path $root 'build\webview-console'
        $workPath = Join-Path $root 'build\.pyinstaller-webview-console'
        $collectName = 'PicturePickConsole'
        Write-Host '[build] 打包 WebView2 调试版（带控制台）...' -ForegroundColor Cyan
    }
    else {
        Remove-Item Env:\PICTUREPICK_SPEC_CONSOLE -ErrorAction SilentlyContinue
        $distPath = Join-Path $root 'build\webview'
        $workPath = Join-Path $root 'build\.pyinstaller-webview'
        $collectName = 'PicturePick'
        Write-Host '[build] 打包 WebView2 兼容版（PyInstaller + pywebview）...' -ForegroundColor Cyan
    }

    & $pythonExe -m PyInstaller --noconfirm --clean `
        --distpath $distPath `
        --workpath $workPath `
        (Join-Path $root 'desktop_webview\picturepick-desktop.spec')
    Assert-LastExit 'WebView2 桌面版打包'

    $webviewExe = Join-Path $distPath "$collectName\PicturePick.exe"
    if (-not (Test-Path $webviewExe)) { throw "桌面版产物缺失：$webviewExe" }

    if (-not $Console) {
        Write-Host '[build] 组装 release\PicturePick-WebView2 ...' -ForegroundColor Cyan
        $releaseDir = Join-Path $root 'release\PicturePick-WebView2'
        if (Test-Path $releaseDir) { Remove-Item $releaseDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
        Copy-Item (Join-Path $distPath "$collectName\*") $releaseDir -Recurse -Force

        $zipPath = Join-Path $root 'release\PicturePick-WebView2-win-x64.zip'
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        Compress-Archive -Path (Join-Path $releaseDir '*') -DestinationPath $zipPath -CompressionLevel Optimal
        Write-Host "       → $releaseDir\PicturePick.exe" -ForegroundColor DarkGray
        Write-Host "       → $zipPath" -ForegroundColor DarkGray
    }
    else {
        Write-Host "调试版产物：$webviewExe" -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '==== 构建完成 ====' -ForegroundColor Green
if ($electronBuilt) {
    Write-Host '桌面版（Electron，推荐）：release\win-unpacked\PicturePick.exe' -ForegroundColor Green
    Write-Host '压缩包：release\PicturePick-<version>-win-x64-portable.zip' -ForegroundColor Green
    if ($Verify) {
        Write-Host ''
        & (Join-Path $root 'scripts\verify-electron.ps1')
    }
    else {
        Write-Host '自检命令：powershell -ExecutionPolicy Bypass -File scripts\verify-electron.ps1' -ForegroundColor Green
    }
}
if ($WebView2 -and -not $Console) {
    Write-Host '兼容方案（WebView2）：release\PicturePick-WebView2\PicturePick.exe' -ForegroundColor DarkGray
}
Write-Host '命令行版：py -3.12 -m picturepick serve --dir <照片目录>（自动打开浏览器；在 backend/ 下执行）' -ForegroundColor Green
