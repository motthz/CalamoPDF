"""PDF editing session: rendering, Word-like text editing, ink (handwriting)
editing, undo/redo and saving."""
import base64
import copy
import io
import difflib
import os
import re
import threading

import pymupdf as fitz
import numpy as np

from . import fonts as F
from . import handwriting as HW
from . import writers as WR
from .handwriting import HandwritingLibrary, PageWritings
from .lexicon import LEXICON, WORD_RE
from .editing import EditingMixin
from .ocr import PRINT_BELOW, Learning, prepare_page, read_page, read_region

REDACT_KW = dict(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
try:
    REDACT_KW["text"] = fitz.PDF_REDACT_TEXT_REMOVE
except AttributeError:
    pass


def _hex(color_int):
    return "#%06x" % (color_int & 0xFFFFFF)


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _mupdf_ctm(page):
    """MuPDF's PDF space -> displayed coordinates (what rendering uses)."""
    mb, ctm = fitz.mupdf.FzRect(), fitz.mupdf.FzMatrix()
    fitz.mupdf.pdf_page_transform(fitz._as_pdf_page(page), mb, ctm)
    return fitz.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)


def _view_matrix(page):
    """Page coordinates (drawing, text extraction) -> displayed coordinates (what the user
    sees and the OCR reads). Built like MuPDF does for rendering, so it is right also on
    pages with /Rotate and a cropbox not at the origin (where page.rotation_matrix is not)."""
    if not page.rotation:
        return fitz.Identity
    try:
        return ~page.transformation_matrix * _mupdf_ctm(page)
    except Exception:
        return page.rotation_matrix


def _unrot(page, rect):
    """Displayed coordinates -> the page's own coordinates, used to draw on it."""
    return fitz.Rect(rect) * ~_view_matrix(page)


def _shown(page, rect):
    """Text extraction / search coordinates -> displayed coordinates."""
    return fitz.Rect(rect) * page.rotation_matrix


def _to_text(page, rect):
    """Displayed coordinates -> the coordinates of text and redactions."""
    return fitz.Rect(rect) * page.derotation_matrix


def _append_words(tw, pos, text, font, size):
    """TextWriter.append without the space characters: each word is placed where it goes
    and the gaps are left empty. Windows fonts draw the space and the no-break space with
    the same glyph, and MuPDF records it as a no-break space: copied or searched, "dopo il"
    would not be "dopo il". Readers put real spaces back from the gaps."""
    x, y = pos
    for part in re.split(r"( +)", text):
        if part and not part.startswith(" "):
            tw.append((x, y), part, font=font, fontsize=size)
        x += font.text_length(part, fontsize=size)
    return x


def _write_upright(page, tw, opacity=-1, pre=None):
    """Write a TextWriter filled in displayed coordinates so the text lands exactly there,
    reading horizontally, whatever the page rotation and cropbox.

    write_text puts the text in PDF space with its own matrix (P -> P * tw.ctm), then
    prepends a cropbox shift; we add the matrix that maps all of that back onto the view."""
    try:
        cb, mb = page.cropbox_position, page.mediabox
        delta = page.rect.height - page.rect.width if page.rotation in (90, 270) else 0
        shift = fitz.Identity
        if bool(cb) or mb.y0 != 0 or delta != 0:
            shift = fitz.Matrix(1, 0, 0, 1, cb.x, cb.y + mb.y0 - delta)
        m = ~tw.ctm * ~_mupdf_ctm(page) * ~shift
    except Exception:
        return tw.write_text(page, opacity=opacity)
    if pre is not None:                       # e.g. a watermark turned around its centre
        m = pre * m
    if abs(m.a - 1) + abs(m.b) + abs(m.c) + abs(m.d - 1) + abs(m.e) + abs(m.f) < 1e-6:
        return tw.write_text(page, opacity=opacity)
    tw.write_text(page, matrix=m, opacity=opacity)


def _write_hidden(page, tw):
    """Invisible (searchable) text, placed in displayed coordinates like _write_upright."""
    if not page.rotation and not bool(page.cropbox_position) and page.mediabox.y0 == 0:
        return tw.write_text(page, render_mode=3)
    try:
        cb, mb = page.cropbox_position, page.mediabox
        delta = page.rect.height - page.rect.width if page.rotation in (90, 270) else 0
        shift = fitz.Identity
        if bool(cb) or mb.y0 != 0 or delta != 0:
            shift = fitz.Matrix(1, 0, 0, 1, cb.x, cb.y + mb.y0 - delta)
        tw.write_text(page, matrix=~tw.ctm * ~_mupdf_ctm(page) * ~shift, render_mode=3)
    except Exception:
        pass


def _image_upright(page, rect, png):
    page.insert_image(_unrot(page, rect), stream=png, overlay=True, rotate=page.rotation)


