<#
Restart the Rise/Fall bot and PROVE the new settings took effect.

This exists because the obvious way silently does not work. Three config
changes were committed, tested, and reported as live while the process actually
trading kept the old settings for over an hour. Every step reported success:

  1. Killing by command line matches NOTHING. Win32_Process.CommandLine - and
     ExecutablePath too - come back empty for task-owned processes from a
     non-elevated session. A matcher on those silently matches zero processes.
     Only ProcessId, ParentProcessId, Name and CreationDate are readable, so
     this script works from those.

  2. Start-ScheduledTask returns 2147946720 (0x80070420, "an instance of this
     task is already running") when the old instance is still up, because
     MultipleInstances is IgnoreNew. Nothing checked it.

  3. Deleting the lock file while a supervisor runs DISARMS the single-instance
     guard rather than stopping anything, because the lock is only read at
     startup. That let a manual test session trade in parallel with the
     task-owned bot.

So: stop by task handle, confirm the tree is gone by pid, only then touch the
lock, start, check the result code, and finally read the log back to confirm a
NEW supervisor is running the settings the config asks for.

    .\tools\redeploy.ps1

Pure ASCII on purpose: PowerShell 5.1 reads .ps1 as ANSI, so one typographic
dash anywhere - even in a comment - is a parse error pointing at the wrong
line. That has bitten this repo three times.
#>
[CmdletBinding()]
param(
    [string]$TaskName = "DerivRiseFallSupervisor",
    [string]$LogName  = "risefall_live.log",
    [int]$WaitSeconds = 60
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $repo $LogName
$lock = Join-Path $repo ".risefall_supervisor.lock"
$py   = Join-Path $repo ".venv\Scripts\python.exe"

function Get-BotPids {
    # Name and pid only. CommandLine is unreadable here, which is the whole point.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Select-Object -ExpandProperty ProcessId
}

Write-Host "=== 1. snapshot before ==="
$before = @(Get-BotPids)
$logBefore = if (Test-Path $log) { Get-Content $log -Raw } else { "" }
$supBefore = ([regex]::Matches($logBefore, "supervisor up")).Count
Write-Host ("  python/pythonw pids: " + ($before -join ", "))
Write-Host ("  'supervisor up' lines so far: " + $supBefore)

Write-Host "=== 2. stop the task by handle ==="
try { Stop-ScheduledTask -TaskName $TaskName } catch { Write-Host ("  stop said: " + $_.Exception.Message) }

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 2
    $state = (Get-ScheduledTask -TaskName $TaskName).State
    $survivors = @(Get-BotPids | Where-Object { $before -contains $_ })
    Write-Host ("  state=" + $state + " survivors=" + $survivors.Count)
} while (($state -eq "Running" -or $survivors.Count -gt 0) -and (Get-Date) -lt $deadline)

# Anything from the BEFORE set that outlived the stop gets killed by pid, with
# /T so the shim-and-child chain goes with it. Restricted to the before-set so
# this can never kill an unrelated python.
$survivors = @(Get-BotPids | Where-Object { $before -contains $_ })
foreach ($p in $survivors) {
    Write-Host ("  force-killing surviving pid " + $p)
    & taskkill /F /T /PID $p 2>&1 | Out-Null
}
Start-Sleep -Seconds 2
$survivors = @(Get-BotPids | Where-Object { $before -contains $_ })
if ($survivors.Count -gt 0) {
    throw ("could not stop pids " + ($survivors -join ", ") + " - refusing to start a second instance")
}
Write-Host "  tree confirmed gone"

Write-Host "=== 3. clear the lock (only now that nothing is running) ==="
if (Test-Path $lock) { Remove-Item $lock -Force; Write-Host "  lock removed" }
else { Write-Host "  no lock file present" }

Write-Host "=== 4. start and CHECK THE RESULT ==="
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ("  LastTaskResult=" + $info.LastTaskResult)
& $py -c "import sys; from pricebot.deploy_verify import decode_task_result, restart_succeeded; c=int(sys.argv[1]); print('  decoded:', decode_task_result(c)); sys.exit(0 if restart_succeeded(c) else 1)" $info.LastTaskResult
if ($LASTEXITCODE -ne 0) { throw "the task did not actually start - see the decoded result above" }

Write-Host "=== 5. wait for a NEW supervisor line in the log ==="
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Seconds 3
    $logAfter = if (Test-Path $log) { Get-Content $log -Raw } else { "" }
    $supAfter = ([regex]::Matches($logAfter, "supervisor up")).Count
    Write-Host ("  'supervisor up' lines now: " + $supAfter)
} while ($supAfter -le $supBefore -and (Get-Date) -lt $deadline)
if ($supAfter -le $supBefore) { throw "no new 'supervisor up' appeared - the restart did not take" }

Write-Host "=== 6. does the RUNNING bot match the config? ==="
& $py -m tools.check_deploy
exit $LASTEXITCODE
