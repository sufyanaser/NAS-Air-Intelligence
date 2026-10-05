param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId
)

$ErrorActionPreference = "Stop"
$Process = Get-Process -Id $ProcessId -ErrorAction Stop
if ($Process.ProcessName -notmatch "python") {
    throw "PID $ProcessId is not a Python process; refusing to stop it."
}
Stop-Process -Id $ProcessId
Write-Output "Stopped NAS Air Intelligence monitor PID $ProcessId"
