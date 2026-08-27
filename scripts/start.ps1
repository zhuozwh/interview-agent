[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONIOENCODING = "utf-8"

# 所有运行时路径都从脚本自身位置解析，避免依赖用户当前目录。
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runDirectory = Join-Path $projectRoot ".run"
$logDirectory = Join-Path $projectRoot "logs"
$pidFile = Join-Path $runDirectory "interview-agent.json"
$stdoutLog = Join-Path $logDirectory "server.out.log"
$stderrLog = Join-Path $logDirectory "server.err.log"
$localUrl = "http://127.0.0.1:8000/"
$healthUrl = "http://127.0.0.1:8000/health"

function Write-Step([string]$message) {
    Write-Host "[Interview Agent] $message" -ForegroundColor Cyan
}

function Test-RecordedProcess($record) {
    try {
        $candidate = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
        $sameExecutable = $candidate.Path -eq [string]$record.executable
        $recordedStart = [DateTimeOffset]::Parse([string]$record.started_at)
        $actualStart = [DateTimeOffset]$candidate.StartTime.ToUniversalTime()
        $sameStart = [Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le 30
        return $sameExecutable -and $sameStart
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Host "[失败] 未找到项目虚拟环境：.venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "处理：请先按 README 的快速开始步骤创建 .venv 并安装项目。"
    exit 1
}

Set-Location -LiteralPath $projectRoot

# 已有 PID 只有在可执行文件、启动时间和健康检查均匹配时才视为本应用。
if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    try {
        $existing = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        if (Test-RecordedProcess $existing) {
            try {
                $existingHealth = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
                if ($existingHealth.status -eq "ok") {
                    Write-Step "服务已经在运行：$localUrl"
                    if (-not $NoBrowser -and -not $CheckOnly) {
                        Start-Process $localUrl
                    }
                    exit 0
                }
            } catch {
                Write-Host "[失败] 已记录的服务进程仍存在，但健康检查失败。" -ForegroundColor Red
                Write-Host "处理：请先运行 stop.cmd，再重新启动。"
                exit 1
            }
        }
    } catch {
        # 损坏或陈旧 PID 不得阻止重新检查；下方会安全移除它。
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Step "已忽略陈旧的本地 PID 记录。"
}

Write-Step "正在执行启动前检查（只读，不调用远端 LLM）…"
& $pythonPath -m interview_agent.preflight
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if ($CheckOnly) {
    Write-Step "检查完成；未启动服务。"
    exit 0
}

New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

# 停止令牌和 PID 文件位置只传给子进程，由真实服务进程写入自身 PID。
$shutdownToken = [guid]::NewGuid().ToString("N")
$previousToken = $env:INTERVIEW_AGENT_SHUTDOWN_TOKEN
$previousPidFile = $env:INTERVIEW_AGENT_PID_FILE
$env:INTERVIEW_AGENT_SHUTDOWN_TOKEN = $shutdownToken
$env:INTERVIEW_AGENT_PID_FILE = $pidFile
try {
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList "-m", "interview_agent.main" `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
} finally {
    if ($null -eq $previousToken) {
        Remove-Item Env:\INTERVIEW_AGENT_SHUTDOWN_TOKEN -ErrorAction SilentlyContinue
    } else {
        $env:INTERVIEW_AGENT_SHUTDOWN_TOKEN = $previousToken
    }
    if ($null -eq $previousPidFile) {
        Remove-Item Env:\INTERVIEW_AGENT_PID_FILE -ErrorAction SilentlyContinue
    } else {
        $env:INTERVIEW_AGENT_PID_FILE = $previousPidFile
    }
}

Write-Step "服务进程已启动，正在等待健康检查…"
$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        if ($health.status -eq "ok" -and (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
            $actualRecord = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
            if (Test-RecordedProcess $actualRecord) {
                $ready = $true
                break
            }
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        try {
            $failedRecord = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
            if (Test-RecordedProcess $failedRecord) {
                Stop-Process -Id ([int]$failedRecord.pid) -Force -ErrorAction SilentlyContinue
            }
        } catch {
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Write-Host "[失败] 服务未能在 20 秒内通过健康检查。" -ForegroundColor Red
    Write-Host "错误日志：$stderrLog"
    if (Test-Path -LiteralPath $stderrLog) {
        Get-Content -LiteralPath $stderrLog -Tail 12
    }
    exit 1
}

Write-Step "启动完成：$localUrl"
Write-Host "停止服务请双击 stop.cmd，或运行 scripts\stop.ps1。"
if (-not $NoBrowser) {
    Start-Process $localUrl
}
