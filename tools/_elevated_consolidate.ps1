<#
Runs elevated. Leaves exactly ONE bot registered: the digit bot, configured
for even + rise only.

Three jobs:
  1. Remove the Rise/Fall task entirely - one bot, not two.
  2. Unregister and re-register the digit task. Re-registering rather than
     re-enabling is deliberate: that task had wedged permanently in "Queued",
     where the scheduler believed an instance was still running and
     MultipleInstances=IgnoreNew refused every new start. Stop-ScheduledTask
     never cleared it. A fresh registration does.
  3. Start it and record what happened where the unelevated session can read it.

Pure ASCII: PowerShell 5.1 reads .ps1 as ANSI, so one typographic dash - even
inside a comment - is a parse error pointing at the wrong line.
#>
$ErrorActionPreference = "Continue"
$repo = "C:\Users\ayori\derivmasterpiece"
$log  = Join-Path $repo "consolidate_out.log"
"=== elevated consolidation $(Get-Date -Format s) ===" | Out-File $log -Encoding utf8

function Say($m) { $m | Out-File $log -Append -Encoding utf8 }

# 1. remove the other bot
foreach ($t in @("DerivRiseFallSupervisor")) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        try {
            Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Say "removed task $t"
        } catch { Say "FAILED to remove ${t}: $($_.Exception.Message)" }
    } else { Say "task $t was not present" }
}

# 2. re-register the digit bot from scratch, clearing the wedged Queued state
$name = "DerivScanTradeSupervisor"
$py   = Join-Path $repo ".venv\Scripts\pythonw.exe"
$sup  = Join-Path $repo "tools\supervisor.py"
if (-not (Test-Path $py))  { Say "python missing at $py";  "EXITCODE=1" | Out-File $log -Append; exit 1 }
if (-not (Test-Path $sup)) { Say "supervisor missing at $sup"; "EXITCODE=1" | Out-File $log -Append; exit 1 }

try {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Say "unregistered the wedged $name"
    }

    # pythonw, not python: a console process receives Ctrl-C style console
    # events and dies with STATUS_CONTROL_C_EXIT when the starting session
    # closes. pythonw has no console for one to arrive on.
    $action  = New-ScheduledTaskAction -Execute $py -Argument ('-u "{0}"' -f $sup) -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -AtLogOn

    # S4U is the point: keeps running after logoff, no stored password.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

    # ExecutionTimeLimit 0 = none. The default is 3 days, after which the
    # scheduler kills it silently and it looks like the bot "just stopped".
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Say "registered $name fresh"

    Start-ScheduledTask -TaskName $name
    Start-Sleep -Seconds 12
    $st = (Get-ScheduledTask -TaskName $name).State
    $rc = (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
    Say "state=$st lastResult=$rc"
    Say ("tasks remaining: " + ((Get-ScheduledTask | Where-Object { $_.TaskName -like 'Deriv*' } | ForEach-Object { $_.TaskName + '=' + $_.State }) -join ', '))
    "EXITCODE=0" | Out-File $log -Append -Encoding utf8
} catch {
    Say "FAILED: $($_.Exception.Message)"
    "EXITCODE=1" | Out-File $log -Append -Encoding utf8
}
