"""Every other element of a page, so it can be dragged like text: drawings (lines, boxes,
tables, logos made of vectors), pictures, form fields and, on a scan, the graphics drawn
in the image (a stamp, a logo, a box, a signature nobody read).

A vector element is the group of drawings that touch each other; whatever lies inside it
(the text of a table, a picture in a frame) goes with it. A scan element is a patch of
ink that is not writing: it is lifted off the paper, the paper is rebuilt under it."""
import io

import numpy as np
import pymupdf as fitz
from PIL import Image

ROLE_KEY = "CalamoRole"          # set on the pictures Calamo itself puts on a page


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def tag_image(doc, xref, role):
    """Mark a picture Calamo inserted: 'Cover' (rebuilt paper), 'Ink' (lifted or written
    writing), 'Graphic' (a lifted scan graphic). Covers and writing are not objects."""
    try:
        if xref:
            doc.xref_set_key(xref, ROLE_KEY, "/" + role)
    except Exception:
        pass


def image_role(doc, xref):
    try:
        t, v = doc.xref_get_key(xref, ROLE_KEY)
        return v.lstrip("/") if t == "name" else None
    except Exception:
        return None


def _union(rects):
    """Bounding box of rects, also of lines (rects with no width or height, which
    PyMuPDF's own union leaves out)."""
    return fitz.Rect(min(r.x0 for r in rects), min(r.y0 for r in rects),
                     max(r.x1 for r in rects), max(r.y1 for r in rects))


def _inside(r, area):
    return area.x0 <= r.x0 and area.y0 <= r.y0 and r.x1 <= area.x1 and r.y1 <= area.y1


def _iou(a, b, grow=1.0):
    a = fitz.Rect(a.x0 - grow, a.y0 - grow, a.x1 + grow, a.y1 + grow)
    b = fitz.Rect(b.x0 - grow, b.y0 - grow, b.x1 + grow, b.y1 + grow)
    i = a & b
    if i.is_empty:
        return 0.0
    return abs(i) / max(abs(a) + abs(b) - abs(i), 1e-6)


def _reach(p):
    """The box MuPDF gives a drawing when it removes what a redaction covers: a stroke
    counts half its width, and ten widths (the miter limit) where it turns a corner."""
    r = fitz.Rect(p["rect"])
    w = float(p.get("width") or 0) if "s" in (p.get("type") or "") else 0.0
    items = p.get("items") or []
    simple = all(it[0] == "re" for it in items) or (len(items) == 1 and items[0][0] == "l")
    e = w / 2 if simple else w * 10
    return fitz.Rect(r.x0 - e, r.y0 - e, r.x1 + e, r.y1 + e)


def _vec(page, dx, dy):
    """A displacement on screen as a displacement in the page's own coordinates."""
    m = page.derotation_matrix
    return fitz.Point(dx, dy) * m - fitz.Point(0, 0) * m


def _dilate(mask, r):
    """Square dilation of a boolean image by r pixels (box filter)."""
    if r <= 0:
        return mask
    m = mask.astype(np.int32)
    cs = np.cumsum(np.pad(m, ((0, 0), (r + 1, r))), axis=1)
    m = (cs[:, 2 * r + 1:] - cs[:, :-2 * r - 1]) > 0
    cs = np.cumsum(np.pad(m.astype(np.int32), ((r + 1, r), (0, 0))), axis=0)
    return (cs[2 * r + 1:] - cs[:-2 * r - 1]) > 0


