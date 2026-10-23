[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [Parameter(Mandatory)][string]$Python
)
$ErrorActionPreference = 'Stop'
foreach ($path in @($ConfigPath, $Python)) {
    if (-not [IO.Path]::IsPathRooted($path)) { throw 'Use absolute installation paths.' }
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$taskName = 'FarmBot-Docker-Watchdog'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'Watchdog task already exists; inspect and explicitly update its installation.'
}
$pythonw = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'pythonw.exe is required.' }
$source = Join-Path $PSScriptRoot '../agent/docker_watchdog.py'
$installed = Join-Path $config.state_root 'docker_watchdog.py'
if (-not [IO.Path]::IsPathRooted($installed)) { throw 'Use an absolute watchdog state root.' }
New-Item -ItemType Directory -Path $config.state_root -Force | Out-Null
if (Test-Path -LiteralPath $installed) { throw 'Preserve the existing watchdog script before replacing it.' }
Copy-Item -LiteralPath $source -Destination $installed
$configHash = (Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($argument in @($installed, $ConfigPath)) {
    if ($argument.Contains('"')) { throw 'Quote in installation path.' }
}
$arguments = '-B -X utf8 "' + $installed + '" --config "' + $ConfigPath + '" --config-sha256 ' + $configHash
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $config.state_root
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$minute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$minute.Repetition.StopAtDurationEnd = $false
$triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), $minute)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $triggers -Settings $settings `
    -Description 'Checks the pinned local Docker engine; starts only its verified host task when the backend is absent.' | Out-Null
Start-ScheduledTask -TaskName $taskName
[pscustomobject]@{TaskName=$taskName; Config=$ConfigPath; Script=$installed} | ConvertTo-Json -Compress
