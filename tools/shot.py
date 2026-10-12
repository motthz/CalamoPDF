"""Dev helper: screenshot the Calamo window. Usage: shot.py out.png"""
import ctypes
import sys
import time

from PIL import ImageGrab

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
hwnd = 0
for _ in range(40):
    hwnd = user32.FindWindowW(None, "Calamo PDF Studio")
    if not hwnd:
        # title changes when a document is open: enumerate windows
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(h, _):
            n = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if "Calamo PDF Studio" in buf.value and user32.IsWindowVisible(h):
                found.append(h)
            return True
        user32.EnumWindows(cb, 0)
        hwnd = found[0] if found else 0
    if hwnd:
        break
    time.sleep(0.5)
if not hwnd:
    sys.exit("window not found")
user32.ShowWindow(hwnd, 9)
user32.SetForegroundWindow(hwnd)
time.sleep(0.6)
rect = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
import ctypes.wintypes  # noqa: E402
rect = ctypes.wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
img.save(sys.argv[1] if len(sys.argv) > 1 else "shot.png")
print(img.size)
