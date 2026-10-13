[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$SourceRoot,
    [string]$EnvironmentFile,
    [string]$LaunchManifest,
    [switch]$ReplaceExisting,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$env:PYTHONUTF8 = '1'
foreach ($path in @($ConfigPath, $Python, $SourceRoot)) {
    if (-not [IO.Path]::IsPathRooted($path)) { throw 'Installation paths must be absolute.' }
}
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
$Python = [IO.Path]::GetFullPath($Python)
$supervisorPython = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $supervisorPython -PathType Leaf)) {
    throw 'The configured Python installation needs pythonw.exe for a console-free guard task.'
}
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$supervisorSource = Join-Path $PSScriptRoot '..\agent\supervisor.py'
$configHash = (Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
$arguments = @('-X', 'utf8', '-B', $supervisorSource, '--config', $ConfigPath,
    '--config-sha256', $configHash, '--source-root', $SourceRoot,
    '--controller-python', $Python, '--controller-python-sha256',
    (Get-FileHash -LiteralPath $Python -Algorithm SHA256).Hash.ToLowerInvariant())
if ($EnvironmentFile) {
    if (-not [IO.Path]::IsPathRooted($EnvironmentFile)) { throw 'Environment path must be absolute.' }
    $EnvironmentFile = [IO.Path]::GetFullPath($EnvironmentFile)
    $environmentHash = (Get-FileHash -LiteralPath $EnvironmentFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $arguments += @('--environment-file', $EnvironmentFile, '--environment-sha256', $environmentHash)
}
if ($LaunchManifest) {
    if (-not [IO.Path]::IsPathRooted($LaunchManifest)) { throw 'Launch manifest path must be absolute.' }
    $LaunchManifest = [IO.Path]::GetFullPath($LaunchManifest)
    $manifestHash = (Get-FileHash -LiteralPath $LaunchManifest -Algorithm SHA256).Hash.ToLowerInvariant()
    $arguments += @('--launch-manifest', $LaunchManifest, '--launch-manifest-sha256', $manifestHash)
}
$checked = & $Python @arguments --check | Out-String
if ($LASTEXITCODE -ne 0) { throw 'Supervisor preflight refused this installation.' }
$profile = $checked | ConvertFrom-Json
$taskName = "FarmBot-Supervisor-$($profile.environment)-$($profile.instance_id)"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and -not $ReplaceExisting) { throw "Task already exists: $taskName. Inspect it before an explicit update." }
if ($ReplaceExisting) {
    $pause = Get-Item -LiteralPath (Join-Path $profile.local_root 'supervisor.pause') -ErrorAction Stop
    if ($pause.PSIsContainer -or $pause.LinkType -or ($task -and $task.State -ne 'Disabled')) {
        throw 'Replacing a guard requires a regular maintenance pause file and a disabled guard task.'
    }
}
$setupRoot = Join-Path $profile.local_root 'supervisor'
$installedScript = Join-Path $setupRoot 'controller_supervisor.py'
if (Test-Path -LiteralPath $installedScript) {
    if (-not $ReplaceExisting -and (Get-FileHash -LiteralPath $installedScript).Hash -ne (Get-FileHash -LiteralPath $supervisorSource).Hash) {
        throw 'Existing supervisor source differs; preserve it and use settled maintenance.'
    }
}
if ($CheckOnly) {
    [pscustomobject]@{ TaskName=$taskName; StateRoot=$profile.local_root; Installable=$true } | ConvertTo-Json -Compress
    return
}
New-Item -ItemType Directory -Path $setupRoot -Force | Out-Null
if ($ReplaceExisting -or -not (Test-Path -LiteralPath $installedScript)) {
    Copy-Item -LiteralPath $supervisorSource -Destination $installedScript -Force
}
# Paths on Windows cannot contain quotes. Double terminal backslashes for native argv quoting.
function Quote-Argument([string]$Value) {
    if ($Value.Contains('"')) { throw 'Quote in installation argument.' }
    return '"' + [regex]::Replace($Value, '(\\+)$', '$1$1') + '"'
}
$arguments[3] = $installedScript
$nativeArguments = ($arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
$action = New-ScheduledTaskAction -Execute $supervisorPython -Argument $nativeArguments -WorkingDirectory $setupRoot
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$minuteTrigger.Repetition.StopAtDurationEnd = $false
$triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), $minuteTrigger)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $triggers `
    -Settings $settings -Force:$ReplaceExisting -Description 'Owns this FarmBot instance controller; never kills an existing controller.' | Out-Null
Enable-ScheduledTask -TaskName $taskName | Out-Null
Start-ScheduledTask -TaskName $taskName
[pscustomobject]@{ TaskName=$taskName; StateRoot=$profile.local_root; Supervisor=$installedScript } | ConvertTo-Json -Compress
