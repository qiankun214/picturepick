# PicturePick Electron 启动诊断（把结果写到 electron-diagnose.log）
# 用法：双击 diagnose-electron.cmd，或在 PowerShell 里执行：
#   powershell -ExecutionPolicy Bypass -File scripts\diagnose-electron.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logPath = Join-Path $root 'electron-diagnose.log'
Set-Content -Path $logPath -Value '' -Encoding UTF8
$tmpDir = Join-Path $root '.tmp'
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

function W([string]$message) {
    $line = "[$([DateTime]::Now.ToString('HH:mm:ss'))] $message"
    Write-Host $line
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

function Run-CmdWithTimeout([string]$cmdLine, [int]$timeoutMs = 15000) {
    $outFile = Join-Path $tmpDir ('diag-cmd-' + [Guid]::NewGuid().ToString('N') + '.out.txt')
    $errFile = Join-Path $tmpDir ('diag-cmd-' + [Guid]::NewGuid().ToString('N') + '.err.txt')
    $proc = $null
    try {
        $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmdLine -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $exited = $proc.WaitForExit($timeoutMs)
        if (-not $exited) {
            try { & taskkill /PID $proc.Id /T /F 2>$null | Out-Null } catch {
                try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
            }
        }
        $outText = if (Test-Path $outFile) { [string](Get-Content $outFile -Raw -ErrorAction SilentlyContinue) } else { '' }
        $errText = if (Test-Path $errFile) { [string](Get-Content $errFile -Raw -ErrorAction SilentlyContinue) } else { '' }
        $code = $null
        try { $code = $proc.ExitCode } catch { $code = '(读取失败)' }
        return [pscustomobject]@{
            Exited = $exited
            ExitCode = $code
            Output = ($outText + $errText).Trim()
        }
    } catch {
        return [pscustomobject]@{ Exited = $false; ExitCode = '(异常)'; Output = $_.Exception.Message }
    } finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

W '=== PicturePick Electron 诊断 ==='
W ("OS: " + [Environment]::OSVersion.VersionString + " | PowerShell " + $PSVersionTable.PSVersion)
W ("PWD: " + (Get-Location).Path)
W ("ELECTRON_RUN_AS_NODE(进入脚本时): '" + $env:ELECTRON_RUN_AS_NODE + "'")
Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
W ("ELECTRON_RUN_AS_NODE(清除后): '" + $env:ELECTRON_RUN_AS_NODE + "'")
W ("VC++ vcruntime140.dll: " + (Test-Path "$env:WINDIR\System32\vcruntime140.dll"))

$electron = Join-Path $root 'desktop\node_modules\electron\dist\electron.exe'
W ("electron.exe: " + $electron + " exists=" + (Test-Path $electron))
if (Test-Path $electron) {
    W ("electron.exe size: " + (Get-Item $electron).Length)
    W ("electron.exe file version: " + (Get-Item $electron).VersionInfo.FileVersion)

    # 1) cmd 内显式清空 ELECTRON_RUN_AS_NODE，跑 --version
    W '--- 测试 1：cmd 内清空环境变量后 electron.exe --version ---'
    $cmdLine = 'set ELECTRON_RUN_AS_NODE=&& "' + $electron + '" --version'
    $r1 = Run-CmdWithTimeout $cmdLine
    W ("exited=" + $r1.Exited + " exitCode=" + $r1.ExitCode)
    W ("输出: " + $r1.Output)

    # 2) Start-Process 直接跑 --version（15s 超时）
    W '--- 测试 2：Start-Process（15s 超时）electron.exe --version ---'
    Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $outFile = Join-Path $tmpDir 'diag-version.stdout.txt'
    $errFile = Join-Path $tmpDir 'diag-version.stderr.txt'
    Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    try {
        $proc = Start-Process -FilePath $electron -ArgumentList '--version' -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $exited = $proc.WaitForExit(15000)
        $code = $null
        try { $code = $proc.ExitCode } catch { $code = '(读取失败)' }
        W ("exitedWithin15s=" + $exited + " exitCode=" + $code)
        if (-not $exited) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        if (Test-Path $outFile) {
            $t = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
            if ($null -ne $t) { W ("stdout: " + ([string]$t).Trim()) }
        }
        if (Test-Path $errFile) {
            $t = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
            if ($null -ne $t) { W ("stderr: " + ([string]$t).Trim()) }
        }
    } catch {
        W ("异常: " + $_.Exception.Message)
    }

    # 3) Node 模式测试（验证二进制本身能执行 JS）
    W '--- 测试 3：ELECTRON_RUN_AS_NODE=1 执行一段 JS ---'
    $probe = Join-Path $tmpDir 'electron-probe.js'
    Set-Content -Path $probe -Value "console.log('NODE_OK electron=' + process.versions.electron + ' node=' + process.versions.node);" -Encoding ASCII
    $cmdLine3 = 'set ELECTRON_RUN_AS_NODE=1&& "' + $electron + '" "' + $probe + '"'
    $r3 = Run-CmdWithTimeout $cmdLine3
    W ("exited=" + $r3.Exited + " exitCode=" + $r3.ExitCode)
    W ("输出: " + $r3.Output)
}

W '--- 当前 PicturePick / electron 进程 ---'
$procs = Get-Process PicturePick, electron -ErrorAction SilentlyContinue
if ($procs) {
    foreach ($p in $procs) { W ("进程: pid=" + $p.Id + " name=" + $p.ProcessName + " path=" + $p.Path) }
} else {
    W '（无）'
}

W '--- 最近 2 小时 Application 日志里的相关错误 ---'
try {
    $events = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = (Get-Date).AddHours(-2) } -ErrorAction Stop |
        Where-Object { $_.Message -match 'PicturePick|electron' } |
        Select-Object -First 10
    if ($events) {
        foreach ($ev in $events) {
            $msg = ($ev.Message -replace '\r?\n', ' | ')
            if ($msg.Length -gt 600) { $msg = $msg.Substring(0, 600) }
            W ("事件: " + $ev.TimeCreated + " " + $ev.ProviderName + " id=" + $ev.Id + " " + $msg)
        }
    } else {
        W '（无相关事件）'
    }
} catch {
    W ("读取事件日志失败: " + $_.Exception.Message)
}

W '=== 诊断结束，请把 electron-diagnose.log 发给开发者 ==='
Write-Host ''
Write-Host ('诊断日志：' + $logPath) -ForegroundColor Green
