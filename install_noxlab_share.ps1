param(
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

$appName = "NoxLab Share"
$projectDir = $PSScriptRoot
$shortcutScript = Join-Path $projectDir "scripts\create_desktop_shortcut.ps1"
$launcher = Join-Path $projectDir "start_noxlab_share.cmd"

if (-not (Test-Path $shortcutScript)) {
    throw "Shortcut setup script is missing: $shortcutScript"
}

if (-not (Test-Path $launcher)) {
    throw "App launcher is missing: $launcher"
}

& $shortcutScript

if ($NoLaunch) {
    Write-Host "$appName shortcuts were created."
    return
}

Start-Process -FilePath $launcher -WorkingDirectory $projectDir

$shell = New-Object -ComObject WScript.Shell
$null = $shell.Popup("$appName shortcuts were created and the app was started.", 4, "$appName Setup", 64)
