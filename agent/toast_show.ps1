param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Body,
    [Parameter(Mandatory = $true)][string]$Icon,
    [Parameter(Mandatory = $true)][string]$Aumid
)

# Show a Windows toast that looks like the MAA client's "task finished" card:
# big image on the left, bold title + body on the right.
#
# We go through PowerShell because winrt 3.2.1 cannot call
# ToastNotificationManager.CreateToastNotifier(aumid) from Python: passing a
# string raises "TypeError: Invalid parameter count" (HSTRING binding bug).
# PowerShell's WinRT projection has no such problem.
#
# Keep this file ASCII-only: PowerShell 5.1 reads a BOM-less UTF-8 script as
# ANSI, and mangled bytes inside the here-string corrupt the XML.

$ErrorActionPreference = 'Stop'

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
$t = & $esc $Title
$b = & $esc $Body
$src = 'file:///' + ($Icon -replace '\\', '/')

$xmlText = @"
<toast><visual><binding template="ToastImageAndText02"><image id="1" src="$src"/><text id="1">$t</text><text id="2">$b</text></binding></visual></toast>
"@

$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml($xmlText)
$n = New-Object Windows.UI.Notifications.ToastNotification $x
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid).Show($n)
Write-Output 'shown'
