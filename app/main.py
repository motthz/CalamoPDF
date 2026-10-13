"""Calamo PDF Studio - desktop entry point."""
import json
import os
import sys
import threading
import traceback

import ctypes
from ctypes import wintypes

import webview

if getattr(sys, "frozen", False):
    BASE = sys._MEIPASS
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from engine import export  # noqa: E402
from engine import fonts as F  # noqa: E402
from engine import handwriting as HW  # noqa: E402
from engine.handreader import READER  # noqa: E402
from engine.document import Session  # noqa: E402
from engine.ocr import available_languages  # noqa: E402
from engine.storage import load_json, save_json  # noqa: E402
import updater  # noqa: E402

VERSION = "1.3.0"

_user32 = ctypes.windll.user32
_user32.FindWindowW.restype = wintypes.HWND
_user32.MonitorFromWindow.restype = wintypes.HANDLE
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_uint]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]
SETTINGS_DEFAULT = {"theme": "dark", "ocrLang": "", "recent": [], "tutorialDone": False, "autoUpdate": True,
                    "updateTried": "", "handModel": True}
OBSOLETE_SETTINGS = ("aiKey", "useAi")


def _dialog_kind(name):
    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, name)
    return {"OPEN": webview.OPEN_DIALOG, "SAVE": webview.SAVE_DIALOG, "FOLDER": webview.FOLDER_DIALOG}[name]


def safe(fn):
    def wrapper(self, *args):
        try:
            return {"ok": True, "data": fn(self, *args)}
        except Exception as e:  # surface every engine error as a toast in the UI
            traceback.print_exc()
            return {"ok": False, "error": str(e) or e.__class__.__name__}
    wrapper.__name__ = fn.__name__
    return wrapper


