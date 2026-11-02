param(
    [switch]$BuildExecutables,
    [string]$IdentityName = $(if ($env:MSIX_IDENTITY_NAME) { $env:MSIX_IDENTITY_NAME } else { "WiZAmbientSync" }),
    [string]$Publisher = $(if ($env:MSIX_PUBLISHER) { $env:MSIX_PUBLISHER } else { "CN=WiZ Ambient Sync" }),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "msix-output"),
    [string]$CertificatePath = "",
    [string]$CertificatePassword = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. Install the Windows App SDK/MSIX tooling or use a Windows runner with makeappx.exe and signtool.exe available."
    }
}

function Convert-ToPackageVersion([string]$Value) {
    $match = [regex]::Match($Value, '^([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?')
    if (-not $match.Success) { throw "VERSION '$Value' does not start with a numeric version." }
    $parts = @($match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value) | ForEach-Object {
        if ($_ -eq '') { '0' } else { $_ }
    }
    return ($parts + '0') -join '.'
}

if ($IdentityName -match '[^A-Za-z0-9.\-]') { throw "IdentityName contains unsupported MSIX characters: $IdentityName" }
if ([string]::IsNullOrWhiteSpace($Publisher)) { throw "Publisher must not be empty." }

$versionText = (Get-Content (Join-Path $PSScriptRoot 'VERSION') -Raw).Trim()
$packageVersion = Convert-ToPackageVersion $versionText
$distDirectory = Join-Path $PSScriptRoot ("dist\" + $versionText)
$stagingDirectory = Join-Path $PSScriptRoot ("packaging\msix\.staging\" + $packageVersion)
$packageDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$packagePath = Join-Path $packageDirectory ("WiZ-Ambient-Sync-$packageVersion-x64.msix")

if ($BuildExecutables) {
    & python (Join-Path $PSScriptRoot 'build.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE." }
}

$guiPath = Join-Path $distDirectory 'WiZAmbientSync.exe'
$enginePath = Join-Path $distDirectory 'wiz-engine.exe'
if (-not (Test-Path -LiteralPath $guiPath)) { throw "GUI executable was not found: $guiPath" }
if (-not (Test-Path -LiteralPath $enginePath)) { throw "Engine executable was not found: $enginePath" }

Require-Command 'makeappx.exe'

$stagingRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'packaging\msix\.staging'))
if (-not $stagingDirectory.StartsWith($stagingRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a staging path outside packaging/msix/.staging."
}
if (Test-Path -LiteralPath $stagingDirectory) { Remove-Item -LiteralPath $stagingDirectory -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $stagingDirectory 'Assets') -Force | Out-Null
New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null

$manifest = Get-Content (Join-Path $PSScriptRoot 'packaging\msix\AppxManifest.xml') -Raw
$manifest = $manifest.Replace('__IDENTITY_NAME__', $IdentityName).Replace('__PUBLISHER__', $Publisher).Replace('__VERSION__', $packageVersion)
$manifest | Set-Content (Join-Path $stagingDirectory 'AppxManifest.xml') -Encoding UTF8

Copy-Item -LiteralPath $guiPath -Destination (Join-Path $stagingDirectory 'WiZAmbientSync.exe')
Copy-Item -LiteralPath $enginePath -Destination (Join-Path $stagingDirectory 'wiz-engine.exe')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app\app_icon.png') -Destination (Join-Path $stagingDirectory 'Assets\StoreLogo.png')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app\app_icon.png') -Destination (Join-Path $stagingDirectory 'Assets\Square44x44Logo.png')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app\app_icon.png') -Destination (Join-Path $stagingDirectory 'Assets\Square150x150Logo.png')

if (Test-Path -LiteralPath $packagePath) { Remove-Item -LiteralPath $packagePath -Force }
& makeappx.exe pack /d $stagingDirectory /p $packagePath /o
if ($LASTEXITCODE -ne 0) { throw "makeappx failed with exit code $LASTEXITCODE." }

if ($CertificatePath) {
    Require-Command 'signtool.exe'
    $signArguments = @('sign', '/fd', 'SHA256', '/f', $CertificatePath)
    if ($CertificatePassword) { $signArguments += @('/p', $CertificatePassword) }
    $signArguments += $packagePath
    & signtool.exe @signArguments
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE." }
}

Write-Host "MSIX created: $packagePath" -ForegroundColor Green
Write-Host "Package version: $packageVersion"
Write-Host "Identity: $IdentityName"
Write-Host "Publisher: $Publisher"
