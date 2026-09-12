param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status',
    [int]$Port = 26121,
    [string]$DatabaseUrl = '',
    [switch]$UseSqlite
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $projectRoot '.runtime'
$pidPath = Join-Path $runtimeRoot 'candidate-server.pid'
$stdoutPath = Join-Path $runtimeRoot 'candidate-server.stdout.log'
$stderrPath = Join-Path $runtimeRoot 'candidate-server.stderr.log'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$healthUrl = "http://127.0.0.1:$Port/healthz/"

function Get-CandidateProcess {
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    try {
        $state = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
    } catch {
        throw 'Candidate server state file is invalid; refusing to control any process.'
    }
    if ($null -eq $state.pid -or [int]$state.port -ne $Port) { return $null }
    $processId = [int]$state.pid
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    $sameExecutable = [string]::Equals(
        [string]$process.Path,
        [string]$pythonPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $sameStart = $process.StartTime.ToUniversalTime().Ticks -eq [long]$state.start_ticks
    if (-not $sameExecutable -or -not $sameStart) {
        throw "PID file does not identify the candidate Django server; refusing to control process $processId."
    }
    return $process
}

function Test-CandidateHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq 'ok' -and $response.database -eq 'ok'
    } catch {
        return $false
    }
}

function Stop-Candidate {
    $process = Get-CandidateProcess
    if ($null -eq $process) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        Write-Output 'Candidate server is not running.'
        return
    }
    Stop-Process -Id $process.Id
    $process.WaitForExit(10000) | Out-Null
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "Candidate server stopped (PID $($process.Id))."
}

function Start-Candidate {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Missing isolated Python runtime: $pythonPath"
    }
    if ($null -ne (Get-CandidateProcess)) {
        Write-Output "Candidate server is already running at $healthUrl"
        return
    }
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    if ($UseSqlite -and $DatabaseUrl) {
        throw 'Choose either -UseSqlite or -DatabaseUrl, not both.'
    }
    if ($UseSqlite) {
        Remove-Item Env:SITEOS_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
        $env:SITEOS_ADMIN_DEBUG = '1'
    } elseif ($DatabaseUrl) {
        $env:SITEOS_ADMIN_DATABASE_URL = $DatabaseUrl
    }
    $envFile = Join-Path $projectRoot '.env'
    if (-not $UseSqlite -and -not $env:SITEOS_ADMIN_DATABASE_URL -and -not (Test-Path -LiteralPath $envFile)) {
        throw 'Set SITEOS_ADMIN_DATABASE_URL, pass -DatabaseUrl, create the ignored .env file, or use -UseSqlite for isolated local review.'
    }
    if (-not $env:SITEOS_ADMIN_DEBUG) { $env:SITEOS_ADMIN_DEBUG = '1' }
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @('manage.py', 'runserver', "127.0.0.1:$Port", '--noreload') `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru
    @{
        pid = $process.Id
        port = $Port
        start_ticks = $process.StartTime.ToUniversalTime().Ticks
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $pidPath -NoNewline
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-CandidateHealth) {
            Write-Output "Candidate server ready at http://127.0.0.1:$Port/admin/ (PID $($process.Id))."
            return
        }
        if ($process.HasExited) {
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
            throw "Candidate server exited during startup. Inspect $stderrPath"
        }
        Start-Sleep -Milliseconds 500
    }
    Stop-Candidate
    throw "Candidate server did not become healthy. Inspect $stderrPath"
}

switch ($Action) {
    'start' { Start-Candidate }
    'stop' { Stop-Candidate }
    'restart' { Stop-Candidate; Start-Candidate }
    'status' {
        $process = Get-CandidateProcess
        if ($null -ne $process -and (Test-CandidateHealth)) {
            Write-Output "Candidate server is healthy at $healthUrl (PID $($process.Id))."
            exit 0
        }
        Write-Output 'Candidate server is stopped or unhealthy.'
        exit 1
    }
}
