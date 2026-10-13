"""PDF editing session: rendering, Word-like text editing, ink (handwriting)
editing, undo/redo and saving."""
import base64
import copy
import difflib
import os
import threading

import pymupdf as fitz
import numpy as np

from . import fonts as F
from . import handwriting as HW
from . import writers as WR
from .handwriting import HandwritingLibrary, PageWritings
from .lexicon import LEXICON, WORD_RE
from .ocr import PRINT_BELOW, Learning, prepare_page, read_page

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


def _write_upright(page, tw):
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
        return tw.write_text(page)
    if abs(m.a - 1) + abs(m.b) + abs(m.c) + abs(m.d - 1) + abs(m.e) + abs(m.f) < 1e-6:
        return tw.write_text(page)
    tw.write_text(page, matrix=m)


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


class Session:
    MAX_UNDO = 40

    def __init__(self):
        self.lock = threading.RLock()
        self.doc = None
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.ink = {}            # page index -> [ink line]
        self.hands = {}          # page index -> PageWritings: the people / typefaces of each page
        self.learning = Learning()
        self.resolver = None
        self.version = 0         # bumps on every change, per page versions below
        self.page_versions = {}

    # ------------------------------------------------------------------ lifecycle
    def open(self, path):
        with self.lock:
            with open(path, "rb") as f:
                data = f.read()
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.needs_pass:
                raise RuntimeError("Il PDF è protetto da password.")
            self.doc, self.path = doc, path
            self.dirty = False
            self.undo_stack, self.redo_stack = [], []
            self.ink, self.page_versions, self.hands = {}, {}, {}
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
            self.path = target
            self.dirty = False
            return self.info()

    # ------------------------------------------------------------------ undo
    def _checkpoint(self):
        self.undo_stack.append((self.doc.tobytes(), copy.deepcopy(self.ink), dict(self.page_versions), dict(self.hands)))
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
        dst.append((self.doc.tobytes(), copy.deepcopy(self.ink), dict(self.page_versions), dict(self.hands)))
        data, ink, versions, self.hands = src.pop()
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
                    "version": self.page_versions.get(index, 0)}

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
            tw.append((x, y), text, font=font, fontsize=size)
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
                self._checkpoint()

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
            self._checkpoint()
            for l in lines:
                page.add_redact_annot(_to_text(page, self._shrink(l["bbox"])))
            page.apply_redactions(**REDACT_KW)
            self._touch(index)
            return self.blocks(index)

    def add_text(self, index, x, y, text, style):
        with self.lock:
            page = self.doc[index]
            self._checkpoint()
            family = style.get("family") or "Arial"
            if family == "Originale":
                family = "Arial"
            font, _ = F.system_font(family, bool(style.get("bold")), bool(style.get("italic")))
            size = float(style.get("size") or 12)
            tw = fitz.TextWriter(page.rect, color=_rgb(style.get("color") or "#111111"))
            for k, line in enumerate(text.replace("\r", "").split("\n")):
                tw.append((x, y + size * 0.8 + size * 1.25 * k), line, font=font, fontsize=size)
            _write_upright(page, tw)
            self._touch(index)
            return self.blocks(index)

    def erase_area(self, index, rect):
        """Whiteout an arbitrary area (paper colour) - useful for scans."""
        with self.lock:
            page = self.doc[index]
            r = fitz.Rect(rect)
            self._checkpoint()
            bg = self._background(page, r)
            page.add_redact_annot(_to_text(page, r), fill=False)
            page.apply_redactions(**REDACT_KW)
            page.draw_rect(_unrot(page, r), color=None, fill=bg, overlay=True)
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
                    if term.lower() in l["text"].lower():
                        hits.append({"page": i, "bbox": l["bbox"], "kind": "ink", "id": j})
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
            res = lib.render(text, xh, _rgb(color) if color else None)
            if not res:
                return None
            return {"img": "data:image/png;base64," + base64.b64encode(res["png"]).decode(),
                    "coverage": round(lib.coverage(text), 2), "style": lib.sid, "kind": lib.kind}

    def _place(self, page, lib, text, x, baseline, xh, color):
        """Draw text composed with a writing, sitting on `baseline` with x-height `xh`;
        scanned print also gets an invisible text layer, so it can still be searched and
        copied like the original OCR'd scan."""
        res = lib.render(text, xh, color, max_width_pt=page.rect.x1 - 18 - x)
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
                tw.append((x, baseline), text, font=font, fontsize=size)
                _write_hidden(page, tw)
            except Exception:
                pass
        return res

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
            self._checkpoint()
            cover = fitz.Rect(r.x0 - 2, r.y0 - h * 0.14, r.x1 + 2, r.y1 + h * 0.14) & page.rect
            bg = self._background(page, cover)
            page.draw_rect(_unrot(page, cover), color=None, fill=bg, overlay=True)
            if text.strip():
                # the line is rewritten with the letters of ITS writing only (never mixed)
                ink_color = _rgb(color) if color else lib.ink_color()
                res = self._place(page, lib, text, r.x0, base, xh, ink_color)
                if res:
                    line["bbox"] = [r.x0, r.y0, max(r.x0 + res["width_pt"], r.x0 + 4), r.y1]
            line["base"], line["xh"] = base, xh
            line["text"] = text
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
            self._checkpoint()
            res = self._place(page, lib, text, x, base, xh, _rgb(color) if color else lib.ink_color())
            if not res:
                return self.blocks(index)
            self.ink.setdefault(index, []).append({
                "bbox": [x, y, x + res["width_pt"], base + xh * 0.6], "raw": text, "text": text, "words": [],
                "source": "user", "doubt": 0.0, "synthetic": True, "kind": lib.kind, "style": lib.sid,
                "base": base, "xh": xh})
            self._touch(index)
            return self.blocks(index)

    # ------------------------------------------------------------------ pages
    def rotate_page(self, index, delta=90):
        with self.lock:
            self._checkpoint()
            p = self.doc[index]
            p.set_rotation((p.rotation + delta) % 360)
            self.ink.pop(index, None)
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
            self.page_versions = {i: self.page_versions.get(i, 0) + 1 for i in range(len(self.doc))}
            self._touch()
            return self.info()
