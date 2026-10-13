"""Automatic updates from the GitHub releases of the project.

At start-up the app asks GitHub for the latest release; when it is newer than
the running version the right file is downloaded in the background:
* installed copy (Inno Setup uninstaller next to the exe) -> the Setup .exe,
  run silently over the current installation;
* portable copy -> the new CalamoPDF.exe, swapped in place of the running one.
The swap happens after the app has closed, through a small hidden script."""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

REPO = "motthz/CalamoPDF"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
UA = "CalamoPDF-updater"


def parse_version(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def frozen():
    return bool(getattr(sys, "frozen", False))


def _exe():
    return os.path.abspath(sys.executable)


def installed():
    """True when running from an Inno Setup installation (not the portable exe)."""
    d = os.path.dirname(_exe())
    return frozen() and any(n.lower().startswith("unins") and n.lower().endswith(".exe") for n in os.listdir(d))


def check(current):
    """Return info about a newer release, or None."""
    req = urllib.request.Request(LATEST, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    if data.get("draft") or data.get("prerelease"):
        return None
    latest = data.get("tag_name") or data.get("name") or ""
    if parse_version(latest) <= parse_version(current):
        return None
    assets = data.get("assets", [])
    want_setup = installed()
    asset = None
    for a in assets:
        name = a.get("name", "").lower()
        if not name.endswith(".exe"):
            continue
        if want_setup and "setup" in name:
            asset = a
        elif not want_setup and "setup" not in name:
            asset = a
        if asset:
            break
    return {
        "version": latest.lstrip("vV"), "notes": (data.get("body") or "").strip()[:1500],
        "page": data.get("html_url") or RELEASES_PAGE,
        "url": asset.get("browser_download_url") if asset else None,
        "name": asset.get("name") if asset else None, "size": asset.get("size", 0) if asset else 0,
        "kind": "setup" if want_setup else "portable",
        "canInstall": bool(asset) and frozen(),
    }


def download(info, progress=None):
    """Download the release file to %TEMP%. Returns its path."""
    folder = os.path.join(tempfile.gettempdir(), "CalamoPDF-update")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, info["name"])
    part = target + ".part"
    req = urllib.request.Request(info["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    if info.get("size") and os.path.getsize(part) != info["size"]:
        os.remove(part)
        raise RuntimeError("Download dell'aggiornamento incompleto.")
    with open(part, "rb") as f:
        if f.read(2) != b"MZ":
            os.remove(part)
            raise RuntimeError("Il file scaricato non è un eseguibile valido.")
    os.replace(part, target)
    return target


def build_script(info, path, relaunch, exe, pids):
    """The hidden .cmd that installs the update once the given processes have exited."""
    ids = ",".join(str(p) for p in pids)
    lines = ["@echo off", "chcp 65001 >nul", "setlocal",
             f'powershell -NoProfile -NonInteractive -Command "Wait-Process -Id {ids} -Timeout 120 -ErrorAction SilentlyContinue"']
    if info["kind"] == "setup":
        appdir = os.path.dirname(exe)
        args = f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /DIR="{appdir}"'
        pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
        if any(p and appdir.lower().startswith(p.lower()) for p in pf):
            args += " /ALLUSERS"
        if relaunch:
            args += " /relaunch=1"
        lines.append(f'"{path}" {args}')
        lines.append(f'del "{path}" >nul 2>&1')
    else:
        lines += ["set n=0", ":swap",
                  f'move /y "{path}" "{exe}" >nul 2>&1 || (set /a n+=1 & ping -n 2 127.0.0.1 >nul & if %n% lss 30 goto swap)']
        if relaunch:
            lines.append(f'start "" "{exe}"')
    lines.append('(goto) 2>nul & del "%~f0"')
    return "\r\n".join(lines) + "\r\n"


def apply(info, path, relaunch):
    """Start the hidden script that installs the update once this process has exited."""
    exe = _exe()
    # the one-file bootloader (parent process) keeps the exe locked until it exits
    pids = [os.getpid()] + ([os.getppid()] if frozen() else [])
    script = os.path.join(os.path.dirname(path), "aggiorna-calamo.cmd")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(build_script(info, path, relaunch, exe, pids))
    flags = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True)
