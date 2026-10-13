# Builds CalamoPDF.exe (copied to the project root, not tracked) and the Windows installer in dist\
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
}
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

.\.venv\Scripts\python.exe tools\make_icon.py
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean CalamoPDF.spec
Copy-Item dist\CalamoPDF.exe .\CalamoPDF.exe -Force

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { & $iscc installer.iss } else { Write-Warning "Inno Setup non trovato: installer non creato." }
Get-ChildItem dist