class Api:
    def __init__(self):
        self._s = Session()
        self._window = None
        self._settings = {**SETTINGS_DEFAULT, **load_json("settings.json", {})}
        if any(k in self._settings for k in OBSOLETE_SETTINGS):
            for k in OBSOLETE_SETTINGS:  # the Claude connection was removed: delete any stored key
                self._settings.pop(k, None)
            save_json("settings.json", self._settings)
        self._update = None              # newer release found on GitHub
        self._update_file = None         # downloaded and ready to install
        self._update_relaunch = False
        self._downloading = False
        self._closing_ok = False
        self._max = False
        self._restore_rect = None
        self._cancel_gen = 0             # bumped to stop the page readings in progress
        self._model = {"downloading": False, "progress": 0.0, "error": None}
        self.startup_path = next((a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.exists(a)), None)

    # ---------------------------------------------------------------- helpers
    def _js(self, code):
        if self._window:
            self._window.evaluate_js(code)

    def _progress(self, label):
        def cb(i, n):
            self._js(f"app.progress({json.dumps(label)}, {i}, {n})")
        return cb

    def _remember(self, path):
        rec = [p for p in self._settings.get("recent", []) if p != path and os.path.exists(p)]
        self._settings["recent"] = [path] + rec[:7]
        save_json("settings.json", self._settings)

    # ---------------------------------------------------------------- app / window
    @safe
    def boot(self):
        recent = [{"path": p, "name": os.path.basename(p), "folder": os.path.dirname(p)}
                  for p in self._settings.get("recent", []) if os.path.exists(p)]
        return {"version": VERSION, "settings": {k: v for k, v in self._settings.items() if k != "recent"},
                "canUpdate": updater.frozen(),
                "recent": recent, "fonts": F.UI_FAMILIES, "languages": available_languages(),
                "startup": self.startup_path, "learning": self._s.learning.stats()}

    # ---------------------------------------------------------------- updates
    @safe
    def check_update(self):
        """Ask GitHub for a newer release; download it in the background if allowed."""
        if self._update and (self._update_file or self._downloading):
            # already found: don't download it twice
            return {**self._update, "ready": bool(self._update_file), "downloading": self._downloading}
        info = updater.check(VERSION)
        self._update = info
        if not info:
            return None
        # an update to this version was already attempted and we are still old: don't loop
        info["retry"] = self._settings.get("updateTried") == info["version"]
        if info["canInstall"] and self._settings.get("autoUpdate", True) and not info["retry"]:
            self._start_download()
            info["downloading"] = True
        return info

    def _start_download(self):
        if self._downloading or self._update_file:
            return
        self._downloading = True
        threading.Thread(target=self._download_update, daemon=True).start()

    @safe
    def download_update(self):
        if not self._update or not self._update.get("canInstall"):
            raise RuntimeError("Nessun aggiornamento installabile.")
        self._start_download()
        return True

    def _download_update(self):
        info = self._update
        shown = [-1]

        def progress(f):
            pct = int(f * 100)
            if pct != shown[0]:
                shown[0] = pct
                self._js(f"app.updateProgress({f:.2f})")
        try:
            path = updater.download(info, progress)
            self._update_file = path
            self._js(f"app.updateReady({json.dumps(info)})")
        except Exception as e:
            traceback.print_exc()
            self._js(f"app.updateFailed({json.dumps(str(e))})")
        finally:
            self._downloading = False

    @safe
    def restart_to_update(self):
        if not self._update_file:
            raise RuntimeError("L'aggiornamento non è ancora pronto.")
        self._update_relaunch = True
        self._closing_ok = True
        self._window.destroy()
        return True

    @safe
    def open_release_page(self):
        import webbrowser
        webbrowser.open((self._update or {}).get("page") or updater.RELEASES_PAGE)
        return True

    def install_pending_update(self):
        """Called after the window has closed."""
        if not self._update_file:
            return
        self._settings["updateTried"] = self._update["version"]
        save_json("settings.json", self._settings)
        updater.apply(self._update, self._update_file, self._update_relaunch)

    @safe
    def set_settings(self, values):
        self._settings.update(values)
        save_json("settings.json", self._settings)
        return True

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._max:
            self._win_restore()
        else:
            self._win_maximize()

    # A frameless WinForms window maximises over the taskbar, so "maximise" is done
    # by hand: fill the monitor's work area and remember the previous rectangle.
    def _hwnd(self):
        return _user32.FindWindowW(None, "Calamo PDF Studio")

    def _win_maximize(self):
        hwnd = self._hwnd()
        if not hwnd:
            return self._window.maximize()
        r = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        self._restore_rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        _user32.GetMonitorInfoW(_user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
        w = mi.rcWork
        _user32.SetWindowPos(hwnd, None, w.left, w.top, w.right - w.left, w.bottom - w.top, 0x0004 | 0x0010)
        self._max = True

    def _win_restore(self):
        hwnd = self._hwnd()
        if hwnd and self._restore_rect:
            _user32.SetWindowPos(hwnd, None, *self._restore_rect, 0x0004 | 0x0010)
        else:
            self._window.restore()
        self._max = False

    def close(self, force=False):
        self._closing_ok = True
        self._window.destroy()

    def _on_closing(self):
        if self._closing_ok or not (self._s.doc and self._s.dirty):
            return True
        self._js("app.requestClose()")
        return False

    # ---------------------------------------------------------------- files
    @safe
    def open_dialog(self):
        res = self._window.create_file_dialog(_dialog_kind("OPEN"), file_types=("Documenti PDF (*.pdf)",))
        if not res:
            return None
        return self._open(res[0])

    @safe
    def open_path(self, path):
        return self._open(path)

    def _open(self, path):
        info = self._s.open(path)
        self._remember(path)
        return info

    @safe
    def new_doc(self):
        return self._s.new_blank()

    @safe
    def info(self):
        return self._s.info()

    @safe
    def save(self):
        if not self._s.path:
            return self._save_as()
        return self._s.save()

    def _save_as(self):
        name = os.path.basename(self._s.path) if self._s.path else "Documento.pdf"
        res = self._window.create_file_dialog(_dialog_kind("SAVE"), save_filename=name,
                                             file_types=("Documenti PDF (*.pdf)",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        info = self._s.save(path)
        self._remember(path)
        return info

    @safe
    def save_as(self):
        return self._save_as()

    def _ask_export_path(self, ext, label):
        base = os.path.splitext(os.path.basename(self._s.path or "Documento.pdf"))[0]
        res = self._window.create_file_dialog(_dialog_kind("SAVE"), save_filename=f"{base}.{ext}",
                                             file_types=(f"{label} (*.{ext})",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        if not path.lower().endswith("." + ext):
            path += "." + ext
        return path

    @safe
    def export_docx(self):
        path = self._ask_export_path("docx", "Documento Word")
        if not path:
            return None
        with self._s.lock:
            export.to_docx(self._s.doc, self._s.ink, path, self._progress("Esportazione in Word"))
        return {"path": path, "name": os.path.basename(path)}

    @safe
    def export_xlsx(self):
        path = self._ask_export_path("xlsx", "Cartella di lavoro Excel")
        if not path:
            return None
        with self._s.lock:
            sheets = export.to_xlsx(self._s.doc, self._s.ink, path, self._progress("Esportazione in Excel"))
        return {"path": path, "name": os.path.basename(path), "sheets": sheets}

    @safe
    def reveal(self, path):
        os.startfile(path)
        return True

    # ---------------------------------------------------------------- pages
    @safe
    def render(self, index, scale, fmt="png"):
        return self._s.render(index, scale, fmt)

    @safe
    def blocks(self, index):
        return self._s.blocks(index)

    @safe
    def rotate_page(self, index):
        return self._s.rotate_page(index)

    @safe
    def delete_page(self, index):
        return self._s.delete_page(index)

    @safe
    def insert_blank(self, index):
        return self._s.insert_blank(index)

    @safe
    def undo(self):
        return self._s.undo()

    @safe
    def redo(self):
        return self._s.redo()

    # ---------------------------------------------------------------- text editing
    @safe
    def edit_block(self, index, bbox, text, style=None):
        return self._s.edit_block(index, bbox, text, style)

    @safe
    def delete_block(self, index, bbox):
        return self._s.delete_block(index, bbox)

    @safe
    def add_text(self, index, x, y, text, style):
        return self._s.add_text(index, x, y, text, style)

    @safe
    def erase_area(self, index, rect):
        return self._s.erase_area(index, rect)

    @safe
    def search(self, term):
        return self._s.search(term)

    @safe
    def replace_all(self, term, repl):
        return self._s.replace_all(term, repl)

    # ---------------------------------------------------------------- handwriting
    def _reader(self):
        if not self._settings.get("handModel", True) or not READER.installed() or not READER.runtime_ok():
            return None
        return READER

    @safe
    def analyze(self, index):
        gen = self._cancel_gen
        return self._s.analyze(index, self._settings.get("ocrLang") or None, reader=self._reader(),
                               cancelled=lambda: gen != self._cancel_gen)

    @safe
    def cancel_analysis(self):
        self._cancel_gen += 1
        return True

    # ---------------------------------------------------------------- handwriting model
    @safe
    def model_status(self):
        st = READER.status()
        return {**st, **self._model, "error": self._model["error"] or st["error"],
                "enabled": self._settings.get("handModel", True)}

    @safe
    def model_download(self):
        if self._model["downloading"]:
            return True

        def run():
            self._model.update(downloading=True, progress=0.0, error=None)
            try:
                if READER.error:
                    READER.remove()
                READER.download(lambda f: self._model.update(progress=round(f, 3)))
                READER.error = None
            except Exception as e:
                traceback.print_exc()
                self._model["error"] = str(e)
            finally:
                self._model["downloading"] = False
        threading.Thread(target=run, daemon=True).start()
        return True

    @safe
    def set_ink_kind(self, index, line_id, kind):
        return self._s.set_ink_kind(index, line_id, kind)

    @safe
    def ink_letters(self, index, line_id):
        return self._s.ink_letters(index, line_id)

    @safe
    def teach_letters(self, index, line_id, segments, update_text=True):
        return self._s.teach_letters(index, line_id, segments, update_text)

    @safe
    def set_line_style(self, index, line_id, sid):
        return self._s.set_line_style(index, line_id, sid)

    @safe
    def rename_style(self, index, sid, name):
        return self._s.rename_style(index, sid, name)

    @safe
    def glyph_variants(self, index, sid, char):
        return self._s._lib(index, sid).variants(char)

    @safe
    def delete_glyph(self, index, sid, char, kind, i):
        lib = self._s._lib(index, sid)
        lib.delete_variant(char, kind, i)
        lib._font = None
        return {**self._s.hand_info(index, lib.sid), "variants": lib.variants(char)}

    @safe
    def forget_letters(self, index, sid):
        lib = self._s._lib(index, sid)
        lib.forget_manual()
        return self._s.hand_info(index, lib.sid)

    # calligraphies saved by name, used only when the user picks one
    @safe
    def styles(self):
        return HW.style_list()

    @safe
    def save_style(self, index, sid, name):
        return self._s.save_style(index, sid, name)

    @safe
    def load_style(self, index, sid, name):
        return self._s.load_style(index, sid, name)

    @safe
    def delete_style(self, name):
        HW.delete_style(name)
        return HW.style_list()

    @safe
    def correct_ink(self, index, line_id, text):
        return self._s.correct_ink(index, line_id, text)

    @safe
    def ink_crop(self, index, line_id):
        return self._s.ink_crop(index, line_id)

    @safe
    def preview_hand(self, index, text, height=None, color=None, sid=None, line_id=None):
        return self._s.preview_hand(index, text, height, color, sid, line_id)

    @safe
    def edit_ink(self, index, line_id, text, color=None):
        return self._s.edit_ink(index, line_id, text, color)

    @safe
    def add_hand(self, index, x, y, text, height=None, color=None, sid=None):
        return self._s.add_hand(index, x, y, text, height, color, sid)

    @safe
    def hand_summary(self, index=0, sid=None):
        if not self._s.doc:
            return {"styles": [], "selected": None, "hand": HW.HandwritingLibrary().summary(), "height": 14.0,
                    "learning": self._s.learning.stats()}
        return self._s.hand_info(index, sid)

    @safe
    def reset_learning(self):
        self._s.learning.reset()
        return self._s.learning.stats()


def on_drop(api):
    def handler(e):
        try:
            files = e.get("dataTransfer", {}).get("files", [])
            for f in files:
                p = f.get("pywebviewFullPath")
                if p and p.lower().endswith(".pdf"):
                    api._js(f"app.openPath({json.dumps(p)})")
                    break
        except Exception:
            traceback.print_exc()
    return handler


def selftest(pdf, out):
    """`CalamoPDF.exe --selftest file.pdf result.json`: exercises the engine headless."""
    import tempfile
    s = Session()
    report = {"open": s.open(pdf)["name"]}
    blocks = s.blocks(0)["blocks"]
    report["blocks"] = len(blocks)
    if blocks:
        s.edit_block(0, blocks[0]["bbox"], blocks[0]["text"] + " ✓")
    from engine.lexicon import LEXICON
    report["lexicon"] = LEXICON.correct("Ottinoo")
    report["onnxruntime"] = READER.runtime_ok()
    if report["lexicon"] != "Ottimo" or not report["onnxruntime"]:
        raise RuntimeError(f"dizionario o onnxruntime mancanti: {report}")
    reader = READER if READER.installed() else None
    report["model"] = bool(reader)
    res = s.analyze(0, reader=reader)
    report["ink"] = [l["text"] for l in res["ink"]]
    report["sources"] = [l["source"] for l in res["ink"]]
    report["glyphs"] = len(res["hand"]["glyphs"])
    if res["ink"]:
        s.edit_ink(0, res["ink"][-1]["id"], "Calamo")
    tmp = tempfile.mkdtemp()
    export.to_docx(s.doc, s.ink, os.path.join(tmp, "t.docx"))
    report["sheets"] = export.to_xlsx(s.doc, s.ink, os.path.join(tmp, "t.xlsx"))
    s.save(os.path.join(tmp, "t.pdf"))
    report["ok"] = True
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        try:
            selftest(sys.argv[i + 1], sys.argv[i + 2])
        except Exception:
            with open(sys.argv[i + 2], "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        return
    api = Api()
    ui = os.path.join(BASE, "ui", "index.html")
    width, height, maximized = 1480, 920, False
    try:
        scr = webview.screens[0]
        if scr.width < width + 40 or scr.height < height + 60:
            width, height, maximized = int(scr.width * 0.9), int(scr.height * 0.9), True
    except Exception:
        pass
    window = webview.create_window(
        "Calamo PDF Studio", ui, js_api=api, width=width, height=height, min_size=(1000, 640),
        frameless=True, easy_drag=False, background_color="#0D0E12", text_select=True,
    )
    if maximized:
        window.events.shown += lambda: api._win_maximize()
    api._window = window
    window.events.closing += api._on_closing

    def loaded():
        try:
            from webview.dom import DOMEventHandler
            window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
            window.dom.document.events.drop += DOMEventHandler(on_drop(api), True, True)
        except Exception:
            traceback.print_exc()
    window.events.loaded += loaded
    webview.start(gui="edgechromium", debug=bool(os.environ.get("CALAMO_DEBUG")),
                  storage_path=os.path.join(os.environ.get("APPDATA", "."), "CalamoPDF", "webview"))
    try:
        api.install_pending_update()
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