def _stack(pngs):
    """Preview of a paragraph: the images of its lines one under the other."""
    from PIL import Image
    imgs = [Image.open(io.BytesIO(p)) if p else None for p in pngs]
    h = max(i.height for i in imgs if i)
    W = max(i.width for i in imgs if i)
    out = Image.new("RGBA", (W, int(h * 1.15) * len(imgs)), (0, 0, 0, 0))
    for k, i in enumerate(imgs):
        if i:
            out.paste(i, (0, int(h * 1.15) * k))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def _overlap(a, b):
    """Share of box a covered by box b."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
    return ix * iy / area


def _norm(t):
    return "".join(c for c in t.lower() if c.isalnum() or c == " ").strip()


def _find_in_line(line, term):
    """Where a term appears in a read line: exact matches, and - since handwriting readings
    contain mistakes - words that are almost the same ("Rassi" finds "Rossi").
    Returns [(bbox, fuzzy)] with the box of the matching words."""
    words = [w for w in line.get("words") or [] if w.get("text")]
    toks = line["text"].split()
    if [w["text"] for w in words] != toks:
        # the text was corrected: spread its words over the line by their length
        x0, y0, x1, y1 = line["bbox"]
        total, pos, words = max(len(line["text"]), 1), 0, []
        for t in toks:
            a = line["text"].index(t, pos)
            pos = a + len(t)
            words.append({"text": t, "bbox": [x0 + (x1 - x0) * a / total, y0, x0 + (x1 - x0) * pos / total, y1]})
    q = _norm(term)
    if not q:
        return []
    n = max(1, len(q.split()))
    out = []
    for k in range(len(words)):
        span = words[k:k + n]
        if len(span) < n and k:
            break
        text = _norm(" ".join(w["text"] for w in span))
        bbox = [min(w["bbox"][0] for w in span), min(w["bbox"][1] for w in span),
                max(w["bbox"][2] for w in span), max(w["bbox"][3] for w in span)]
        if q in text:
            out.append((bbox, False))
        elif len(q) >= 4 and abs(len(text) - len(q)) <= max(2, len(q) // 3):
            # OCR look-alikes cost less (see Lexicon.distance)
            if LEXICON.distance(text, q) <= max(1.0, len(q) * 0.25):
                out.append((bbox, True))
    return out


class Session(EditingMixin):
    MAX_UNDO = 40

    def __init__(self):
        self.lock = threading.RLock()
        self.doc = None
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.ink = {}            # page index -> [ink line]
        self.hands = {}          # page index -> PageWritings: the people / typefaces of each page
        self.ignored = {}        # page index -> [rect]: "this is not writing", said by the user
        self.learning = Learning()
        self.resolver = None
        self.version = 0         # bumps on every change, per page versions below
        self.page_versions = {}

    # ------------------------------------------------------------------ lifecycle
    def open(self, path, password=None):
        with self.lock:
            with open(path, "rb") as f:
                data = f.read()
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.needs_pass:
                if not password:
                    raise RuntimeError("PASSWORD_REQUIRED")
                if not doc.authenticate(password):
                    raise RuntimeError("PASSWORD_WRONG")
            self.doc, self.path = doc, path
            self._password = password
            self.dirty = False
            self.undo_stack, self.redo_stack = [], []
            self.ink, self.page_versions, self.hands = {}, {}, {}
            self.ignored = {}
            self.resolver = F.FontResolver(doc)
            self.version += 1
            return self.info()

    def new_blank(self):
        with self.lock:
            doc = fitz.open()
            doc.new_page(width=595, height=842)
            self.doc, self.path = doc, None
            self.dirty = True
            self.undo_stack, self.redo_stack = [], []
            self.ink, self.page_versions, self.hands = {}, {}, {}
            self.ignored = {}
            self.resolver = F.FontResolver(doc)
            self.version += 1
            return self.info()

    def info(self):
        pages = [{"w": p.rect.width, "h": p.rect.height, "v": self.page_versions.get(i, 0)}
                 for i, p in enumerate(self.doc)]
        return {"path": self.path, "name": os.path.basename(self.path) if self.path else "Senza titolo.pdf",
                "pages": pages, "dirty": self.dirty, "canUndo": bool(self.undo_stack),
                "canRedo": bool(self.redo_stack), "analyzed": sorted(self.ink.keys())}

    def save(self, path=None):
        with self.lock:
            target = path or self.path
            if not target:
                raise RuntimeError("Nessun percorso di salvataggio.")
            tmp = target + ".calamo-tmp"
            self.doc.save(tmp, garbage=3, deflate=True, clean=False)
            os.replace(tmp, target)
            # saving compacts the document in memory and renumbers its objects; PyMuPDF's
            # record of the fonts already written then points at the wrong objects, and text
            # written after the save would be unreadable when copied or searched. Go on from
            # the saved file instead.
            self._reload(target)
            self.path = target
            self.dirty = False
            return self.info()

    def _reload(self, path):
        with open(path, "rb") as f:
            doc = fitz.open(stream=f.read(), filetype="pdf")
        if doc.needs_pass and not doc.authenticate(getattr(self, "_password", None) or ""):
            return                                  # keep working on the document in memory
        self.doc = doc
        self.resolver = F.FontResolver(doc)

    # ------------------------------------------------------------------ undo
    def _snapshot(self, page=None):
        """What undo needs to go back. An edit on one page saves only that page (fast and
        small even on big documents); page operations, forms and links save everything."""
        if page is not None and self._page_only_ok(page):
            one = fitz.open()
            one.insert_pdf(self.doc, from_page=page, to_page=page, links=False, annots=True)
            return ("page", page, one.tobytes(garbage=1, deflate=True), copy.deepcopy(self.ink.get(page)),
                    self.hands.get(page), self.page_versions.get(page, 0), list(self.ignored.get(page, [])))
        return ("doc", self.doc.tobytes(), copy.deepcopy(self.ink), dict(self.page_versions), dict(self.hands))

    def _page_only_ok(self, page):
        try:
            p = self.doc[page]
            return (len(self.doc) > 1 and not self.doc.is_form_pdf and p.first_link is None
                    and not self.doc.get_toc())
        except Exception:
            return False

    def _checkpoint(self, page=None):
        self.undo_stack.append(self._snapshot(page))
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _touch(self, *pages):
        self.dirty = True
        self.version += 1
        for p in pages:
            self.page_versions[p] = self.page_versions.get(p, 0) + 1

    def _restore(self, src, dst):
        if not src:
            return self.info()
        snap = src.pop()
        if snap[0] == "page":
            _, i, data, ink, hands, ver, ignored = snap
            self.ignored[i] = ignored
            dst.append(self._snapshot(i))
            one = fitz.open(stream=data, filetype="pdf")
            self.doc.delete_page(i)
            self.doc.insert_pdf(one, start_at=i, links=False, annots=True)
            for store, value in ((self.ink, ink), (self.hands, hands)):
                if value is None:
                    store.pop(i, None)
                else:
                    store[i] = value
            self.page_versions[i] = max(ver, self.page_versions.get(i, 0)) + 1
            if self.ink.get(i):
                try:
                    self._relearn(i)
                except Exception:
                    pass
        else:
            dst.append(self._snapshot())
            _, data, ink, versions, self.hands = snap
            self.doc = fitz.open(stream=data, filetype="pdf")
            self.resolver = F.FontResolver(self.doc)
            self.ink = ink
            # every page gets a new version so the UI re-renders it
            self.page_versions = {i: max(versions.get(i, 0), self.page_versions.get(i, 0)) + 1
                                  for i in range(len(self.doc))}
        self.dirty = True
        self.version += 1
        return self.info()

    def undo(self):
        with self.lock:
            return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        with self.lock:
            return self._restore(self.redo_stack, self.undo_stack)

    # ------------------------------------------------------------------ rendering
    def render(self, index, scale, fmt="png"):
        with self.lock:
            page = self.doc[index]
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=True)
            data = pix.tobytes("jpeg", jpg_quality=88) if fmt == "jpeg" else pix.tobytes("png")
            mime = "image/jpeg" if fmt == "jpeg" else "image/png"
            return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))

    def _background(self, page, rect):
        """Median paper colour in a ring just outside rect."""
        ring = fitz.Rect(rect.x0 - 4, rect.y0 - 4, rect.x1 + 4, rect.y1 + 4) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=ring, alpha=False)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        if a.size == 0:
            return (1, 1, 1)
        edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        lum = edge.mean(axis=1)
        light = edge[lum >= np.percentile(lum, 40)]
        c = np.median(light if len(light) else edge, axis=0) / 255.0
        return tuple(float(v) for v in c)

    def _cover_letters(self, page, rect, zoom=3.0):
        """Remove only the letters inside rect: the paper under them is rebuilt from what
        surrounds them, so a highlighter, the borders of a table, the rules of a notebook
        and the shading of a scan go on as before. False when there were no letters."""
        rect = fitz.Rect(rect) & page.rect
        if rect.is_empty:
            return False
        m = max(rect.height * 1.5, 14)
        ctx = fitz.Rect(rect.x0 - 2 * m, rect.y0 - m, rect.x1 + 2 * m, rect.y1 + m) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=ctx, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        # the rendered pixels start at a whole pixel: work from where they really are
        ox, oy = pix.x / zoom, pix.y / zoom
        x0, y0 = int((rect.x0 - ox) * zoom), int((rect.y0 - oy) * zoom)
        x1, y1 = int(round((rect.x1 - ox) * zoom)), int(round((rect.y1 - oy) * zoom))
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, rgb.shape[1]), min(y1, rgb.shape[0])
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        from .letters import erase_letters
        patch, n, (x0, y0, x1, y1) = erase_letters(rgb, (x0, y0, x1, y1), max(rect.height / 1.28, 9.0) * zoom)
        if patch is None:
            return False
        from PIL import Image
        buf = io.BytesIO()
        img = Image.fromarray(patch, "RGB")
        # twice the pixels: drawing it back on the page then hardly softens anything
        img.resize((img.width * 2, img.height * 2), Image.BICUBIC).save(buf, "PNG")
        target = fitz.Rect(ox + x0 / zoom, oy + y0 / zoom, ox + x1 / zoom, oy + y1 / zoom)
        page.insert_image(_unrot(page, target), stream=buf.getvalue(), rotate=page.rotation, overlay=True)
        return True

    def _cell_right(self, page, r, zoom=2.0):
        """Right border of the table cell holding the line r (displayed coordinates), or
        None: rewritten text must stay inside its cell."""
        h = r.height
        ctx = fitz.Rect(r.x0, r.y0 - h, page.rect.x1, r.y1 + h) & page.rect
        if ctx.is_empty:
            return None
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=ctx, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        from .letters import cell_right
        row = (int((r.y0 - ctx.y0) * zoom), int((r.y1 - ctx.y0) * zoom))
        x = cell_right(rgb, row, int((r.x1 - ctx.x0) * zoom) - 2, h / 1.28 * zoom)
        return ctx.x0 + x / zoom if x is not None else None

    def _border_below(self, page, x0, x1, y0, y1, zoom=2.0):
        """A horizontal table border between y0 and y1 (displayed coordinates)."""
        r = fitz.Rect(x0, y0, x1, y1) & page.rect
        if r.is_empty or r.width < 8:
            return False
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=r, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        from .letters import analyse
        letters, vert, horiz, ink = analyse(rgb, (y1 - y0) / 3 * zoom)
        return bool(horiz.any())

    def _cover(self, page, rect, zoom=3.0):
        """Hide what is inside rect (displayed coordinates) by rebuilding the paper around
        it: every row continues the colour found left and right of the area, so ruled
        lines, shading and the grain of a scan go on under the removed writing. A plain
        white page simply gets a white rectangle."""
        rect = fitz.Rect(rect) & page.rect
        if rect.is_empty:
            return
        m = max(4.0, min(rect.height * 0.8, 14.0))
        ring = fitz.Rect(rect.x0 - m, rect.y0, rect.x1 + m, rect.y1) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=ring, alpha=False)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        H, W = a.shape[:2]
        lx = int(round((rect.x0 - ring.x0) * zoom))
        rx = int(round((rect.x1 - ring.x0) * zoom))
        bw = max(int(m * zoom * 0.8), 3)
        left = a[:, max(lx - bw, 0):lx].astype(np.float32)
        right = a[:, rx:min(rx + bw, W)].astype(np.float32)
        sides = [x for x in (left, right) if x.shape[1] >= 2]
        if H < 2 or not sides:
            page.draw_rect(_unrot(page, rect), color=None, fill=self._background(page, rect), overlay=True)
            return
        border = np.concatenate(sides, axis=1)
        if float(border.std()) < 1.5:                    # flat paper (a digital page)
            c = tuple(float(v) / 255 for v in np.median(border.reshape(-1, 3), axis=0))
            page.draw_rect(_unrot(page, rect), color=None, fill=c, overlay=True)
            return
        # each side's colour per row; a word touching the area is sparse in a row, the median ignores it
        cols = [np.median(x, axis=1) for x in sides]
        # rows hit by neighbouring writing are much darker than the paper: use the lighter side
        if len(cols) == 2:
            lum = [c.mean(axis=1) for c in cols]
            gap = lum[0] - lum[1]
            cols = [np.where((gap < -40)[:, None], cols[1], cols[0]), np.where((gap > 40)[:, None], cols[0], cols[1])]
        w = rx - lx
        t = np.linspace(0, 1, max(w, 1))[None, :, None]
        fill = cols[0][:, None, :] * (1 - t) + cols[-1][:, None, :] * t
        # the grain of the paper: small deviations of the border pixels, reused at random
        dev = border - np.median(border, axis=1, keepdims=True)
        dev = dev[(np.abs(dev).max(axis=2) < 14)]
        if len(dev):
            rng = np.random.default_rng(0)
            fill = fill + dev[rng.integers(0, len(dev), size=fill.shape[:2])]
        patch = np.clip(fill, 0, 255).astype(np.uint8)
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(patch).save(buf, "JPEG", quality=92)
        target = fitz.Rect(ring.x0 + lx / zoom, ring.y0, ring.x0 + rx / zoom, ring.y1)
        page.insert_image(_unrot(page, target), stream=buf.getvalue(), rotate=page.rotation, overlay=True)

    # ------------------------------------------------------------------ text model
    TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP

    @staticmethod
    def _line_list(b, page):
        """Lines of a block in displayed coordinates. Only text that reads horizontally
        on screen is editable in place (on a rotated page that is rotated text)."""
        upright = fitz.Point(1, 0) * fitz.Matrix(-page.rotation)
        lines = []
        for l in b["lines"]:
            d = l.get("dir", (1, 0))
            if d[0] * upright.x + d[1] * upright.y < 0.99:
                return []
            # invisible text (the OCR layer of scanners, or of print rewritten by Calamo) is
            # not something the user sees, so it is not offered for editing
            spans = [dict(s, origin=tuple(fitz.Point(s["origin"]) * page.rotation_matrix))
                     for s in l["spans"] if s["text"] and s.get("alpha", 255) > 0]
            if spans:
                lines.append({"bbox": _shown(page, l["bbox"]), "spans": spans,
                              "text": "".join(s["text"] for s in spans)})
        return lines

    @staticmethod
    def _paragraph(lines, block_rect):
        """Join visual lines into paragraph text. Returns (text, styles, starts)
        where styles[i] is the span of char i and starts[j] the char offset of line j."""
        chars, styles, starts = [], [], []
        for j, l in enumerate(lines):
            starts.append(len(chars))
            for s in l["spans"]:
                for c in s["text"]:
                    chars.append(c)
                    styles.append(s)
            if j == len(lines) - 1:
                break
            txt = l["text"].rstrip()
            full = l["bbox"].width >= block_rect.width * 0.82
            # drop trailing spaces of the visual line
            while chars and chars[-1] == " ":
                chars.pop()
                styles.pop()
            if full and txt.endswith("-") and len(txt) > 1 and txt[-2].isalpha():
                chars.pop()
                styles.pop()
            elif full:
                chars.append(" ")
                styles.append(l["spans"][-1])
            else:
                chars.append("\n")
                styles.append(l["spans"][-1])
        return "".join(chars), styles, starts

    def blocks(self, index):
        with self.lock:
            page = self.doc[index]
            d = page.get_text("dict", flags=self.TEXT_FLAGS)
            out = []
            for bi, b in enumerate(d["blocks"]):
                if b.get("type") != 0:
                    continue
                lines = self._line_list(b, page)
                if not lines or not "".join(x["text"] for x in lines).strip():
                    continue
                brect = _shown(page, b["bbox"])
                text, _, _ = self._paragraph(lines, brect)
                spans = [s for l in lines for s in l["spans"] if s["text"].strip()] or lines[0]["spans"]
                main = max(spans, key=lambda s: len(s["text"]))
                bold, italic, serif, mono = F.style_from(F.clean_name(main["font"]), main["flags"])
                o0, o1 = lines[0]["spans"][0]["origin"], lines[-1]["spans"][0]["origin"]
                lh = (o1[1] - o0[1]) / (len(lines) - 1) if len(lines) > 1 else main["size"] * 1.2
                out.append({
                    "id": bi, "bbox": list(brect), "text": text, "nlines": len(lines),
                    "font": F.clean_name(main["font"]), "css": F.css_family(main["font"], main["flags"]),
                    "size": round(main["size"], 2), "color": _hex(main["color"]),
                    "bold": bold, "italic": italic, "lineHeight": lh,
                    "align": self._align(lines[0]["bbox"], brect) if len(lines) == 1 else "left",
                    "firstBaseline": o0[1],
                })
            styles = self.hands[index].listing(self.ink.get(index, [])) if index in self.hands else []
            return {"blocks": out, "ink": self._ink_public(index), "styles": styles,
                    "objects": self.objects(index), "version": self.page_versions.get(index, 0)}

    def _raw_block(self, page, ref):
        """ref: block bbox (preferred - indices shift after edits) or index."""
        d = page.get_text("dict", flags=self.TEXT_FLAGS)
        if isinstance(ref, (list, tuple)):
            target = fitz.Rect(ref)
            b, score = None, 0.0
            for cand in d["blocks"]:
                if cand.get("type") != 0:
                    continue
                r = _shown(page, cand["bbox"])
                inter = r & target
                if inter.is_empty:
                    continue
                iou = inter.get_area() / max(r.get_area() + target.get_area() - inter.get_area(), 1e-6)
                if iou > score:
                    b, score = cand, iou
            if b is None or score < 0.5:
                raise RuntimeError("Il testo è cambiato nel frattempo: riprova.")
        else:
            b = d["blocks"][ref]
        return b, self._line_list(b, page)

    @staticmethod
    def _runs(text, styles):
        runs = []
        for c, s in zip(text, styles):
            if runs and runs[-1][1] is s:
                runs[-1][0] += c
            else:
                runs.append([c, s])
        return [(t, s) for t, s in runs]

    def _font_for(self, page, s, text, override):
        return self.resolver.resolve(page, s["font"], s["flags"], text,
                                     override.get("family"), override.get("bold"), override.get("italic"))

    def _measure(self, page, runs, override):
        w = 0.0
        for text, s in runs:
            if text:
                font = self._font_for(page, s, text, override)
                w += font.text_length(text, fontsize=override.get("size") or s["size"])
        return w

    def _write_runs(self, page, writers, x, y, runs, override):
        for text, s in runs:
            if not text:
                continue
            font = self._font_for(page, s, text, override)
            size = override.get("size") or s["size"]
            color = _rgb(override["color"]) if override.get("color") else \
                tuple(((s["color"] >> k) & 255) / 255 for k in (16, 8, 0))
            tw = fitz.TextWriter(page.rect, color=color)
            _append_words(tw, (x, y), text, font, size)
            writers.append(tw)
            x += font.text_length(text, fontsize=size)
        return x

    def _take_line(self, page, text, styles, pos, width, override):
        """Greedy word wrap: returns end offset of the line starting at pos and
        the offset where the next line starts."""
        n = len(text)
        nl = text.find("\n", pos)
        hard_end = n if nl < 0 else nl
        end, i = pos, pos
        while i < hard_end:
            j = i
            while j < hard_end and text[j] == " ":
                j += 1
            k = j
            while k < hard_end and text[k] != " ":
                k += 1
            seg_w = self._measure(page, self._runs(text[pos:k], styles[pos:k]), override)
            if seg_w > width and end > pos:
                nxt = end
                while nxt < hard_end and text[nxt] == " ":
                    nxt += 1
                return end, nxt
            end = i = k
        return hard_end, (hard_end + 1 if hard_end < n else n)

    def edit_block(self, index, block_ref, new_text, style=None, checkpoint=True):
        """Replace the text of a block like a word processor: only the lines that
        change are rewritten, inline styles are preserved, paragraphs re-wrap.
        style: optional overrides {family, size, color, bold, italic, align} for the whole block."""
        style = {k: v for k, v in (style or {}).items() if v not in (None, "", "Originale")}
        new_text = new_text.replace("\r", "")
        with self.lock:
            page = self.doc[index]
            b, lines = self._raw_block(page, block_ref)
            if not lines:
                raise RuntimeError("Questo testo non è modificabile (ruotato).")
            brect = _shown(page, b["bbox"])
            old, old_styles, starts = self._paragraph(lines, brect)
            if new_text == old and not style:
                return self.blocks(index)
            if checkpoint:
                self._checkpoint(index)

            # carry styles through the edit
            p = 0
            while p < min(len(old), len(new_text)) and old[p] == new_text[p]:
                p += 1
            q = 0
            while q < min(len(old), len(new_text)) - p and old[-1 - q] == new_text[-1 - q]:
                q += 1
            ins = new_text[p:len(new_text) - q]
            ref_style = old_styles[p - 1] if p > 0 else (old_styles[0] if old_styles else lines[0]["spans"][0])
            if p < len(old) and p > 0 and old[p - 1] in " \n":
                ref_style = old_styles[p]
            styles = old_styles[:p] + [ref_style] * len(ins) + old_styles[len(old) - q:]
            shift = len(new_text) - len(old)

            # geometry
            first = lines[0]["spans"][0]
            size_ratio = (style["size"] / first["size"]) if style.get("size") else 1.0
            if len(lines) > 1:
                lh = (lines[-1]["spans"][0]["origin"][1] - first["origin"][1]) / (len(lines) - 1)
            else:
                lh = first["size"] * 1.22
            lh *= size_ratio
            single = len(lines) == 1
            restyle = bool(style)
            first_dirty = 0 if restyle else max(0, max(j for j, st in enumerate(starts) if st <= p))
            # a change that touches the start of a line can pull words back onto the previous line
            if not restyle and first_dirty > 0 and p - starts[first_dirty] < 1:
                first_dirty -= 1

            writers, redact = [], []
            pos = starts[first_dirty]
            j = first_dirty
            while pos < len(new_text) or j == first_dirty:
                if j < len(lines):
                    L = lines[j]
                    y = first["origin"][1] + (L["spans"][0]["origin"][1] - first["origin"][1]) * size_ratio
                    x = L["spans"][0]["origin"][0]
                    align = style.get("align") or self._align(L["bbox"], brect)
                else:
                    y = first["origin"][1] + (lines[-1]["spans"][0]["origin"][1] - first["origin"][1]) * size_ratio \
                        + lh * (j - len(lines) + 1)
                    x = brect.x0
                    align = style.get("align") or "left"
                if align != "left":
                    x = brect.x0
                limit = (page.rect.x1 - 36 - x) if single else (brect.x1 - x + 1.5)
                limit = max(limit, 30)
                end, nxt = self._take_line(page, new_text, styles, pos, limit, style)
                # re-synchronised with the original layout? keep the rest untouched
                if not restyle and j > first_dirty and j < len(lines) and pos - shift == starts[j] \
                        and new_text[pos:] == old[starts[j]:]:
                    break
                runs = self._runs(new_text[pos:end].rstrip(" "), styles[pos:end])
                if j < len(lines):
                    redact.append(lines[j]["bbox"])
                w = self._measure(page, runs, style)
                if align == "center":
                    x = brect.x0 + (brect.width - w) / 2
                elif align == "right":
                    x = brect.x1 - w
                writers.append((x, y, runs))
                pos = nxt
                j += 1
                if pos >= len(new_text):
                    # the paragraph got shorter: clear the remaining old lines
                    for k in range(j, len(lines)):
                        redact.append(lines[k]["bbox"])
                    break
            for r in redact:
                page.add_redact_annot(_to_text(page, self._shrink(r)))
            if redact:
                page.apply_redactions(**REDACT_KW)
            tws = []
            for x, y, runs in writers:
                self._write_runs(page, tws, x, y, runs, style)
            for tw in tws:
                _write_upright(page, tw)
            self._touch(index)
            res = self.blocks(index)
            grown = len(writers) + first_dirty > len(lines)
            res["warning"] = "Il paragrafo è più lungo dell'originale: controlla che non si sovrapponga al testo sotto." \
                if grown and not single else None
            return res

    @staticmethod
    def _shrink(r):
        h = r.height
        return fitz.Rect(r.x0, r.y0 + h * 0.18, r.x1, r.y1 - h * 0.18)

    @staticmethod
    def _align(line_rect, block_rect):
        if block_rect.width - line_rect.width < 6:
            return "left"
        lc, bc = (line_rect.x0 + line_rect.x1) / 2, (block_rect.x0 + block_rect.x1) / 2
        if abs(lc - bc) < 2.5 and line_rect.x0 - block_rect.x0 > 6:
            return "center"
        if abs(line_rect.x1 - block_rect.x1) < 2 and line_rect.x0 - block_rect.x0 > 6:
            return "right"
        return "left"

    def delete_block(self, index, block_id):
        with self.lock:
            page = self.doc[index]
            b, lines = self._raw_block(page, block_id)
            self._checkpoint(index)
            for l in lines:
                page.add_redact_annot(_to_text(page, self._shrink(l["bbox"])))
            page.apply_redactions(**REDACT_KW)
            self._touch(index)
            return self.blocks(index)

    def add_text(self, index, x, y, text, style):
        with self.lock:
            page = self.doc[index]
            self._checkpoint(index)
            family = style.get("family") or "Arial"
            if family == "Originale":
                family = "Arial"
            font, _ = F.system_font(family, bool(style.get("bold")), bool(style.get("italic")))
            size = float(style.get("size") or 12)
            tw = fitz.TextWriter(page.rect, color=_rgb(style.get("color") or "#111111"))
            for k, line in enumerate(text.replace("\r", "").split("\n")):
                _append_words(tw, (x, y + size * 0.8 + size * 1.25 * k), line, font, size)
            _write_upright(page, tw)
            self._touch(index)
            return self.blocks(index)

    def erase_area(self, index, rect):
        """Whiteout an arbitrary area (paper colour) - useful for scans."""
        with self.lock:
            page = self.doc[index]
            r = fitz.Rect(rect)
            self._checkpoint(index)
            page.add_redact_annot(_to_text(page, r), fill=False)
            page.apply_redactions(**REDACT_KW)
            self._cover(page, r)
            self._touch(index)
            return self.blocks(index)

    # ------------------------------------------------------------------ search / replace
    def search(self, term):
        with self.lock:
            hits = []
            if not term:
                return hits
            for i, page in enumerate(self.doc):
                for r in page.search_for(term):
                    r = _shown(page, r)
                    hits.append({"page": i, "bbox": [r.x0, r.y0, r.x1, r.y1], "kind": "text"})
                for j, l in enumerate(self.ink.get(i, [])):
                    for bbox, fuzzy in _find_in_line(l, term):
                        hits.append({"page": i, "bbox": bbox, "kind": "ink", "id": j, "fuzzy": fuzzy})
            return hits

    def replace_all(self, term, repl):
        with self.lock:
            if not term:
                return {"count": 0, "info": self.info()}
            count, touched = 0, []
            self._checkpoint()
            for i, page in enumerate(self.doc):
                d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES)
                targets = []
                for bi, b in enumerate(d["blocks"]):
                    if b.get("type") != 0:
                        continue
                    txt = "\n".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]
                                    if any(s["text"] for s in l["spans"]))
                    if term in txt:
                        targets.append((bi, txt))
                if not targets:
                    continue
                for bi, txt in targets:
                    count += txt.count(term)
                    self.edit_block(i, list(_shown(page, d["blocks"][bi]["bbox"])), txt.replace(term, repl), checkpoint=False)
                    touched.append(i)
            if not touched:
                self.undo_stack.pop()
            return {"count": count, "info": self.info()}

    # ------------------------------------------------------------------ ink / handwriting
    def _ink_public(self, index):
        out = []
        for j, l in enumerate(self.ink.get(index, [])):
            out.append({"id": j, "bbox": l["bbox"], "text": l["text"], "raw": l["raw"],
                        "source": l["source"], "doubt": l["doubt"], "synthetic": l.get("synthetic", False),
                        "kind": l.get("kind", "hand"), "style": l.get("style")})
        return out

    def _result(self, index, sid=None, **extra):
        """What the UI needs after a change: the lines, and the writings of the page."""
        return {"ink": self._ink_public(index), **self.hand_info(index, sid), **extra}

    def _doc_words(self, index):
        """Words already on the page (digital text and confirmed readings): the dictionary
        prefers them, so names and terms of the document are read the same everywhere."""
        words = set()
        try:
            for w in self.doc[index].get_text("words"):
                words.add(w[4].lower().strip(".,;:!?()\"'"))
        except Exception:
            pass
        for lines in self.ink.values():
            for l in lines:
                if l.get("source") == "user":
                    words.update(t.lower().strip(".,;:!?()\"'") for t in l["text"].split())
        return words

    def analyze(self, index, lang=None, include_native=False, reader=None, cancelled=None):
        """Read a page. The slow part (OCR, handwriting model) runs WITHOUT the document lock,
        so the user can keep scrolling and working while pages are being read."""
        with self.lock:
            doc = self.doc
            job = prepare_page(doc[index], include_native)
            version = self.page_versions.get(index, 0)
            prefer = self._doc_words(index)
        lines = read_page(job, self.learning, lang, reader, LEXICON, prefer, cancelled)
        with self.lock:
            if self.doc is not doc:
                raise RuntimeError("Il documento è cambiato durante la lettura.")
            if self.page_versions.get(index, 0) != version:
                raise RuntimeError(f"La pagina {index + 1} è stata modificata durante la lettura: leggila di nuovo.")
            # what the user said is not writing stays out; what they outlined by hand stays in
            lines = [l for l in lines if not self._is_ignored(index, l["bbox"])]
            kept = [l for l in self.ink.get(index, []) if l.get("marked") and l.get("feat")]
            lines = [l for l in lines if not any(_overlap(l["bbox"], k["bbox"]) > 0.5 for k in kept)] + kept
            old = self.hands.get(index)
            pw = self.hands[index] = PageWritings()
            # lines written the same way (same pen, stroke, slant, size) belong to one writing
            groups = WR.group([l["feat"] for l in lines], boxes=[l["bbox"] for l in lines]) if lines else []
            for g in sorted(set(groups)):
                members = [l for l, k in zip(lines, groups) if k == g]
                # printed or handwritten is decided for the whole writing, so it is coherent
                score = float(np.mean([l["handScore"] for l in members]))
                kind = "hand" if score >= PRINT_BELOW else "print"
                feat = WR._mean([l["feat"] for l in members])
                sid = pw.new(kind, feat)
                for l in members:
                    l["style"], l["kind"] = sid, kind
            self.ink[index] = lines
            self._carry_over(old, pw)
            self._relearn(index)
            return self._result(index, read=sum(1 for l in lines if l["source"] != "unread"),
                                model=sum(1 for l in lines if l["source"] == "model"))

    def _is_ignored(self, index, bbox):
        return any(_overlap(bbox, r) > 0.6 for r in self.ignored.get(index, []))

    def remove_ink(self, index, line_id):
        """'This is not writing': the line goes, and a new reading will not bring it back."""
        with self.lock:
            lines = self.ink.get(index, [])
            line = lines[line_id]
            if line.get("synthetic"):
                raise RuntimeError("Questa riga è stata scritta con Calamo: per toglierla usa la gomma.")
            self._checkpoint(index)
            del lines[line_id]
            self.ignored.setdefault(index, []).append(list(line["bbox"]))
            self._lines_removed(index, [line_id])
            self._relearn(index)
            self.writings(index).drop_empty({l.get("style") for l in lines})
            return self._result(index)

    def _lines_removed(self, index, removed):
        """Letters marked on the lines that follow keep pointing to their own line."""
        removed = sorted(removed)
        for lib in self.writings(index).libs.values():
            for ch in list(lib.manual):
                gs = []
                for g in lib.manual[ch]:
                    if g.src and g.src[0] == "manual":
                        if g.src[1] in removed:
                            continue
                        g.src = ("manual", g.src[1] - sum(1 for r in removed if r < g.src[1]))
                    gs.append(g)
                if gs:
                    lib.manual[ch] = gs
                else:
                    del lib.manual[ch]
            lib._font = None

    def mark_ink(self, index, rect, lang=None, reader=None):
        """Writing the reading missed, outlined by the user: read it and add it to the page,
        in the writing of the person whose hand it looks like."""
        with self.lock:
            doc = self.doc
            job = prepare_page(doc[index], True)
            prefer = self._doc_words(index)
            hs = [l["bbox"][3] - l["bbox"][1] for l in self.ink.get(index, []) if not l.get("synthetic")]
            line_h = float(np.median(hs)) if hs else None
        new = read_region(job, [rect], self.learning, lang, reader, LEXICON, prefer, line_h)
        with self.lock:
            if self.doc is not doc:
                raise RuntimeError("Il documento è cambiato durante la lettura.")
            self._checkpoint(index)
            lines = self.ink.setdefault(index, [])
            r = fitz.Rect(rect)
            # lines of the automatic reading inside the outline are replaced by it
            drop = [j for j, l in enumerate(lines) if not l.get("synthetic")
                    and r.contains(fitz.Point((l["bbox"][0] + l["bbox"][2]) / 2, (l["bbox"][1] + l["bbox"][3]) / 2))]
            if drop:
                for j in reversed(drop):
                    del lines[j]
                self._lines_removed(index, drop)
            self.ignored[index] = [g for g in self.ignored.get(index, []) if _overlap(g, list(rect)) < 0.3]
            pw = self.writings(index)
            cents = [(lib.feat, lib.kind) for lib in pw.libs.values() if lib.feat is not None]
            sids = [sid for sid, lib in pw.libs.items() if lib.feat is not None]
            added = []
            for l in new:
                i, d = WR.closest(l["feat"], cents) if cents else (None, None)
                if i is not None and d is not None and d < 1.3:
                    sid = sids[i]
                else:
                    sid = pw.new(l["kind"], l["feat"])
                    cents.append((l["feat"], l["kind"]))
                    sids.append(sid)
                l["style"], l["kind"] = sid, pw.get(sid).kind
                lines.append(l)
                added.append(len(lines) - 1)
            self._relearn(index)
            pw.drop_empty({l.get("style") for l in lines})
            return self._result(index, added=added)

    @staticmethod
    def _carry_over(old, pw):
        """Letters marked, names and loaded calligraphies survive a new reading of the page."""
        if not old:
            return
        cents = [(lib.feat, lib.kind) for lib in pw.libs.values()]
        sids = list(pw.libs)
        for lib in old.libs.values():
            if not (lib.manual or lib.loaded or not lib.auto_name) or lib.feat is None or not cents:
                continue
            i, d = WR.closest(lib.feat, cents, lib.kind)
            if i is None or d > 1.5:
                continue
            new = pw.libs[sids[i]]
            for ch, gs in lib.manual.items():
                new.manual.setdefault(ch, []).extend(gs)
            if lib.loaded:
                new.loaded, new.loaded_name = lib.loaded, lib.loaded_name
                new.loaded_ink, new.loaded_height = getattr(lib, "loaded_ink", None), getattr(lib, "loaded_height", None)
            if not lib.auto_name:
                new.name, new.auto_name = lib.name, False

    def _relearn(self, index, sids=None):
        """Learn every writing of the page from its own lines only."""
        page = self.doc[index]
        pw = self.writings(index)
        for sid, lib in pw.libs.items():
            if sids is not None and sid not in sids:
                continue
            lib.clear()
            for j, l in enumerate(self.ink.get(index, [])):
                if l.get("style") == sid:
                    lib.learn_line(page, l, j)

    # ------------------------------------------------------------------ writings of a page
    def writings(self, index):
        """The writings (people, typefaces) of one page, created empty the first time."""
        if index not in self.hands:
            self.hands[index] = PageWritings()
        return self.hands[index]

    def _lib(self, index, sid=None, kind="hand"):
        """A writing by id, else the main one of that kind (created if the page has none)."""
        pw = self.writings(index)
        lib = pw.get(sid) if sid is not None else None
        if lib is None:
            lib = pw.default(kind)
        if lib is None:
            lib = pw.get(pw.new(kind))
        return lib

    def _line_lib(self, index, line):
        sid = line.get("style")
        lib = self.writings(index).get(sid) if sid is not None else None
        if lib is None:                         # lines from older versions / added by hand
            lib = self._lib(index, None, line.get("kind", "hand"))
            line["style"] = lib.sid
        return lib

    def hand_info(self, index, sid=None):
        pw = self.writings(index)
        lines = self.ink.get(index, [])
        lib = pw.get(sid) if sid is not None else (pw.default("hand") or pw.default("print"))
        return {"styles": pw.listing(lines), "selected": lib.sid if lib else None,
                "hand": lib.summary() if lib else HandwritingLibrary().summary(),
                "height": lib.typical_height() if lib else 14.0, "learning": self.learning.stats()}

    def rename_style(self, index, sid, name):
        lib = self.writings(index).get(sid)
        name = (name or "").strip()
        if lib and name:
            lib.name, lib.auto_name = name, False
        return self._result(index, sid)

    def save_style(self, index, sid, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Scrivi un nome per la calligrafia.")
        lib = self.writings(index).get(sid)
        if lib is None or not (lib.glyphs or lib.manual):
            raise RuntimeError("Questa scrittura non ha ancora lettere apprese: analizza la pagina o segna le lettere prima.")
        HW.save_style(dict(lib.to_style(name), kind=lib.kind))
        return HW.style_list()

    def load_style(self, index, sid, name):
        lib = self._lib(index, sid)
        if name:
            lib.load_style(HW.get_style(name))
        else:
            lib.unload_style()
        lib._font = None
        return self.hand_info(index, lib.sid)

    def set_ink_kind(self, index, line_id, kind):
        """The user says that a writing is handwritten ("hand") or printed ("print"):
        it applies to every line of the same writing, so the page stays coherent."""
        with self.lock:
            kind = "print" if kind == "print" else "hand"
            line = self.ink[index][line_id]
            lib = self._line_lib(index, line)
            pw = self.writings(index)
            pw.set_kind(lib.sid, kind)
            changed = 0
            for l in self.ink.get(index, []):
                if l.get("style") == lib.sid:
                    l["kind"] = kind
                    if kind == "hand" and l["source"] == "ocr":
                        l["doubt"] = min(l["doubt"], 0.4)
                    changed += 1
            self._relearn(index, {lib.sid})
            return self._result(index, lib.sid, changed=changed)

    def set_line_style(self, index, line_id, sid):
        """Move a line to another writing of the page ("new" = a writing of its own)."""
        with self.lock:
            line = self.ink[index][line_id]
            pw = self.writings(index)
            old = self._line_lib(index, line)
            if sid == "new":
                sid = pw.new(line.get("kind", "hand"), line.get("feat"))
            new = pw.get(int(sid))
            if new is None:
                raise RuntimeError("Scrittura non trovata.")
            line["style"], line["kind"] = new.sid, new.kind
            new.take_manual(old, line_id)
            self._relearn(index, {old.sid, new.sid})
            pw.drop_empty({l.get("style") for l in self.ink.get(index, [])})
            return self._result(index, new.sid)

    def merge_styles(self, index, src, dst):
        """Two writings are the same person (or typeface): all lines of src go to dst."""
        with self.lock:
            pw = self.writings(index)
            a, b = pw.get(int(src)), pw.get(int(dst))
            if a is None or b is None or a is b:
                raise RuntimeError("Scrittura non trovata.")
            for j, l in enumerate(self.ink.get(index, [])):
                if l.get("style") == a.sid:
                    l["style"], l["kind"] = b.sid, b.kind
                    b.take_manual(a, j)
            for ch, gs in a.manual.items():          # letters marked on no line in particular
                b.manual.setdefault(ch, []).extend(gs)
            a.manual = {}
            if not b.loaded and a.loaded:
                b.loaded, b.loaded_name = a.loaded, a.loaded_name
            a.loaded = {}
            del pw.libs[a.sid]
            pw._names()
            self._relearn(index, {b.sid})
            return self._result(index, b.sid)

    def split_style(self, index, sid):
        """A writing that mixes two people: its lines are divided in two by how they are
        written (ink, stroke, slant, size), the two most different lines leading each part."""
        with self.lock:
            pw = self.writings(index)
            lib = pw.get(int(sid))
            members = [(j, l) for j, l in enumerate(self.ink.get(index, [])) if l.get("style") == lib.sid]
            measured = [(j, l) for j, l in members if l.get("feat")]
            if len(measured) < 2:
                raise RuntimeError("Servono almeno due righe lette per dividere una scrittura.")
            best, pair = -1.0, None
            for x in range(len(measured)):
                for y in range(x + 1, len(measured)):
                    d = WR.distance(measured[x][1]["feat"], measured[y][1]["feat"])
                    if d > best:
                        best, pair = d, (measured[x][1]["feat"], measured[y][1]["feat"])
            # a few rounds of 2-means on the writer distance
            cents = list(pair)
            for _ in range(4):
                side = {j: int(WR.distance(l["feat"], cents[1]) < WR.distance(l["feat"], cents[0]))
                        for j, l in measured}
                parts = [[l["feat"] for j, l in measured if side[j] == k] for k in (0, 1)]
                if not all(parts):
                    break
                cents = [WR._mean(p) for p in parts]
            if not all(parts):
                raise RuntimeError("Le righe di questa scrittura sembrano tutte uguali.")
            new = pw.get(pw.new(lib.kind, cents[1]))
            lib.feat = cents[0]
            for j, l in members:
                k = side.get(j)
                if k is None:                       # a line written by the user: nearest line
                    near = min(measured, key=lambda m: abs(m[1]["bbox"][1] - l["bbox"][1]))
                    k = side[near[0]]
                if k == 1:
                    l["style"] = new.sid
                    new.take_manual(lib, j)
            self._relearn(index, {lib.sid, new.sid})
            return self._result(index, new.sid)

    def ink_letters(self, index, line_id):
        """Image of a line plus where each letter is, for marking letters one by one."""
        with self.lock:
            page = self.doc[index]
            line = self.ink[index][line_id]
            lib = self._line_lib(index, line)
            clip = lib._line_clip(page, line["bbox"])
            zoom = 4
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            return {"img": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode(),
                    "rect": [clip.x0, clip.y0, clip.x1, clip.y1], "line": line["bbox"], "text": line["text"],
                    "segments": lib.propose_segments(page, line)}

    def teach_letters(self, index, line_id, segments, update_text=True):
        """Store the letters/digits the user marked (in the line's own writing) and,
        optionally, use them as the line reading."""
        with self.lock:
            page = self.doc[index]
            line = self.ink[index][line_id]
            lib = self._line_lib(index, line)
            stored = lib.learn_manual(page, line["bbox"], segments, line_id, line["text"])
            learned = 0
            marked = sorted((s for s in segments if s.get("char", "").strip()), key=lambda s: s["x0"])
            if update_text and marked:
                text = self._letters_text(line, marked)
                if text != line["text"]:
                    learned = self.learning.learn(line["raw"] if line["source"] != "user" else line["text"], text)
                    line["text"] = text
                line["source"], line["doubt"] = "user", 0.0
            return self._result(index, lib.sid, stored=stored, learned=learned, stats=self.learning.stats())

    @staticmethod
    def _letters_text(line, marked):
        """Reading of a line from the letters marked by the user, with spaces between words."""
        chars = "".join(s["char"] for s in marked)
        if chars == "".join(line["text"].split()):
            return line["text"]                     # same letters: keep the spacing of the reading
        h = line["bbox"][3] - line["bbox"][1]
        words = line.get("words") or []
        breaks = [(a["bbox"][2], b["bbox"][0]) for a, b in zip(words, words[1:])]
        gaps = [cur["x0"] - prev["x1"] for prev, cur in zip(marked, marked[1:])]
        typical = sorted(gaps)[len(gaps) // 2] if gaps else 0
        text = marked[0]["char"]
        for prev, cur, gap in zip(marked, marked[1:], gaps):
            # a space where the OCR saw two words, or where the gap is much wider than between letters
            mid = (prev["x1"] + cur["x0"]) / 2
            split = any(a - h * 0.1 <= mid <= b + h * 0.1 for a, b in breaks) or gap > max(h * 0.25, typical + h * 0.15)
            text += (" " if split else "") + cur["char"]
        return text

    def correct_ink(self, index, line_id, text):
        """User teaches the app what is really written."""
        with self.lock:
            line = self.ink[index][line_id]
            learned = self.learning.learn(line["raw"] if line["source"] != "user" else line["text"], text) \
                if not line.get("synthetic") and line["raw"] else 0
            if line["source"] == "unread" and not line["words"] and text.strip():
                # an unread line becomes one word per space-separated chunk spread on its width
                line["words"] = [{"text": text, "bbox": list(line["bbox"])}]
            before = line["text"]
            line["text"] = text
            line["source"] = "user"
            line["doubt"] = 0.0
            lib = self._line_lib(index, line)
            # the same writer makes the same mistakes: fix them in the other lines too
            fixed = self._propagate(index, lib, before, text, line_id)
            self._relearn(index, {lib.sid})
            return self._result(index, lib.sid, learned=learned, stats=self.learning.stats(), propagated=fixed)

    def _propagate(self, index, lib, before, after, skip):
        """Apply a correction to the other lines of the same writing that the user has not
        confirmed yet: whole words first ("Firnoato" -> "Firmato" everywhere), then the
        letter swaps learned from it ("rn" -> "m") when they turn a non-word into a word."""
        a, b = before.split(), after.split()
        words, swaps = {}, []
        if len(a) == len(b):
            for x, y in zip(a, b):
                cx, cy = LEXICON.core(x), LEXICON.core(y)
                if cx and cy and cx != cy:
                    words[cx.lower()] = cy
                    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, cx.lower(), cy.lower()).get_opcodes():
                        if tag != "equal" and i2 - i1 <= 2 and j2 - j1 <= 2:
                            swaps.append((cx.lower()[i1:i2], cy.lower()[j1:j2]))
        lib.confusions = getattr(lib, "confusions", []) + swaps
        if not words and not lib.confusions:
            return []
        changed = []
        for j, l in enumerate(self.ink.get(index, [])):
            if j == skip or l.get("style") != lib.sid or l.get("synthetic") or l["source"] in ("user", "unread"):
                continue
            out, n = [], 0
            for tok in l["text"].split(" "):
                m = WORD_RE.match(tok)
                if not m:
                    out.append(tok)
                    continue
                pre, core, post = m.groups()
                new = words.get(core.lower())
                if new is None and not LEXICON.known(core):
                    for wrong, right in lib.confusions:
                        if wrong and wrong in core.lower():
                            cand = core.lower().replace(wrong, right)
                            if LEXICON.known(cand):
                                new = cand
                                break
                if new and new.lower() != core.lower():
                    if core[:1].isupper():
                        new = new[:1].upper() + new[1:]
                    out.append(pre + new + post)
                    n += 1
                else:
                    out.append(tok)
            if n:
                l["text"] = " ".join(out)
                l["source"] = "learned"
                l["doubt"] = min(l["doubt"], 0.3)
                changed.append(j)
        return changed

    def ink_crop(self, index, line_id):
        with self.lock:
            page = self.doc[index]
            r = fitz.Rect(self.ink[index][line_id]["bbox"])
            r = fitz.Rect(r.x0 - 6, r.y0 - r.height * 0.4, r.x1 + 6, r.y1 + r.height * 0.4) & page.rect
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=r, alpha=False)
            return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()

    def preview_hand(self, index, text, height=None, color=None, sid=None, line_id=None):
        with self.lock:
            if line_id is not None:
                line = self.ink[index][line_id]
                lib = self._line_lib(index, line)
                xh = HW.measure_line(self.doc[index], line["bbox"], line["text"])[1] \
                    if not line.get("synthetic") else line.get("xh") or lib.typical_xh()
            else:
                lib = self._lib(index, sid)
                xh = height * 0.45 if height else lib.typical_xh()
            parts = [lib.render(t, xh, _rgb(color) if color else None) if t.strip() else None
                     for t in text.split("\n")]
            if not any(parts):
                return None
            png = parts[0]["png"] if len(parts) == 1 else _stack([p["png"] if p else None for p in parts])
            return {"img": "data:image/png;base64," + base64.b64encode(png).decode(),
                    "coverage": round(lib.coverage(text), 2), "style": lib.sid, "kind": lib.kind}

    def _place(self, page, lib, text, x, baseline, xh, color, right=None):
        """Draw text composed with a writing, sitting on `baseline` with x-height `xh`;
        scanned print also gets an invisible text layer, so it can still be searched and
        copied like the original OCR'd scan."""
        res = lib.render(text, xh, color, max_width_pt=(right if right else page.rect.x1 - 18) - x)
        if not res:
            return None
        top = baseline + res["top_offset_pt"]
        rect = fitz.Rect(x, top, x + res["width_pt"], top + res["height_pt"])
        _image_upright(page, rect, res["png"])
        if lib.kind == "print":
            try:
                font, _ = F.system_font("Arial")
                size = xh / 0.52
                # as wide as the picture of the text, so a search highlights the right words
                size *= min(max(res["width_pt"] / max(font.text_length(text, fontsize=size), 1), 0.3), 3)
                tw = fitz.TextWriter(page.rect)
                _append_words(tw, (x, baseline), text, font, size)
                _write_hidden(page, tw)
            except Exception:
                pass
        return res

    def _line_pitch(self, index, lib, xh):
        """Distance between two baselines of this writing, measured on the page (the
        lines of a paragraph written by the same person); else a usual handwriting pitch."""
        ys = sorted((l["bbox"][1] + l["bbox"][3]) / 2 for l in self.ink.get(index, [])
                    if l.get("style") == lib.sid and l.get("source") != "unread")
        gaps = [b - a for a, b in zip(ys, ys[1:]) if xh * 2.2 <= b - a <= xh * 7]
        return float(np.median(gaps)) if gaps else xh * (2.6 if lib.kind == "print" else 3.4)

    def _wrap(self, lib, text, xh, max_w):
        """Lines of a text: the user's line breaks, and long lines broken between words
        where they would run off the page."""
        out = []
        for para in text.split("\n"):
            words = para.split()
            if not words:
                out.append("")
                continue
            cur = words[0]
            for w in words[1:]:
                res = lib.render(cur + " " + w, xh, seed=0)
                if res and res["width_pt"] > max_w:
                    out.append(cur)
                    cur = w
                else:
                    cur += " " + w
            out.append(cur)
        while out and not out[-1]:
            out.pop()
        return out

    def _free(self, page, index, rect, skip=()):
        """True if nothing is written in rect: no read line, no text, no ink on the image."""
        for j, l in enumerate(self.ink.get(index, [])):
            if j not in skip and fitz.Rect(l["bbox"]).intersects(rect):
                return False
        if page.get_text("text", clip=_to_text(page, rect)).strip():
            return False
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=rect, alpha=False)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        if not a.size:
            return False
        g = a.mean(axis=2)
        return float((g < np.percentile(g, 90) - 60).mean()) < 0.004

    def _place_lines(self, index, page, lib, text, x, base, xh, color, skip=(), right=None):
        """Write a (multi-line) text; returns [(text, res, baseline)] for each written line.
        The following lines go below only where the page is free: otherwise the rest of the
        text is squeezed into the last line that fits, never written over other writing."""
        pitch = self._line_pitch(index, lib, xh)
        # inside a table the text stays in its cell, elsewhere within the page margin
        in_cell = bool(right)
        right = min(page.rect.x1 - 24, right - 3) if right else page.rect.x1 - 24
        lines = self._wrap(lib, text, xh, right - x)
        rows = 1
        while rows < len(lines):
            b = base + rows * pitch
            rect = fitz.Rect(x, b - xh * 1.9, right, b + xh * 0.9)
            if rect.y1 > page.rect.y1 or not self._free(page, index, rect, skip):
                break
            if in_cell and self._border_below(page, x, right, base, b + xh * 0.9):
                break                                  # the next line would leave the cell
            rows += 1
        if rows < len(lines):
            lines = lines[:rows - 1] + [" ".join(t for t in lines[rows - 1:] if t)]
        done = []
        for k, t in enumerate(lines):
            if not t.strip():
                continue
            b = base + k * pitch
            res = self._place(page, lib, t, x, b, xh, color, right)
            if res:
                done.append((t, res, b))
        return done

    def edit_ink(self, index, line_id, text, color=None):
        with self.lock:
            page = self.doc[index]
            line = self.ink[index][line_id]
            lib = self._line_lib(index, line)
            r = fitz.Rect(line["bbox"])
            h = r.height
            # size and baseline of the line being replaced, measured before covering it
            if line.get("synthetic") and line.get("base"):
                base, xh = line["base"], line["xh"]
            else:
                base, xh = HW.measure_line(page, r, line["text"])
            self._checkpoint(index)
            cover = fitz.Rect(r.x0 - 2, r.y0 - h * 0.14, r.x1 + 2, r.y1 + h * 0.14) & page.rect
            right = self._cell_right(page, r)
            if not self._cover_letters(page, cover):
                self._cover(page, cover)
            written = []
            if text.strip():
                # the line is rewritten with the letters of ITS writing only (never mixed)
                ink_color = _rgb(color) if color else lib.ink_color()
                written = self._place_lines(index, page, lib, text, r.x0, base, xh, ink_color, skip=(line_id,),
                                            right=right)
            if written:
                text, res, _ = written[0]
                line["bbox"] = [r.x0, r.y0, max(r.x0 + res["width_pt"], r.x0 + 4), r.y1]
            # the following lines of a paragraph become lines of the same writing
            for t, res, b in written[1:]:
                self.ink[index].append({
                    "bbox": [r.x0, b - (base - r.y0), r.x0 + res["width_pt"], b + (r.y1 - base)], "raw": t, "text": t,
                    "words": [], "source": "user", "doubt": 0.0, "synthetic": True, "kind": lib.kind,
                    "style": lib.sid, "base": b, "xh": xh, "feat": line.get("feat"), "handScore": line.get("handScore")})
            line["base"], line["xh"] = base, xh
            line["text"] = text if written or not text.strip() else text.replace("\n", " ")
            line["synthetic"] = True
            line["source"] = "user"
            line["doubt"] = 0.0
            self._touch(index)
            return self.blocks(index)

    def add_hand(self, index, x, y, text, height=None, color=None, sid=None):
        with self.lock:
            page = self.doc[index]
            lib = self._lib(index, sid)
            xh = float(height) * 0.45 if height else lib.typical_xh()
            base = y + xh * 1.6                    # (x, y) is the top-left of the new line
            self._checkpoint(index)
            for t, res, b in self._place_lines(index, page, lib, text, x, base, xh,
                                               _rgb(color) if color else lib.ink_color()):
                self.ink.setdefault(index, []).append({
                    "bbox": [x, b - xh * 1.6, x + res["width_pt"], b + xh * 0.6], "raw": t, "text": t, "words": [],
                    "source": "user", "doubt": 0.0, "synthetic": True, "kind": lib.kind, "style": lib.sid,
                    "base": b, "xh": xh})
            self._touch(index)
            return self.blocks(index)

    # ------------------------------------------------------------------ pages
    def rotate_page(self, index, delta=90):
        with self.lock:
            self._checkpoint()
            p = self.doc[index]
            p.set_rotation((p.rotation + delta) % 360)
            self.ink.pop(index, None)
            self.ignored.pop(index, None)
            self._touch(index)
            return self.info()

    def delete_page(self, index):
        with self.lock:
            if len(self.doc) <= 1:
                raise RuntimeError("Il documento deve avere almeno una pagina.")
            self._checkpoint()
            self.doc.delete_page(index)
            self.ink = {(k if k < index else k - 1): v for k, v in self.ink.items() if k != index}
            self.hands = {(k if k < index else k - 1): v for k, v in self.hands.items() if k != index}
            self.ignored = {(k if k < index else k - 1): v for k, v in self.ignored.items() if k != index}
            self.page_versions = {i: self.page_versions.get(i, 0) + 1 for i in range(len(self.doc))}
            self._touch()
            return self.info()

    def insert_blank(self, index):
        with self.lock:
            self._checkpoint()
            ref = self.doc[min(index, len(self.doc) - 1)].rect
            self.doc.new_page(pno=index + 1, width=ref.width, height=ref.height)
            self.ink = {(k if k <= index else k + 1): v for k, v in self.ink.items()}
            self.hands = {(k if k <= index else k + 1): v for k, v in self.hands.items()}
            self.ignored = {(k if k <= index else k + 1): v for k, v in self.ignored.items()}
            self.page_versions = {i: self.page_versions.get(i, 0) + 1 for i in range(len(self.doc))}
            self._touch()
            return self.info()
