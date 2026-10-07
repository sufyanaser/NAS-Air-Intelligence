<#
.SYNOPSIS
  Phase 1 acceptance smoke test for the built NAS Air desktop app (Windows).
.DESCRIPTION
  Launches the release exe and verifies: the window opens, React renders, the Python sidecar
  was started automatically and reports ready, no console window is visible anywhere in the
  process tree, and no sidecar/FFmpeg process survives a graceful close or a hard kill.
.PARAMETER Exe
  Path to nas-air-desktop.exe (default: the release build).
#>
param(
    [string]$Exe = (Join-Path $PSScriptRoot "..\src-tauri\target\release\nas-air-desktop.exe"),
    [string]$Python = (Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe")
)
$ErrorActionPreference = "Stop"
$Exe = (Resolve-Path $Exe).Path
$Python = (Resolve-Path $Python).Path
$debugPort = 9333
$failures = New-Object System.Collections.Generic.List[string]
function Check($name, $ok, $detail = "") {
    $mark = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host ("[{0}] {1} {2}" -f $mark, $name, $detail)
    if (-not $ok) { $failures.Add($name) }
}

Add-Type @"
using System; using System.Collections.Generic; using System.Runtime.InteropServices; using System.Text;
public static class WinEnum {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
  public static List<string> Visible(HashSet<uint> pids) {
    var r = new List<string>();
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid);
      if (pids.Contains(pid) && IsWindowVisible(h)) { var sb = new StringBuilder(256); GetClassName(h, sb, 256); r.Add(pid + ":" + sb); }
      return true; }, IntPtr.Zero);
    return r;
  }
  public static void Close(HashSet<uint> pids) {
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid);
      if (pids.Contains(pid) && IsWindowVisible(h)) { PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero); }
      return true; }, IntPtr.Zero);
  }
}
"@

function Get-Tree([int]$rootPid) {
    $all = Get-CimInstance Win32_Process
    $ids = New-Object System.Collections.Generic.HashSet[int]
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($rootPid)
    while ($queue.Count) {
        $p = $queue.Dequeue()
        if ($ids.Add($p)) { $all | Where-Object { $_.ParentProcessId -eq $p } | ForEach-Object { $queue.Enqueue([int]$_.ProcessId) } }
    }
    $all | Where-Object { $ids.Contains([int]$_.ProcessId) }
}
function Strays { Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @("nas-air-sidecar", "ffmpeg", "ffprobe") } }

if (Strays) { throw "Stray sidecar/ffmpeg processes already running; stop them first." }

$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$debugPort"
$app = Start-Process -FilePath $Exe -PassThru
Write-Host "launched pid $($app.Id)"

# 1) wait for the UI to report ready (React rendered + sidecar health = ready)
$cdp = @'
import json, sys, time, urllib.request
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from websockets.sync.client import connect
port = sys.argv[1]
deadline = time.time() + 60
text = ""
while time.time() < deadline:
    try:
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
        page = next(p for p in pages if p.get("type") == "page")
        with connect(page["webSocketDebuggerUrl"]) as ws:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": "document.body.innerText"}}))
            text = json.loads(ws.recv())["result"]["result"].get("value", "")
        if "Engine ready" in text or "unavailable" in text:
            break
    except Exception:
        pass
    time.sleep(1)
print(text.replace("\n", " | "))
'@
$cdpFile = Join-Path $env:TEMP "nas-air-smoke-cdp.py"
Set-Content -Path $cdpFile -Value $cdp -Encoding utf8
$uiText = & $Python $cdpFile $debugPort
Write-Host "UI text: $uiText"
Check "React UI rendered" ($uiText -match "NAS Air Intelligence")
Check "Health check returns ready" ($uiText -match "Engine ready")

# 2) sidecar started automatically, and nothing in the tree has a visible window except the app
$tree = Get-Tree $app.Id
$sidecar = $tree | Where-Object { $_.Name -eq "nas-air-sidecar.exe" }
Check "Python sidecar started automatically" ([bool]$sidecar) "($(@($sidecar).Count) process(es))"
$treePids = New-Object "System.Collections.Generic.HashSet[uint32]"
$tree | ForEach-Object { [void]$treePids.Add([uint32]$_.ProcessId) }
$sidecarPids = New-Object "System.Collections.Generic.HashSet[uint32]"
$sidecar | ForEach-Object { [void]$sidecarPids.Add([uint32]$_.ProcessId) }
$consoleHosts = $tree | Where-Object { $_.Name -in @("conhost.exe", "cmd.exe", "powershell.exe", "OpenConsole.exe") }
# A CREATE_NO_WINDOW console process always has a windowless conhost.exe; that is expected.
# What matters is that no console *window* is visible (checked below).
Write-Host ("[INFO] windowless console hosts in tree: {0}" -f @($consoleHosts).Count)
$visibleSidecar = [WinEnum]::Visible($sidecarPids)
Check "No visible sidecar window" ($visibleSidecar.Count -eq 0) ($visibleSidecar -join ",")
$visibleAll = [WinEnum]::Visible($treePids) | Where-Object { $_ -match "ConsoleWindowClass|CASCADIA" }
Check "No visible console window anywhere in the tree" (-not $visibleAll) ($visibleAll -join ",")

# 3) graceful close leaves nothing behind
Add-Type -AssemblyName UIAutomationClient
$win = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
    [System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition) |
    Where-Object { $_.Current.ProcessId -eq $app.Id -and $_.Current.Name -eq 'NAS Air Intelligence' } |
    Select-Object -First 1
if ($win) {
    $win.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
} else {
    [void]$app.CloseMainWindow()
    [WinEnum]::Close($treePids)
}
$null = $app.WaitForExit(15000)
Start-Sleep -Seconds 2
Check "App exited on close" $app.HasExited
$left = @(Strays)
Check "No orphan sidecar/FFmpeg after graceful close" ($left.Count -eq 0) (($left | ForEach-Object Name) -join ",")

# 4) hard kill (crash) leaves nothing behind either
$app = Start-Process -FilePath $Exe -PassThru
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline -and -not (Strays)) { Start-Sleep -Milliseconds 500 }
Check "Sidecar started on second launch" ([bool](Strays))
Stop-Process -Id $app.Id -Force
Start-Sleep -Seconds 4
$left = @(Strays)
Check "No orphan sidecar/FFmpeg after hard kill" ($left.Count -eq 0) (($left | ForEach-Object Name) -join ",")

if ($failures.Count) { Write-Host "`nFAILED: $($failures -join '; ')"; exit 1 }
Write-Host "`nALL CHECKS PASSED"
