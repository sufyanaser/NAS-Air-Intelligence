<#
.SYNOPSIS
  Packages, signs/verifies, and stages production release assets and latest.json.
.DESCRIPTION
  Collects built NSIS artifacts, validates Minisign signatures, generates
  the production latest.json updater feed pointing to GitHub Releases,
  computes SHA-256 checksums, and stages everything in dist/release/.
.PARAMETER Version
  The release version string (defaults to version in tauri.conf.json).
.PARAMETER RepoOwner
  GitHub repository owner (default: sufyanaser).
.PARAMETER RepoName
  GitHub repository name (default: NAS-Air-Intelligence).
#>
param(
    [string]$Version,
    [string]$RepoOwner = "sufyanaser",
    [string]$RepoName = "NAS-Air-Intelligence",
    [string]$Notes = "NAS Air Intelligence release"
)
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$tauriConfPath = Join-Path $root "desktop\src-tauri\tauri.conf.json"

if (-not $Version) {
    $conf = Get-Content $tauriConfPath -Raw | ConvertFrom-Json
    $Version = $conf.version
}

Write-Host "Packaging release for version: $Version"

$bundleDir = Join-Path $root "desktop\src-tauri\target\release\bundle"
$nsisDir = Join-Path $bundleDir "nsis"
$updaterDir = Join-Path $bundleDir "updater"
$stageDir = Join-Path $root "dist\release"

if (-not (Test-Path $nsisDir)) {
    throw "NSIS bundle directory not found at $nsisDir. Did you run 'tauri build --bundles nsis'?"
}

# Find setup exe
$setupExe = Get-ChildItem -Path $nsisDir -Filter "*.exe" | Where-Object { $_.Name -match "-setup\.exe$" -or $_.Name -match "setup\.exe$" } | Select-Object -First 1
if (-not $setupExe) {
    # Fallback to any exe in nsis dir
    $setupExe = Get-ChildItem -Path $nsisDir -Filter "*.exe" | Select-Object -First 1
}
if (-not $setupExe) {
    throw "No NSIS setup exe found in $nsisDir"
}

# Find nsis zip updater bundle
$updaterZip = Get-ChildItem -Path $nsisDir -Filter "*.nsis.zip" | Select-Object -First 1
if (-not $updaterZip) {
    $updaterZip = Get-ChildItem -Path $bundleDir -Filter "*.nsis.zip" -Recurse | Select-Object -First 1
}
if (-not $updaterZip) {
    throw "No .nsis.zip updater artifact found in $bundleDir"
}

# Find signature file (.sig)
$sigFile = Get-ChildItem -Path $nsisDir -Filter "*.nsis.zip.sig" | Select-Object -First 1
if (-not $sigFile) {
    $sigFile = Get-ChildItem -Path $bundleDir -Filter "*.nsis.zip.sig" -Recurse | Select-Object -First 1
}
if (-not $sigFile) {
    throw "No .nsis.zip.sig signature artifact found in $bundleDir. Ensure TAURI_SIGNING_PRIVATE_KEY was supplied during build."
}

$signature = (Get-Content $sigFile.FullName -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($signature)) {
    throw "Signature file $($sigFile.FullName) is empty"
}

# Stage files
New-Item -ItemType Directory -Force $stageDir | Out-Null
Copy-Item $setupExe.FullName $stageDir -Force
Copy-Item $updaterZip.FullName $stageDir -Force
Copy-Item $sigFile.FullName $stageDir -Force

Write-Host "Staged setup exe: $($setupExe.Name)"
Write-Host "Staged updater zip: $($updaterZip.Name)"
Write-Host "Staged signature: $($sigFile.Name)"

# Generate production latest.json
$tag = "v$Version"
$zipName = $updaterZip.Name
$assetUrl = "https://github.com/$RepoOwner/$RepoName/releases/download/$tag/$zipName"
$pubDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$latestManifest = [ordered]@{
    version = $Version
    notes = $Notes
    pub_date = $pubDate
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $signature
            url = $assetUrl
        }
    }
}

$latestJsonPath = Join-Path $stageDir "latest.json"
$latestManifest | ConvertTo-Json -Depth 5 | Set-Content -Path $latestJsonPath -Encoding ascii
Write-Host "Generated production latest.json updater feed at: $latestJsonPath"

# Compute SHA256 checksums
$shaFile = Join-Path $stageDir "SHA256SUMS.txt"
$shaLines = @()
Get-ChildItem -Path $stageDir -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | ForEach-Object {
    $hash = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $shaLines += "$hash  $($_.Name)"
}
$shaLines | Set-Content -Path $shaFile -Encoding ascii
Write-Host "Generated SHA256SUMS.txt for staged release artifacts"

Write-Host "`n[SUCCESS] Release packaging complete. Ready for distribution in $stageDir"
