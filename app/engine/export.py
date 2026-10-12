"""Export to Word (.docx) and Excel (.xlsx), keeping styles, images and tables,
plus the recognised handwriting."""
import io
import re

import pymupdf as fitz
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import fonts as F

HAND_FONT = "Segoe Print"


def _tables(page):
    try:
        return [t for t in page.find_tables().tables if t.row_count >= 2 and t.col_count >= 2]
    except Exception:
        return []


def _inside(rect, rects):
    c = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    return any(r.contains(c) for r in rects)


# --------------------------------------------------------------------------- Word
def to_docx(doc, ink, path, progress=None):
    word = Document()
    st = word.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    st.paragraph_format.space_after = Pt(0)
    first = doc[0].rect
    sec = word.sections[0]
    sec.page_width, sec.page_height = Pt(first.width), Pt(first.height)
    if first.width > first.height:
        sec.orientation = WD_ORIENT.LANDSCAPE
    margin = Pt(40)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = margin
    usable = first.width - 80

    for pi, page in enumerate(doc):
        if progress:
            progress(pi, len(doc))
        tables = _tables(page)
        trects = [fitz.Rect(t.bbox) for t in tables]
        items = []
        d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES | fitz.TEXT_PRESERVE_LIGATURES)
        for b in d["blocks"]:
            r = fitz.Rect(b["bbox"])
            if b["type"] == 0:
                if _inside(r, trects):
                    continue
                items.append((r.y0, r.x0, "text", b))
            elif b["type"] == 1 and b.get("image") and r.width > 8 and r.height > 8:
                items.append((r.y0, r.x0, "image", b))
        for t, tr in zip(tables, trects):
            items.append((tr.y0, tr.x0, "table", t))
        for l in ink.get(pi, []):
            items.append((l["bbox"][1], l["bbox"][0], "ink", l))
        items.sort(key=lambda it: (round(it[0] / 4), it[1]))

        last_bottom = None
        for y, x, kind, obj in items:
            if kind == "text":
                para = word.add_paragraph()
                pf = para.paragraph_format
                if last_bottom is not None:
                    gap = obj["bbox"][1] - last_bottom
                    pf.space_before = Pt(max(0, min(gap, 40)))
                indent = obj["bbox"][0] - page.rect.x0 - 40
                if indent > 6:
                    pf.left_indent = Pt(min(indent, usable * 0.6))
                lines = [l for l in obj["lines"] if any(s["text"].strip() for s in l["spans"])]
                if len(lines) == 1:
                    bw = obj["bbox"][2] - obj["bbox"][0]
                    cx = (obj["bbox"][0] + obj["bbox"][2]) / 2
                    if abs(cx - page.rect.width / 2) < 12 and bw < page.rect.width * 0.7:
                        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        pf.left_indent = None
                for li, l in enumerate(lines):
                    for s in l["spans"]:
                        text = s["text"]
                        if not text:
                            continue
                        run = para.add_run(text)
                        bold, italic, serif, mono = F.style_from(F.clean_name(s["font"]), s["flags"])
                        run.bold, run.italic = bold, italic
                        run.font.size = Pt(round(s["size"] * 2) / 2)
                        run.font.name = F.css_family(s["font"], s["flags"])
                        c = s["color"]
                        run.font.color.rgb = RGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255)
                        if s["flags"] & 1:
                            run.font.superscript = True
                    if li < len(lines) - 1:
                        last_text = "".join(s["text"] for s in l["spans"]).rstrip()
                        if last_text.endswith("-") and para.runs:
                            para.runs[-1].text = para.runs[-1].text.rstrip()[:-1]
                        else:
                            para.add_run(" ")
                last_bottom = obj["bbox"][3]
            elif kind == "image":
                try:
                    stream = io.BytesIO(obj["image"])
                    w = min(obj["bbox"][2] - obj["bbox"][0], usable)
                    word.add_picture(stream, width=Pt(w))
                    word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    pass
                last_bottom = obj["bbox"][3]
            elif kind == "table":
                data = obj.extract()
                rows, cols = len(data), max(len(r) for r in data)
                tbl = word.add_table(rows=rows, cols=cols)
                tbl.style = "Table Grid"
                for ri, row in enumerate(data):
                    for ci, val in enumerate(row):
                        cell = tbl.cell(ri, ci)
                        cell.text = (val or "").replace("\n", " ")
                        if ri == 0:
                            for p in cell.paragraphs:
                                for run in p.runs:
                                    run.bold = True
                last_bottom = obj.bbox[3]
            elif kind == "ink":
                para = word.add_paragraph()
                if last_bottom is not None:
                    para.paragraph_format.space_before = Pt(max(0, min(obj["bbox"][1] - last_bottom, 40)))
                run = para.add_run(obj["text"])
                run.font.name = HAND_FONT
                run.font.size = Pt(max(8, min(round((obj["bbox"][3] - obj["bbox"][1]) * 0.8), 28)))
                run.font.color.rgb = RGBColor(0x1F, 0x2A, 0x5A)
                last_bottom = obj["bbox"][3]
        if pi < len(doc) - 1:
            word.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    word.save(path)
    return path


