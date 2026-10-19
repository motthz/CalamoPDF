"""Calamo PDF Studio - desktop entry point."""
import json
import os
import re
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
from engine import files as FILES  # noqa: E402
from engine import convert as CONVERT  # noqa: E402
from engine import extras as EX  # noqa: E402
from engine import batch as BATCH  # noqa: E402
from engine import signing as SIGN  # noqa: E402
from engine import fonts as F  # noqa: E402
from engine import handwriting as HW  # noqa: E402
from engine.handreader import READER  # noqa: E402
from engine.document import Session  # noqa: E402
from engine.ocr import available_languages  # noqa: E402
from engine.storage import load_json, save_json  # noqa: E402
import updater  # noqa: E402

VERSION = "1.5.1"

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
        self._tabs = [Session()]         # the open documents, one per tab
        self._ti = 0
        self._window = None
        self._settings = {**SETTINGS_DEFAULT, **load_json("settings.json", {})}
        if any(k in self._settings for k in OBSOLETE_SETTINGS):
            for k in OBSOLETE_SETTINGS:  # the Claude connection was removed: delete any stored key
                self._settings.pop(k, None)
            save_json("settings.json", self._settings)
        self._update = None              # newer release found on GitHub
        import engine.handwriting as HWmod
        HWmod.REWRITE_READER = self._reader      # rewritten lines: the most legible composition
        self._update_file = None         # downloaded and ready to install
        self._update_relaunch = False
        self._downloading = False
        self._closing_ok = False
        self._max = False
        self._restore_rect = None
        self._cancel_gen = 0             # bumped to stop the page readings in progress
        self._model = {"downloading": False, "progress": 0.0, "error": None}
        # the dictionary takes a moment to load: do it now, not on the first page read
        from engine.lexicon import LEXICON
        threading.Thread(target=LEXICON._load, daemon=True).start()
        self.startup_path = next((a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.exists(a)), None)

    # ---------------------------------------------------------------- tabs
    @property
    def _s(self):
        return self._tabs[self._ti]

    def _new_session(self):
        s = Session()
        s.learning = self._tabs[0].learning          # one memory of corrections for all tabs
        return s

    def _adopt(self, session):
        """Show a newly opened document: in this tab if it is empty, else in a new one."""
        if self._s.doc is None:
            self._tabs[self._ti] = session
        else:
            self._tabs.append(session)
            self._ti = len(self._tabs) - 1
        self._cancel_gen += 1                         # readings of the tab left behind stop
        return session.info()

    def _tabs_public(self):
        return {"active": self._ti, "tabs": [{"i": k, "name": t.info()["name"] if t.doc else "Nuova scheda",
                                              "dirty": bool(t.doc and t.dirty), "pages": len(t.doc) if t.doc else 0,
                                              "path": t.path} for k, t in enumerate(self._tabs)]}

    @safe
    def tabs(self):
        return self._tabs_public()

    @safe
    def switch_tab(self, i):
        if not (0 <= int(i) < len(self._tabs)):
            raise RuntimeError("Scheda non trovata.")
        self._ti = int(i)
        self._cancel_gen += 1
        return self._s.info() if self._s.doc else None

    @safe
    def close_tab(self, i):
        i = int(i)
        if not (0 <= i < len(self._tabs)):
            raise RuntimeError("Scheda non trovata.")
        learning = self._tabs[0].learning
        self._tabs.pop(i)
        if not self._tabs:
            self._tabs = [Session()]
            self._tabs[0].learning = learning
            self._ti = 0
        elif self._ti > i:
            self._ti -= 1
        elif self._ti == i:
            self._ti = min(i, len(self._tabs) - 1)
        self._cancel_gen += 1
        return {"info": self._s.info() if self._s.doc else None, **self._tabs_public()}

    @safe
    def dirty_tabs(self):
        return [k for k, t in enumerate(self._tabs) if t.doc and t.dirty]

    @safe
    def tab_info(self, i):
        t = self._tabs[int(i)]
        return t.info() if t.doc else None

    @safe
    def render_tab(self, i, index, scale):
        return self._tabs[int(i)].render(index, scale, "jpeg")

    @safe
    def copy_pages(self, src_tab, pages, dst_tab, at):
        """Drag pages from one open document to another (or within the same one)."""
        src, dst = self._tabs[int(src_tab)], self._tabs[int(dst_tab)]
        res = dst.insert_from(src, pages, at)
        return {**res, "tab": int(dst_tab)}

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
        if self._closing_ok or not any(t.doc and t.dirty for t in self._tabs):
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

    def _open(self, path, password=None):
        # already open: go to its tab
        for k, t in enumerate(self._tabs):
            if t.path and t.doc and os.path.normcase(os.path.abspath(t.path)) == os.path.normcase(os.path.abspath(path)):
                self._ti = k
                return t.info()
        s = self._new_session()
        try:
            s.open(path, password)
        except RuntimeError as e:
            if str(e) in ("PASSWORD_REQUIRED", "PASSWORD_WRONG"):
                return {"needPassword": True, "path": path, "wrong": str(e) == "PASSWORD_WRONG"}
            raise
        self._remember(path)
        return self._adopt(s)

    @safe
    def open_with_password(self, path, password):
        return self._open(path, password)

    @safe
    def new_doc(self):
        s = self._new_session()
        s.new_blank()
        return self._adopt(s)

    @safe
    def info(self):
        return self._s.info()

    @safe
    def save(self):
        if not self._s.path:
            return self._save_as()
        return self._s.save()

    def _save_as(self):
        name = os.path.basename(self._s.path) if self._s.path else (self._s.title or "Documento.pdf")
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

    # ---------------------------------------------------------------- file pickers
    def _pick(self, label, exts, multiple=False):
        pattern = ";".join(f"*.{e}" for e in exts)
        res = self._window.create_file_dialog(_dialog_kind("OPEN"), allow_multiple=multiple,
                                             file_types=(f"{label} ({pattern})",))
        if not res:
            return None
        return list(res) if multiple else res[0]

    def _pick_save(self, suggested, label="Documenti PDF", ext="pdf"):
        res = self._window.create_file_dialog(_dialog_kind("SAVE"), save_filename=suggested,
                                             file_types=(f"{label} (*.{ext})",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        return path if path.lower().endswith("." + ext) else path + "." + ext

    def _base(self):
        return os.path.splitext(os.path.basename(self._s.path or "Documento.pdf"))[0]

    # ---------------------------------------------------------------- pages
    @safe
    def move_page(self, src, dst):
        return self._s.move_page(src, dst)

    @safe
    def merge_pdf(self, after=None):
        paths = self._pick("Documenti PDF", ["pdf"], multiple=True)
        if not paths:
            return None
        info, added, at = None, 0, None
        for p in paths:
            info = self._s.merge_pdf(p, after)
            at = info["at"] if at is None else at
            if after is not None:
                after += info["added"]
            added += info["added"]
        return {**info, "added": added, "at": at, "files": len(paths)}

    @safe
    def extract_pages(self, spec):
        from engine.editing import parse_pages
        parse_pages(spec, len(self._s.doc))          # a wrong range is said before asking where to save
        path = self._pick_save(f"{self._base()} - estratto.pdf")
        return self._s.extract_pages(spec, path) if path else None

    # ---------------------------------------------------------------- whole files: merge / split
    def _current_src(self):
        if not self._s.doc:
            raise RuntimeError("Nessun documento aperto.")
        with self._s.lock:
            return (os.path.basename(self._s.path or "Documento.pdf"), self._s.doc.tobytes())

    @safe
    def pick_pdfs(self, multiple=True):
        paths = self._pick("Documenti PDF", ["pdf"], multiple=multiple)
        if not paths:
            return []
        return [FILES.describe(p) for p in (paths if multiple else [paths])]

    @safe
    def merge_files(self, items, open_after=False):
        """items: [{path or "@current", pages}] in order -> a new PDF chosen by the user."""
        srcs = []
        for it in items:
            if it.get("path") == "@current":
                name, data = self._current_src()
                srcs.append({"src": (name, data), "name": name, "pages": it.get("pages", "")})
            else:
                srcs.append({"src": it["path"], "name": os.path.basename(it["path"]), "pages": it.get("pages", "")})
        if not srcs:
            raise RuntimeError("Aggiungi almeno un PDF da unire.")
        path = self._pick_save("PDF unito.pdf")
        if not path:
            return None
        res = FILES.merge(srcs, path)
        if open_after:
            res["info"] = self._open(path)
        return res

    @safe
    def split_file(self, source, mode, value=None):
        """source: a path or "@current"; the files go to a folder chosen by the user."""
        if source == "@current":
            src = self._current_src()
            base = os.path.splitext(src[0])[0]
        else:
            src, base = source, os.path.splitext(os.path.basename(source))[0]
        res = self._window.create_file_dialog(_dialog_kind("FOLDER"))
        if not res:
            return None
        folder = res[0] if isinstance(res, (list, tuple)) else res
        return FILES.split(src, base, folder, mode, value)

    # ---------------------------------------------------------------- convert
    def _adopt_bytes(self, data, title):
        s = self._new_session()
        s.open_bytes(data, title)
        return self._adopt(s)

    @safe
    def convert_files(self):
        """Word, Excel, PowerPoint, images, web pages... to PDF: opened in a new tab."""
        import tempfile
        paths = self._pick("Documenti e immagini", CONVERT.ALL_EXT, multiple=True)
        if not paths:
            return None
        out = os.path.join(tempfile.mkdtemp(prefix="calamo-conv-"), "conv.pdf")
        res = CONVERT.files_to_pdf(paths, out)
        with open(out, "rb") as f:
            data = f.read()
        base = os.path.splitext(os.path.basename(paths[0]))[0]
        info = self._adopt_bytes(data, base + ".pdf")
        return {"info": info, "how": res["how"], "files": len(paths)}

    @safe
    def convert_url(self, url):
        import tempfile
        out = os.path.join(tempfile.mkdtemp(prefix="calamo-web-"), "web.pdf")
        try:
            CONVERT.url_to_pdf(url, out)
        except Exception as e:
            raise RuntimeError(f"Non riesco a scaricare la pagina: {e}")
        with open(out, "rb") as f:
            data = f.read()
        host = re.sub(r"^https?://", "", url).split("/")[0] or "Pagina web"
        return {"info": self._adopt_bytes(data, host + ".pdf")}

    @safe
    def export_images(self, spec, fmt="png", dpi=150):
        from engine.editing import parse_pages
        pages = parse_pages(spec, len(self._s.doc))
        res = self._window.create_file_dialog(_dialog_kind("FOLDER"))
        if not res:
            return None
        folder = res[0] if isinstance(res, (list, tuple)) else res
        with self._s.lock:
            return CONVERT.pdf_to_images(self._s.doc, pages, folder, self._base(), fmt, dpi)

    # ---------------------------------------------------------------- searchable, tables, arrange, reading, translation
    @safe
    def make_searchable(self, index):
        return self._s.make_searchable(index)

    @safe
    def tables(self, index):
        return self._s.tables(index)

    @safe
    def table_cells(self, index, tid):
        return self._s.table_cells(index, tid)

    @safe
    def edit_table(self, index, tid, changes):
        return self._s.edit_table(index, tid, changes)

    @safe
    def table_row(self, index, tid, r, op):
        return self._s.table_row(index, tid, r, op)

    @safe
    def arrange(self, index, moves):
        return self._s.arrange(index, moves)

    @safe
    def reading_order(self, index):
        return self._s.reading_order(index)

    @safe
    def languages_translate(self):
        return [[k, v] for k, v in EX.LANGS.items()]

    @safe
    def translate_doc(self, target):
        """A translated copy of the document, in a new tab (the original is not touched)."""
        import copy
        src = self._s
        with src.lock:
            data = src.doc.tobytes()
            ink, hands = copy.deepcopy(src.ink), copy.deepcopy(src.hands)
        base = os.path.splitext(src.info()["name"])[0]
        s = self._new_session()
        s.open_bytes(data, f"{base} ({target.split('-')[0].upper()}).pdf")
        s.ink, s.hands = ink, hands
        done = s.translate_into(target, EX.translate_texts, self._progress("Traduzione"))
        info = self._adopt(s)
        return {"info": info, "translated": done}

    @safe
    def move_block(self, index, bbox, dx, dy):
        return self._s.move_block(index, bbox, dx, dy)

    @safe
    def move_ink(self, index, line_id, dx, dy):
        return self._s.move_ink(index, line_id, dx, dy)

    @safe
    def graphics(self, index):
        return self._s.graphics(index)

    @safe
    def move_graphic(self, index, item, dx, dy):
        return self._s.move_graphic(index, item, dx, dy)

    @safe
    def delete_pages(self, spec):
        return self._s.delete_pages(spec)

    @safe
    def duplicate_page(self, index):
        return self._s.duplicate_page(index)

    # ---------------------------------------------------------------- objects, pictures, signatures
    @safe
    def objects(self, index):
        return self._s.objects(index)

    @safe
    def move_object(self, index, src, oid, rect):
        return self._s.move_object(index, src, oid, rect)

    @safe
    def delete_object(self, index, src, oid):
        return self._s.delete_object(index, src, oid)

    @safe
    def edit_object(self, index, oid, content=None, color=None):
        return self._s.edit_object(index, oid, content, color)

    @safe
    def insert_picture(self, index, x, y):
        path = self._pick("Immagini", ["png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"])
        if not path:
            return None
        with open(path, "rb") as f:
            return self._s.insert_picture(index, f.read(), x, y)

    @safe
    def replace_picture(self, index, src, oid):
        path = self._pick("Immagini", ["png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"])
        if not path:
            return None
        with open(path, "rb") as f:
            return self._s.replace_picture(index, src, oid, f.read())

    @safe
    def crop_picture(self, index, oid, frac):
        return self._s.crop_picture(index, oid, frac)

    @safe
    def signatures(self):
        return self._s.signatures()

    @safe
    def save_signature_drawn(self, name, data_url):
        import base64
        return self._s.save_signature(name, base64.b64decode(data_url.split(",", 1)[1]), clean=False)

    @safe
    def import_signature(self, name):
        path = self._pick("Immagini", ["png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"])
        if not path:
            return None
        with open(path, "rb") as f:
            return self._s.save_signature(name or os.path.splitext(os.path.basename(path))[0], f.read(), clean=True)

    @safe
    def delete_signature(self, name):
        return self._s.delete_signature(name)

    @safe
    def place_signature(self, index, name, x, y):
        return self._s.place_signature(index, name, x, y)

    # ---------------------------------------------------------------- markup and comments
    @safe
    def mark_text(self, index, rect, kind, color=None):
        return self._s.mark_text(index, rect, kind, color)

    @safe
    def add_note(self, index, x, y, text, color=None):
        return self._s.add_note(index, x, y, text, color)

    @safe
    def add_shape(self, index, kind, a, b, color=None, width=2):
        return self._s.add_shape(index, kind, a, b, color, width)

    @safe
    def comments(self):
        return self._s.comments()

    # ---------------------------------------------------------------- redaction, stamps
    @safe
    def find_sensitive(self, kinds, term=""):
        return self._s.find_sensitive(kinds, term)

    @safe
    def redact(self, areas, metadata=False):
        return self._s.redact(areas, metadata)

    @safe
    def stamp_pages(self, spec):
        return self._s.stamp_pages(spec)

    # ---------------------------------------------------------------- compare, optimise, protect
    @safe
    def compare_with(self):
        path = self._pick("Documenti PDF", ["pdf"])
        return self._s.compare_with(path) if path else None

    @safe
    def save_optimized(self, quality="media"):
        path = self._pick_save(f"{self._base()} - leggero.pdf")
        return self._s.save_optimized(path, quality) if path else None

    @safe
    def save_protected(self, user_pw, owner_pw, allow_print=True, allow_copy=False, allow_edit=False):
        if not user_pw and not owner_pw:
            raise RuntimeError("Scrivi almeno una password.")
        path = self._pick_save(f"{self._base()} - protetto.pdf")
        return self._s.save_protected(path, user_pw, owner_pw, allow_print, allow_copy, allow_edit) if path else None

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

    # ---------------------------------------------------------------- forms
    @safe
    def fields(self, index=None):
        return self._s.fields(index)

    @safe
    def fill_fields(self, values):
        return self._s.fill_fields(values)

    @safe
    def add_field(self, index, kind, rect, name="", options=None, multiline=False):
        return self._s.add_field(index, kind, rect, name, options, multiline)

    @safe
    def edit_field(self, index, xref, name=None, options=None, multiline=None, required=None):
        return self._s.edit_field(index, xref, name, options, multiline, required)

    @safe
    def delete_field(self, index, xref):
        return self._s.delete_field(index, xref)

    @safe
    def detect_fields(self, spec=""):
        return self._s.detect_fields(spec)

    @safe
    def flatten(self, forms=True, annots=True):
        return self._s.flatten(forms, annots)

    # ---------------------------------------------------------------- page size, crop, sheets
    @safe
    def crop_pages(self, spec="", rect=None, margins=None, auto=False):
        return self._s.crop_pages(spec, rect, margins, auto)

    @safe
    def reset_crop(self, spec=""):
        return self._s.reset_crop(spec)

    @safe
    def resize_pages(self, spec="", size="A4", width=None, height=None):
        return self._s.resize_pages(spec, size, width, height)

    @safe
    def make_nup(self, cols, rows, size="A4", spec="", border=False):
        data = self._s.nup_bytes(cols, rows, size, spec, border)
        return {"info": self._adopt_bytes(data, f"{self._base()} - {int(cols) * int(rows)} per foglio.pdf")}

    @safe
    def make_booklet(self, size="A4"):
        data = self._s.booklet_bytes(size)
        return {"info": self._adopt_bytes(data, f"{self._base()} - opuscolo.pdf")}

    # ---------------------------------------------------------------- bookmarks, links, properties
    @safe
    def bookmarks(self):
        return self._s.bookmarks()

    @safe
    def set_bookmarks(self, items):
        return self._s.set_bookmarks(items)

    @safe
    def bookmarks_from_headings(self):
        return self._s.bookmarks_from_headings()

    @safe
    def links(self, index):
        return self._s.links(index)

    @safe
    def add_link(self, index, rect, uri=None, target_page=None):
        return self._s.add_link(index, rect, uri, target_page)

    @safe
    def delete_link(self, index, xref):
        return self._s.delete_link(index, xref)

    @safe
    def open_url(self, url):
        import webbrowser
        if not re.match(r"^(https?|mailto):", url or "", re.I):
            raise RuntimeError("Questo collegamento non è un indirizzo web.")
        webbrowser.open(url)
        return True

    @safe
    def properties(self):
        return self._s.properties()

    @safe
    def set_properties(self, props):
        return self._s.set_properties(props)

    # ---------------------------------------------------------------- password, colour, archive
    @safe
    def save_unlocked(self):
        path = self._pick_save(f"{self._base()} - senza password.pdf")
        return self._s.save_unlocked(path) if path else None

    @safe
    def remove_password(self):
        return self._s.remove_password()

    @safe
    def to_grayscale(self):
        return self._s.to_grayscale()

    @safe
    def preflight(self):
        return self._s.preflight()

    @safe
    def save_pdfa(self):
        path = self._pick_save(f"{self._base()} - PDF-A.pdf")
        return self._s.save_pdfa(path, self._progress("Creazione del PDF/A")) if path else None

    @safe
    def export_other(self, kind):
        """PowerPoint, web page (HTML) or plain text."""
        ext, label = {"pptx": ("pptx", "Presentazione PowerPoint"), "html": ("html", "Pagina web"),
                      "txt": ("txt", "Testo")}[kind]
        path = self._ask_export_path(ext, label)
        if not path:
            return None
        if kind == "pptx":
            return self._s.export_pptx(path, self._progress("Esportazione in PowerPoint"))
        if kind == "html":
            return self._s.export_html(path)
        return self._s.export_text(path)

    # ---------------------------------------------------------------- digital signatures
    @safe
    def certificates(self):
        return SIGN.list_certificates()

    @safe
    def create_certificate(self, name, email="", org="", password=""):
        c = SIGN.create_certificate(name, email, org, password)
        return {"created": c, "list": SIGN.list_certificates()}

    @safe
    def import_certificate(self):
        import shutil
        path = self._pick("Certificati digitali", ["p12", "pfx"])
        if not path:
            return None
        dst = os.path.join(SIGN.cert_dir(), os.path.basename(path))
        if os.path.normcase(os.path.abspath(dst)) != os.path.normcase(os.path.abspath(path)):
            shutil.copyfile(path, dst)
        return {"created": {"path": dst, "name": os.path.splitext(os.path.basename(dst))[0]},
                "list": SIGN.list_certificates()}

    @safe
    def certificate_info(self, path, password):
        return SIGN.certificate_info(path, password)

    @safe
    def sign_digital(self, cert_path, password, index=0, rect=None, field=None, reason="", location="", tsa=""):
        """Signed into a new file, opened in a tab: a signed PDF must not be changed after."""
        path = self._pick_save(f"{self._base()} - firmato.pdf")
        if not path:
            return None
        res = self._s.sign_digital(path, cert_path, password, index, rect, field, reason, location, tsa)
        if self._s.path and os.path.normcase(os.path.abspath(self._s.path)) == os.path.normcase(os.path.abspath(path)):
            self._s.open(path)                   # the file under the open document was replaced
            res["info"] = self._s.info()
        else:
            res["info"] = self._open(path)
        return res

    @safe
    def verify_signatures(self):
        return self._s.verify_signatures()

    # ---------------------------------------------------------------- repair, many files
    @safe
    def repair_file(self):
        src = self._pick("Documenti PDF", ["pdf"])
        if not src:
            return None
        base = os.path.splitext(os.path.basename(src))[0]
        out = self._pick_save(f"{base} - riparato.pdf")
        if not out:
            return None
        res = FILES.repair(src, out)
        res["info"] = self._open(out)
        return res

    @safe
    def batch_run(self, paths, action, opts=None):
        res = self._window.create_file_dialog(_dialog_kind("FOLDER"))
        if not res:
            return None
        folder = res[0] if isinstance(res, (list, tuple)) else res
        return BATCH.run(paths, action, opts or {}, folder, self._progress("Elaborazione dei file"))

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
        """The handwriting reader, if installed and switched on (also used to choose the
        most legible way of rewriting a line)."""
        if not self._settings.get("handModel", True) or not READER.installed() or not READER.runtime_ok():
            return None
        return READER

    @safe
    def analyze(self, index):
        gen = self._cancel_gen
        return self._s.analyze(index, self._settings.get("ocrLang") or None, reader=self._reader(),
                               cancelled=lambda: gen != self._cancel_gen)

    @safe
    def remove_ink(self, index, line_id):
        return self._s.remove_ink(index, line_id)

    @safe
    def mark_ink(self, index, rect):
        return self._s.mark_ink(index, rect, self._settings.get("ocrLang") or None, reader=self._reader())

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
    def separate_lines(self, index, ids, kind):
        return self._s.separate_lines(index, ids, kind)

    @safe
    def keep_lines(self, index, ids):
        return self._s.keep_lines(index, ids)

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
    def merge_styles(self, index, src, dst):
        return self._s.merge_styles(index, src, dst)

    @safe
    def split_style(self, index, sid):
        return self._s.split_style(index, sid)

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
    # the libraries of 1.5: PowerPoint, PDF/A, digital signature (missing modules show up only in the exe)
    report["pptx"] = s.export_pptx(os.path.join(tmp, "t.pptx"))["slides"]
    report["pdfa"] = s.save_pdfa(os.path.join(tmp, "a.pdf"))["ok"]
    cert = SIGN.create_certificate("Selftest Calamo", "", "", "selftest")
    try:
        s.sign_digital(os.path.join(tmp, "f.pdf"), cert["path"], "selftest", 0, [300, 700, 500, 760])
        with open(os.path.join(tmp, "f.pdf"), "rb") as f:
            report["signature"] = SIGN.verify(f.read())[0]["intact"]
    finally:
        os.remove(cert["path"])
    if not report["signature"]:
        raise RuntimeError(f"firma digitale non valida: {report}")
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
        frameless=True, easy_drag=False, background_color="#111113", text_select=True,
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
