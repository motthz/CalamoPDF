"""Engine smoke test: edit text, analyse handwriting, rewrite it, export."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CALAMO_DATA", os.path.join(ROOT, "build", "smoke", "data"))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine.document import Session  # noqa: E402
from engine import export  # noqa: E402

out_dir = os.path.join(ROOT, "build", "smoke")
os.makedirs(out_dir, exist_ok=True)
s = Session()
s.open(os.path.join(ROOT, "samples", "esempio.pdf"))
b = s.blocks(0)["blocks"]
for x in b:
    print("BLOCK", x["id"], x["css"], x["size"], repr(x["text"][:60]))
title = next(x for x in b if "Relazione" in x["text"])
s.edit_block(0, title["bbox"], "Relazione trimestrale 2026")
body = next(x for x in b if "presente documento" in x["text"])
s.edit_block(0, body["bbox"], body["text"].replace("terzo trimestre", "terzo trimestre fiscale"))
res = s.analyze(0)
for l in res["ink"]:
    print("INK", l["id"], l["source"], l["doubt"], repr(l["text"]))
print("GLYPHS", "".join(g["char"] for g in res["hand"]["glyphs"]), res["hand"]["ink"])
ink = res["ink"]
if ink:
    target = ink[-1]
    s.correct_ink(0, target["id"], "Firmato: Marco")
    s.edit_ink(0, target["id"], "Firmato: Marta Rossi")
s.save(os.path.join(out_dir, "edited.pdf"))
export.to_docx(s, os.path.join(out_dir, "edited.docx"))
print("sheets", export.to_xlsx(s, os.path.join(out_dir, "edited.xlsx")))
import pymupdf as fitz  # noqa: E402
fitz.open(os.path.join(out_dir, "edited.pdf"))[0].get_pixmap(dpi=110).save(os.path.join(out_dir, "page.png"))
print("OK")
