<#
Tails alerts.jsonl and toasts each new entry.

This is the half of the alerting that has a desktop. The supervisor runs S4U
in session 0 and cannot display anything, so it only writes alerts; this
script runs in the logged-in interactive session and does the showing.

Starts at the END of the file on purpose. Logging in after an outage should
show what is happening now, not replay a burst of stale toasts from hours
ago - and the alert that matters is always the most recent one.

    .\tools\alert_watcher.ps1                 # follow alerts.jsonl
    .\tools\alert_watcher.ps1 -FromStart      # replay everything (debugging)

Pure ASCII - see the note in notify.ps1.
#>
[CmdletBinding()]
param(
    [string]$AlertsFile,
    [string]$NotifyScript,
    [switch]$FromStart,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
if (-not $AlertsFile)   { $AlertsFile = Join-Path $repo "alerts.jsonl" }
if (-not $NotifyScript) { $NotifyScript = Join-Path $PSScriptRoot "notify.ps1" }

if (-not (Test-Path $NotifyScript)) { throw "notify.ps1 not found at $NotifyScript" }

function Get-LineCount([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    try { return @(Get-Content -LiteralPath $path -ErrorAction Stop).Count } catch { return 0 }
}

# Skip whatever is already there unless explicitly replaying.
$seen = if ($FromStart) { 0 } else { Get-LineCount $AlertsFile }

Write-Output "watching $AlertsFile (starting at line $seen); toasts via $NotifyScript"

while ($true) {
    Start-Sleep -Seconds $PollSeconds

    $total = Get-LineCount $AlertsFile
    if ($total -lt $seen) {
        # File shrank - truncated or rotated. Re-follow from the new end
        # rather than replaying it from the top.
        $seen = $total
        continue
    }
    if ($total -eq $seen) { continue }

    $new = @(Get-Content -LiteralPath $AlertsFile | Select-Object -Skip $seen)
    $seen = $total

    foreach ($line in $new) {
        if (-not $line.Trim()) { continue }
        try { $alert = $line | ConvertFrom-Json } catch { continue }  # half-written line
        $level = if ($alert.level) { $alert.level } else { "info" }
        $title = if ($level -eq "problem") { "Deriv bot PROBLEM" } else { "Deriv bot" }
        $body = "$($alert.event): $($alert.message)"
        & $NotifyScript -Title $title -Message $body | Out-Null
        Write-Output "[$(Get-Date -Format 'HH:mm:ss')] toasted -> $title / $body"
    }
}
