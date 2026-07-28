<#
Shows a Windows toast. Adapted from marketlab\notify.ps1 so this repo does
not depend on a sibling project.

This CANNOT be called usefully by the supervisor: that task runs with an S4U
principal in session 0, which has no desktop, and the toast APIs fail there
(the catch below is what that failure looks like). Only alert_watcher.ps1,
which runs in the logged-in interactive session, should call this.

MUST BE RUN BY powershell.exe (Windows PowerShell 5.1), NOT pwsh 7. The
WinRT toast types are not loadable from PowerShell 7 on .NET Core - it fails
with "Unable to find type [Windows.UI.Notifications.ToastNotificationManager]"
and the catch below turns that into a silent no-op. install_task.ps1 -Alerts
therefore registers the watcher against powershell.exe explicitly.

Pure ASCII on purpose. Windows PowerShell 5.1 reads .ps1 as ANSI unless the
file has a BOM, so a non-ASCII character inside a string terminates it early
and breaks the parse - that is exactly how install_task.ps1 failed in an
elevated 5.1 window.
#>
param(
    [string]$Title = "Deriv bot",
    [string]$Message = "event"
)
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
    $safeTitle = [System.Security.SecurityElement]::Escape($Title)
    $safeMsg = [System.Security.SecurityElement]::Escape($Message)
    $template = "<toast><visual><binding template=""ToastText02""><text id=""1"">$safeTitle</text><text id=""2"">$safeMsg</text></binding></visual></toast>"
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($template)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Deriv bot").Show($toast)
    Write-Output "toast shown: $Title - $Message"
} catch {
    # Toast APIs unavailable (e.g. non-interactive session) - alerts.jsonl
    # and scan_trade_live.log still have everything.
    Write-Output "toast failed: $_"
}
