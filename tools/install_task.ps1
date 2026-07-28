<#
Registers the supervisor as a Windows Scheduled Task so the bot keeps
running after this terminal (or the Claude session that started it) closes,
and comes back by itself after a reboot.

Why a Scheduled Task rather than a background process: anything launched
from a shell is a child of that shell and dies with it. A task is owned by
the scheduler, so logging out or closing the window leaves it alone.

The task starts the SUPERVISOR, never main.py directly - the supervisor is
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
    [switch]$Alerts,
    [string]$TaskName = "DerivScanTradeSupervisor",
    [string]$AlertTaskName = "DerivAlertWatcher"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
# pythonw.exe, not python.exe: a console-mode task owns a console window, and
# closing it (or Ctrl+C in it) kills the whole tree - that is what killed the
# first install, seen as LastTaskResult 0xC000013A STATUS_CONTROL_C_EXIT.
# pythonw allocates no console, so there is nothing to close. The supervisor
# redirects its own stdout/stderr to the log file to compensate.
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"
$supervisor = Join-Path $repo "tools\supervisor.py"

if ($Uninstall) {
    foreach ($name in @($TaskName, $AlertTaskName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "Removed scheduled task '$name'."
        } else {
            Write-Output "No scheduled task '$name' to remove."
        }
    }
    return
}

if ($Alerts) {
    # The alert watcher is deliberately NOT S4U. It needs the desktop that
    # the supervisor lacks: session 0 cannot show a toast, which is the whole
    # reason alerting is split across two processes. An Interactive principal
    # at logon puts it in the user's session, and registering it needs no
    # elevation.
    $watcher = Join-Path $PSScriptRoot "alert_watcher.ps1"
    if (-not (Test-Path $watcher)) { throw "alert_watcher.ps1 not found at $watcher" }

    $aAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watcher`"" `
        -WorkingDirectory $repo
    $aTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $aSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $AlertTaskName -Action $aAction -Trigger $aTrigger `
        -Settings $aSettings -Description "Shows Deriv bot alerts as toasts (interactive session)" `
        -Force -ErrorAction Stop | Out-Null
    Write-Output "Installed alert watcher task '$AlertTaskName' (Interactive)."

    if ($Start) {
        Start-ScheduledTask -TaskName $AlertTaskName
        Write-Output "Alert watcher started."
    }
    return
}

if (-not (Test-Path $python))     { throw "venv pythonw not found at $python - create the venv first." }
if (-not (Test-Path $supervisor)) { throw "supervisor not found at $supervisor" }

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-u `"$supervisor`"" -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# S4U ("run whether the user is logged on or not", without storing a
# password) puts the task in a non-interactive session with NO console.
# That matters: registered interactively the task owns a console window, so
# closing that window or Ctrl+C in it kills the whole tree - observed as
# LastTaskResult 0xC000013A (STATUS_CONTROL_C_EXIT), which is exactly how
# the first install died. With S4U there is no window to close.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U -RunLevel Limited

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

# S4U additionally survives logoff, but registering it needs elevation. Fall
# back rather than fail: pythonw already removes the console-close problem,
# which is the failure actually observed. Never report success on a failed
# Register - the first attempt did, and hid that the principal never changed.
$registered = $false
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Deriv scan-trade supervisor (demo account)" `
        -Force -ErrorAction Stop | Out-Null
    $registered = $true
    Write-Output "Registered with S4U (survives logoff as well as console close)."
} catch {
    Write-Warning "S4U registration failed ($($_.Exception.Message.Trim()))."
    Write-Warning "Falling back to an interactive principal. Re-run this script in an"
    Write-Warning "ELEVATED PowerShell to get S4U and survive logoff too."
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "Deriv scan-trade supervisor (demo account)" `
        -Force -ErrorAction Stop | Out-Null
    $registered = $true
}
if (-not $registered) { throw "Failed to register '$TaskName'." }

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
