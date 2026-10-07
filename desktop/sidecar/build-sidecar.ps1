<#
.SYNOPSIS
  Freezes the NAS Air Python sidecar into the Tauri externalBin location.
.NOTES
  Uses a clean venv so only what the sidecar imports is bundled (no ML stack).
  The sidecar is a *console-subsystem* exe on purpose: the desktop starts it with
  CREATE_NO_WINDOW (no window) and talks to it over stdin/stdout pipes, which a
  --noconsole build would not provide.
#>
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$work = Join-Path $root "desktop\.sidecar-build"
$venv = Join-Path $work "venv"
$triple = "x86_64-pc-windows-msvc"
$out = Join-Path $root "desktop\src-tauri\binaries"

New-Item -ItemType Directory -Force $work, $out | Out-Null
if (-not (Test-Path "$venv\Scripts\python.exe")) { python -m venv $venv }
$py = "$venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip pyinstaller
# No --no-deps: the sidecar imports openpyxl (and other base dependencies) eagerly, so pip
# must actually install them into this venv for PyInstaller to find and bundle them. The
# heavy, genuinely unused ones (fastapi/uvicorn/the ML stack) are kept out of the frozen
# binary by the --exclude-module flags below regardless of what is installed here.
& $py -m pip install --quiet --upgrade --force-reinstall --no-deps $root
& $py -m pip install --quiet --upgrade $root

& $py -m PyInstaller --noconfirm --clean --onefile --name "nas-air-sidecar" `
    --distpath "$work\dist" --workpath "$work\build" --specpath $work `
    --exclude-module faster_whisper --exclude-module ctranslate2 --exclude-module av `
    --exclude-module numpy --exclude-module fastapi --exclude-module uvicorn `
    (Join-Path $root "desktop\sidecar\entry.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item "$work\dist\nas-air-sidecar.exe" (Join-Path $out "nas-air-sidecar-$triple.exe") -Force
Write-Host "sidecar ready: $out\nas-air-sidecar-$triple.exe"