def _smooth(v, k=9):
    """Running median along the first axis (a word touching the band is ignored)."""
    if len(v) < 3:
        return v
    k = min(k, len(v) - (1 - len(v) % 2))
    pad = k // 2
    p = np.pad(v, ((pad, pad),) + ((0, 0),) * (v.ndim - 1), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(p, k, axis=0)
    return np.median(win, axis=-1)


def ink_alpha(rgb, k=20):
    """How strongly each pixel is drawn over the paper around it, 0..1, whatever the
    colour (a yellow marker counts as much as black ink)."""
    from .letters import paper
    o = rgb[:, :, :3].astype(np.float32)
    p = np.dstack([paper(rgb[:, :, c].astype(np.float32), k) for c in range(3)])
    p = np.maximum(p, 1)
    return np.clip(((p - o) / p).max(axis=2), 0, 1)


def paper_under(rgb, box, band=6):
    """The paper inside box (x0, y0, x1, y1 px of rgb), rebuilt from the paper around it:
    each side gives its colour along its length, and the inside blends the four sides."""
    x0, y0, x1, y1 = box
    H, W = rgb.shape[:2]
    a = rgb.astype(np.float32)

    def side(sl_rows, sl_cols, axis):
        part = a[sl_rows, sl_cols]
        if part.size == 0 or part.shape[axis] == 0:
            return None
        # across the band, the median: paper, not a stroke crossing it
        return _smooth(np.median(part, axis=axis), 15)

    top = side(slice(max(y0 - band, 0), y0), slice(x0, x1), 0)
    bot = side(slice(y1, min(y1 + band, H)), slice(x0, x1), 0)
    left = side(slice(y0, y1), slice(max(x0 - band, 0), x0), 1)
    right = side(slice(y0, y1), slice(x1, min(x1 + band, W)), 1)
    h, w = y1 - y0, x1 - x0
    est = []
    if top is not None or bot is not None:
        t_, b_ = top if top is not None else bot, bot if bot is not None else top
        t = np.linspace(0, 1, h)[:, None, None]
        est.append(t_[None] * (1 - t) + b_[None] * t)
    if left is not None or right is not None:
        l_, r_ = left if left is not None else right, right if right is not None else left
        t = np.linspace(0, 1, w)[None, :, None]
        est.append(l_[:, None] * (1 - t) + r_[:, None] * t)
    if not est:
        return np.full((h, w, 3), 255, np.float32)
    # each estimate is best near its own sides; their mean is good enough in the middle
    return np.mean(est, axis=0)


def lift(rgb, box, paper):
    """The graphic in box over the paper: (RGBA uint8, mask of its pixels).
    Each pixel is seen as some colour laid over the paper with some opacity; the
    smallest opacity explaining it is taken (so black stays black, yellow stays yellow)."""
    x0, y0, x1, y1 = box
    o = rgb[y0:y1, x0:x1].astype(np.float32)
    p = np.maximum(paper, 1)
    a = np.clip(((p - o) / p).max(axis=2), 0, 1)
    a[a < 0.12] = 0
    mask = _dilate(a > 0.18, 2) & (a > 0)
    a = np.where(mask, a, 0)
    k = np.maximum(a, 1e-3)[..., None]
    colour = np.clip((o - (1 - k) * paper) / k, 0, 255)
    rgba = np.dstack([colour, a * 255]).astype(np.uint8)
    return rgba, mask


class GraphicsMixin:
    # ------------------------------------------------------------------ finding the elements
    @staticmethod
    def _vector_groups(page):
        """Drawings that touch each other, as groups: [(rect, [paths])] in the page's
        own coordinates. The page background and frames around the whole page are left out."""
        area = abs(page.rect) or 1
        paths = []
        for p in page.get_drawings():
            r = fitz.Rect(p["rect"])
            if p.get("fill") is None and p.get("color") is None:
                continue                                    # invisible (a clip)
            if abs(r) > area * 0.5:
                continue                                    # background, page frame
            paths.append((r, p))
        if not paths:
            return []
        tol = 1.2
        n = len(paths)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        order = sorted(range(n), key=lambda i: paths[i][0].x0)
        active = []
        for i in order:
            r = paths[i][0]
            active = [j for j in active if paths[j][0].x1 + 2 * tol >= r.x0]
            for j in active:
                q = paths[j][0]
                if q.y0 - tol <= r.y1 + tol and r.y0 - tol <= q.y1 + tol:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
            active.append(i)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        out = []
        for members in groups.values():
            rect = _union([paths[i][0] for i in members])
            if abs(rect) > area * 0.7 or (rect.width < 2 and rect.height < 2):
                continue
            out.append((rect, [paths[i][1] for i in sorted(members, key=lambda i: paths[i][1].get("seqno", 0))]))
        out.sort(key=lambda g: (g[0].y0, g[0].x0))
        return out

    def _content_images(self, page):
        """Pictures of the page content that are elements of their own (not the scan behind
        the page, not what Calamo put there to rebuild paper or writing)."""
        area = abs(page.rect)
        out = []
        for im in page.get_image_info(xrefs=True):
            x = im.get("xref")
            r = fitz.Rect(im["bbox"])
            if not x or r.is_empty or abs(r & fitz.Rect(page.mediabox)) > area * 0.5:
                continue
            if im.get("width", 9) <= 2 and im.get("height", 9) <= 2:
                continue
            if image_role(self.doc, x) in ("Cover", "Ink"):
                continue
            out.append((r, im))
        return out

    @staticmethod
    def _is_scan(page):
        area = abs(page.rect)
        return any(abs(fitz.Rect(i["bbox"]) & fitz.Rect(page.mediabox)) > area * 0.5 for i in page.get_image_info())

    def graphics(self, index):
        """The elements of a page that are not text: [{id, kind, label, bbox}] in displayed
        coordinates. kind: 'vector', 'image', 'field', 'scan'."""
        cache = self.__dict__.setdefault("_gfx_cache", {})
        with self.lock:
            page = self.doc[index]
            ink = self.ink.get(index, [])
            key = (id(self.doc), self.page_versions.get(index, 0), tuple(tuple(l["bbox"]) for l in ink))
            hit = cache.get(index)
            if hit and hit[0] == key:
                return hit[1]
            shown = lambda r: fitz.Rect(r) * page.rotation_matrix
            out = []
            for rect, paths in self._vector_groups(page):
                n_text = len(page.get_text("words", clip=rect))
                out.append({"kind": "vector", "bbox": list(shown(rect)),
                            "label": "Tabella" if n_text >= 4 and len(paths) >= 4 else "Disegno"})
            for r, im in self._content_images(page):
                out.append({"kind": "image", "bbox": list(shown(r)), "xref": im["xref"],
                            "label": "Grafica" if image_role(self.doc, im["xref"]) == "Graphic" else "Immagine"})
            for w in page.widgets() or []:
                out.append({"kind": "field", "bbox": list(shown(w.rect)), "xref": w.xref,
                            "label": "Campo modulo"})
            scan = None
            if self._is_scan(page):
                zoom = 2.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
                text = [l["bbox"] for l in ink] + [list(shown(w[:4])) for w in page.get_text("words")]
                scan = (rgb, zoom, text, [g["bbox"] for g in out], bool(ink))
        if scan is not None:
            try:
                out += [{"kind": "scan", "bbox": b, "label": "Grafica"} for b in self._scan_parts(*scan)]
            except Exception:
                pass
        for k, g in enumerate(out):
            g["id"] = k
        with self.lock:
            if self.page_versions.get(index, 0) == key[1]:
                cache[index] = (key, out)
        return out

    @staticmethod
    def _scan_parts(rgb, zoom, text, hard, analysed):
        """Patches of ink of a scan that are not writing: boxes in page points.
        text: the lines of writing (read, or found as text-like when not read yet);
        hard: elements already found (drawings, pictures), left out."""
        from . import writers as WR
        mask = ink_alpha(rgb) > 0.25
        H, W = mask.shape

        def px(r, pad_x=0.0, pad_y=0.0):
            return (int(max(r[0] * zoom - pad_x, 0)), int(max(r[1] * zoom - pad_y, 0)),
                    int(min(r[2] * zoom + pad_x, W)), int(min(r[3] * zoom + pad_y, H)))
        for r in hard:
            x0, y0, x1, y1 = px(r, 2, 2)
            mask[y0:y1, x0:x1] = False
        text = [list(b) for b in text]
        if not analysed:
            # not read yet: what looks like lines of text is writing, not a graphic
            try:
                text += WR.unread_lines(rgb, zoom, text + list(hard))
            except Exception:
                pass
        in_text = np.zeros_like(mask)
        tboxes = []
        for r in text:
            h = (r[3] - r[1]) * zoom
            x0, y0, x1, y1 = px(r, h * 0.4, h * 0.25)
            in_text[y0:y1, x0:x1] = True
            tboxes.append((x0, y0, x1, y1))

        def within_line(x0, y0, x1, y1):
            """Does the piece fit in one line of writing? (a stamp's ring is cut by two)"""
            a = max((x1 - x0) * (y1 - y0), 1)
            return any(max(min(x1, b[2]) - max(x0, b[0]), 0) * max(min(y1, b[3]) - max(y0, b[1]), 0) >= a * 0.75
                       for b in tboxes)
        grown = _dilate(mask, int(1.5 * zoom))
        found = []
        area = W * H
        for x0, y0, x1, y1, n in WR._components(grown):
            w, h = (x1 - x0) / zoom, (y1 - y0) / zoom
            if max(w, h) < 8 or min(w, h) < 1.5 or (x1 - x0) * (y1 - y0) > area * 0.5:
                continue
            ink = mask[y0:y1, x0:x1]
            total = int(ink.sum())
            if total < 25 * zoom * zoom:
                continue                                    # specks
            if (ink & in_text[y0:y1, x0:x1]).sum() >= total * 0.6 and within_line(x0, y0, x1, y1):
                continue                                    # writing: a line, not a graphic
            found.append((x0, y0, x1, y1))
        # what lies inside a bigger graphic (the words of a stamp) is part of it
        out = []
        for b in found:
            if any(o != b and o[0] <= b[0] + 2 and o[1] <= b[1] + 2 and b[2] <= o[2] + 2 and b[3] <= o[3] + 2
                   for o in found):
                continue
            out.append([float(b[0]) / zoom, float(b[1]) / zoom, float(b[2]) / zoom, float(b[3]) / zoom])
        return out

    # ------------------------------------------------------------------ moving them
    def move_graphic(self, index, item, dx, dy, checkpoint=True):
        """Drag an element found by graphics(): item = {kind, bbox, xref?} (displayed
        coordinates), dx/dy on screen."""
        from .document import _overlap
        with self.lock:
            page = self.doc[index]
            box = fitz.Rect(item["bbox"])
            R = page.rect
            dx = max(min(float(dx), R.x1 - box.x1), R.x0 - box.x0)
            dy = max(min(float(dy), R.y1 - box.y1), R.y0 - box.y0)
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return self.blocks(index)
            kind = item.get("kind")
            if checkpoint:
                self._checkpoint(index)
            if kind == "vector":
                self._move_vector(page, index, box, dx, dy)
            elif kind == "image":
                self._move_image(page, int(item["xref"]), box, dx, dy)
            elif kind == "field":
                w = next((w for w in page.widgets() if w.xref == int(item["xref"])), None)
                if w is None:
                    raise RuntimeError("Campo non trovato.")
                v = _vec(page, dx, dy)
                w.rect = fitz.Rect(w.rect) + (v.x, v.y, v.x, v.y)
                w.update()
            elif kind == "scan":
                self._move_scan(page, box, dx, dy)
            else:
                raise RuntimeError("Elemento sconosciuto.")
            # what Calamo read of the scan inside the element went with it
            for l in self.ink.get(index, []):
                if _overlap(l["bbox"], list(box)) > 0.8 and kind in ("vector", "scan"):
                    x0, y0, x1, y1 = l["bbox"]
                    l["bbox"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
                    for wd in l.get("words") or []:
                        if wd.get("bbox"):
                            a, b, c, d = wd["bbox"]
                            wd["bbox"] = [a + dx, b + dy, c + dx, d + dy]
            self._touch(index)
            res = self.blocks(index)
            res["moved"] = [box.x0 + dx, box.y0 + dy, box.x1 + dx, box.y1 + dy]
            return res

    def _move_vector(self, page, index, box, dx, dy):
        """The drawings of a group, and the text, pictures and fields inside it."""
        from .document import REDACT_KW, _append_words, _to_text, _write_upright
        own = box * page.derotation_matrix
        best, score = None, 0.0
        for rect, paths in self._vector_groups(page):
            s = _iou(rect, own)
            if s > score:
                best, score = (rect, paths), s
        if best is None or score < 0.5:
            raise RuntimeError("Il disegno è cambiato nel frattempo: riprova.")
        rect, members = best
        area = fitz.Rect(rect.x0 - 1.5, rect.y0 - 1.5, rect.x1 + 1.5, rect.y1 + 1.5)
        # the removal takes every drawing wholly inside its box (as MuPDF measures them):
        # those are the ones drawn again
        red = _union([_reach(p) for p in members])
        red = fitz.Rect(red.x0 - 0.3, red.y0 - 0.3, red.x1 + 0.3, red.y1 + 0.3)
        paths = [p for p in page.get_drawings()
                 if (p.get("fill") is not None or p.get("color") is not None) and _inside(_reach(p), red)]
        v = _vec(page, dx, dy)
        # text wholly inside (a table's cells, the label of a box)
        d = page.get_text("dict", flags=self.TEXT_FLAGS)
        pieces, lines = [], []
        for b in d["blocks"]:
            if b.get("type") != 0 or not _inside(fitz.Rect(b["bbox"]), area):
                continue
            for l in self._line_list(b, page):
                lines.append(l)
                pieces += [sp for sp in l["spans"] if sp["text"].strip()]
        images = [(r, im) for r, im in self._content_images(page) if _inside(r, area)]
        fields = [w for w in (page.widgets() or []) if _inside(fitz.Rect(w.rect), area)]
        pngs = []
        for r, im in images:
            if self._image_uses(im["xref"]) > 1:
                continue
            pngs.append((r, im["xref"], self._image_png(im["xref"])))
        # away from where they were
        for l in lines:
            page.add_redact_annot(_to_text(page, self._shrink(l["bbox"])))
        if lines:
            page.apply_redactions(**REDACT_KW)
        page.add_redact_annot(red, fill=False)
        kw = dict(REDACT_KW, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
        if "text" in kw:
            kw["text"] = fitz.PDF_REDACT_TEXT_NONE
        page.apply_redactions(**kw)
        for _, xref, _ in pngs:
            page.delete_image(xref)
        # and drawn again where they go, in their order
        shape = page.new_shape()
        for p in paths:
            for it in p["items"]:
                op = it[0]
                if op == "l":
                    shape.draw_line(it[1] + v, it[2] + v)
                elif op == "re":
                    shape.draw_rect(fitz.Rect(it[1]) + (v.x, v.y, v.x, v.y))
                elif op == "qu":
                    shape.draw_quad(fitz.Quad([q + v for q in fitz.Quad(it[1])]))
                elif op == "c":
                    shape.draw_bezier(it[1] + v, it[2] + v, it[3] + v, it[4] + v)
            cap = p.get("lineCap") or 0
            shape.finish(fill=p.get("fill"), color=p.get("color"), width=p.get("width") or 0,
                         lineCap=max(cap) if isinstance(cap, (list, tuple)) else int(cap),
                         lineJoin=int(p.get("lineJoin") or 0), dashes=p.get("dashes") or None,
                         even_odd=bool(p.get("even_odd")), closePath=bool(p.get("closePath")),
                         fill_opacity=p.get("fill_opacity") if p.get("fill_opacity") is not None else 1,
                         stroke_opacity=p.get("stroke_opacity") if p.get("stroke_opacity") is not None else 1)
        shape.commit()
        for r, xref, png in pngs:
            nx = page.insert_image(fitz.Rect(r) + (v.x, v.y, v.x, v.y), stream=png, keep_proportion=False)
            tag_image(self.doc, nx, image_role(self.doc, xref) or "Graphic")
        for w in fields:
            w.rect = fitz.Rect(w.rect) + (v.x, v.y, v.x, v.y)
            w.update()
        tws = []
        for sp in pieces:
            font = self._font_for(page, sp, sp["text"], {})
            color = tuple(((sp["color"] >> k) & 255) / 255 for k in (16, 8, 0))
            tw = fitz.TextWriter(page.rect, color=color)
            _append_words(tw, (sp["origin"][0] + dx, sp["origin"][1] + dy), sp["text"], font, sp["size"])
            tws.append(tw)
        for tw in tws:
            _write_upright(page, tw)

    def _move_image(self, page, xref, box, dx, dy):
        own = box * page.derotation_matrix
        found = None
        for r, im in self._content_images(page):
            if im["xref"] == xref and (found is None or abs(r & own) > abs(found[0] & own)):
                found = (r, im)
        if found is None:
            raise RuntimeError("Immagine non trovata (forse è già stata spostata).")
        if self._image_uses(xref) > 1:
            raise RuntimeError("Questa immagine è usata in più punti del documento: non la sposto per non cambiarli tutti.")
        r, im = found
        t = im.get("transform") or (1, 0, 0, 1, 0, 0)
        if abs(t[1]) > 1e-3 or abs(t[2]) > 1e-3 or t[0] < 0 or t[3] < 0:
            raise RuntimeError("Questa immagine è ruotata: spostala con lo strumento Oggetti.")
        png = self._image_png(xref)
        role = image_role(self.doc, xref)
        page.delete_image(xref)
        v = _vec(page, dx, dy)
        nx = page.insert_image(fitz.Rect(r) + (v.x, v.y, v.x, v.y), stream=png, keep_proportion=False)
        tag_image(self.doc, nx, role or "Graphic")

    def _move_scan(self, page, box, dx, dy, zoom=3.0):
        """Lift a graphic of the scan: the paper is rebuilt under it, and it is laid down
        again, with its own colours and soft edges, dx/dy further on."""
        from .document import _unrot
        m = 6.0
        ctx = fitz.Rect(box.x0 - m, box.y0 - m, box.x1 + m, box.y1 + m) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=ctx, alpha=False, annots=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        ox, oy = pix.x / zoom, pix.y / zoom
        x0, y0 = max(int((box.x0 - ox) * zoom), 0), max(int((box.y0 - oy) * zoom), 0)
        x1 = min(int(round((box.x1 - ox) * zoom)), rgb.shape[1])
        y1 = min(int(round((box.y1 - oy) * zoom)), rgb.shape[0])
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise RuntimeError("Elemento troppo piccolo.")
        paper = paper_under(rgb, (x0, y0, x1, y1), band=max(int(5 * zoom), 3))
        rgba, mask = lift(rgb, (x0, y0, x1, y1), paper)
        # only the pieces of this element: not the end of a line of writing that
        # reaches into its box
        from .handwriting import HandwritingLibrary
        lab, stats = HandwritingLibrary._strokes(_dilate(ink_alpha(rgb, int(7 * zoom)) > 0.25, int(1.5 * zoom)))
        tol = 2 * zoom
        keep = [k for k, (a0, a1, _, b0, b1) in stats.items()
                if a0 >= x0 - tol and a1 <= x1 + tol and b0 >= y0 - tol and b1 <= y1 + tol]
        if keep:
            # the boxes of its pieces (a filled shape is ink inside too), less the others
            own = np.zeros((y1 - y0, x1 - x0), bool)
            for k in keep:
                a0, a1, _, b0, b1 = stats[k]
                own[max(b0 - y0, 0):max(b1 - y0, 0), max(a0 - x0, 0):max(a1 - x0, 0)] = True
            sub = lab[y0:y1, x0:x1]
            own &= ~((sub > 0) & ~np.isin(sub, keep))
            if (mask & own).sum() >= mask.sum() * 0.5:      # else it touches something now: all of the box
                mask &= own
                rgba[:, :, 3] = np.where(own, rgba[:, :, 3], 0)
        if not mask.any():
            raise RuntimeError("Non trovo inchiostro in questo elemento.")
        # the paper where it was
        orig = rgb[y0:y1, x0:x1].astype(np.float32)
        # the paper with the grain of the scan around it
        ring = np.concatenate([rgb[max(y0 - 6, 0):y0, x0:x1].reshape(-1, 3), rgb[y1:y1 + 6, x0:x1].reshape(-1, 3)]).astype(np.float32)
        fill = paper
        if len(ring) > 20:
            dev = ring - np.median(ring, axis=0)
            mad = float(np.median(np.abs(dev)))
            dev = dev[np.abs(dev).max(axis=1) <= min(2.5 * mad + 1, 14)]
            if mad >= 1 and len(dev):           # a flat (digital) paper has no grain
                rng = np.random.default_rng(0)
                fill = paper + dev[rng.integers(0, len(dev), size=paper.shape[:2])]
        # around the strokes too: a scan's compression leaves a halo there (not over other ink)
        o_alpha = np.clip(((np.maximum(paper, 1) - orig) / np.maximum(paper, 1)).max(axis=2), 0, 1)
        area = _dilate(mask, 1) | (_dilate(mask, int(1.5 * zoom)) & (o_alpha < 0.3))
        cover = np.where(area[..., None], fill, orig)
        cover = np.clip(cover, 0, 255).astype(np.uint8)
        target = fitz.Rect(ox + x0 / zoom, oy + y0 / zoom, ox + x1 / zoom, oy + y1 / zoom)
        xref = page.insert_image(_unrot(page, target), stream=_png(Image.fromarray(cover, "RGB")),
                                 rotate=page.rotation, overlay=True)
        tag_image(self.doc, xref, "Cover")
        # the graphic where it goes, cut to its ink
        ys, xs = np.nonzero(rgba[:, :, 3] > 0)
        ya, yb, xa, xb = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        part = fitz.Rect(target.x0 + xa / zoom + dx, target.y0 + ya / zoom + dy,
                         target.x0 + xb / zoom + dx, target.y0 + yb / zoom + dy)
        xref = page.insert_image(_unrot(page, part), stream=_png(Image.fromarray(rgba[ya:yb, xa:xb], "RGBA")),
                                 rotate=page.rotation, overlay=True)
        tag_image(self.doc, xref, "Graphic")
