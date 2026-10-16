"""Measure how well Calamo reads and rewrites handwriting on real pages.

    python tools/eval_handwriting.py page1.png page2.pdf ... [--truth testo.txt] [--out cartella]

For every page (an image, or the first page of a PDF):
* reading: which share of the words of --truth (the text the page really holds) Calamo
  read, and which share of what it read is right (recall / precision);
* kind: how many lines were taken for handwriting and how many for print;
* rewriting: a few sentences are written with the page's main writing and read back by
  the handwriting reader: the character error rate (0 = read perfectly) tells how legible
  the rewritten text is, without judging it by eye. "best" is the composition Calamo
  chooses in the app (the most legible of a few).

The pages are only read from disk: nothing is uploaded or kept. On Windows the system
OCR is used; elsewhere, if the rapidocr_onnxruntime package is installed, it stands in
for it (otherwise only the handwriting reader reads)."""
import argparse
import collections
import io
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CALAMO_DATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(ROOT, "app"))

import numpy as np  # noqa: E402
import pymupdf as fitz  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import engine.handwriting as HW  # noqa: E402
import engine.ocr as O  # noqa: E402
from engine.document import Session  # noqa: E402
from engine.handreader import READER  # noqa: E402
from engine.quality import legibility  # noqa: E402

PHRASES = ["Stefano Motta", "Da fare", "gestione sessioni", "Sviluppo Applicazione Web", "30/12/2025",
           "Visto il 20/10", "Mario Bianchi", "consegna rinviata", "Documentazione tecnica",
           "Completato", "Rinviato a gennaio", "Luca Rossi", "In attesa", "14/03/2026", "approvato",
           "pagamento ricevuto", "nuova scadenza", "verificare con il cliente"]


def _stand_in_ocr():
    """rapidocr in place of the Windows OCR (lines cut into words by their letters)."""
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()

    def read(png, lang=None):
        im = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        res, _ = ocr(im)
        out = []
        for box, text, _score in res or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            words = text.split()
            n = max(sum(len(w) for w in words) + len(words) - 1, 1)
            cur, line = 0, []
            for w in words:
                a = x0 + (x1 - x0) * cur / n
                cur += len(w)
                b = x0 + (x1 - x0) * cur / n
                cur += 1
                line.append({"text": w, "box": [a, y0, b, y1]})
            out.append(line)
        return out
    return read


def _words(t):
    return [w for w in re.findall(r"[\wàèéìòù/]+", t.lower()) if w]


def _page_pdf(path):
    if path.lower().endswith(".pdf"):
        return path
    img = Image.open(path)
    w, h = img.size
    doc = fitz.open()
    page = doc.new_page(width=595, height=595 * h / w)
    page.insert_image(page.rect, filename=path)
    out = os.path.join(tempfile.mkdtemp(), os.path.splitext(os.path.basename(path))[0] + ".pdf")
    doc.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="+")
    ap.add_argument("--truth", help="text file with what the pages say")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "eval"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if sys.platform != "win32":
        try:
            O.windows_ocr = _stand_in_ocr()
        except Exception:
            O.windows_ocr = lambda png, lang=None: []
        O.available_languages = lambda: ["it-IT"]
    reader = READER if READER.installed() else None
    if reader is None:
        print("Il lettore della scrittura a mano non è installato: niente misura della riscrittura.")
    HW.REWRITE_READER = lambda: reader
    truth = collections.Counter(_words(open(args.truth, encoding="utf-8").read())) if args.truth else None
    report = {}
    for path in args.pages:
        name = os.path.basename(path)
        s = Session()
        s.open(_page_pdf(path))
        s.analyze(0, "it-IT", reader=reader)
        lines = s.ink.get(0, [])
        e = {"lines": len(lines), "kinds": dict(collections.Counter(l.get("kind") for l in lines)),
             "writings": [[lib.name, lib.kind, len(lib.heights)] for lib in s.writings(0).libs.values()]}
        if truth:
            got = collections.Counter(_words(" ".join(l["text"] for l in lines if l["source"] != "unread")))
            e["recall"] = round(sum(min(c, got[w]) for w, c in truth.items()) / max(sum(truth.values()), 1), 3)
            e["precision"] = round(sum(min(c, truth[w]) for w, c in got.items()) / max(sum(got.values()), 1), 3)
        pw = s.writings(0)
        lib = pw.default("hand") or pw.default("print")
        if lib is not None and reader is not None:
            rows = []
            for mode in ("plain", "best"):
                cers = []
                for t in PHRASES:
                    r = lib.render_best(t, 9.0) if mode == "best" else lib.render(t, 9.0, seed=1)
                    c, got_text = legibility(r["png"], t, reader) if r else (1.0, "")
                    cers.append(c)
                    if r and mode == "best":
                        rows.append((Image.open(io.BytesIO(r["png"])).convert("RGBA"), f"{c:.2f}  {got_text}"))
                e["rewrite_" + mode] = round(float(np.mean(cers)), 3)
            W = max(im.width for im, _ in rows) + 420
            H = sum(im.height + 8 for im, _ in rows) + 10
            sheet = Image.new("RGB", (W, H), "white")
            d = ImageDraw.Draw(sheet)
            y = 5
            for im, label in rows:
                sheet.paste(im, (10, y), im)
                d.text((im.width + 30, y + im.height // 3), label, fill="black")
                y += im.height + 8
            sheet.save(os.path.join(args.out, os.path.splitext(name)[0] + "_riscrittura.png"))
        report[name] = e
        print(name, json.dumps(e, ensure_ascii=False), flush=True)
    keys = sorted({k for e in report.values() for k in e if k.startswith(("recall", "precision", "rewrite"))})
    for k in keys:
        vals = [e[k] for e in report.values() if k in e]
        print(f"{k}: media {np.mean(vals):.3f}  {vals}")
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
