param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string]$Url,

    [string]$Duration = "24h",
    [int]$SegmentSeconds = 300,
    [ValidateSet("none", "baseline", "ina")]
    [string]$Analyzer = "baseline",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StdOut = Join-Path $LogDir "monitor-$Stamp.out.log"
$StdErr = Join-Path $LogDir "monitor-$Stamp.err.log"

$Arguments = @(
    "-m", "nas_air_intelligence.cli",
    "monitor",
    "--name", $Name,
    "--url", $Url,
    "--duration", $Duration,
    "--segment-seconds", "$SegmentSeconds",
    "--analyzer", $Analyzer
)

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdOut `
    -RedirectStandardError $StdErr `
    -WindowStyle Hidden `
    -PassThru

[PSCustomObject]@{
    PID = $Process.Id
    Station = $Name
    Duration = $Duration
    StdOut = $StdOut
    StdErr = $StdErr
}