# --------------------------------------------------------------------------- Excel
_num = re.compile(r"^-?[\d.]+(,\d+)?$|^-?[\d,]+(\.\d+)?$")


def _cell_value(v):
    if v is None:
        return None
    s = str(v).replace("\n", " ").strip()
    t = s.replace("€", "").replace("%", "").replace(" ", "")
    if t and _num.match(t):
        try:
            if "," in t and "." in t:
                t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
            elif "," in t:
                t = t.replace(",", ".")
            val = float(t)
            if s.endswith("%"):
                return val / 100
            return int(val) if val.is_integer() and "." not in t else val
        except ValueError:
            pass
    return s


def _layout_rows(page, ink_lines):
    """No ruled tables: rebuild a grid from word positions."""
    words = page.get_text("words")
    items = [(w[0], w[1], w[2], w[3], w[4]) for w in words]
    for l in ink_lines:
        items.append((l["bbox"][0], l["bbox"][1], l["bbox"][2], l["bbox"][3], l["text"]))
    if not items:
        return []
    items.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows, cur, cy = [], [], None
    for w in items:
        mid = (w[1] + w[3]) / 2
        if cy is None or abs(mid - cy) <= max(3, (w[3] - w[1]) * 0.45):
            cur.append(w)
            cy = mid if cy is None else (cy + mid) / 2
        else:
            rows.append(cur)
            cur, cy = [w], mid
    if cur:
        rows.append(cur)
    # column anchors: cluster cell starting x positions
    cells_per_row = []
    for row in rows:
        row.sort(key=lambda w: w[0])
        cells, cell = [], [row[0]]
        for w in row[1:]:
            h = max(w[3] - w[1], 4)
            if w[0] - cell[-1][2] > h * 1.2:
                cells.append(cell)
                cell = [w]
            else:
                cell.append(w)
        cells.append(cell)
        cells_per_row.append([(c[0][0], " ".join(x[4] for x in c)) for c in cells])
    anchors = sorted({round(x / 12) * 12 for row in cells_per_row for x, _ in row})
    merged = []
    for a in anchors:
        if not merged or a - merged[-1] > 24:
            merged.append(a)
    out = []
    for row in cells_per_row:
        line = [None] * len(merged)
        for x, text in row:
            ci = min(range(len(merged)), key=lambda k: abs(merged[k] - x))
            line[ci] = (line[ci] + " " + text) if line[ci] else text
        out.append(line)
    return out


def to_xlsx(doc, ink, path, progress=None):
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="1F2430")
    head_font = Font(bold=True, color="F4D58D")
    thin = Side(style="thin", color="C9CED8")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    zebra = PatternFill("solid", fgColor="F5F6F9")
    count = 0

    def write(ws, rows, header=True):
        widths = {}
        for ri, row in enumerate(rows, start=1):
            for ci, val in enumerate(row, start=1):
                v = _cell_value(val)
                c = ws.cell(row=ri, column=ci, value=v)
                c.border = border
                c.alignment = Alignment(vertical="top", wrap_text=True)
                if header and ri == 1:
                    c.fill, c.font = head_fill, head_font
                elif ri % 2 == 0:
                    c.fill = zebra
                if isinstance(v, float) and val and str(val).strip().endswith("%"):
                    c.number_format = "0.00%"
                widths[ci] = max(widths.get(ci, 8), min(len(str(v or "")) + 2, 60))
        for ci, w in widths.items():
            ws.column_dimensions[get_column_letter(ci)].width = w
        if header and rows:
            ws.freeze_panes = "A2"

    for pi, page in enumerate(doc):
        if progress:
            progress(pi, len(doc))
        tables = _tables(page)
        for ti, t in enumerate(tables, start=1):
            ws = wb.create_sheet(f"P{pi + 1} Tabella {ti}"[:31])
            write(ws, t.extract(), header=True)
            count += 1
        if not tables:
            rows = _layout_rows(page, ink.get(pi, []))
            if rows:
                ws = wb.create_sheet(f"Pagina {pi + 1}")
                write(ws, rows, header=False)
                count += 1
    if not wb.sheetnames:
        wb.create_sheet("Vuoto")
    wb.save(path)
    return count
