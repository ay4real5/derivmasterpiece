<#
Registers the S/R bot (run_sr_bot.py) as Windows Scheduled Tasks so it keeps
running after this terminal closes and comes back after logon/reboot,
mirroring tools/install_task.ps1's approach but for the two-account S/R bot.

Two tasks, one per account:
  DerivSRBotAccount1 - .env,     direction=call, R_50
  DerivSRBotAccount2 - .env.ac2, direction=both, R_50, app-id 343GsiWjpyIskHP1nbTzi

Each run naturally exits after --minutes 1440 (24h), so besides an AtLogOn
trigger there's a Daily trigger to relaunch it every day; RestartCount/
RestartInterval in Settings covers a crash before the 24h mark.

    .\tools\install_sr_task.ps1              # install / update both
    .\tools\install_sr_task.ps1 -Start       # install and start both now
    .\tools\install_sr_task.ps1 -Uninstall   # remove both
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"

# 6-group ladder (deriv_bot/group_ladder.py), tuned from tools/ladder_lab.py
# over 1,500 simulated 60-day careers at the measured 48% win rate:
#
#   --recovery-mode breakeven : recovery restores the group to zero instead of
#       also delivering the whole target in one win. The old rule made the
#       Group 4-6 ladders cost 13k/24k/46k against a 10k bankroll - they could
#       never complete. Break-even brings them to 2.9k/3.9k/4.9k.
#   --on-exhaust reset : a run that loses every rung is written off and the
#       group restarts, instead of stopping the bot forever. Median career
#       length went from 13 days to the full 60.
#   --group-targets gentle : 20/30/40/50/60/70 instead of 20/32/64/128/256/512.
#       Completes 100% of groups against the escalation's 71%, at LOWER ruin
#       (8.4% vs 10.7%) - strictly better, not a tradeoff.
#
# TRADE RATE: maximise it while this is a DEMO account.
#
# Cost per trade is roughly constant at ~0.73, so total bleed scales linearly
# with volume - at ~80 trades/day the simulated 60-day mean loss is 3,488 with
# 9.3% ruin, at ~40 it is 1,839 with 2.5%. Those numbers are correct and they
# were briefly used to justify throttling to poll 20s / cooldown 300s.
#
# That was the wrong objective. On demo the bleed is not real money; DATA is
# the only output. Measured live rates before the throttle: AC1 127 trades/day,
# AC2 223/day, ~350 combined. Halving that pushed the CALL-vs-PUT question
# (needs ~700 trades per arm) from ~5 days to ~10 - paying real time to save
# fake money. Reverted to poll 10s / cooldown 120s.
#
# Rule for later: throttle on the numbers above ONLY when real money is in
# play. While on demo, faster is strictly better.
#
# --rescan-minutes 15 -> 5: levels were going stale faster than they were
# redrawn. Price parked ~0.38-0.44% from the nearest level against a 0.35%
# tolerance - permanently just out of reach - and a 15-minute wait for fresh
# levels wasted most of an hour. 5 minutes tracks price drift closely enough
# to keep levels reachable.
#
# --max-daily-loss 5000 is kept high for the same reason: fewer forced stops
# means more data. It must come down before any real-money run - and note the
# cap already overshot once (stopped at -2,238 against a 1,300 limit) because
# a stake is never compared to the remaining headroom before being placed.
$commonArgs = "--symbol R_50 --minutes 1440 --poll 10 --duration 55 " +
    "--group-system --recovery-mode breakeven --on-exhaust reset " +
    "--group-targets gentle --max-daily-loss 5000 --target-profit 3000 " +
    "--cooldown 60 --max-per-line 50 --no-confirm --require-wick " +
    "--adaptive-tolerance --retire-after-losses 2 --rescan-minutes 5"

$tasks = @(
    # Separate --lines files per account. They previously shared lines.json,
    # and with --rescan-minutes on both, either bot could overwrite the level
    # set the other was mid-trade on.
    @{ Name = "DerivSRBotAccount1"; Log = "sr_bot.log";
       Args = "run_sr_bot.py $commonArgs --direction call --lines lines.json" },
    @{ Name = "DerivSRBotAccount2"; Log = "sr_bot_ac2.log";
       Args = "run_sr_bot.py $commonArgs --direction both --env-file .env.ac2 " +
              "--app-id 343GsiWjpyIskHP1nbTzi --output-prefix ac2 " +
              "--lines ac2_lines.json" }
)

if ($Uninstall) {
    foreach ($t in $tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Output "Removed scheduled task '$($t.Name)'."
        } else {
            Write-Output "No scheduled task '$($t.Name)' to remove."
        }
    }
    return
}

if (-not (Test-Path $pythonw)) { throw "venv pythonw not found at $pythonw - create the venv first." }

foreach ($t in $tasks) {
    # cmd.exe /c so `>>` redirection captures pythonw's stdout into the log
    # file exactly like the repo's start_sr_bot*.bat scripts do - pythonw has
    # no console but still has stdout/stderr handles cmd.exe can redirect.
    $cmdArgs = "/c `"`"$pythonw`" -u $($t.Args) >> `"$($t.Log)`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $repo

    $atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $daily = New-ScheduledTaskTrigger -Daily -At (Get-Date).Date.AddHours(6)

    # SELF-HEALING: this network's TLS-inspecting proxy intermittently kills
    # the process at startup (SSL CERTIFICATE_VERIFY_FAILED), which the
    # in-process retry can outlast but not always. A repetition trigger
    # relaunches every 5 minutes; MultipleInstances IgnoreNew plus the
    # per-account lockfile means a relaunch while the bot is ALIVE exits
    # immediately instead of doubling the trade rate. So: dead -> restarted
    # within 5 min, alive -> no-op.
    $heal = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 25) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U -RunLevel Limited

    $registered = $false
    try {
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger @($atLogon, $daily, $heal) `
            -Principal $principal -Settings $settings `
            -Description "Deriv S/R Rise/Fall bot (demo account)" -Force -ErrorAction Stop | Out-Null
        $registered = $true
        Write-Output "Registered '$($t.Name)' with S4U (survives logoff too)."
    } catch {
        Write-Warning "S4U registration for '$($t.Name)' failed ($($_.Exception.Message.Trim()))."
        Write-Warning "Falling back to an interactive principal. Re-run elevated for S4U/logoff survival."
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger @($atLogon, $daily, $heal) `
            -Settings $settings -Description "Deriv S/R Rise/Fall bot (demo account)" `
            -Force -ErrorAction Stop | Out-Null
        $registered = $true
    }
    if (-not $registered) { throw "Failed to register '$($t.Name)'." }
    Write-Output "  runs: $pythonw -u $($t.Args)"
    Write-Output "  log:  $($t.Log)"

    if ($Start) {
        Start-ScheduledTask -TaskName $t.Name
        Write-Output "  started."
    }
}
