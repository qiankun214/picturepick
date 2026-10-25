# PicturePick 一键构建
# 用法：
#   pwsh -File scripts\build.ps1                  # 构建 WebView2 桌面版（推荐，默认）
#   pwsh -File scripts\build.ps1 -WithElectron    # 额外构建 Electron 版（需要 Electron 能在本机运行）
#   pwsh -File scripts\build.ps1 -SkipFrontend -SkipDesktop
param(
    [switch]$SkipInstall,
    [switch]$SkipFrontend,
    [switch]$SkipDesktop,
    [switch]$WithElectron,
    [switch]$WithInstaller
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 沙箱/离线环境下的 npm & 二进制缓存配置（详见 docs/ARCHITECTURE.md §5）----
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
    Write-Host '[build] 打包 WebView2 桌面版（PyInstaller）...' -ForegroundColor Cyan
    python -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $root 'build\webview') `
        --workpath (Join-Path $root 'build\.pyinstaller-webview') `
        (Join-Path $root 'desktop_webview\picturepick-desktop.spec')
    Assert-LastExit 'WebView2 桌面版打包'

    $webviewExe = Join-Path $root 'build\webview\PicturePick\PicturePick.exe'
    if (-not (Test-Path $webviewExe)) { throw "桌面版产物缺失：$webviewExe" }

    Write-Host '[build] 组装 release\PicturePick-WebView2 ...' -ForegroundColor Cyan
    $releaseDir = Join-Path $root 'release\PicturePick-WebView2'
    if (Test-Path $releaseDir) { Remove-Item $releaseDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    Copy-Item (Join-Path $root 'build\webview\PicturePick\*') $releaseDir -Recurse -Force

    $zipPath = Join-Path $root 'release\PicturePick-WebView2-win-x64.zip'
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $releaseDir '*') -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "       → $releaseDir\PicturePick.exe" -ForegroundColor DarkGray
    Write-Host "       → $zipPath" -ForegroundColor DarkGray
}

# ---- 3. Electron 桌面版（可选，历史方案）----
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
Write-Host '桌面版（推荐）：release\PicturePick-WebView2\PicturePick.exe（或 zip 解压后运行）' -ForegroundColor Green
Write-Host '命令行版：python -m picturepick serve --dir <照片目录>（在 backend 目录，自动打开浏览器）' -ForegroundColor Green
