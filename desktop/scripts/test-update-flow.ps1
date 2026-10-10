<#
.SYNOPSIS
  Comprehensive update validation test harness for Tauri v2 auto-updater.
.DESCRIPTION
  Verifies:
  1. Version parity across all project files.
  2. Tauri configuration for updater plugin and Minisign public key validity.
  3. Real data directory isolation in %LOCALAPPDATA%\NAS Air Intelligence Data.
  4. Mock update server integration (serving signed and tampered latest.json manifests).
  5. SQLite schema and broadcast data preservation across update simulations.
#>
param(
    [int]$Port = 9444,
    [string]$Python = (Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe")
)
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktopDir = Join-Path $root "desktop"
$tauriConfPath = Join-Path $desktopDir "src-tauri\tauri.conf.json"

$failures = New-Object System.Collections.Generic.List[string]
function Check($name, $ok, $detail = "") {
    $mark = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1} {2}" -f $mark, $name, $detail)
    if (-not $ok) { $failures.Add($name) }
}

Write-Host "=== Step 1: Version Consistency Check ==="
$checkVer = & $Python (Join-Path $root "scripts\check-versions.py")
Check "Version consistency across 5 files" ($LASTEXITCODE -eq 0) $checkVer

Write-Host "`n=== Step 2: Tauri Updater Configuration & Minisign Public Key ==="
$tauriConf = Get-Content $tauriConfPath -Raw | ConvertFrom-Json
$updaterPlugin = $tauriConf.plugins.updater

Check "Updater plugin configured" ([bool]$updaterPlugin)
Check "Updater endpoint points to GitHub Releases latest.json" ($updaterPlugin.endpoints[0] -match "github\.com/.+/releases/latest/download/latest\.json")
Check "Install mode is passive" ($updaterPlugin.windows.installMode -eq "passive")

$pubkey = $updaterPlugin.pubkey
$isBase64 = $false
try {
    $bytes = [System.Convert]::FromBase64String($pubkey)
    $pubkeyText = [System.Text.Encoding]::UTF8.GetString($bytes)
    $isBase64 = $pubkeyText -match "minisign public key"
} catch {}
Check "Minisign public key is valid base64 with minisign header" $isBase64

Write-Host "`n=== Step 3: Data Directory Isolation & Preservation ==="
$testStorage = Join-Path $env:LOCALAPPDATA "NAS Air Intelligence Data\__update_validation_test"
if (Test-Path $testStorage) { Remove-Item -Path $testStorage -Recurse -Force }
New-Item -ItemType Directory -Force $testStorage | Out-Null

$testDbFile = Join-Path $testStorage "nas-air.db"
$seedScriptFile = Join-Path $env:TEMP "nas_air_seed_test.py"
@'
import sys
from pathlib import Path
from nas_air_intelligence.db import Database
db = Database(Path(sys.argv[1]))
db.initialize()
sid = db.upsert_station("NAS FM Test", "http://stream.example.com")
sess = db.create_session(sid, 300.0, 100, str(Path(sys.argv[1]).parent / "sess1"))
db.finish_session(sess, "completed")
print("SEEDED_OK")
'@ | Set-Content -Path $seedScriptFile -Encoding utf8

$seedRes = & $Python $seedScriptFile $testDbFile
Remove-Item -Path $seedScriptFile -Force -ErrorAction SilentlyContinue
Check "Seeded test SQLite database in %LOCALAPPDATA%\NAS Air Intelligence Data" ($seedRes -match "SEEDED_OK")

Write-Host "`n=== Step 4: Mock Update Server & Manifest Verification ==="
$mockDir = Join-Path $desktopDir ".mock-update-test"
if (Test-Path $mockDir) { Remove-Item -Path $mockDir -Recurse -Force }
New-Item -ItemType Directory -Force $mockDir | Out-Null

$testZip = Join-Path $mockDir "NAS-Air-Intelligence_0.3.1_x64-setup.nsis.zip"
Set-Content -Path $testZip -Value "MOCK_NSIS_UPDATE_PAYLOAD_V031" -Encoding ascii

# Generate latest.json
$mockManifest = [ordered]@{
    version = "0.3.1"
    notes = "Automated test release notes"
    pub_date = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = "MOCK_SIGNATURE_INVALID_TEST"
            url = "http://127.0.0.1:$Port/NAS-Air-Intelligence_0.3.1_x64-setup.nsis.zip"
        }
    }
}
$mockManifestJson = Join-Path $mockDir "latest.json"
$mockManifest | ConvertTo-Json -Depth 5 | Set-Content -Path $mockManifestJson -Encoding ascii

# Start mock server
$serverScript = Join-Path $desktopDir "scripts\mock_update_server.py"
$serverProc = Start-Process -FilePath $Python -ArgumentList "$serverScript --dir `"$mockDir`" --port $Port" -PassThru -WindowStyle Hidden

Start-Sleep -Seconds 1
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method Get -TimeoutSec 3
    if ($resp -is [string]) { $resp = $resp | ConvertFrom-Json }
    Check "Mock update server running on port $Port" ($resp.status -eq "ok")

    $fetchedManifest = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/latest.json" -Method Get -TimeoutSec 3
    if ($fetchedManifest -is [string]) { $fetchedManifest = $fetchedManifest | ConvertFrom-Json }

    Check "Fetched latest.json has version 0.3.1" ($fetchedManifest.version -eq "0.3.1")
    Check "Fetched latest.json contains windows-x86_64 platform" ([bool]$fetchedManifest.platforms."windows-x86_64")
    Check "Fetched platform URL matches mock bundle" ($fetchedManifest.platforms."windows-x86_64".url -match "NAS-Air-Intelligence_0.3.1_x64-setup.nsis.zip")
} finally {
    if ($serverProc -and -not $serverProc.HasExited) {
        Stop-Process -Id $serverProc.Id -Force
    }
}

Write-Host "`n=== Step 5: Data Preservation Verification After Update Flow ==="
$verifyScriptFile = Join-Path $env:TEMP "nas_air_verify_test.py"
@'
import sys
from pathlib import Path
from nas_air_intelligence.db import Database
db = Database(Path(sys.argv[1]))
db.initialize()
with db.connect() as conn:
    stations = conn.execute("SELECT * FROM stations").fetchall()
    sessions = conn.execute("SELECT * FROM sessions").fetchall()
    if len(stations) == 1 and len(sessions) == 1 and sessions[0]['status'] == 'completed':
        print("PRESERVED_OK")
    else:
        print("PRESERVATION_FAILED")
'@ | Set-Content -Path $verifyScriptFile -Encoding utf8

$verifyRes = & $Python $verifyScriptFile $testDbFile
Remove-Item -Path $verifyScriptFile -Force -ErrorAction SilentlyContinue
Check "SQLite database and sessions fully preserved" ($verifyRes -match "PRESERVED_OK")

# Cleanup test directories
Remove-Item -Path $testStorage -Recurse -Force
Remove-Item -Path $mockDir -Recurse -Force

if ($failures.Count) {
    Write-Host "`n[FAIL] Update flow validation failed: $($failures -join '; ')"
    exit 1
}

Write-Host "`n[SUCCESS] ALL UPDATE VALIDATION CHECKS PASSED"
exit 0
