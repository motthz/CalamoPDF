"""What a page is made of, for exporting it to other formats: paragraphs of text, the lines
Calamo read on a scan, tables (also drawn by hand on a scan), pictures and the graphics of a
scan cut out on their own. A scanned page is NOT exported as one picture of the page: its
text becomes text, its tables tables, and only its drawings (a stamp, a logo) pictures."""
import io

import numpy as np
import pymupdf as fitz
from PIL import Image

from . import writers as WR

ZOOM = 2.0


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- tables of a scan
class _Line:
    """A ruled line of a scan as a straight line: y = a·x + b for a horizontal one
    (x = a·y + b for a vertical one), between lo and hi along it. Hand-drawn and
    photographed tables slant, so a fixed y or x is not enough."""
    __slots__ = ("a", "b", "lo", "hi")

    def __init__(self, a, b, lo, hi):
        self.a, self.b, self.lo, self.hi = a, b, lo, hi

    def at(self, t):
        return self.a * t + self.b


def _fit_lines(lab, stats, horizontal, min_len):
    out = []
    for k, (x0, x1, n, y0, y1) in stats.items():
        length, thick = (x1 - x0, y1 - y0) if horizontal else (y1 - y0, x1 - x0)
        if length < min_len or thick > length * 0.3:
            continue
        ys, xs = np.nonzero(lab[y0:y1, x0:x1] == k)
        ys, xs = ys + y0, xs + x0
        t, v = (xs, ys) if horizontal else (ys, xs)
        if len(t) > 4000:
            sel = np.linspace(0, len(t) - 1, 4000).astype(int)
            t, v = t[sel], v[sel]
        a, b = np.polyfit(t.astype(float), v.astype(float), 1) if t.max() > t.min() else (0.0, float(v.mean()))
        out.append(_Line(float(a), float(b), float(t.min()), float(t.max())))
    # pieces of one line broken by a gap (a pen lifted, a fold) become one line
    out.sort(key=lambda l: (l.at((l.lo + l.hi) / 2), l.lo))
    merged = []
    for l in out:
        for m in merged:
            mid = (max(l.lo, m.lo) + min(l.hi, m.hi)) / 2
            if abs(l.at(mid) - m.at(mid)) < 10 and (l.lo <= m.hi + 120 and m.lo <= l.hi + 120):
                lo, hi = min(l.lo, m.lo), max(l.hi, m.hi)
                # keep the longer piece's slope, centred between the two
                main = l if l.hi - l.lo > m.hi - m.lo else m
                m.a, m.b = main.a, (l.at(mid) + m.at(mid)) / 2 - main.a * mid
                m.lo, m.hi = lo, hi
                break
        else:
            merged.append(l)
    return merged


