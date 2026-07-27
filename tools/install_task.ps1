<#
Registers the supervisor as a Windows Scheduled Task so the bot keeps
running after this terminal (or the Claude session that started it) closes,
and comes back by itself after a reboot.

Why a Scheduled Task rather than a background process: anything launched
from a shell is a child of that shell and dies with it. A task is owned by
the scheduler, so logging out or closing the window leaves it alone.

The task starts the SUPERVISOR, never main.py directly — the supervisor is
what refuses to relaunch past the daily loss limit. Pointing the task at
main.py would restore the exact failure mode the supervisor exists to
prevent (each fresh process starting daily_pnl back at 0.00).

    .\tools\install_task.ps1              # install / update
    .\tools\install_task.ps1 -Start       # install and start it now
    .\tools\install_task.ps1 -Uninstall   # remove it
    .\tools\install_task.ps1 -KeepAwake   # also stop the machine sleeping on AC

Note the machine still has to be ON. A Scheduled Task does not run on a
powered-off laptop; -KeepAwake only prevents *sleep* while it is plugged in.
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Uninstall,
    [switch]$KeepAwake,
    [string]$TaskName = "DerivScanTradeSupervisor"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$supervisor = Join-Path $repo "tools\supervisor.py"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed scheduled task '$TaskName'."
    } else {
        Write-Output "No scheduled task '$TaskName' to remove."
    }
    return
}

if (-not (Test-Path $python))     { throw "venv python not found at $python — create the venv first." }
if (-not (Test-Path $supervisor)) { throw "supervisor not found at $supervisor" }

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-u `"$supervisor`"" -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# RestartCount/Interval cover the scheduler's own failures; the supervisor
# handles ordinary crashes itself and is a long-running process, so no
# ExecutionTimeLimit.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Deriv scan-trade supervisor (demo account)" `
    -Force | Out-Null

Write-Output "Installed scheduled task '$TaskName'."
Write-Output "  runs: $python -u `"$supervisor`""
Write-Output "  from: $repo"

if ($KeepAwake) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Output "Sleep and hibernate disabled while on AC power (battery settings untouched)."
}

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Started. Follow it with: Get-Content scan_trade_live.log -Wait -Tail 20"
}
