"""PDF editing beyond text: pages, signatures, pictures, markup and comments, redaction,
headers / page numbers / watermarks, comparing two versions, optimising and protecting.

Everything here works in displayed coordinates (what the user sees, as the rest of the
app), converted to the page's own coordinates where PDF objects are created."""
import base64
import datetime
import difflib
import io
import os
import re
import secrets

import numpy as np
import pymupdf as fitz
from PIL import Image

from .storage import path as data_path

MARK_TYPES = {"highlight": fitz.PDF_ANNOT_HIGHLIGHT, "underline": fitz.PDF_ANNOT_UNDERLINE,
              "strike": fitz.PDF_ANNOT_STRIKE_OUT}
TYPE_NAMES = {"Highlight": "Evidenziato", "Underline": "Sottolineato", "StrikeOut": "Barrato",
              "Text": "Nota", "FreeText": "Testo", "Square": "Rettangolo", "Circle": "Ellisse",
              "Line": "Freccia", "Stamp": "Immagine", "Ink": "Disegno", "Polygon": "Poligono",
              "PolyLine": "Linea", "Image": "Immagine"}
# what a stamp annotation made by Calamo is: a signature or a picture
SIG_TAG, IMG_TAG = "Calamo:firma", "Calamo:immagine"

# sensitive data to black out, as found in Italian documents
SENSITIVE = {
    "cf": ("Codice fiscale", r"\b[A-Z]{6}\s?\d{2}[A-Z]\d{2}\s?[A-Z]\d{3}[A-Z]\b"),
    "iban": ("IBAN", r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b"),
    "email": ("Email", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": ("Telefono", r"(?<![\w/])(?:\+39[\s.]?)?(?:3\d{2}|0\d{1,3})[\s./-]?\d{3,4}[\s.-]?\d{3,4}(?![\w/])"),
    "card": ("Carta di credito", r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
}


def _hex(color, default=(1, 0.85, 0)):
    try:
        c = color.lstrip("#")
        return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:
        return default


def _rgbhex(c):
    if not c:
        return None
    return "#%02x%02x%02x" % tuple(int(max(0, min(1, v)) * 255) for v in c[:3])


def parse_pages(spec, n):
    """'1-3, 5, 8-' -> [0, 1, 2, 4, 7, ...] (0-based, within n pages). Empty = all."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(n))
    out = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
        elif part.isdigit():
            a = b = int(part)
        else:
            raise RuntimeError(f"Intervallo di pagine non valido: “{part}”. Esempio: 1-3, 5")
        for p in range(max(a, 1), min(b, n) + 1):
            if p - 1 not in out:
                out.append(p - 1)
    if not out:
        raise RuntimeError("Nessuna pagina in questo intervallo.")
    return out


def clean_signature(img):
    """A photo or scan of a signature -> only the ink, on a transparent background,
    trimmed to its edges (the paper, its shadows and grain disappear)."""
    im = img.convert("RGB")
    if max(im.size) > 1600:
        im.thumbnail((1600, 1600))
    a = np.asarray(im).astype(np.float32)
    gray = a.mean(axis=2) * 0.6 + a.min(axis=2) * 0.4
    k = max(min(gray.shape) // 8, 8)
    from .letters import paper
    bg = paper(gray, k)
    dark = np.clip((bg - gray) / np.maximum(bg, 1), 0, 1)       # 0 paper .. 1 ink
    ink_level = float(np.percentile(dark[dark > 0.25], 60)) if (dark > 0.25).any() else 0.5
    alpha = np.clip((dark - 0.12) / max(ink_level - 0.12, 0.05), 0, 1)
    if alpha.max() <= 0:
        raise RuntimeError("Nell'immagine non trovo una firma: usa una foto più contrastata.")
    # the colour of the ink: the darkest pixels' average (blue, black...)
    sel = alpha > 0.8
    col = a[sel].mean(axis=0) if sel.any() else np.array([20, 20, 60], np.float32)
    rgba = np.zeros(a.shape[:2] + (4,), np.uint8)
    rgba[:, :, :3] = col.astype(np.uint8)
    rgba[:, :, 3] = (alpha * 255).astype(np.uint8)
    ys, xs = np.nonzero(alpha > 0.15)
    pad = 6
    rgba = rgba[max(ys.min() - pad, 0):ys.max() + pad, max(xs.min() - pad, 0):xs.max() + pad]
    return Image.fromarray(rgba, "RGBA")


def _shown_rect(page, r):
    return fitz.Rect(r) * page.rotation_matrix


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _sig_dir():
    d = data_path("firme")
    os.makedirs(d, exist_ok=True)
    return d


class EditingMixin:
    """Mixed into Session: uses self.doc, self.lock, self._checkpoint, self._touch,
    self.info, self.ink, self.hands, self.ignored, self.page_versions."""

    # ================================================================== pages
    def _reorder(self, order):
        """Pages were rearranged: order[new index] = old index (None for a new page)."""
        for name in ("ink", "hands", "ignored"):
            old = getattr(self, name)
            setattr(self, name, {i: old[o] for i, o in enumerate(order) if o is not None and o in old})
        self.page_versions = {i: (self.page_versions.get(o, 0) if o is not None else 0) + 1
                              for i, o in enumerate(order)}
        self._touch()

    def move_page(self, src, dst):
        """Drag a page to another position."""
        with self.lock:
            n = len(self.doc)
            if not (0 <= src < n and 0 <= dst < n) or src == dst:
                return self.info()
            self._checkpoint()
            order = list(range(n))
            order.insert(dst, order.pop(src))
            self.doc.select(order)
            self._reorder(order)
            return self.info()

    def merge_pdf(self, path, after=None):
        """Add the pages of another PDF (at the end, or after page `after`)."""
        with self.lock:
            other = fitz.open(path)
            if other.needs_pass:
                raise RuntimeError("Il PDF da unire è protetto da password.")
            n, m = len(self.doc), len(other)
            at = n if after is None else min(after + 1, n)
            self._checkpoint()
            self.doc.insert_pdf(other, start_at=at if at < n else -1, annots=True, links=True)
            order = list(range(at)) + [None] * m + list(range(at, n))
            self._reorder(order)
            return {**self.info(), "added": m, "at": at}

    def extract_pages(self, spec, path):
        """Save some pages as a new PDF (the document stays as it is)."""
        with self.lock:
            pages = parse_pages(spec, len(self.doc))
            out = fitz.open()
            for p in pages:
                out.insert_pdf(self.doc, from_page=p, to_page=p, annots=True)
            out.save(path, garbage=3, deflate=True)
            return {"pages": len(pages), "path": path}

    def split_every(self, every, folder, base):
        """Split the document into files of `every` pages each."""
        with self.lock:
            every = max(int(every), 1)
            n = len(self.doc)
            paths = []
            for k, a in enumerate(range(0, n, every)):
                out = fitz.open()
                out.insert_pdf(self.doc, from_page=a, to_page=min(a + every, n) - 1, annots=True)
                p = os.path.join(folder, f"{base}_parte{k + 1}.pdf")
                out.save(p, garbage=3, deflate=True)
                paths.append(p)
            return {"files": paths}

    def delete_pages(self, spec):
        with self.lock:
            pages = parse_pages(spec, len(self.doc))
            if len(pages) >= len(self.doc):
                raise RuntimeError("Il documento deve avere almeno una pagina.")
            self._checkpoint()
            keep = [i for i in range(len(self.doc)) if i not in pages]
            self.doc.select(keep)
            self._reorder(keep)
            return self.info()

    def duplicate_page(self, index):
        with self.lock:
            self._checkpoint()
            n = len(self.doc)
            self.doc.fullcopy_page(index, index + 1 if index + 1 < n else -1)
            order = list(range(index + 1)) + [None] + list(range(index + 1, n))
            self._reorder(order)
            return self.info()

    # ================================================================== objects on the page
    def _annot_by_id(self, page, xref):
        for a in page.annots():
            if a.xref == int(xref):
                return a
        raise RuntimeError("Oggetto non trovato (forse è già stato eliminato).")

    def _shown(self, page, rect):
        return fitz.Rect(rect) * page.rotation_matrix

    def _text_rect(self, page, rect):
        return fitz.Rect(rect) * page.derotation_matrix

    def objects(self, index):
        """Annotations, signatures and pictures of a page, for selecting and editing."""
        with self.lock:
            page = self.doc[index]
            out = []
            for a in page.annots():
                t = a.type[1]
                if t in ("Popup", "Link", "Widget", "Redact"):
                    continue
                info = a.info
                kind = "signature" if info.get("subject") == SIG_TAG else t
                out.append({"id": a.xref, "src": "annot", "type": kind,
                            "label": "Firma" if kind == "signature" else TYPE_NAMES.get(t, t),
                            "rect": list(self._shown(page, a.rect)),
                            "content": "" if t == "Stamp" else info.get("content", ""),
                            "color": _rgbhex(a.colors.get("stroke") or a.colors.get("fill"))})
            area = abs(page.rect)
            from .graphics import image_role
            for im in page.get_image_info(xrefs=True):
                r = fitz.Rect(im["bbox"]) & page.rect
                if not im.get("xref") or r.is_empty or abs(r) > area * 0.6:
                    continue                    # the scan behind the page is not an object
                if image_role(self.doc, im["xref"]) in ("Cover", "Ink"):
                    continue                    # paper rebuilt by Calamo, writing (it is a line)
                if im.get("width", 9) <= 2 and im.get("height", 9) <= 2:
                    continue                    # what is left of a deleted picture
                out.append({"id": im["xref"], "src": "image", "type": "Image", "label": "Immagine",
                            "rect": list(self._shown(page, r)), "content": "", "color": None})
            return out

    def _image_uses(self, xref):
        n = 0
        for p in self.doc:
            n += sum(1 for im in p.get_images(full=True) if im[0] == xref)
        return n

    def _image_png(self, xref):
        pix = fitz.Pixmap(self.doc, xref)
        info = self.doc.extract_image(xref)
        if info and info.get("smask"):
            try:
                mask = fitz.Pixmap(self.doc, info["smask"])
                pix = fitz.Pixmap(pix, mask)
            except Exception:
                pass
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png")

    def _stamp(self, page, rect, png, tag, content=""):
        """A picture as a stamp annotation: it can then be moved, resized and deleted."""
        img = Image.open(io.BytesIO(png))
        if page.rotation:               # the stamp is drawn in the page's own orientation
            img = img.rotate(page.rotation, expand=True)
        a = page.add_stamp_annot(self._text_rect(page, rect), stamp=_png(img))
        a.set_info(subject=tag, content=content, title="Calamo")
        a.update()
        return a

    def _content_image_to_stamp(self, page, xref):
        """A picture of the page content becomes an object Calamo can move."""
        if self._image_uses(xref) > 1:
            raise RuntimeError("Questa immagine è usata in più punti del documento: non la sposto per non cambiarli tutti.")
        rect = next((fitz.Rect(i["bbox"]) for i in page.get_image_info(xrefs=True) if i["xref"] == xref), None)
        png = self._image_png(xref)
        page.delete_image(xref)
        return self._stamp(page, self._shown(page, rect), png, IMG_TAG)

    def move_object(self, index, src, oid, rect, checkpoint=True):
        """Move / resize an object (rect in displayed coordinates)."""
        with self.lock:
            page = self.doc[index]
            rect = fitz.Rect(rect)
            if rect.width < 2 or rect.height < 2:
                raise RuntimeError("Troppo piccolo.")
            if checkpoint:
                self._checkpoint(index)
            if src == "image":
                a = self._content_image_to_stamp(page, int(oid))
            else:
                a = self._annot_by_id(page, oid)
            if a.type[1] == "Line":
                # an arrow keeps its direction, stretched to the new box
                old = self._shown(page, a.rect)
                v = a.vertices
                m = page.rotation_matrix
                pts = [fitz.Point(p) * m for p in v]
                sx = rect.width / max(old.width, 1e-3)
                sy = rect.height / max(old.height, 1e-3)
                new = [fitz.Point(rect.x0 + (p.x - old.x0) * sx, rect.y0 + (p.y - old.y0) * sy) * page.derotation_matrix
                       for p in pts]
                col = a.colors.get("stroke")
                w = a.border.get("width", 2)
                page.delete_annot(a)
                a = page.add_line_annot(new[0], new[-1])
                a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
                a.set_colors(stroke=col, fill=col)
                a.set_border(width=w)
                a.update()
            else:
                a.set_rect(self._text_rect(page, rect))
                a.update()
                # a border makes the box grow when it is redrawn: put it where it was asked
                got = self._shown(page, a.rect)
                if max(abs(got.x0 - rect.x0), abs(got.y0 - rect.y0), abs(got.x1 - rect.x1), abs(got.y1 - rect.y1)) > 0.3:
                    fix = fitz.Rect(2 * rect.x0 - got.x0, 2 * rect.y0 - got.y0, 2 * rect.x1 - got.x1, 2 * rect.y1 - got.y1)
                    if fix.width > 2 and fix.height > 2:
                        a.set_rect(self._text_rect(page, fix))
                        a.update()
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def delete_object(self, index, src, oid):
        with self.lock:
            page = self.doc[index]
            self._checkpoint(index)
            if src == "image":
                if self._image_uses(int(oid)) > 1:
                    raise RuntimeError("Questa immagine è usata anche in altre pagine: usa la gomma per toglierla solo qui.")
                page.delete_image(int(oid))
            else:
                page.delete_annot(self._annot_by_id(page, oid))
            self._touch(index)
            return {"objects": self.objects(index)}

    def edit_object(self, index, oid, content=None, color=None):
        """Change the text of a note / comment, or the colour of a mark or shape."""
        with self.lock:
            page = self.doc[index]
            a = self._annot_by_id(page, oid)
            self._checkpoint(index)
            if content is not None:
                a.set_info(content=content)
            if color:
                c = _hex(color)
                t = a.type[1]
                if t in ("Square", "Circle", "Line"):
                    a.set_colors(stroke=c, fill=c if t == "Line" else None)
                else:
                    a.set_colors(stroke=c)
            a.update()
            self._touch(index)
            return {"objects": self.objects(index)}

    # ================================================================== dragging page content
    def move_block(self, index, bbox, dx, dy, checkpoint=True):
        """Drag a block of digital text: every piece is written again, with its own font,
        size and colour, dx/dy further on (displayed coordinates); the original is removed."""
        from .document import REDACT_KW, _append_words, _to_text, _write_upright
        with self.lock:
            page = self.doc[index]
            b, lines = self._raw_block(page, bbox)
            if not lines:
                raise RuntimeError("Questo testo non si può spostare (è ruotato).")
            old = fitz.Rect(_shown_rect(page, b["bbox"]))
            R = page.rect
            dx = max(min(float(dx), R.x1 - old.x1), R.x0 - old.x0)
            dy = max(min(float(dy), R.y1 - old.y1), R.y0 - old.y0)
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return self.blocks(index)
            if checkpoint:
                self._checkpoint(index)
            pieces = []
            for l in lines:
                for sp in l["spans"]:
                    if sp["text"].strip():
                        pieces.append(sp)
            for l in lines:
                page.add_redact_annot(_to_text(page, self._shrink(l["bbox"])))
            page.apply_redactions(**REDACT_KW)
            tws = []
            for sp in pieces:
                font = self._font_for(page, sp, sp["text"], {})
                color = tuple(((sp["color"] >> k) & 255) / 255 for k in (16, 8, 0))
                tw = fitz.TextWriter(page.rect, color=color)
                _append_words(tw, (sp["origin"][0] + dx, sp["origin"][1] + dy), sp["text"], font, sp["size"])
                tws.append(tw)
            for tw in tws:
                _write_upright(page, tw)
            self._touch(index)
            res = self.blocks(index)
            res["moved"] = [old.x0 + dx, old.y0 + dy, old.x1 + dx, old.y1 + dy]
            return res

    def move_ink(self, index, line_id, dx, dy, checkpoint=True):
        """Drag a line of a scan (handwriting or print read by Calamo): only the ink is
        lifted - the paper, a highlighter or a table under it stay - and put down dx/dy
        further on; where it was, the paper is rebuilt."""
        from .document import _image_upright
        with self.lock:
            page = self.doc[index]
            lines = self.ink.get(index, [])
            if not (0 <= int(line_id) < len(lines)):
                raise RuntimeError("Riga non trovata.")
            line = lines[int(line_id)]
            box = fitz.Rect(line["bbox"])
            R = page.rect
            dx = max(min(float(dx), R.x1 - box.x1), R.x0 - box.x0)
            dy = max(min(float(dy), R.y1 - box.y1), R.y0 - box.y0)
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return self.blocks(index)
            png, area = self._lift_ink(page, box)
            if png is None:
                raise RuntimeError("Non trovo inchiostro in questa riga.")
            if checkpoint:
                self._checkpoint(index)
            if not self._cover_letters(page, box):
                self._cover(page, box)
            _image_upright(page, fitz.Rect(area.x0 + dx, area.y0 + dy, area.x1 + dx, area.y1 + dy), png)
            line["bbox"] = [box.x0 + dx, box.y0 + dy, box.x1 + dx, box.y1 + dy]
            for w in line.get("words") or []:
                if w.get("bbox"):
                    x0, y0, x1, y1 = w["bbox"]
                    w["bbox"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
            self._touch(index)
            res = self.blocks(index)
            res["moved"] = line["bbox"]
            return res

    def _lift_ink(self, page, box, zoom=3.0):
        """The ink of the letters in box as a transparent PNG (and the area it covers).
        The paper under the letters is rebuilt the same way the eraser does (a highlighter,
        a table, ruled lines go on); each pixel's opacity is how much darker than that
        rebuilt paper it is, its colour the ink's. Borders and lines stay on the page."""
        from PIL import Image
        from .letters import erase_letters, _gray
        m = max(box.height * 1.5, 14)
        ctx = fitz.Rect(box.x0 - 2 * m, box.y0 - m, box.x1 + 2 * m, box.y1 + m) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=ctx, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        ox, oy = pix.x / zoom, pix.y / zoom
        x0, y0 = max(int((box.x0 - ox) * zoom), 0), max(int((box.y0 - oy) * zoom), 0)
        x1 = min(int(round((box.x1 - ox) * zoom)), rgb.shape[1])
        y1 = min(int(round((box.y1 - oy) * zoom)), rgb.shape[0])
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None, box
        patch, n, (x0, y0, x1, y1) = erase_letters(rgb, (x0, y0, x1, y1), max(box.height / 1.28, 9.0) * zoom)
        if patch is None or not n:
            return None, box
        orig = rgb[y0:y1, x0:x1].astype(np.float32)
        bg = patch.astype(np.float32)
        go, gb = _gray(rgb[y0:y1, x0:x1]), _gray(patch)
        solid = go < np.percentile(go[gb - go > 20], 30) if (gb - go > 20).any() else go < 0
        ink = np.median(orig[solid], axis=0) if solid.any() else np.array([20.0, 20.0, 20.0])
        depth = np.maximum(gb - float(_gray(ink[None, None, :].astype(np.uint8))[0, 0]), 30)
        alpha = np.clip((gb - go) / depth, 0, 1)
        alpha[alpha < 0.08] = 0
        # the colour each pixel has without the paper behind it (the soft edges keep the pen's tone)
        a = np.maximum(alpha, 1e-3)[..., None]
        colour = np.clip((orig - (1 - a) * bg) / a, 0, 255)
        colour = np.where((alpha > 0.35)[..., None], colour, ink[None, None, :])
        rgba = np.dstack([colour, alpha * 255]).astype(np.uint8)
        ys, xs = np.nonzero(alpha > 0)
        if not len(ys):
            return None, box
        ya, yb, xa, xb = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        img = Image.fromarray(rgba[ya:yb, xa:xb], "RGBA")
        return _png(img), fitz.Rect(ox + (x0 + xa) / zoom, oy + (y0 + ya) / zoom,
                                    ox + (x0 + xb) / zoom, oy + (y0 + yb) / zoom)

    # ================================================================== pictures
    def insert_picture(self, index, png, x, y, width=None):
        """Insert a picture centred at (x, y), as an object that can be moved and resized."""
        with self.lock:
            page = self.doc[index]
            img = Image.open(io.BytesIO(png))
            img.load()
            w = float(width or min(220, page.rect.width * 0.4))
            h = w * img.height / max(img.width, 1)
            if h > page.rect.height * 0.6:
                h = page.rect.height * 0.6
                w = h * img.width / max(img.height, 1)
            rect = fitz.Rect(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
            rect = rect + (min(0, page.rect.x1 - rect.x1) + max(0, page.rect.x0 - rect.x0),
                           min(0, page.rect.y1 - rect.y1) + max(0, page.rect.y0 - rect.y0)) * 2
            self._checkpoint(index)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            a = self._stamp(page, rect, _png(img), IMG_TAG)
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def replace_picture(self, index, src, oid, png):
        """Put another picture in place of one, in the same box (keeping its proportions)."""
        with self.lock:
            page = self.doc[index]
            obj = next((o for o in self.objects(index) if str(o["id"]) == str(oid) and o["src"] == src), None)
            if obj is None:
                raise RuntimeError("Immagine non trovata.")
            self._checkpoint(index)
            box = fitz.Rect(obj["rect"])
            if src == "image":
                if self._image_uses(int(oid)) > 1:
                    raise RuntimeError("Questa immagine è usata anche in altre pagine.")
                page.delete_image(int(oid))
            else:
                page.delete_annot(self._annot_by_id(page, oid))
            img = Image.open(io.BytesIO(png))
            s = min(box.width / img.width, box.height / img.height)
            w, h = img.width * s, img.height * s
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            a = self._stamp(page, fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                            _png(img.convert("RGBA")), IMG_TAG)
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def crop_picture(self, index, oid, frac):
        """Crop a picture object: frac = (left, top, right, bottom) shares of its size."""
        with self.lock:
            page = self.doc[index]
            obj = next((o for o in self.objects(index) if str(o["id"]) == str(oid)), None)
            if obj is None or obj["src"] != "annot":
                raise RuntimeError("Spostala o ridimensionala una volta prima di ritagliarla.")
            a = self._annot_by_id(page, oid)
            pix = page.get_pixmap(clip=a.rect * page.rotation_matrix, dpi=200, alpha=True, annots=True)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            l, t, r, b = (max(0.0, min(0.95, float(v))) for v in frac)
            W, H = img.size
            img = img.crop((int(W * l), int(H * t), int(W * (1 - r)), int(H * (1 - b))))
            box = fitz.Rect(obj["rect"])
            new = fitz.Rect(box.x0 + box.width * l, box.y0 + box.height * t,
                            box.x1 - box.width * r, box.y1 - box.height * b)
            self._checkpoint(index)
            page.delete_annot(a)
            a = self._stamp(page, new, _png(img), IMG_TAG)
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    # ================================================================== signatures
    def signatures(self):
        out = []
        for f in sorted(os.listdir(_sig_dir())):
            if f.lower().endswith(".png"):
                with open(os.path.join(_sig_dir(), f), "rb") as fh:
                    out.append({"name": f[:-4], "img": "data:image/png;base64," + base64.b64encode(fh.read()).decode()})
        return out

    def _sig_name(self, name):
        name = re.sub(r"[^\w\- àèéìòù]", "", (name or "").strip())[:40] or "Firma"
        base, k = name, 2
        while os.path.exists(os.path.join(_sig_dir(), name + ".png")):
            name = f"{base} {k}"
            k += 1
        return name

    def save_signature(self, name, image_bytes, clean=True):
        """A signature drawn in the app (transparent PNG) or taken from a photo."""
        img = Image.open(io.BytesIO(image_bytes))
        if clean:
            img = clean_signature(img)
        else:
            img = img.convert("RGBA")
            bb = img.getbbox()
            if not bb:
                raise RuntimeError("La firma è vuota.")
            img = img.crop((max(bb[0] - 6, 0), max(bb[1] - 6, 0), bb[2] + 6, bb[3] + 6))
        name = self._sig_name(name)
        img.save(os.path.join(_sig_dir(), name + ".png"))
        return self.signatures()

    def delete_signature(self, name):
        p = os.path.join(_sig_dir(), os.path.basename(name) + ".png")
        if os.path.exists(p):
            os.remove(p)
        return self.signatures()

    def place_signature(self, index, name, x, y, width=None):
        with self.lock:
            p = os.path.join(_sig_dir(), os.path.basename(name) + ".png")
            if not os.path.exists(p):
                raise RuntimeError("Firma non trovata.")
            with open(p, "rb") as f:
                png = f.read()
            page = self.doc[index]
            img = Image.open(io.BytesIO(png))
            w = float(width or 150)
            h = w * img.height / max(img.width, 1)
            rect = fitz.Rect(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
            self._checkpoint(index)
            a = self._stamp(page, rect, png, SIG_TAG, content=f"Firma: {name}")
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    # ================================================================== markup and comments
    def _words_in(self, index, rect):
        """Words (displayed boxes) touched by rect: digital text and read handwriting."""
        page = self.doc[index]
        r = fitz.Rect(rect)
        out = []
        for w in page.get_text("words"):
            b = self._shown(page, fitz.Rect(w[:4]))
            c = fitz.Point((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
            if r.contains(c) or (b.intersects(r) and abs(b & r) > abs(b) * 0.4):
                out.append(b)
        for l in self.ink.get(index, []):
            for w in (l.get("words") or [{"bbox": l["bbox"]}]):
                b = fitz.Rect(w["bbox"])
                if b.intersects(r) and abs(b & r) > abs(b) * 0.4:
                    out.append(b)
        return out

    def mark_text(self, index, rect, kind, color=None):
        """Highlight / underline / strike out the text inside the dragged rectangle; on a
        scan without readable text, the rectangle itself is marked."""
        with self.lock:
            page = self.doc[index]
            words = self._words_in(index, rect)
            if words:
                # one quad per line of text
                words.sort(key=lambda b: ((b.y0 + b.y1) / 2, b.x0))
                lines = []
                for b in words:
                    if lines and abs((lines[-1].y0 + lines[-1].y1) / 2 - (b.y0 + b.y1) / 2) < b.height * 0.5:
                        lines[-1] |= b
                    else:
                        lines.append(fitz.Rect(b))
            else:
                lines = [fitz.Rect(rect)]
            quads = [self._text_rect(page, b).quad for b in lines if not b.is_empty]
            if not quads:
                raise RuntimeError("Trascina sopra al testo da segnare.")
            self._checkpoint(index)
            c = _hex(color, {"highlight": (1, 0.87, 0.2), "underline": (0.1, 0.4, 0.9),
                             "strike": (0.85, 0.1, 0.1)}[kind])
            if kind == "highlight":
                a = page.add_highlight_annot(quads=quads)
            elif kind == "underline":
                a = page.add_underline_annot(quads=quads)
            else:
                a = page.add_strikeout_annot(quads=quads)
            a.set_colors(stroke=c)
            a.set_info(title="Calamo")
            a.update()
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def add_note(self, index, x, y, text, color=None):
        with self.lock:
            page = self.doc[index]
            self._checkpoint(index)
            p = fitz.Point(x, y) * page.derotation_matrix
            a = page.add_text_annot(p, text or "", icon="Comment")
            a.set_colors(stroke=_hex(color, (1, 0.8, 0.1)))
            a.set_info(title="Calamo")
            a.update()
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def add_shape(self, index, kind, a_pt, b_pt, color=None, width=2):
        """Rectangle, ellipse or arrow from point a to point b (displayed coordinates)."""
        with self.lock:
            page = self.doc[index]
            c = _hex(color, (0.9, 0.15, 0.15))
            p, q = fitz.Point(a_pt), fitz.Point(b_pt)
            if p.distance_to(q) < 3:
                raise RuntimeError("Trascina per disegnare la forma.")
            self._checkpoint(index)
            if kind == "arrow":
                a = page.add_line_annot(p * page.derotation_matrix, q * page.derotation_matrix)
                a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
                a.set_colors(stroke=c, fill=c)
            else:
                r = self._text_rect(page, fitz.Rect(p, q).normalize())
                a = page.add_rect_annot(r) if kind == "rect" else page.add_circle_annot(r)
                a.set_colors(stroke=c)
            a.set_border(width=float(width))
            a.set_info(title="Calamo")
            a.update()
            self._touch(index)
            return {"objects": self.objects(index), "id": a.xref}

    def comments(self):
        """Every comment, mark and shape of the document, page by page."""
        with self.lock:
            out = []
            for i, page in enumerate(self.doc):
                for a in page.annots():
                    t = a.type[1]
                    if t in ("Popup", "Link", "Widget", "Redact") or a.info.get("subject") in (SIG_TAG, IMG_TAG):
                        continue
                    text = a.info.get("content", "")
                    if not text and t in ("Highlight", "Underline", "StrikeOut"):
                        try:
                            text = page.get_textbox(a.rect).strip()
                        except Exception:
                            text = ""
                    out.append({"page": i, "id": a.xref, "type": TYPE_NAMES.get(t, t), "text": text,
                                "rect": list(self._shown(page, a.rect)),
                                "color": _rgbhex(a.colors.get("stroke") or a.colors.get("fill"))})
            return out

    # ================================================================== redaction
    def find_sensitive(self, kinds=(), term=""):
        """Where the sensitive data are: [{page, bbox, text, kind}] (text and handwriting)."""
        with self.lock:
            pats = [(k, re.compile(SENSITIVE[k][1], re.I if k == "email" else 0)) for k in kinds if k in SENSITIVE]
            if term and term.strip():
                pats.append(("term", re.compile(re.escape(term.strip()), re.I)))
            hits = []
            for i, page in enumerate(self.doc):
                # lines of text with the box of every character run, to map matches to boxes
                rows = []
                for b in page.get_text("rawdict")["blocks"]:
                    for line in b.get("lines", []):
                        chars = [c for s in line["spans"] for c in s["chars"]]
                        if chars:
                            rows.append(("".join(c["c"] for c in chars),
                                         [self._shown(page, fitz.Rect(c["bbox"])) for c in chars]))
                for l in self.ink.get(i, []):
                    if l.get("text"):
                        t = l["text"]
                        x0, y0, x1, y1 = l["bbox"]
                        step = (x1 - x0) / max(len(t), 1)
                        rows.append((t, [fitz.Rect(x0 + k * step, y0, x0 + (k + 1) * step, y1) for k in range(len(t))]))
                for text, boxes in rows:
                    for kind, rx in pats:
                        for m in rx.finditer(text):
                            if kind == "card" and not _luhn(m.group()):
                                continue
                            r = fitz.Rect()
                            for b in boxes[m.start():m.end()]:
                                r |= b
                            if not r.is_empty:
                                hits.append({"page": i, "bbox": list(r), "text": m.group(), "kind": kind,
                                             "label": SENSITIVE[kind][0] if kind in SENSITIVE else "Testo"})
            # a match inside a longer one (a "phone number" inside an IBAN) is the same data
            out = []
            for h in sorted(hits, key=lambda h: -len(h["text"])):
                b = fitz.Rect(h["bbox"])
                if not any(o["page"] == h["page"] and abs(b & fitz.Rect(o["bbox"])) > abs(b) * 0.6 for o in out):
                    out.append(h)
            return sorted(out, key=lambda h: (h["page"], h["bbox"][1], h["bbox"][0]))

    def redact(self, areas, metadata=False):
        """Black out areas for good: the text under them is removed (not just covered),
        the pixels of scans are blacked out. areas: [{page, bbox}]."""
        with self.lock:
            by_page = {}
            for a in areas:
                by_page.setdefault(int(a["page"]), []).append(fitz.Rect(a["bbox"]))
            if not by_page and not metadata:
                return self.info()
            if len(by_page) == 1 and not metadata:
                self._checkpoint(next(iter(by_page)))
            else:
                self._checkpoint()
            for i, rects in by_page.items():
                page = self.doc[i]
                for r in rects:
                    page.add_redact_annot(self._text_rect(page, fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)),
                                          fill=(0, 0, 0))
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
                # what was read on the page under the black boxes is gone too
                lines = self.ink.get(i, [])
                keep = []
                for l in lines:
                    b = fitz.Rect(l["bbox"])
                    if any(abs(b & r) > abs(b) * 0.3 for r in rects):
                        self.ignored.setdefault(i, []).append(list(b))
                        continue
                    keep.append(l)
                if len(keep) != len(lines):
                    self.ink[i] = keep
                    try:
                        self._relearn(i)
                    except Exception:
                        pass
                self._touch(i)
            if metadata:
                self.doc.set_metadata({})
                try:
                    self.doc.del_xml_metadata()
                except Exception:
                    pass
            return {**self.info(), "redacted": sum(len(v) for v in by_page.values())}

    # ================================================================== headers, numbers, watermark
    def stamp_pages(self, spec):
        """Header / footer with variables ({n} {N} {data} {file}) and a watermark."""
        from .document import _append_words, _write_upright
        with self.lock:
            n = len(self.doc)
            pages = parse_pages(spec.get("pages", ""), n)
            start = int(spec.get("start") or 1)
            size = float(spec.get("size") or 10)
            color = _hex(spec.get("color") or "#333333", (0.2, 0.2, 0.2))
            align = spec.get("align", "center")
            margin = float(spec.get("margin") or 24)
            font = fitz.Font("helv")
            today = datetime.date.today().strftime("%d/%m/%Y")
            fname = os.path.splitext(os.path.basename(self.path or "Documento.pdf"))[0]
            wm = (spec.get("watermark") or "").strip()
            if not (spec.get("header") or "").strip() and not (spec.get("footer") or "").strip() and not wm:
                raise RuntimeError("Scrivi un'intestazione, un piè di pagina o una filigrana.")
            self._checkpoint()
            for k, i in enumerate(pages):
                page = self.doc[i]
                R = page.rect

                def fill(t):
                    return (t.replace("{n}", str(start + k)).replace("{N}", str(start + len(pages) - 1))
                            .replace("{data}", today).replace("{file}", fname))
                for key, y in (("header", R.y0 + margin), ("footer", R.y1 - margin + size * 0.8)):
                    t = fill((spec.get(key) or "").strip())
                    if not t:
                        continue
                    w = font.text_length(t, fontsize=size)
                    x = {"left": R.x0 + margin, "right": R.x1 - margin - w}.get(align, (R.x0 + R.x1 - w) / 2)
                    tw = fitz.TextWriter(page.rect, color=color)
                    _append_words(tw, (x, y), t, font, size)
                    _write_upright(page, tw)
                if wm:
                    wsize = float(spec.get("wm_size") or max(36, min(R.width, R.height) / max(len(wm), 4) * 1.4))
                    wcol = _hex(spec.get("wm_color") or "#c0392b", (0.75, 0.2, 0.2))
                    w = font.text_length(wm, fontsize=wsize)
                    c = fitz.Point((R.x0 + R.x1) / 2, (R.y0 + R.y1) / 2)
                    tw = fitz.TextWriter(page.rect, color=wcol)
                    _append_words(tw, (c.x - w / 2, c.y + wsize * 0.35), wm, font, wsize)
                    angle = float(spec.get("wm_angle", 45))
                    rot = fitz.Matrix(1, 0, 0, 1, -c.x, -c.y) * fitz.Matrix(-angle) * fitz.Matrix(1, 0, 0, 1, c.x, c.y)
                    _write_upright(page, tw, opacity=float(spec.get("wm_opacity") or 0.18), pre=rot)
                self._touch(i)
            return {**self.info(), "stamped": len(pages)}

    # ================================================================== comparing
    def compare_with(self, path):
        """What changed between this document and another version of it: per page, the
        boxes (on this document) of what differs, and the text added / removed."""
        with self.lock:
            other = fitz.open(path)
            if other.needs_pass:
                raise RuntimeError("Il PDF da confrontare è protetto da password.")
            pages = []
            n = max(len(self.doc), len(other))
            for i in range(n):
                if i >= len(self.doc):
                    pages.append({"page": i, "status": "added", "boxes": [], "added": [_page_text(other[i])[:200]],
                                  "removed": []})
                    continue
                if i >= len(other):
                    pages.append({"page": i, "status": "removed", "boxes": [list(self.doc[i].rect)],
                                  "added": [], "removed": [_page_text(self.doc[i])[:200]]})
                    continue
                pages.append(self._compare_page(i, other[i]))
            changed = [p for p in pages if p["status"] != "same"]
            return {"pages": pages, "changed": len(changed), "total": n,
                    "other": os.path.basename(path)}

    def _compare_page(self, i, opage):
        page = self.doc[i]
        wa = self._page_words(i, page)
        wb = [(w[4], self._shown(opage, fitz.Rect(w[:4]))) for w in opage.get_text("words")]
        if len(wa) >= 5 or len(wb) >= 5:
            sm = difflib.SequenceMatcher(None, [w for w, _ in wa], [w for w, _ in wb], autojunk=False)
            boxes, added, removed = [], [], []
            for op, a0, a1, b0, b1 in sm.get_opcodes():
                if op == "equal":
                    continue
                if a1 > a0:
                    # one box per line (a union across lines would cover half the page);
                    # rotated text (a watermark) has a huge box: listed, not boxed
                    lines = []
                    for _, b in wa[a0:a1]:
                        if abs(b) > abs(page.rect) / 12:
                            continue
                        if lines and min(b.y1, lines[-1].y1) - max(b.y0, lines[-1].y0) > 0.5 * min(b.height, lines[-1].height):
                            lines[-1] |= b
                        else:
                            lines.append(fitz.Rect(b))
                    boxes.extend(list(r) for r in lines)
                    removed.append(" ".join(w for w, _ in wa[a0:a1]))
                elif wa:
                    # inserted text: mark where it goes on this page
                    near = wa[min(a0, len(wa) - 1)][1]
                    boxes.append([near.x0 - 2, near.y0, near.x0 + 2, near.y1])
                if b1 > b0:
                    added.append(" ".join(w for w, _ in wb[b0:b1]))
            return {"page": i, "status": "changed" if boxes or added or removed else "same", "boxes": boxes,
                    "added": added[:30], "removed": removed[:30]}
        # pages without text (scans): compare the pictures
        z = 0.75
        a = _gray_pix(page, z)
        b = _gray_pix(opage, z, a.shape)
        diff = np.abs(a.astype(int) - b.astype(int)) > 60
        boxes = []
        if diff.mean() > 0.0003:
            from .writers import _components
            grow = diff.copy()
            for k in range(1, 5):
                grow[k:] |= diff[:-k]
                grow[:, k:] |= diff[:, :-k]
            for x0, y0, x1, y1, cnt in _components(grow):
                if cnt >= 6:
                    boxes.append([x0 / z, y0 / z, x1 / z, y1 / z])
        return {"page": i, "status": "changed" if boxes else "same", "boxes": boxes[:200],
                "added": [], "removed": [], "pixels": True}

    def _page_words(self, i, page):
        words = [(w[4], self._shown(page, fitz.Rect(w[:4]))) for w in page.get_text("words")]
        for l in self.ink.get(i, []):
            if l.get("text") and l.get("source") != "unread":
                for w in (l.get("words") or []):
                    if w.get("text"):
                        words.append((w["text"], fitz.Rect(w["bbox"])))
        words.sort(key=lambda w: (round(w[1].y0 / 4), w[1].x0))
        return words

    # ================================================================== optimise and protect
    def save_optimized(self, path, quality="media"):
        """A lighter copy: pictures recompressed to a resolution good for screen and print,
        duplicate objects merged, everything compressed."""
        with self.lock:
            # never compact the open document itself (see Session.save): work on a copy
            data = self.doc.tobytes()
            d = fitz.open(stream=data, filetype="pdf")
            before = len(fitz.open(stream=data, filetype="pdf").tobytes(garbage=1, deflate=True))
            dpi, q = {"alta": (220, 85), "media": (150, 75), "bassa": (110, 60)}.get(quality, (150, 75))
            try:
                d.rewrite_images(dpi_threshold=dpi + 30, dpi_target=dpi, quality=q, lossy=True,
                                 lossless=True, bitonal=False, color=True, gray=True)
            except Exception:
                pass
            d.save(path, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True,
                   use_objstms=1)
            after = os.path.getsize(path)
            return {"before": before, "after": after, "path": path}

    def save_protected(self, path, user_pw="", owner_pw="", allow_print=True, allow_copy=False, allow_edit=False):
        """A copy with a password to open it and/or limits on printing, copying, editing."""
        with self.lock:
            if not user_pw and not owner_pw:
                raise RuntimeError("Scrivi almeno una password.")
            # without an owner password the limits must still hold for whoever opens the file
            owner = owner_pw or secrets.token_hex(16)
            perm = fitz.PDF_PERM_ACCESSIBILITY
            if allow_print:
                perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
            if allow_copy:
                perm |= fitz.PDF_PERM_COPY
            if allow_edit:
                perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ASSEMBLE
            copy = fitz.open(stream=self.doc.tobytes(), filetype="pdf")       # the open document stays as it is
            copy.save(path, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_AES_256,
                          owner_pw=owner, user_pw=user_pw or "", permissions=perm)
            return {"path": path}


def _luhn(s):
    d = [int(c) for c in s if c.isdigit()]
    if len(d) < 13:
        return False
    tot = 0
    for k, v in enumerate(reversed(d)):
        if k % 2:
            v *= 2
            v -= 9 if v > 9 else 0
        tot += v
    return tot % 10 == 0


def _page_text(page):
    return " ".join(page.get_text().split())


def _gray_pix(page, zoom, shape=None):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].mean(axis=2)
    if shape is not None and a.shape != shape:
        a = np.asarray(Image.fromarray(a.astype(np.uint8)).resize((shape[1], shape[0])), np.float32)
    return a
