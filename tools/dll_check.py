"""Which package loaded before onnxruntime makes its import crash? Each case runs in a
process of its own (a crash there is only an exit code). Also lists the copies of the
Microsoft C++ runtime (msvcp140.dll) the packages bring with them, with their version."""
import ctypes
import glob
import os
import site
import subprocess
import sys

CASES = ["", "numpy", "webview", "clr_loader", "pythonnet", "winrt.windows.media.ocr", "pymupdf", "lxml.etree",
         "cryptography.hazmat.bindings._rust", "tokenizers", "PIL.Image", "pyhanko.sign", "docx", "openpyxl", "pptx",
         "aiohttp"]


def version(path):
    size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return "?"
    buf = ctypes.create_string_buffer(size)
    ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf)
    p, n = ctypes.c_void_p(), ctypes.c_uint()
    ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
    ms, ls = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32))[2:4]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def main():
    roots = site.getsitepackages() + [os.path.dirname(sys.executable), os.environ.get("WINDIR", "") + "\\System32"]
    for r in roots:
        for f in glob.glob(os.path.join(r, "**", "msvcp140*.dll"), recursive=r.endswith("site-packages")):
            print(f"{version(f):18s} {f}", flush=True)
    for f in glob.glob(os.path.join(os.environ.get("WINDIR", ""), "System32", "onnxruntime*.dll")):
        print(f"{version(f):18s} {f}  (onnxruntime di Windows)", flush=True)
    for mod in CASES:
        code = (f"import {mod}; " if mod else "") + "import onnxruntime; print('ok', onnxruntime.__version__)"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
        tail = (r.stderr or "").strip().splitlines()[-1:] if r.returncode else []
        print(f"{'prima ' + mod if mod else 'da solo':42s} -> {'ok' if r.returncode == 0 else hex(r.returncode & 0xFFFFFFFF)} {tail}",
              flush=True)
    # the order of the app: everything main.py imports, then onnxruntime
    app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
    code = (f"import sys; sys.path.insert(0, {app!r}); import webview; import engine.document, engine.ocr, engine.export, "
            "engine.signing, engine.convert; import onnxruntime as o; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    print(f"{'come l app':42s} -> {'ok' if r.returncode == 0 else hex(r.returncode & 0xFFFFFFFF)}", flush=True)


if __name__ == "__main__":
    main()
