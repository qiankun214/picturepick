# PicturePick desktop dependency install script (pins sandbox-required environment variables).
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#         powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipInstall   # print env only

param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

$env:npm_config_cache = 'E:\picturePick\.cache\npm-desktop'
$env:npm_config_logs_dir = 'E:\picturePick\.cache\npm-desktop\_logs'
$env:npm_config_foreground_scripts = 'true'   # lifecycle scripts inherit stdio, avoids spawn EPERM
$env:electron_config_cache = 'E:\picturePick\.cache\electron'  # lowercase! electron install.js reads only this
$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'  # github is unreachable here
$env:ELECTRON_BUILDER_CACHE = 'E:\picturePick\.cache\electron-builder'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'

Write-Host 'Environment variables set:'
@(
    $env:npm_config_cache,
    $env:npm_config_logs_dir,
    $env:npm_config_foreground_scripts,
    $env:electron_config_cache,
    $env:ELECTRON_MIRROR,
    $env:ELECTRON_BUILDER_CACHE,
    $env:ELECTRON_BUILDER_BINARIES_MIRROR
) | ForEach-Object { Write-Host "  $_" }

if ($SkipInstall) { return }

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    npm install --foreground-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed, exit=$LASTEXITCODE" }
    Write-Host 'npm install completed.'
}
finally {
    Pop-Location
}
