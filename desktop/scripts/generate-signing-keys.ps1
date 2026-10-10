<#
.SYNOPSIS
  Generates a fresh Minisign keypair for Tauri v2 auto-updates.
.DESCRIPTION
  Runs `npx tauri signer generate` to produce a public key (for tauri.conf.json)
  and a private key (for GitHub Repository Secrets TAURI_SIGNING_PRIVATE_KEY).
#>
param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "..\.keys")
)
$ErrorActionPreference = "Stop"

$desktopDir = Resolve-Path (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force $OutputDir | Out-Null
$keyPath = Join-Path $OutputDir "tauri.key"

Write-Host "Generating Minisign keypair using Tauri CLI..."
Push-Location $desktopDir
try {
    & cmd.exe /c "npx.cmd tauri signer generate -w `"$keyPath`""
} finally {
    Pop-Location
}

if (Test-Path $keyPath) {
    Write-Host "`n[SUCCESS] Keypair generated at: $OutputDir"
    Write-Host "1. Public key is in tauri.key.pub (set in desktop/src-tauri/tauri.conf.json under plugins.updater.pubkey)"
    Write-Host "2. Private key is in tauri.key (set in GitHub Secrets as TAURI_SIGNING_PRIVATE_KEY)"
    Write-Host "3. Keep tauri.key secure and NEVER commit it to git!"
} else {
    Write-Warning "Could not find generated key at $keyPath"
}
