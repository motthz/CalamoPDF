"""Creates samples/esempio.pdf: printed text, a table and handwritten notes."""
import io
import os
import sys

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "samples", "esempio.pdf")
os.makedirs(os.path.dirname(out), exist_ok=True)

doc = fitz.open()
page = doc.new_page(width=595, height=842)
page.insert_text((60, 80), "Relazione trimestrale", fontname="hebo", fontsize=22, color=(0.1, 0.12, 0.2))
body = ("Il presente documento riassume i risultati del terzo trimestre. Le vendite sono cresciute "
        "in tutte le regioni, con un incremento particolarmente marcato nel Nord Italia. Il team "
        "commerciale ha raggiunto il 112% dell'obiettivo previsto.")
page.insert_textbox(fitz.Rect(60, 100, 535, 190), body, fontname="tiro", fontsize=12, lineheight=1.35)

rows = [["Regione", "Q2", "Q3", "Variazione"], ["Nord", "120.400", "141.900", "17,9%"],
        ["Centro", "98.200", "104.300", "6,2%"], ["Sud", "76.800", "83.100", "8,2%"]]
x0, y0, cw, rh = 60, 210, 118, 24
for r, row in enumerate(rows):
    for c, val in enumerate(row):
        rect = fitz.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
        page.draw_rect(rect, color=(0.6, 0.62, 0.68), width=0.7,
                       fill=(0.92, 0.93, 0.96) if r == 0 else None)
        page.insert_text((rect.x0 + 6, rect.y1 - 8), val, fontname="hebo" if r == 0 else "helv", fontsize=10.5)

page.insert_text((60, 360), "Note a mano del responsabile:", fontname="helv", fontsize=11, color=(0.3, 0.3, 0.35))

font = ImageFont.truetype(os.path.join(os.environ["WINDIR"], "Fonts", "Inkfree.ttf"), 64)
notes = ["Ottimo lavoro a tutto il team", "Rivedere i dati del Sud entro venerdi", "Firmato: Marco"]
y = 380
for line in notes:
    img = Image.new("RGBA", (1700, 110), (255, 255, 255, 0))
    ImageDraw.Draw(img).text((4, 10), line, font=font, fill=(25, 40, 110, 255))
    bbox = img.getbbox()
    img = img.crop((0, 0, bbox[2] + 6, 110))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    w = img.width * 0.3
    page.insert_image(fitz.Rect(70, y, 70 + w, y + 33), stream=buf.getvalue())
    y += 40
doc.save(out)
print(out)
