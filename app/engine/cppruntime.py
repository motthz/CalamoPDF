"""The Microsoft C++ runtime the app's libraries share (see load_newest)."""
import ctypes
import os
import sys


def load_newest():
    """Load the newest Microsoft C++ runtime there is before anything else does. The winrt
    package (Windows OCR) carries an old msvcp140.dll (14.29); once a process has loaded it,
    loading onnxruntime (the handwriting reader, which needs 14.40 or later) crashes the whole
    app. Windows reuses a library already loaded under the same name, so loading the newest
    copy first gives it to both."""
    if sys.platform != "win32":
        return

    def version(path):
        try:
            size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
            buf = ctypes.create_string_buffer(size)
            ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf)
            p, n = ctypes.c_void_p(), ctypes.c_uint()
            ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
            ms, ls = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32))[2:4]
            return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        except Exception:
            return (0,)
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    found = [os.path.join(d, "msvcp140.dll") for d in
             (here, os.path.dirname(sys.executable), os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32"))]
    found = [f for f in found if os.path.exists(f)]
    if found:
        try:
            ctypes.WinDLL(max(found, key=version))
        except OSError:
            pass
