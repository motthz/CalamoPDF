"""Serves the real interface (like dev_server.py) for tools/ui_test.js: the file dialogs
answer from a queue the test writes (next.json), and a few test documents are made.
Usage: python tools/ui_harness.py <work folder>"""
import io
import json
import os
import sys
import types

WORK = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build/uitest")
os.makedirs(WORK, exist_ok=True)
os.environ["CALAMO_DATA"] = os.path.join(WORK, "data")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
if sys.platform != "win32":                   # the window calls are Windows only
    import ctypes

    class _Calls:
        def __getattr__(self, name):
            f = lambda *a, **k: 0  # noqa: E731
            setattr(self, name, f)
            return f
    ctypes.windll = types.SimpleNamespace(user32=_Calls())
if "webview" not in sys.modules:
    try:
        import webview  # noqa: F401
    except Exception:
        sys.modules["webview"] = types.SimpleNamespace(OPEN_DIALOG=10, SAVE_DIALOG=20, FOLDER_DIALOG=30)
import webview  # noqa: E402

import pymupdf as fitz  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def make_docs():
    # a digital page: a table with its text, a drawing, a picture, a form field
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    p.insert_text((60, 60), "Documento di prova", fontsize=16)
    for i in range(4):
        p.draw_line((60, 100 + i * 20), (300, 100 + i * 20), color=(0, 0, 0), width=1)
    for x in (60, 180, 300):
        p.draw_line((x, 100), (x, 160), color=(0, 0, 0), width=1)
    for i in range(3):
        p.insert_text((65, 115 + i * 20), f"Voce {i + 1}", fontsize=10)
        p.insert_text((185, 115 + i * 20), f"{(i + 1) * 10},00", fontsize=10)
    p.draw_circle((450, 120), 25, color=(0.8, 0, 0), fill=(1, 0.8, 0))
    im = Image.new("RGB", (80, 50), (30, 120, 200))
    ImageDraw.Draw(im).ellipse((10, 10, 70, 40), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    p.insert_image(fitz.Rect(60, 300, 140, 350), stream=buf.getvalue())
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = "nome"
    w.rect = fitz.Rect(300, 300, 450, 320)
    w.field_value = "Mario"
    p.add_widget(w)
    d.save(os.path.join(WORK, "dig.pdf"))
    for name in ("a", "b"):
        d = fitz.open()
        for k in range(3):
            d.new_page(width=420, height=595).insert_text((60, 80), f"{name.upper()}{k + 1}", fontsize=40)
        d.save(os.path.join(WORK, name + ".pdf"))
    # a form made for paper, with headings (forms, bookmarks, links, signatures)
    d = fitz.open()
    p = d.new_page(width=595, height=842)
    p.insert_text((60, 60), "Modulo di iscrizione", fontsize=18)
    p.insert_text((60, 110), "Nome: ____________________", fontsize=11)
    p.insert_text((60, 140), "Cognome: .......................................", fontsize=11)
    p.draw_rect((60, 190, 72, 202), color=(0, 0, 0), width=0.8)
    p.insert_text((78, 200), "Accetto le condizioni", fontsize=11)
    for k in range(2):
        q = d.new_page(width=595, height=842)
        q.insert_text((60, 80), f"Capitolo {k + 1}", fontsize=20)
        q.insert_text((60, 120), "Testo normale del capitolo, con qualche parola.", fontsize=11)
    d.save(os.path.join(WORK, "modulo.pdf"))
    # a scanned form: a table drawn on paper, with writing and a mark in its cells
    from PIL import ImageFont
    img = Image.new("RGB", (1240, 1754), (240, 237, 228))
    dr = ImageDraw.Draw(img)
    font = None
    for f in ("DejaVuSans.ttf", "arial.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(f, 30)
            break
        except Exception:
            continue
    for y in range(300, 1001, 140):
        dr.line((120, y, 1100, y + 6), fill=(30, 40, 150), width=4)
    for x in (120, 520, 1100):
        dr.line((x, 300, x + 4, 1006), fill=(30, 40, 150), width=4)
    for k in range(5):
        dr.text((150, 345 + k * 140), f"Voce numero {k + 1}", fill=(30, 40, 150), font=font)
    dr.ellipse((700, 470, 780, 550), outline=(200, 30, 30), width=6)          # a mark in a cell
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    d = fitz.open()
    d.new_page(width=595, height=842).insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())
    d.save(os.path.join(WORK, "scan_tab.pdf"))
    # a page to read aloud
    d = fitz.open()
    d.new_page().insert_text((72, 100), "Buongiorno. Questa è una prova di lettura ad alta voce.", fontsize=14)
    d.save(os.path.join(WORK, "voce.pdf"))


make_docs()
import updater  # noqa: E402
updater.check = lambda cur: None          # no update banner during the test
OUT = os.path.join(WORK, "out")
os.makedirs(OUT, exist_ok=True)
NEXT = os.path.join(WORK, "next.json")


class FakeWindow:
    def create_file_dialog(self, kind, **kw):
        q = json.load(open(NEXT)) if os.path.exists(NEXT) else []
        v = q.pop(0) if q else None
        json.dump(q, open(NEXT, "w"))
        if v is None:
            return None
        if kind == getattr(webview, "SAVE_DIALOG", 20):
            return os.path.join(OUT, v)
        if kind == getattr(webview, "FOLDER_DIALOG", 30):
            return [OUT]
        return [os.path.join(WORK, x) for x in v.split("|")]

    def evaluate_js(self, code):
        pass

    def destroy(self):
        pass


src = open(os.path.join(ROOT, "tools", "dev_server.py"), encoding="utf-8").read().replace(
    "api = main.Api()", 'api = main.Api(); api._window = FakeWindow(); api._settings["tutorialDone"] = True')
sys.argv = ["dev_server.py"]
exec(compile(src, "dev_server.py", "exec"))
