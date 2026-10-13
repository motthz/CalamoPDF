# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Calamo PDF Studio (one-file, windowed)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = (collect_submodules("winrt") + collect_submodules("webview.platforms")
          + ["clr", "openpyxl", "docx"])
datas = [("app/ui", "ui"), ("assets/icon.ico", "assets")] + collect_data_files("webview") + collect_data_files("docx")

a = Analysis(
    ["app/main.py"],
    pathex=["app"],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PyQt5", "PyQt6", "PySide6",
              "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa", "webview.platforms.android"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="CalamoPDF",
    icon="assets/icon.ico",
    version="version_info.txt",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
