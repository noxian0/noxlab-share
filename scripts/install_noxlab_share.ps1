$ErrorActionPreference = "Stop"

$sourceDir = $PSScriptRoot
$appName = "NoxLab Share"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\NoxLab Share"
$exeSource = Join-Path $sourceDir "NoxLab Share.exe"
$iconSource = Join-Path $sourceDir "noxlab_share.ico"

if (-not (Test-Path $exeSource)) {
    throw "Installer payload is missing: $exeSource"
}

New-Item -ItemType Directory -Force -Path $installDir | Out-Null

$exeTarget = Join-Path $installDir "NoxLab Share.exe"
$iconTarget = Join-Path $installDir "noxlab_share.ico"

Copy-Item -LiteralPath $exeSource -Destination $exeTarget -Force

if (Test-Path $iconSource) {
    Copy-Item -LiteralPath $iconSource -Destination $iconTarget -Force
}

foreach ($doc in @("README.md", "LICENSE")) {
    $source = Join-Path $sourceDir $doc
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $installDir $doc) -Force
    }
}

function New-AppShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $folder = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $folder | Out-Null

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $exeTarget
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Description = "Start $appName"
    if (Test-Path $iconTarget) {
        $shortcut.IconLocation = "$iconTarget,0"
    }
    $shortcut.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
New-AppShortcut -Path (Join-Path $desktop "$appName.lnk")
New-AppShortcut -Path (Join-Path $installDir "Open $appName.lnk")

$programs = [Environment]::GetFolderPath("Programs")
$startMenuDir = Join-Path $programs $appName
New-AppShortcut -Path (Join-Path $startMenuDir "$appName.lnk")

Start-Process -FilePath $exeTarget

$shell = New-Object -ComObject WScript.Shell
$null = $shell.Popup("$appName installed successfully.", 4, "$appName Setup", 64)
