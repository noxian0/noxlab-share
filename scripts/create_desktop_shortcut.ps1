param(
    [string]$ShortcutName = "NoxLab Share",
    [string]$FolderShortcutName = "Open NoxLab Share"
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$standalone = Join-Path $projectDir "dist\NoxLab Share.exe"
$pythonw = Join-Path $projectDir ".venv\Scripts\pythonw.exe"
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$launcher = Join-Path $projectDir "start_noxlab_share.cmd"
$icon = Join-Path $projectDir "assets\noxlab_share.ico"

if (Test-Path $standalone) {
    $target = $standalone
    $arguments = ""
} elseif (Test-Path $pythonw) {
    $target = $pythonw
    $arguments = "-m noxlab_share"
} elseif (Test-Path $python) {
    $target = $python
    $arguments = "-m noxlab_share"
} elseif (Test-Path $launcher) {
    $target = $launcher
    $arguments = ""
} else {
    throw "No launcher was found. Create the virtual environment first or restore start_noxlab_share.cmd."
}

function New-NoxLabShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectDir
    $shortcut.Description = $Description

    if (Test-Path $icon) {
        $shortcut.IconLocation = $icon
    }

    $shortcut.Save()
}

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
if ($desktop -eq $projectDir) {
    $desktop = Split-Path -Parent $projectDir
}

$desktopShortcutPath = Join-Path $desktop "$ShortcutName.lnk"
$folderShortcutPath = Join-Path $projectDir "$FolderShortcutName.lnk"

New-NoxLabShortcut -Path $desktopShortcutPath -Description "Start NoxLab Share"
New-NoxLabShortcut -Path $folderShortcutPath -Description "Open NoxLab Share from this folder"

Write-Host "Desktop shortcut created: $desktopShortcutPath"
Write-Host "Folder shortcut created: $folderShortcutPath"
