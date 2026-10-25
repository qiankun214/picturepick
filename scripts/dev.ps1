# PicturePick 开发模式启动脚本：后端 FastAPI(8000) + 前端 Vite(5173)
# 用法：pwsh -File scripts\dev.ps1 [-BackendPort 8000]
param(
    [int]$BackendPort = 8000
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 沙箱/受限 HOME 环境下的 npm 缓存与脚本策略
$env:npm_config_cache = Join-Path $root '.cache\npm-frontend'
$env:npm_config_logs_dir = Join-Path $root '.cache\npm-frontend\_logs'
$env:npm_config_foreground_scripts = 'true'

if (-not (Test-Path (Join-Path $root 'frontend\node_modules'))) {
    Write-Host '[dev] 安装前端依赖（首次较慢）...' -ForegroundColor Yellow
    npm --prefix frontend install --foreground-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
}

$backend = $null
try {
    Write-Host "[dev] 启动后端 http://127.0.0.1:$BackendPort ..." -ForegroundColor Cyan
    $backend = Start-Process -FilePath 'python' `
        -ArgumentList '-m', 'picturepick', 'serve', '--port', "$BackendPort", '--no-open' `
        -WorkingDirectory (Join-Path $root 'backend') -PassThru -NoNewWindow

    $ready = $false
    for ($i = 0; $i -lt 100; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$BackendPort/api/health"
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready) { throw "后端在 $BackendPort 端口未就绪（可能端口被占用）" }

    Write-Host "[dev] 后端就绪。打开 http://127.0.0.1:5173 使用开发前端（Ctrl+C 退出）" -ForegroundColor Green
    npm --prefix frontend run dev
}
finally {
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
        Write-Host '[dev] 已停止后端' -ForegroundColor DarkGray
    }
}
