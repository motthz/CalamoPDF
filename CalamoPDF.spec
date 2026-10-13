# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Calamo PDF Studio (one-file, windowed)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

hidden = (collect_submodules("winrt") + collect_submodules("webview.platforms")
          + ["clr", "openpyxl", "docx", "onnxruntime", "tokenizers"])
# the Italian word list; the handwriting model itself is downloaded on first use
datas = ([("app/ui", "ui"), ("assets/icon.ico", "assets"), ("app/engine/data", "engine/data")]
         + collect_data_files("webview") + collect_data_files("docx") + collect_data_files("onnxruntime"))
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["app/main.py"],
    pathex=["app"],
    binaries=binaries,
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
