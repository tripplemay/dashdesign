param(
    [Parameter(Mandatory=$true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Path)) {
    throw "File not found: $Path"
}

$timestampUrl = $env:WINDOWS_TIMESTAMP_URL
if (-not $timestampUrl) {
    $timestampUrl = "http://timestamp.digicert.com"
}

$certPath = $env:WINDOWS_SIGN_CERT_PATH
$certPassword = $env:WINDOWS_SIGN_CERT_PASSWORD
$tempCert = $null

if (-not $certPath -and $env:WINDOWS_CERTIFICATE_BASE64) {
    $tempCert = Join-Path $env:RUNNER_TEMP "dashdesign-signing.pfx"
    [IO.File]::WriteAllBytes($tempCert, [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_BASE64))
    $certPath = $tempCert
    $certPassword = $env:WINDOWS_CERTIFICATE_PASSWORD
}

if (-not $certPath) {
    throw "Set WINDOWS_SIGN_CERT_PATH or WINDOWS_CERTIFICATE_BASE64."
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
        Where-Object { $_.FullName -match "x64" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
}

if (-not $signtool) {
    throw "signtool.exe not found."
}

$args = @(
    "sign",
    "/fd", "SHA256",
    "/tr", $timestampUrl,
    "/td", "SHA256",
    "/f", $certPath
)
if ($certPassword) {
    $args += @("/p", $certPassword)
}
$args += $Path

& $signtool @args
if ($LASTEXITCODE -ne 0) {
    throw "signtool failed with exit code $LASTEXITCODE"
}

& $signtool verify /pa /v $Path
if ($LASTEXITCODE -ne 0) {
    throw "signtool verify failed with exit code $LASTEXITCODE"
}
