<#
Registers the Rise/Fall supervisor as a Windows Scheduled Task so it survives
logoff and comes back after a reboot.

MUST BE RUN ELEVATED. Registering a task with an S4U principal - the thing
that lets it keep running after you log out - requires admin. Without
elevation this fails with "Access is denied".

    Right-click PowerShell, "Run as administrator", then:
        cd C:\Users\ayori\derivmasterpiece
        .\tools\install_risefall_task.ps1
        .\tools\install_risefall_task.ps1 -Uninstall

This is separate from install_task.ps1, which owns the DIGIT bot's task. Both
can run at once; they use different configs, different journals and different
log files.

This file is deliberately pure ASCII. Windows PowerShell 5.1 reads .ps1 as
ANSI, so a single typographic dash anywhere in it - including inside a comment
or a message string - produces a parse error that points at the wrong line.
That has bitten this repo three times.

The machine still has to be ON. A Scheduled Task does not run on a powered-off
laptop.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$TaskName = "DerivRiseFallSupervisor",
    [double]$MaxDailyLoss = 100.0,
    [double]$SessionMinutes = 30.0
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $repo ".venv\Scripts\pythonw.exe"
$sup  = Join-Path $repo "tools\risefall_supervisor.py"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task $TaskName"
    } else {
        Write-Host "Task $TaskName was not installed"
    }
    exit 0
}

if (-not (Test-Path $py))  { throw "python not found at $py" }
if (-not (Test-Path $sup)) { throw "supervisor not found at $sup" }

# pythonw.exe, not python.exe: a console process receives Ctrl-C style
# console events and dies with STATUS_CONTROL_C_EXIT when the session that
# started it closes. pythonw has no console, so nothing can send it one.
$arguments = '-u "{0}" --config config.risefall.yaml --max-daily-loss {1} --minutes {2}' -f $sup, $MaxDailyLoss, $SessionMinutes

$action = New-ScheduledTaskAction -Execute $py -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn

# S4U is the whole point: the task keeps running after logoff without storing
# a password. LogonType Interactive would stop the moment you sign out.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

# ExecutionTimeLimit 0 means no limit. The default is 3 days, after which the
# scheduler kills the task silently and it looks like the bot "just stopped".
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize

Write-Host ""
Write-Host "Installed and started. Watch it with:"
Write-Host "    Get-Content risefall_live.log -Tail 20 -Wait"
Write-Host "Stop it with:"
Write-Host "    .\tools\install_risefall_task.ps1 -Uninstall"
