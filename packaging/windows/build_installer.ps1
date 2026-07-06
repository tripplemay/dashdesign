param(
    [string]$Version = "0.1.0",
    [string]$SourceDir = "dist\DashDesign",
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

$SourceDir = (Resolve-Path $SourceDir).Path
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}
$OutputDir = (Resolve-Path $OutputDir).Path

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $default = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $default) {
        $iscc = Get-Item $default
    }
}

if (-not $iscc) {
    throw "Inno Setup ISCC.exe not found. Install Inno Setup 6 first."
}

& $iscc `
    "/DAppVersion=$Version" `
    "/DSourceDir=$SourceDir" `
    "/DOutputDir=$OutputDir" `
    "packaging\windows\DashDesign.iss"

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
