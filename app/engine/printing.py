"""Printing straight to a Windows printer, without any dialog ("stampa immediata").

Every page is drawn by Calamo at the printer's resolution (at most 300 dpi: more does not
show on paper and only fills the memory) and sent through the Windows print spooler with
GDI. The page keeps its proportions, turned when its orientation differs from the paper's,
centred on the sheet. The classic print with the settings window is done by the interface."""
import sys

import numpy as np
import pymupdf as fitz

from .sync import PDF_LOCK

MAX_DPI = 300


def _api():
    if sys.platform != "win32":
        raise RuntimeError("La stampa immediata funziona solo su Windows.")
    import ctypes
    from ctypes import wintypes
    gdi = ctypes.WinDLL("gdi32")
    spool = ctypes.WinDLL("winspool.drv")
    gdi.CreateDCW.restype = wintypes.HDC
    gdi.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    gdi.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    for name in ("StartPage", "EndPage", "EndDoc", "DeleteDC", "AbortDoc"):
        getattr(gdi, name).argtypes = [wintypes.HDC]
    gdi.StartDocW.argtypes = [wintypes.HDC, ctypes.c_void_p]
    gdi.StretchDIBits.argtypes = [wintypes.HDC] + [ctypes.c_int] * 8 + [ctypes.c_void_p, ctypes.c_void_p,
                                                                        wintypes.UINT, wintypes.DWORD]
    spool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, gdi, spool


def default_printer():
    """The name of the Windows default printer, or None."""
    try:
        ctypes, wintypes, gdi, spool = _api()
    except RuntimeError:
        return None
    n = wintypes.DWORD(0)
    spool.GetDefaultPrinterW(None, ctypes.byref(n))
    if not n.value:
        return None
    buf = ctypes.create_unicode_buffer(n.value)
    return buf.value if spool.GetDefaultPrinterW(buf, ctypes.byref(n)) else None


def _bitmap_info(ctypes, w, h):
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]
    bi = BITMAPINFOHEADER()
    bi.biSize, bi.biWidth, bi.biHeight = ctypes.sizeof(BITMAPINFOHEADER), w, -h      # top-down rows
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 24, 0
    return bi


def print_pages(doc, pages, title="Documento", printer=None, output=None, progress=None):
    """Print pages (0-based indices) of doc. printer: a printer name (default: the Windows
    default printer); output: a file the printer driver writes to instead of the paper
    (used by the tests with "Microsoft Print to PDF"). Returns the printer used."""
    ctypes, wintypes, gdi, spool = _api()
    printer = printer or default_printer()
    if not printer:
        raise RuntimeError("Non c'è una stampante predefinita: scegline una con «Stampa…».")
    hdc = gdi.CreateDCW("WINSPOOL", printer, None, None)
    if not hdc:
        raise RuntimeError(f"Non riesco a usare la stampante «{printer}».")

    class DOCINFOW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_int), ("lpszDocName", wintypes.LPCWSTR), ("lpszOutput", wintypes.LPCWSTR),
                    ("lpszDatatype", wintypes.LPCWSTR), ("fwType", wintypes.DWORD)]
    di = DOCINFOW(ctypes.sizeof(DOCINFOW), title[:200], output, None, 0)
    started = False
    try:
        # the printable area (pixels) and where it sits on the sheet
        HORZRES, VERTRES, LOGX, LOGY = 8, 10, 88, 90
        pw, ph = gdi.GetDeviceCaps(hdc, HORZRES), gdi.GetDeviceCaps(hdc, VERTRES)
        dx, dy = gdi.GetDeviceCaps(hdc, LOGX) or 300, gdi.GetDeviceCaps(hdc, LOGY) or 300
        if pw <= 0 or ph <= 0:
            raise RuntimeError(f"La stampante «{printer}» non risponde.")
        if gdi.StartDocW(hdc, ctypes.byref(di)) <= 0:
            raise RuntimeError(f"La stampante «{printer}» non accetta la stampa.")
        started = True
        n = len(pages)
        for k, i in enumerate(pages):
            if progress:
                progress(k, n)
            with PDF_LOCK:
                page = doc[int(i)]
                r = page.rect
                turn = (r.width > r.height) != (pw > ph)          # landscape page on portrait paper
                pw_pt, ph_pt = (r.height, r.width) if turn else (r.width, r.height)
                # as big as fits the printable area, at most MAX_DPI
                fit = min(pw / (pw_pt / 72 * dx), ph / (ph_pt / 72 * dy))
                dpi = min(MAX_DPI, dx * min(1.0, fit), dy * min(1.0, fit))
                m = fitz.Matrix(dpi / 72, dpi / 72).prerotate(90 if turn else 0)
                pix = page.get_pixmap(matrix=m, alpha=False, annots=True)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
            w, h = img.shape[1], img.shape[0]
            stride = (w * 3 + 3) & ~3
            buf = np.zeros((h, stride), np.uint8)
            buf[:, :w * 3] = img[:, :, ::-1].reshape(h, w * 3)          # RGB -> BGR, rows padded
            bi = _bitmap_info(ctypes, w, h)
            # size on paper: the page's own size, shrunk to the printable area if needed
            out_w = int(min(pw, pw_pt / 72 * dx * min(1.0, fit)))
            out_h = int(min(ph, ph_pt / 72 * dy * min(1.0, fit)))
            x, y = (pw - out_w) // 2, (ph - out_h) // 2
            if gdi.StartPage(hdc) <= 0:
                raise RuntimeError("La stampa si è interrotta.")
            gdi.StretchDIBits(hdc, x, y, out_w, out_h, 0, 0, w, h, buf.ctypes.data, ctypes.byref(bi), 0, 0x00CC0020)
            gdi.EndPage(hdc)
        gdi.EndDoc(hdc)
        started = False
        if progress:
            progress(n, n)
        return printer
    finally:
        if started:
            gdi.AbortDoc(hdc)
        gdi.DeleteDC(hdc)
