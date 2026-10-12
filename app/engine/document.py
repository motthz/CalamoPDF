"""PDF editing session: rendering, Word-like text editing, ink (handwriting)
editing, undo/redo and saving."""
import base64
import copy
import os
import threading

import pymupdf as fitz
import numpy as np

from . import fonts as F
from .handwriting import HandwritingLibrary
from .ocr import Learning, ai_refine, analyze_page

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


class Session:
    MAX_UNDO = 40

    def __init__(self):
        self.lock = threading.RLock()
        self.doc = None
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.ink = {}            # page index -> [ink line]
        self.hand = HandwritingLibrary()
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
            self.ink, self.page_versions = {}, {}
            self.hand.clear()
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
            self.ink, self.page_versions = {}, {}
            self.hand.clear()
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
        self.undo_stack.append((self.doc.tobytes(), copy.deepcopy(self.ink), dict(self.page_versions)))
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
        dst.append((self.doc.tobytes(), copy.deepcopy(self.ink), dict(self.page_versions)))
        data, ink, versions = src.pop()
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
    def _line_list(b):
        lines = []
        for l in b["lines"]:
            if l.get("dir", (1, 0))[0] < 0.99:      # rotated text is not editable in place
                return []
            spans = [s for s in l["spans"] if s["text"]]
            if spans:
                lines.append({"bbox": fitz.Rect(l["bbox"]), "spans": spans,
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
                lines = self._line_list(b)
                if not lines or not "".join(x["text"] for x in lines).strip():
                    continue
                text, _, _ = self._paragraph(lines, fitz.Rect(b["bbox"]))
                spans = [s for l in lines for s in l["spans"] if s["text"].strip()] or lines[0]["spans"]
                main = max(spans, key=lambda s: len(s["text"]))
                bold, italic, serif, mono = F.style_from(F.clean_name(main["font"]), main["flags"])
                o0, o1 = lines[0]["spans"][0]["origin"], lines[-1]["spans"][0]["origin"]
                lh = (o1[1] - o0[1]) / (len(lines) - 1) if len(lines) > 1 else main["size"] * 1.2
                out.append({
                    "id": bi, "bbox": list(b["bbox"]), "text": text, "nlines": len(lines),
                    "font": F.clean_name(main["font"]), "css": F.css_family(main["font"], main["flags"]),
                    "size": round(main["size"], 2), "color": _hex(main["color"]),
                    "bold": bold, "italic": italic, "lineHeight": lh,
                    "align": self._align(lines[0]["bbox"], fitz.Rect(b["bbox"])) if len(lines) == 1 else "left",
                    "firstBaseline": o0[1],
                })
            return {"blocks": out, "ink": self._ink_public(index), "version": self.page_versions.get(index, 0)}

    def _raw_block(self, page, ref):
        """ref: block bbox (preferred - indices shift after edits) or index."""
        d = page.get_text("dict", flags=self.TEXT_FLAGS)
        if isinstance(ref, (list, tuple)):
            target = fitz.Rect(ref)
            b, score = None, 0.0
            for cand in d["blocks"]:
                if cand.get("type") != 0:
                    continue
                r = fitz.Rect(cand["bbox"])
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
        return b, self._line_list(b)

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
            brect = fitz.Rect(b["bbox"])
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
                page.add_redact_annot(self._shrink(r))
            if redact:
                page.apply_redactions(**REDACT_KW)
            tws = []
            for x, y, runs in writers:
                self._write_runs(page, tws, x, y, runs, style)
            for tw in tws:
                tw.write_text(page)
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
                page.add_redact_annot(self._shrink(l["bbox"]))
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
            yy = y + size * 0.8
            for line in text.replace("\r", "").split("\n"):
                tw.append((x, yy), line, font=font, fontsize=size)
                yy += size * 1.25
            tw.write_text(page)
            self._touch(index)
            return self.blocks(index)

    def erase_area(self, index, rect):
        """Whiteout an arbitrary area (paper colour) - useful for scans."""
        with self.lock:
            page = self.doc[index]
            r = fitz.Rect(rect)
            self._checkpoint()
            bg = self._background(page, r)
            page.add_redact_annot(r, fill=False)
            page.apply_redactions(**REDACT_KW)
            page.draw_rect(r, color=None, fill=bg, overlay=True)
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
                    self.edit_block(i, list(d["blocks"][bi]["bbox"]), txt.replace(term, repl), checkpoint=False)
                    touched.append(i)
            if not touched:
                self.undo_stack.pop()
            return {"count": count, "info": self.info()}

    # ------------------------------------------------------------------ ink / handwriting
    def _ink_public(self, index):
        out = []
        for j, l in enumerate(self.ink.get(index, [])):
            out.append({"id": j, "bbox": l["bbox"], "text": l["text"], "raw": l["raw"],
                        "source": l["source"], "doubt": l["doubt"], "synthetic": l.get("synthetic", False)})
        return out

    def analyze(self, index, lang=None, api_key=None, include_native=False):
        with self.lock:
            page = self.doc[index]
            lines = analyze_page(page, self.learning, lang, include_native)
            ai_error = None
            if api_key and lines:
                try:
                    lines = ai_refine(page, lines, api_key, self.learning)
                except Exception as e:  # keep the OCR reading if the AI is unavailable
                    ai_error = str(e)
            self.ink[index] = lines
            for k in range(len(self.ink.get(index, []))):
                self.hand.forget((index, k))
            for j, l in enumerate(lines):
                self.hand.learn_line(page, l, (index, j))
            return {"ink": self._ink_public(index), "aiError": ai_error, "hand": self.hand.summary()}

    def correct_ink(self, index, line_id, text):
        """User teaches the app what is really written."""
        with self.lock:
            line = self.ink[index][line_id]
            learned = self.learning.learn(line["raw"] if line["source"] != "user" else line["text"], text) \
                if not line.get("synthetic") else 0
            line["text"] = text
            line["source"] = "user"
            line["doubt"] = 0.0
            page = self.doc[index]
            self.hand.learn_line(page, line, (index, line_id))
            return {"ink": self._ink_public(index), "learned": learned, "hand": self.hand.summary(),
                    "stats": self.learning.stats()}

    def ink_crop(self, index, line_id):
        with self.lock:
            page = self.doc[index]
            r = fitz.Rect(self.ink[index][line_id]["bbox"])
            r = fitz.Rect(r.x0 - 6, r.y0 - r.height * 0.4, r.x1 + 6, r.y1 + r.height * 0.4) & page.rect
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=r, alpha=False)
            return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()

    def preview_hand(self, text, height=None, color=None):
        with self.lock:
            h = height or self.hand.typical_height()
            res = self.hand.render(text, h, _rgb(color) if color else None)
            if not res:
                return None
            return {"img": "data:image/png;base64," + base64.b64encode(res["png"]).decode(),
                    "coverage": round(self.hand.coverage(text), 2)}

    def edit_ink(self, index, line_id, text, color=None):
        with self.lock:
            page = self.doc[index]
            line = self.ink[index][line_id]
            r = fitz.Rect(line["bbox"])
            h = r.height
            self._checkpoint()
            cover = fitz.Rect(r.x0 - 2, r.y0 - h * 0.14, r.x1 + 2, r.y1 + h * 0.14) & page.rect
            bg = self._background(page, cover)
            page.draw_rect(cover, color=None, fill=bg, overlay=True)
            if text.strip():
                ink_color = _rgb(color) if color else self.hand.ink_color()
                res = self.hand.render(text, h, ink_color, max_width_pt=page.rect.x1 - 18 - r.x0)
                if res:
                    top = r.y0 + res["top_offset_pt"]
                    rect = fitz.Rect(r.x0, top, r.x0 + res["width_pt"], top + res["height_pt"])
                    page.insert_image(rect, stream=res["png"], overlay=True)
                    line["bbox"] = [r.x0, r.y0, max(r.x0 + res["width_pt"], r.x0 + 4), r.y1]
            line["text"] = text
            line["synthetic"] = True
            line["source"] = "user"
            line["doubt"] = 0.0
            self._touch(index)
            return self.blocks(index)

    def add_hand(self, index, x, y, text, height=None, color=None):
        with self.lock:
            page = self.doc[index]
            h = float(height or self.hand.typical_height())
            self._checkpoint()
            res = self.hand.render(text, h, _rgb(color) if color else None, max_width_pt=page.rect.x1 - 18 - x)
            if not res:
                return self.blocks(index)
            top = y + res["top_offset_pt"]
            rect = fitz.Rect(x, top, x + res["width_pt"], top + res["height_pt"])
            page.insert_image(rect, stream=res["png"], overlay=True)
            self.ink.setdefault(index, []).append({
                "bbox": [x, y, x + res["width_pt"], y + h], "raw": text, "text": text, "words": [],
                "source": "user", "doubt": 0.0, "synthetic": True})
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
            self.page_versions = {i: self.page_versions.get(i, 0) + 1 for i in range(len(self.doc))}
            self._touch()
            return self.info()

    def insert_blank(self, index):
        with self.lock:
            self._checkpoint()
            ref = self.doc[min(index, len(self.doc) - 1)].rect
            self.doc.new_page(pno=index + 1, width=ref.width, height=ref.height)
            self.ink = {(k if k <= index else k + 1): v for k, v in self.ink.items()}
            self.page_versions = {i: self.page_versions.get(i, 0) + 1 for i in range(len(self.doc))}
            self._touch()
            return self.info()
