param(
    [string]$Version = "0.1.0",
    [switch]$BuildSetup
)

$ErrorActionPreference = "Stop"

$projectDir = Get-Location
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$distName = "NoxLabShare-$Version-windows"
$releaseDir = Join-Path $projectDir "release"
$payloadDir = Join-Path $releaseDir "setup_payload"
$zipPath = Join-Path $releaseDir "$distName.zip"
$setupPath = Join-Path $releaseDir "NoxLabShare-$Version-Setup.exe"
$iconPath = Join-Path $projectDir "assets\noxlab_share.ico"
$launcherPath = Join-Path $projectDir "noxlab_share_launcher.py"

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

if (-not (Test-Path $iconPath)) {
    & $python .\scripts\generate_icon.py
}

& $python -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name "NoxLab Share" `
    --icon $iconPath `
    --add-data "$iconPath;assets" `
    --collect-all qrcode `
    --collect-all PIL `
    $launcherPath

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$exePath = Join-Path $projectDir "dist\NoxLab Share.exe"
if (-not (Test-Path $exePath)) {
    throw "Expected PyInstaller output was not found: $exePath"
}

if (Test-Path $payloadDir) {
    Remove-Item -LiteralPath $payloadDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null

Copy-Item -LiteralPath $exePath -Destination (Join-Path $payloadDir "NoxLab Share.exe") -Force
Copy-Item -LiteralPath $iconPath -Destination (Join-Path $payloadDir "noxlab_share.ico") -Force
Copy-Item -LiteralPath "README.md" -Destination $payloadDir -Force
Copy-Item -LiteralPath "LICENSE" -Destination $payloadDir -Force
Copy-Item -LiteralPath "scripts\install_noxlab_share.ps1" -Destination $payloadDir -Force
Copy-Item -LiteralPath "scripts\install_noxlab_share.cmd" -Destination (Join-Path $payloadDir "Install.cmd") -Force

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $payloadDir "*") -DestinationPath $zipPath
Write-Host "Portable release ZIP created: $zipPath"

if ($BuildSetup) {
    $setupStubSource = Join-Path $projectDir "scripts\setup_stub.cs"
    $setupStubPath = Join-Path $releaseDir "NoxLabShareSetupStub.exe"

    if (Test-Path $setupStubPath) {
        Remove-Item -LiteralPath $setupStubPath -Force
    }
    if (Test-Path $setupPath) {
        Remove-Item -LiteralPath $setupPath -Force
    }

    $setupStubCode = Get-Content -LiteralPath $setupStubSource -Raw
    Add-Type `
        -TypeDefinition $setupStubCode `
        -ReferencedAssemblies @("System.Windows.Forms.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll") `
        -OutputAssembly $setupStubPath `
        -OutputType WindowsApplication

    $marker = [System.Text.Encoding]::ASCII.GetBytes("`n--NOXLAB-SHARE-PAYLOAD-V1--`n")
    [byte[]]$stubBytes = [System.IO.File]::ReadAllBytes($setupStubPath)
    [byte[]]$zipBytes = [System.IO.File]::ReadAllBytes($zipPath)

    $output = [System.IO.File]::Create($setupPath)
    try {
        $output.Write($stubBytes, 0, $stubBytes.Length)
        $output.Write($marker, 0, $marker.Length)
        $output.Write($zipBytes, 0, $zipBytes.Length)
    } finally {
        $output.Dispose()
    }

    Remove-Item -LiteralPath $setupStubPath -Force
    Write-Host "Unsigned setup installer created: $setupPath"
} else {
    Write-Host "Setup installer skipped. Use -BuildSetup to create the unsigned setup EXE."
}
