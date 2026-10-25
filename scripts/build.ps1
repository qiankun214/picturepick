# PicturePick 一键构建
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Verify            # 构建后自动做窗口自检
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Console            # 构建带控制台的调试版（看 traceback）
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WithElectron       # 额外构建 Electron 版（本机可能无法运行）
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipFrontend       # 只重新打包桌面端
param(
    [switch]$SkipInstall,
    [switch]$SkipFrontend,
    [switch]$SkipDesktop,
    [switch]$WithElectron,
    [switch]$WithInstaller,
    [switch]$Console,
    [switch]$Verify
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

# ---- 0. 依赖检查 ----
Write-Host '[build] 检查 Python 打包依赖（fastapi/uvicorn/Pillow/pywebview/pythonnet/PyInstaller）...' -ForegroundColor Cyan
python -c "import fastapi, uvicorn, PIL, webview, pythonnet, PyInstaller; print('deps ok')" 2>&1 | Out-String | Write-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host '[build] 缺少依赖，请先执行：' -ForegroundColor Yellow
    Write-Host '        python -m pip install -r backend\requirements.txt'
    Write-Host '        python -m pip install -r desktop_webview\requirements.txt'
    Write-Host '        python -m pip install pyinstaller'
    throw '打包依赖不完整'
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

# ---- 2. WebView2 桌面版（PyInstaller + pywebview）----
if (-not $SkipDesktop) {
    if ($Console) {
        $env:PICTUREPICK_SPEC_CONSOLE = '1'
        $distPath = Join-Path $root 'build\webview-console'
        $workPath = Join-Path $root 'build\.pyinstaller-webview-console'
        $collectName = 'PicturePickConsole'
        Write-Host '[build] 打包 WebView2 调试版（带控制台，用于查看 traceback）...' -ForegroundColor Cyan
    }
    else {
        Remove-Item Env:\PICTUREPICK_SPEC_CONSOLE -ErrorAction SilentlyContinue
        $distPath = Join-Path $root 'build\webview'
        $workPath = Join-Path $root 'build\.pyinstaller-webview'
        $collectName = 'PicturePick'
        Write-Host '[build] 打包 WebView2 桌面版（PyInstaller）...' -ForegroundColor Cyan
    }

    python -m PyInstaller --noconfirm --clean `
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
        Write-Host "运行它可以在控制台直接看到报错：`"$webviewExe`" --dir `"$root\samples\photos`"" -ForegroundColor DarkGray
    }
}

# ---- 3. Electron 桌面版（可选，历史方案；本机若 Electron 无法启动则不要用）----
if ($WithElectron) {
    if (-not $SkipInstall -and -not (Test-Path (Join-Path $root 'desktop\node_modules'))) {
        Write-Host '[build] 安装 Electron 依赖...' -ForegroundColor Cyan
        npm --prefix desktop install --foreground-scripts --no-audit --no-fund
        Assert-LastExit 'Electron 依赖安装'
    }
    Write-Host '[build] 编译 Electron 主进程/preload' -ForegroundColor Cyan
    npm --prefix desktop run build
    Assert-LastExit 'Electron TypeScript 编译'

    Write-Host '[build] PyInstaller 打包 Electron 版后端 sidecar...' -ForegroundColor Cyan
    npm --prefix desktop run pack:backend
    Assert-LastExit 'PyInstaller 后端打包'

    Write-Host '[build] 组装 Electron 绿色版...' -ForegroundColor Cyan
    npm --prefix desktop run pack:portable
    Assert-LastExit 'Electron 绿色版打包'

    if ($WithInstaller) {
        Write-Host '[build] electron-builder 生成安装包...' -ForegroundColor Yellow
        npm --prefix desktop run dist:sandbox
        if ($LASTEXITCODE -ne 0) { Write-Host '[build] electron-builder 失败；绿色版仍可用' -ForegroundColor Yellow }
    }
}

Write-Host ''
Write-Host '==== 构建完成 ====' -ForegroundColor Green
if (-not $SkipDesktop -and -not $Console) {
    Write-Host '桌面版（推荐）：release\PicturePick-WebView2\PicturePick.exe（或 zip 解压后运行）' -ForegroundColor Green
    if ($Verify) {
        Write-Host ''
        & (Join-Path $root 'scripts\verify-desktop.ps1') -Exe (Join-Path $root 'release\PicturePick-WebView2\PicturePick.exe')
    }
    else {
        Write-Host '自检命令：powershell -ExecutionPolicy Bypass -File scripts\verify-desktop.ps1' -ForegroundColor Green
    }
}
Write-Host '命令行版：cd backend; python -m picturepick serve --dir <照片目录>（自动打开浏览器）' -ForegroundColor Green
