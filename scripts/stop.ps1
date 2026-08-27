[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

# 停止脚本只处理真实服务进程写入且身份再次核验通过的 PID。
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $projectRoot ".run\interview-agent.json"
$shutdownUrl = "http://127.0.0.1:8000/api/system/shutdown"

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host "[Interview Agent] 没有找到运行记录；服务可能已经停止。" -ForegroundColor Yellow
    exit 0
}

try {
    $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
} catch {
    Write-Host "[失败] PID 记录已损坏，未终止任何进程。" -ForegroundColor Red
    Write-Host "处理：确认 8000 端口没有 Interview Agent 后，手动删除 .run\interview-agent.json。"
    exit 1
}

$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "[Interview Agent] 进程已经退出，已清理陈旧运行记录。" -ForegroundColor Yellow
    exit 0
}

$sameExecutable = $process.Path -eq [string]$record.executable
try {
    $recordedStart = [DateTimeOffset]::Parse([string]$record.started_at)
    $actualStart = [DateTimeOffset]$process.StartTime.ToUniversalTime()
    $sameStart = [Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le 30
} catch {
    $sameStart = $false
}
if (-not $sameExecutable -or -not $sameStart) {
    Write-Host "[失败] PID 当前属于其他进程，未执行停止。" -ForegroundColor Red
    Write-Host "处理：请人工检查 PID $($record.pid)；脚本不会终止身份不匹配的进程。"
    exit 1
}

$graceful = $false
try {
    $headers = @{ "X-Interview-Agent-Shutdown-Token" = $record.shutdown_token }
    $response = Invoke-RestMethod -Uri $shutdownUrl -Method Post -Headers $headers -TimeoutSec 5
    $graceful = $response.status -eq "stopping"
} catch {
    Write-Host "[Interview Agent] 优雅停止请求失败，将在身份复核后执行本机兜底停止。" -ForegroundColor Yellow
}

if ($graceful) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Process -Id $record.pid -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
}

if (Get-Process -Id $record.pid -ErrorAction SilentlyContinue) {
    # 上方已核验可执行文件与启动时间；只对该 PID 使用最终兜底。
    Stop-Process -Id $record.pid -Force
    Wait-Process -Id $record.pid -Timeout 5 -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "[Interview Agent] 服务已停止。" -ForegroundColor Green