def scan_tables(rgb, zoom=ZOOM):
    """Tables ruled on a scanned page (printed, or drawn by hand and slanting):
    [{'bbox': [x0, y0, x1, y1] (points), 'h': [_Line], 'v': [_Line]}] with the lines in px
    of the image, sorted; R rows have R + 1 horizontal lines, borders included."""
    from .handwriting import HandwritingLibrary
    from .letters import _gray, paper
    g = _gray(rgb)
    mask = (paper(g, int(10 * zoom)) - g > 35) & (g < 225)     # also light grey borders
    band = max(int(10 * zoom), 4)
    # a wavy line (crumpled paper, a hand-drawn border) drifts across rows: thickened
    # across its length, its pieces still fill the strips the line detector looks at
    thick_v, thick_h = mask.copy(), mask.copy()
    for k in (1, 2):
        thick_h[k:] |= mask[:-k]
        thick_h[:-k] |= mask[k:]
        thick_v[:, k:] |= mask[:, :-k]
        thick_v[:, :-k] |= mask[:, k:]
    vert = WR.rules(thick_v, band, chain=3, fill=0.6, axes=(0,)) & mask
    horiz = WR.rules(thick_h, band, chain=3, chain_h=4, fill=0.6, axes=(1,)) & mask
    H, W = mask.shape
    edge = 6 * zoom
    hs = [l for l in _fit_lines(*HandwritingLibrary._strokes(horiz), True, 50 * zoom)
          if edge < l.at((l.lo + l.hi) / 2) < H - edge]
    vs = [l for l in _fit_lines(*HandwritingLibrary._strokes(vert), False, 25 * zoom)
          if edge < l.at((l.lo + l.hi) / 2) < W - edge]           # the edge of a photographed sheet is not a border
    if len(hs) < 2 or len(vs) < 2:
        return []
    tol = 8 * zoom
    # a horizontal and a vertical line belong together when they cross (or nearly)
    parent = list(range(len(hs) + len(vs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, h in enumerate(hs):
        for j, v in enumerate(vs):
            x = v.at(h.at(v.at(h.at((h.lo + h.hi) / 2))))   # where they meet (two steps are enough)
            y = h.at(x)
            if h.lo - tol <= x <= h.hi + tol and v.lo - tol <= y <= v.hi + tol:
                a, b = find(i), find(len(hs) + j)
                if a != b:
                    parent[a] = b
    groups = {}
    for i in range(len(hs) + len(vs)):
        groups.setdefault(find(i), []).append(i)
    tables = []
    for members in groups.values():
        th = [hs[i] for i in members if i < len(hs)]
        tv = [vs[i - len(hs)] for i in members if i >= len(hs)]
        if len(th) < 2 or len(tv) < 2:
            continue
        x0, x1 = min(h.lo for h in th), max(h.hi for h in th)
        y0, y1 = min(v.lo for v in tv), max(v.hi for v in tv)
        if x1 - x0 < 80 * zoom or y1 - y0 < 30 * zoom:
            continue
        # rows: the lines across most of the table; columns: the lines down most of it
        th = [h for h in th if h.hi - h.lo >= (x1 - x0) * 0.4]
        tv = [v for v in tv if v.hi - v.lo >= (y1 - y0) * 0.4]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        th.sort(key=lambda h: h.at(cx))
        tv.sort(key=lambda v: v.at(cy))
        # the outer border, where it was not found, is the edge of the lines
        if not th or th[0].at(cx) - y0 > tol:
            th.insert(0, _Line(0.0, y0, x0, x1))
        if th[-1].at(cx) < y1 - tol:
            th.append(_Line(0.0, y1, x0, x1))
        if not tv or tv[0].at(cy) - x0 > tol:
            tv.insert(0, _Line(0.0, x0, y0, y1))
        if tv[-1].at(cy) < x1 - tol:
            tv.append(_Line(0.0, x1, y0, y1))
        if len(th) < 3 or len(tv) < 3:
            continue                          # a box, not a table
        tables.append({"bbox": [x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom], "h": th, "v": tv, "zoom": zoom})
    return tables


def fill_table(t, pieces):
    """Put text pieces [(bbox in points, text)] into the cells of a scanned table: a piece
    goes to the cell holding its centre (rows and columns follow the slant of the lines);
    pieces of one cell are kept in reading order. Returns the rows (lists of strings) and
    the pieces used."""
    z, hl, vl = t["zoom"], t["h"], t["v"]
    R, C = len(hl) - 1, len(vl) - 1
    cells = [[[] for _ in range(C)] for _ in range(R)]
    used = []
    for bbox, text in pieces:
        cx, cy = (bbox[0] + bbox[2]) / 2 * z, (bbox[1] + bbox[3]) / 2 * z
        ys = [h.at(cx) for h in hl]
        xs = [v.at(cy) for v in vl]
        if not (ys[0] - 2 <= cy <= ys[-1] + 2 and xs[0] - 2 <= cx <= xs[-1] + 2):
            continue
        r = min(max(sum(1 for y in ys if y <= cy) - 1, 0), R - 1)
        c = min(max(sum(1 for x in xs if x <= cx) - 1, 0), C - 1)
        cells[r][c].append((bbox[1], bbox[0], text))
        used.append(tuple(bbox))
    out = []
    for r in range(R):
        line = []
        for c in range(C):
            parts = sorted(cells[r][c], key=lambda p: (round(p[0] / 4), p[1]))
            line.append(" ".join(p[2] for p in parts if p[2]).strip())
        out.append(line)
    # rows and columns empty everywhere (a doubled border) go
    keep_r = [k for k in range(R) if any(out[k])] or list(range(R))
    keep_c = [k for k in range(C) if any(out[r][k] for r in range(R))] or list(range(C))
    return [[out[r][c] for c in keep_c] for r in keep_r], used


# --------------------------------------------------------------------------- the page as items
def page_items(session, index, with_background=False):
    """Everything of a page, in displayed coordinates, sorted top to bottom:
    {'kind': 'text', 'bbox', 'block'}  digital text (a get_text block, upright)
    {'kind': 'ink', 'bbox', 'line'}    a line Calamo read on the scan
    {'kind': 'table', 'bbox', 'rows'}  a table (list of rows of strings)
    {'kind': 'image', 'bbox', 'png'}   a picture or a graphic of the scan
    Also returns the page info: {'scan': bool, 'background': png or None} where the
    background (only if asked) is the page with its text taken away, for formats that lay
    text boxes over the page (PowerPoint, web page)."""
    from .document import _shown
    from .graphics import image_role
    from .export import _tables
    s = session
    with s.lock:
        page = s.doc[index]
        R = page.rect
        scan = s._is_scan(page)
        ink = [l for l in s.ink.get(index, []) if (l.get("text") or "").strip() and l.get("source") != "unread"]
        items = []
        # digital tables (ruled with lines) and their text
        dtables = _tables(page)
        trects = [_shown(page, fitz.Rect(t.bbox)) for t in dtables]
        for t, tr in zip(dtables, trects):
            try:
                rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()]
            except Exception:
                continue
            items.append({"kind": "table", "bbox": list(tr), "rows": rows})
        d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
        blocks = []
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            lines = []
            for l in b["lines"]:
                spans = [sp for sp in l["spans"] if sp["text"]]
                if ink:                          # the scanner's own OCR: Calamo's reading replaces it
                    spans = [sp for sp in spans if sp.get("alpha", 255) > 0]
                if "".join(sp["text"] for sp in spans).strip():
                    lines.append(dict(l, spans=spans))
            if not lines:
                continue
            r = _shown(page, fitz.Rect(b["bbox"]))
            c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if any(tr.contains(c) for tr in trects):
                continue
            blocks.append((r, dict(b, lines=lines)))
        # pictures of the page content (not the scan itself, not what Calamo put there)
        pictures = []
        area = abs(R)
        for im in page.get_image_info(xrefs=True):
            x = im.get("xref")
            r = _shown(page, fitz.Rect(im["bbox"])) & R
            if not x or r.is_empty or r.width < 8 or r.height < 8:
                continue
            if image_role(s.doc, x) in ("Cover", "Ink"):
                continue
            if abs(r) > area * 0.5:
                continue                      # the scan behind the page: its content is exported instead
            try:
                pictures.append((r, s._image_png(x)))
            except Exception:
                continue
        pix = None
        if scan or with_background:
            pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False, annots=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy() if pix else None
    stables = []
    if scan and rgb is not None:
        try:
            stables = scan_tables(rgb)
        except Exception:
            stables = []
        # text inside a scanned table: Calamo's lines, else the scanner's words
        pieces = [(l["bbox"], l["text"]) for l in ink]
        if not ink:
            pieces = [(list(r), "".join(sp["text"] for l in b["lines"] for sp in l["spans"]).strip()) for r, b in blocks]
        for t in stables:
            rows, used = fill_table(t, pieces)
            if any(any(c for c in row) for row in rows):
                items.append({"kind": "table", "bbox": t["bbox"], "rows": rows})
                t["used"] = used
    used_boxes = [tuple(b) for t in stables for b in t.get("used", [])]
    for r, b in blocks:
        if tuple(r) not in used_boxes:
            items.append({"kind": "text", "bbox": list(r), "block": b})
    for l in ink:
        if tuple(l["bbox"]) not in used_boxes:
            items.append({"kind": "ink", "bbox": list(l["bbox"]), "line": l})
    for r, png in pictures:
        items.append({"kind": "image", "bbox": list(r), "png": png})
    recognised = bool(ink or blocks or stables)
    if scan and rgb is not None:
        if recognised:
            # the drawings of the scan (stamps, logos, boxes) as pictures of their own
            for g in s.graphics(index):
                if g["kind"] != "scan":
                    continue
                b = fitz.Rect(g["bbox"])
                if any(abs(b & fitz.Rect(t["bbox"])) > abs(b) * 0.5 for t in stables):
                    continue
                img = _cut(rgb, b)
                if img is not None:
                    items.append({"kind": "image", "bbox": list(b), "png": img})
        else:
            # nothing to read (a photo): the page stays a picture
            items.append({"kind": "image", "bbox": list(R), "png": _png(Image.fromarray(rgb))})
    background = None
    if with_background and rgb is not None:
        text_boxes = [it["bbox"] for it in items if it["kind"] in ("ink", "text")]
        text_boxes += [list(b) for b in used_boxes]           # the text of the scanned tables too
        background = _without_text(rgb, text_boxes)
    items.sort(key=lambda it: (round(it["bbox"][1] / 4), it["bbox"][0]))
    return items, {"scan": scan, "background": background, "size": (R.width, R.height)}


def _cut(rgb, box, zoom=ZOOM):
    """A graphic of the scan on a transparent background (the paper taken away)."""
    from .graphics import lift, paper_under
    H, W = rgb.shape[:2]
    x0, y0 = max(int(box.x0 * zoom), 0), max(int(box.y0 * zoom), 0)
    x1, y1 = min(int(round(box.x1 * zoom)), W), min(int(round(box.y1 * zoom)), H)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    paper = paper_under(rgb, (x0, y0, x1, y1), band=4)
    rgba, mask = lift(rgb, (x0, y0, x1, y1), paper)
    if not mask.any():
        return None
    return _png(Image.fromarray(rgba, "RGBA"))


def _without_text(rgb, boxes, zoom=ZOOM):
    """The page with the letters of its text lines taken away (tables, drawings, the
    paper stay), as a PNG: the background of a slide or of a web page."""
    from .letters import erase_letters
    img = rgb.copy()
    H, W = img.shape[:2]
    for b in boxes:
        x0, y0 = max(int(b[0] * zoom) - 1, 0), max(int(b[1] * zoom) - 1, 0)
        x1, y1 = min(int(round(b[2] * zoom)) + 1, W), min(int(round(b[3] * zoom)) + 1, H)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        # only the surroundings of the line: the paper is rebuilt from them
        h = y1 - y0
        cx0, cy0 = max(x0 - 2 * h, 0), max(y0 - h, 0)
        cx1, cy1 = min(x1 + 2 * h, W), min(y1 + h, H)
        ctx = img[cy0:cy1, cx0:cx1]
        try:
            patch, n, (a0, b0, a1, b1) = erase_letters(ctx, (x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0),
                                                       max((b[3] - b[1]) / 1.28, 6.0) * zoom)
        except Exception:
            patch = None
        if patch is not None:
            ctx[b0:b1, a0:a1] = patch
    return _png(Image.fromarray(img))


def text_size(line, lines, scale=1.0):
    """Font size (points) for a read line laid over its page (a slide, a web page): as tall
    as its letters, but never more than the usual line of the page (a box the OCR drew over
    two lines would come out huge) and never wider than its box."""
    x0, y0, x1, y1 = line["bbox"]
    hs = sorted(l["bbox"][3] - l["bbox"][1] for l in lines) or [y1 - y0]
    med = hs[len(hs) // 2]
    t = (line.get("text") or "").strip()
    size = min((y1 - y0) * 0.72, med * 0.85)
    if t:
        size = min(size, (x1 - x0) / (0.5 * len(t)))
    return max(5.0, size) * scale
