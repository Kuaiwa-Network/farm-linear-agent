[CmdletBinding()]
param(
    [switch]$CheckOnly
)

# Reload the checked-out FarmBot revision on the existing Windows installation.
# The scheduled supervisor restarts the receiver after its verified child exits.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configPath = Join-Path $repoRoot '.local\agent\config.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "FarmBot config is missing: $configPath"
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$stateRoot = if ($config.local_root) { [IO.Path]::GetFullPath($config.local_root) }
             else { Join-Path $repoRoot '.local' }
$pidPath = Join-Path $stateRoot 'agent\receiver.pid'
$supervisorPath = Join-Path $stateRoot 'setup\supervisor.ps1'
$port = if ($config.port) { [int]$config.port } else { 8765 }

$task = Get-ScheduledTask -TaskName 'FarmBot-Receiver'
if ($task.State -ne 'Running' -or $task.Actions.Count -ne 1) {
    throw 'FarmBot-Receiver must be a single running scheduled task.'
}
$action = $task.Actions[0]
$supervisorMatch = [regex]::Match($action.Arguments, '(?:^|\s)-File\s+"([^"]+)"')
$pythonMatch = [regex]::Match($action.Arguments, '(?:^|\s)-Python\s+"([^"]+)"')
if (-not $supervisorMatch.Success -or -not $pythonMatch.Success -or
        $action.Arguments -notmatch '(?:^|\s)-Component\s+receiver(?:\s|$)' -or
        [IO.Path]::GetFullPath($action.WorkingDirectory) -ine $repoRoot -or
        [IO.Path]::GetFullPath($supervisorMatch.Groups[1].Value) -ine $supervisorPath) {
    throw 'FarmBot-Receiver does not point to this installation.'
}
$pythonPath = [IO.Path]::GetFullPath($pythonMatch.Groups[1].Value)
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Configured Python is missing: $pythonPath"
}

function Assert-OwnedReceiver([int]$ProcessId) {
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    $expectedArgs = '-u -m agent.service serve --config "' + $configPath.Replace('\', '/') + '"'
    if (-not $info -or -not $info.ExecutablePath -or
            [IO.Path]::GetFullPath($info.ExecutablePath) -ine $pythonPath -or
            -not $info.CommandLine -or -not $info.CommandLine.EndsWith($expectedArgs)) {
        throw "PID $ProcessId is not the verified FarmBot receiver."
    }
    return $info
}

Push-Location -LiteralPath $repoRoot
try {
    $doctorJson = & $pythonPath -m agent.service doctor --config $configPath | Out-String
    $report = $doctorJson | ConvertFrom-Json
    if ($null -eq $report.counts.total -or -not $report.ledger -or
            [IO.Path]::GetFullPath($report.ledger) -ine (Join-Path $stateRoot 'agent\ledger.sqlite3')) {
        throw 'FarmBot doctor could not verify the intended ledger.'
    }
    $busyJobs = @($report.jobs | Where-Object {
        $_.state -in @('queued', 'running', 'awaiting_resource') -or $null -ne $_.worker_pid
    })
    $reservations = @($report.reservations | Where-Object { $null -ne $_ })
    if ($busyJobs.Count -or $reservations.Count) {
        throw "FarmBot is busy ($($busyJobs.Count) jobs, $($reservations.Count) reservations). Try again after work settles."
    }

    $receiverId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$receiverId)) {
        throw 'Receiver PID file is invalid.'
    }
    $null = Assert-OwnedReceiver $receiverId
    $revision = (& git rev-parse --short HEAD).Trim()
    if ($CheckOnly) {
        Write-Output "Ready to redeploy $revision (receiver PID $receiverId)."
        return
    }

    # Hold a handle to the verified process so a reused PID cannot name a different child.
    $receiver = Get-Process -Id $receiverId
    $null = $receiver.Handle
    if ([IO.Path]::GetFullPath($receiver.Path) -ine $pythonPath) {
        throw 'Receiver process changed before restart.'
    }
    $null = Assert-OwnedReceiver $receiverId
    $receiver.Kill()
    $null = $receiver.WaitForExit(5000)
    Write-Output "Restarting FarmBot at $revision..."

    $healthUrl = "http://127.0.0.1:$port/health"
    $deadline = (Get-Date).AddMinutes(2)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $nextId = 0
        if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$nextId) -or
                $nextId -eq $receiverId) {
            continue
        }
        $null = Assert-OwnedReceiver $nextId
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                Write-Output "FarmBot ready at $revision (receiver PID $nextId, health 200)."
                return
            }
        } catch {
            # The supervisor may still be starting the new receiver.
        }
    }
    throw "FarmBot did not become healthy within two minutes. Check $stateRoot\agent\logs."
} finally {
    Pop-Location
}
