import base64
import json
import os
import subprocess
import time
import urllib.request

from websockets.sync.client import connect

exe = r"C:\Users\sufya\AppData\Local\NAS Air Intelligence\nas-air-desktop.exe"
env = dict(**os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9446"

# Clean up any lingering processes
kill_cmd = (
    "Get-Process -Name nas-air*, ffmpeg*, ffprobe* "
    "-ErrorAction SilentlyContinue | Stop-Process -Force"
)
subprocess.run(["powershell", "-Command", kill_cmd], check=False)
time.sleep(1)

proc = subprocess.Popen([exe], env=env)
print("launched pid:", proc.pid)
time.sleep(4)

pages = json.load(urllib.request.urlopen("http://127.0.0.1:9446/json/list", timeout=5))
page = next(p for p in pages if p.get("type") == "page")


def eval_js(ws, expr):
    ws.send(
        json.dumps(
            {
                "id": int(time.time() * 1000) % 100000,
                "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            }
        )
    )
    res = json.loads(ws.recv())
    return res.get("result", {}).get("result", {}).get("value")


def capture(ws, filename):
    ws.send(
        json.dumps({"id": int(time.time() * 1000) % 100000, "method": "Page.captureScreenshot"})
    )
    res = json.loads(ws.recv())
    data = base64.b64decode(res["result"]["data"])
    with open(filename, "wb") as f:
        f.write(data)
    print(f"Captured {filename}")


with connect(page["webSocketDebuggerUrl"]) as ws:
    # 1. Ready Dark
    capture(ws, "real_01_ready_dark.png")

    # 2. Light Mode
    eval_js(ws, 'document.querySelector(".btn-topbar-icon").click()')
    time.sleep(0.5)
    capture(ws, "real_07_light_mode.png")
    eval_js(ws, 'document.querySelector(".btn-topbar-icon").click()')
    time.sleep(0.5)

    # 3. Timeline Tab
    eval_js(ws, 'document.querySelectorAll(".tab-btn")[1].click()')
    time.sleep(0.5)
    capture(ws, "real_03_timeline.png")

    # 4. Programming Intelligence Tab
    eval_js(ws, 'document.querySelectorAll(".tab-btn")[2].click()')
    time.sleep(0.5)
    capture(ws, "real_04_programming_intelligence.png")

    # 5. Export Tab
    eval_js(ws, 'document.querySelectorAll(".tab-btn")[3].click()')
    time.sleep(0.5)
    capture(ws, "real_05_completed_export.png")

    # Return to Overview
    eval_js(ws, 'document.querySelectorAll(".tab-btn")[0].click()')
    time.sleep(0.5)

    # Fill form and Start Al Nakhla FM
    eval_js(ws, 'document.querySelector("input[name=\'station\']").value = "Al Nakhla FM"')
    eval_js(
        ws,
        'document.querySelector("input[name=\'source\']").value = "https://www.al-nakhla.net/ar/radio"',
    )
    eval_js(ws, 'document.querySelector(".btn-start").click()')
    print("Started monitoring Al Nakhla FM...")

    # Wait for monitoring session to become active (resolving -> capturing)
    deadline = time.time() + 30
    active = False
    while time.time() < deadline:
        text = eval_js(ws, "document.body.innerText")
        keywords = ("LIVE MONITORING", "CAPTURING", "RESOLVING", "MONITORING")
        if any(k in text for k in keywords):
            active = True
            break
        time.sleep(1)

    print("Active session established:", active)
    time.sleep(4)
    # 6. Active Monitoring Dark
    capture(ws, "real_02_active_monitoring_dark.png")

    # 7. Quit Dialog
    # Trigger native close request via UI Automation while active
    ps_close = f"""
Add-Type -AssemblyName UIAutomationClient
$scope = [System.Windows.Automation.TreeScope]::Children
$cond = [System.Windows.Automation.Condition]::TrueCondition
$win = [System.Windows.Automation.AutomationElement]::RootElement.FindAll($scope, $cond) |
    Where-Object {{
        $_.Current.ProcessId -eq {proc.pid} -and $_.Current.Name -eq 'NAS Air Intelligence'
    }} | Select-Object -First 1
if ($win) {{
    $win.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
}}
"""
    subprocess.run(["powershell", "-Command", ps_close], check=False)
    time.sleep(1.2)
    capture(ws, "real_06_quit_dialog.png")

print("All 7 real Tauri screenshots captured successfully!")
proc.terminate()
time.sleep(1)
if proc.poll() is None:
    proc.kill()
