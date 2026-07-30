# Wrapper run under elevation. Logs everything so the result is verifiable
# from the non-elevated session that launched it.
$ErrorActionPreference = "Continue"
$repo = "C:\Users\ayori\derivmasterpiece"
$log  = Join-Path $repo "install_task_out.log"
"=== elevated install started $(Get-Date -Format s) ===" | Out-File $log -Encoding utf8
try {
    Set-Location $repo
    & (Join-Path $repo "tools\install_risefall_task.ps1") *>&1 |
        Out-File $log -Append -Encoding utf8
    "EXITCODE=0" | Out-File $log -Append -Encoding utf8
} catch {
    ("FAILED: " + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    "EXITCODE=1" | Out-File $log -Append -Encoding utf8
}
