"""Dev helper: headless Edge screenshot after the page has settled.
Usage: capture.py URL out.png [wait_seconds]   (needs: pip install websocket-client)"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

url, out = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 12
edge = os.path.join(os.environ["ProgramFiles(x86)"], "Microsoft", "Edge", "Application", "msedge.exe")
port = 9333
proc = subprocess.Popen([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--remote-debugging-port={port}",
                         "--window-size=1440,880", "--force-device-scale-factor=1.5",
                         f"--user-data-dir={tempfile.mkdtemp()}", url])
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
            page = next(t for t in tabs if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.3)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True)
    time.sleep(wait)
    ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            break
    with open(out, "wb") as f:
        f.write(base64.b64decode(msg["result"]["data"]))
    print("saved", out)
finally:
    proc.kill()
