"""More editing for a Session: a searchable text layer for scans, pages copied between open
documents, tables (cells, rows), several elements moved at once (align / distribute), the
text of a page in reading order (read aloud) and the translation of a document."""
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pymupdf as fitz

from . import fonts as F


def _shown(page, r):
    return fitz.Rect(r) * page.rotation_matrix


class ExtrasMixin:

    # ================================================================== searchable scans
    def make_searchable(self, index):
        """Put an invisible text layer under what Calamo has read on the page (handwriting
        and scanned print): the scan can then be searched, selected and copied. Lines that
        already have text under them (a digital page, an earlier OCR) are left alone."""
        from .document import _append_words, _write_hidden
        with self.lock:
            page = self.doc[index]
            lines = [l for l in self.ink.get(index, [])
                     if (l.get("text") or "").strip() and l.get("source") != "unread" and not l.get("searchable")]
            if not lines:
                return {"written": 0, "page": index}
            present = [_shown(page, w[:4]) for w in page.get_text("words")]
            todo = []
            for l in lines:
                box = fitz.Rect(l["bbox"])
                under = sum(abs(box & r) for r in present)
                if under > abs(box) * 0.35:
                    l["searchable"] = True            # already searchable
                    continue
                todo.append(l)
            if not todo:
                return {"written": 0, "page": index}
            self._checkpoint(index)
            font, _ = F.system_font("Arial")
            tw = fitz.TextWriter(page.rect)
            n = 0
            for l in todo:
                words = [w for w in (l.get("words") or []) if (w.get("text") or "").strip() and w.get("bbox")]
                pieces = [(w["text"], fitz.Rect(w["bbox"])) for w in words] if words else [(l["text"], fitz.Rect(l["bbox"]))]
                for text, r in pieces:
                    h = max(r.height, 4)
                    natural = font.text_length(text, fontsize=1) or 1
                    # as wide as the ink, so a search highlights the right place
                    size = min(max(r.width / natural, h * 0.45), h * 1.3)
                    _append_words(tw, (r.x0, r.y1 - h * 0.22), text, font, size)
                    n += len(text.split())
                l["searchable"] = True
            _write_hidden(page, tw)
            self._touch(index)
            return {"written": n, "page": index}

    # ================================================================== pages between documents
    def insert_from(self, other, pages, at):
        """Copy pages of another open document (or of this one) before position `at`."""
        with self.lock:
            src = fitz.open(stream=other.doc.tobytes(), filetype="pdf") if other is self else other.doc
            n = len(self.doc)
            at = min(max(int(at), 0), n)
            pages = [int(p) for p in pages if 0 <= int(p) < len(src)]
            if not pages:
                raise RuntimeError("Nessuna pagina da copiare.")
            self._checkpoint()
            for k, p in enumerate(pages):
                pos = at + k
                self.doc.insert_pdf(src, from_page=p, to_page=p, start_at=pos if pos < len(self.doc) else -1,
                                    annots=True, links=True)
            self._reorder(list(range(at)) + [None] * len(pages) + list(range(at, n)))
            return {**self.info(), "added": len(pages), "at": at}

    # ================================================================== several elements at once
    def arrange(self, index, moves):
        """Move several elements in one step (align, distribute): moves = [{kind: 'obj' |
        'blk' | 'ink' | 'gfx', id, src, bbox / rect, dx, dy}] (gfx: an element of graphics()). One undo for all."""
        with self.lock:
            moves = [m for m in moves if abs(float(m.get("dx", 0))) + abs(float(m.get("dy", 0))) > 0.3]
            if not moves:
                return self.blocks(index)
            self._checkpoint(index)
            # pictures and marks last: rewriting text never touches them
            for m in sorted(moves, key=lambda m: {"gfx": 0, "ink": 1, "blk": 2, "obj": 3}.get(m["kind"], 4)):
                dx, dy = float(m["dx"]), float(m["dy"])
                if m["kind"] == "gfx":
                    self.move_graphic(index, {"kind": m.get("gkind"), "bbox": m["bbox"], "xref": m.get("xref")},
                                      dx, dy, checkpoint=False)
                elif m["kind"] == "blk":
                    self.move_block(index, m["bbox"], dx, dy, checkpoint=False)
                elif m["kind"] == "ink":
                    self.move_ink(index, m["id"], dx, dy, checkpoint=False)
                else:
                    r = fitz.Rect(m["rect"])
                    self.move_object(index, m["src"], m["id"], [r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy],
                                     checkpoint=False)
            return self.blocks(index)

    # ================================================================== reading aloud
    def reading_order(self, index):
        """The text of a page as it is read: digital text and what Calamo read of the scan,
        top to bottom; two columns are read one after the other."""
        with self.lock:
            page = self.doc[index]
            W = page.rect.width
            from .export import _hidden_rects
            hidden = [_shown(page, r) for r in _hidden_rects(page)]       # the OCR layer: read from the ink lines
            words = [(_shown(page, w[:4]), w[4], w[5], w[6]) for w in page.get_text("words", flags=fitz.TEXT_MEDIABOX_CLIP)]
            ink = [l for l in self.ink.get(index, []) if l.get("source") != "unread" and (l.get("text") or "").strip()]
            if ink:          # without a reading, a scanner's own OCR layer is what there is to read
                words = [w for w in words if not any(abs(w[0] & h) > abs(w[0]) * 0.5 for h in hidden)]
        # words of a line, cut where a wide gap separates two columns
        segs = []
        for r, text, block, line in words:
            if segs and segs[-1]["key"] == (block, line) and r.x0 - segs[-1]["bbox"].x1 < max(r.height * 2.5, 18):
                segs[-1]["bbox"] |= r
                segs[-1]["text"] += " " + text
            else:
                segs.append({"key": (block, line), "bbox": fitz.Rect(r), "text": text, "kind": "text"})
        segs += [{"key": None, "bbox": fitz.Rect(l["bbox"]), "text": l["text"], "kind": "ink"} for l in ink]
        mid = W / 2
        left = [s for s in segs if s["bbox"].x1 <= mid + 8]
        right = [s for s in segs if s["bbox"].x0 >= mid - 8]
        cols = len(left) >= 3 and len(right) >= 3 and len(left) + len(right) >= len(segs) * 0.9

        def col(s):
            return 0 if not cols or s["bbox"].x1 <= mid + 8 else 1
        segs.sort(key=lambda s: (col(s), round(s["bbox"].y0 / 4), s["bbox"].x0))
        # consecutive lines of one paragraph are read together
        out = []
        for s in segs:
            p = out[-1] if out else None
            if p and p["col"] == col(s) and p["kind"] == s["kind"] == "text" and p["block"] == s["key"][0] \
                    and 0 <= s["bbox"].y0 - p["last"].y1 < s["bbox"].height * 1.2:
                p["text"] += " " + s["text"]
                p["bbox"] |= s["bbox"]
                p["last"] = s["bbox"]
            else:
                out.append({"text": s["text"], "bbox": fitz.Rect(s["bbox"]), "kind": s["kind"], "col": col(s),
                            "block": s["key"][0] if s["key"] else None, "last": s["bbox"]})
        return [{"text": o["text"], "bbox": list(o["bbox"]), "kind": o["kind"]} for o in out]

    # ================================================================== tables
    def tables(self, index):
        """Tables of the page (digital ones, with lines): their cells and text."""
        with self.lock:
            return [self._table_public(t, k) for k, t in enumerate(self._find_tables(index))]

    def _find_tables(self, index):
        page = self.doc[index]
        try:
            # on the page alone, without its comments: a rectangle or an arrow drawn over a
            # table would be taken for some of its lines
            if page.first_annot is not None:
                one = fitz.open()
                one.insert_pdf(self.doc, from_page=index, to_page=index, annots=False, links=False)
                found = one[0].find_tables()
            else:
                found = page.find_tables()
        except Exception:
            return []
        out = []
        for t in found.tables:
            rows = []
            for row in t.rows:
                cells = [(_shown(page, c) if c else None) for c in row.cells]
                rows.append(cells)
            if len(rows) < 1 or max(len(r) for r in rows) < 2:
                continue
            out.append({"bbox": _shown(page, t.bbox), "rows": rows})
        return out

    def _table_public(self, t, k):
        page_rows = []
        for cells in t["rows"]:
            page_rows.append([{"bbox": list(c) if c else None} for c in cells])
        return {"id": k, "bbox": list(t["bbox"]), "rows": page_rows}

    def table_cells(self, index, tid):
        """Text of every cell of a table, for the table editor."""
        with self.lock:
            t = self._table(index, tid)
            page = self.doc[index]
            spans = self._spans(page)
            out = []
            for cells in t["rows"]:
                out.append([self._cell_text(spans, c) if c else None for c in cells])
            return {"id": tid, "bbox": list(t["bbox"]), "cells": out}

    def _table(self, index, tid):
        ts = self._find_tables(index)
        if not (0 <= int(tid) < len(ts)):
            raise RuntimeError("Tabella non trovata: forse la pagina è cambiata.")
        return ts[int(tid)]

    def _spans(self, page):
        """Visible spans of the page in displayed coordinates, by line."""
        out = []
        for b in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            for l in b.get("lines", []):
                spans = [dict(s, origin=tuple(fitz.Point(s["origin"]) * page.rotation_matrix),
                              sbbox=_shown(page, s["bbox"]))
                         for s in l["spans"] if s["text"].strip() and s.get("alpha", 255) > 0]
                if spans:
                    out.append({"bbox": _shown(page, l["bbox"]), "spans": spans})
        return out

    @staticmethod
    def _in_cell(r, cell):
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        return cell.x0 - 0.5 <= c.x <= cell.x1 + 0.5 and cell.y0 - 0.5 <= c.y <= cell.y1 + 0.5

    def _cell_lines(self, spans, cell):
        return [l for l in spans if self._in_cell(l["bbox"], cell)]

    def _cell_text(self, spans, cell):
        lines = sorted(self._cell_lines(spans, cell), key=lambda l: (round(l["bbox"].y0), l["bbox"].x0))
        return "\n".join("".join(s["text"] for s in l["spans"]).strip() for l in lines)

    def _cell_style(self, spans, cell, t):
        """Font, size, colour and alignment of the text of a cell (or of its column)."""
        lines = self._cell_lines(spans, cell)
        if not lines:
            # an empty cell writes like the cells of its column, else like the table
            col = [c for row in t["rows"][1:] for c in row if c is not None and abs(c.x0 - cell.x0) < 1.5]
            col.sort(key=lambda c: abs(c.y0 - cell.y0))
            for c in col + [c for row in t["rows"][::-1] for c in row if c is not None]:
                lines = self._cell_lines(spans, c)
                if lines:
                    s = lines[0]["spans"][0]
                    return {"span": s, "align": "left", "pad": max(min(s["sbbox"].x0 - c.x0, 8), 2),
                            "base": s["origin"][1] - c.y0}
            return {"span": None, "align": "left", "pad": 3, "base": None}
        first = min(lines, key=lambda l: l["bbox"].y0)
        s = first["spans"][0]
        lb = first["bbox"]
        left, right = lb.x0 - cell.x0, cell.x1 - lb.x1
        align = "left"
        if left > 4 and abs(left - right) < 2.5:
            align = "center"
        elif right < 4 < left:
            align = "right"
        return {"span": s, "align": align, "pad": max(min(left, 8), 2), "base": s["origin"][1] - cell.y0}

    def _clear_cell(self, page, spans, cell):
        from .document import _to_text
        n = 0
        for l in self._cell_lines(spans, cell):
            page.add_redact_annot(_to_text(page, self._shrink(l["bbox"])))
            n += 1
        return n

    def _write_cell(self, page, cell, text, st):
        """Text in a cell with the cell's style; it wraps inside the cell and gets smaller
        if it does not fit."""
        from .document import _append_words, _write_upright
        text = (text or "").strip()
        if not text:
            return
        s = st["span"]
        if s is not None:
            font = self._font_for(page, s, text, {})
            size = s["size"]
            color = tuple(((s["color"] >> k) & 255) / 255 for k in (16, 8, 0))
        else:
            font, _ = F.system_font("Arial")
            size, color = min(10.0, cell.height * 0.6), (0, 0, 0)
        pad = st["pad"]
        width = max(cell.width - 2 * pad, 6)

        def wrap(sz):
            out = []
            for para in text.split("\n"):
                line = ""
                for w in para.split(" "):
                    t = (line + " " + w).strip()
                    if line and font.text_length(t, fontsize=sz) > width:
                        out.append(line)
                        line = w
                    else:
                        line = t
                out.append(line)
            return out
        lines = wrap(size)
        for _ in range(12):
            too_wide = any(font.text_length(l, fontsize=size) > width for l in lines)
            if len(lines) * size * 1.18 <= cell.height - 1 and not too_wide or size < 5:
                break
            size *= 0.92
            lines = wrap(size)
        lh = size * 1.18
        if len(lines) == 1 and st["base"] is not None and st["base"] < cell.height:
            y = cell.y0 + st["base"]
        else:
            y = cell.y0 + (cell.height - lh * len(lines)) / 2 + size * 0.9
        tw = fitz.TextWriter(page.rect, color=color)
        for k, l in enumerate(lines):
            w = font.text_length(l, fontsize=size)
            x = {"center": cell.x0 + (cell.width - w) / 2, "right": cell.x1 - pad - w}.get(st["align"], cell.x0 + pad)
            _append_words(tw, (x, y + k * lh), l, font, size)
        _write_upright(page, tw)

    def edit_table(self, index, tid, changes):
        """changes: [{r, c, text}] - new text of some cells, written with the cell's style."""
        from .document import REDACT_KW
        with self.lock:
            page = self.doc[index]
            t = self._table(index, tid)
            spans = self._spans(page)
            todo = []
            for ch in changes:
                r, c = int(ch["r"]), int(ch["c"])
                if r >= len(t["rows"]) or c >= len(t["rows"][r]) or t["rows"][r][c] is None:
                    continue
                cell = t["rows"][r][c]
                if self._cell_text(spans, cell) == (ch.get("text") or "").strip():
                    continue
                todo.append((cell, ch.get("text") or "", self._cell_style(spans, cell, t)))
            if not todo:
                return self.table_cells(index, tid)
            self._checkpoint(index)
            if sum(self._clear_cell(page, spans, cell) for cell, _, _ in todo):
                page.apply_redactions(**REDACT_KW)
            for cell, text, st in todo:
                self._write_cell(page, cell, text, st)
            self._touch(index)
            return self.table_cells(index, tid)

    def _table_lines(self, page, t):
        """Colour and width of the table's borders (from the drawing of its lines)."""
        box = t["bbox"]
        color, width = (0, 0, 0), 0.5
        try:
            for d in page.get_drawings():
                r = _shown(page, d["rect"])
                if d.get("color") and abs(r.y1 - box.y1) < 2 and r.width > box.width * 0.3:
                    color, width = d["color"], d.get("width") or 0.5
                    break
                if d.get("color") and abs(r & box) > 0 and (r.height < 2 or r.width < 2):
                    color, width = d["color"], d.get("width") or 0.5
        except Exception:
            pass
        return color, float(width)

    def _shift_below(self, index, page, t, dy):
        """Move the text under the table by dy (a row added or removed)."""
        box = t["bbox"]
        below = [b for b in self.blocks(index)["blocks"] if b["bbox"][1] >= box.y1 - 1]
        if dy > 0:
            for b in below:
                if b["bbox"][3] + dy > page.rect.y1 - 6:
                    raise RuntimeError("Sotto la tabella non c'è spazio per una riga in più.")
            try:
                for d in page.get_drawings():
                    r = _shown(page, d["rect"])
                    if r.y0 >= box.y1 + 1 and r.y0 < box.y1 + dy and r.x1 > box.x0 and r.x0 < box.x1:
                        raise RuntimeError("Sotto la tabella c'è un disegno: non posso aggiungere una riga.")
            except RuntimeError:
                raise
            except Exception:
                pass
            for img in page.get_image_info():
                r = _shown(page, img["bbox"])
                if r.y0 >= box.y1 - 1 and r.y0 < box.y1 + dy and abs(r) < abs(page.rect) * 0.6:
                    raise RuntimeError("Sotto la tabella c'è un'immagine: non posso aggiungere una riga.")
        for b in sorted(below, key=lambda b: -b["bbox"][1] if dy > 0 else b["bbox"][1]):
            self.move_block(index, b["bbox"], 0, dy, checkpoint=False)

    def table_row(self, index, tid, r, op):
        """Add a row under row r ('add') or delete row r ('delete'). The rows below keep
        their text (moved one row down or up); the table grows or shrinks at the bottom and
        the text under the table follows."""
        from .document import REDACT_KW, _to_text, _unrot
        with self.lock:
            page = self.doc[index]
            t = self._table(index, tid)
            rows = t["rows"]
            r = int(r)
            if not (0 <= r < len(rows)):
                raise RuntimeError("Riga non trovata.")
            if any(c is None for c in rows[-1]) or len({len(x) for x in rows}) != 1:
                raise RuntimeError("Questa tabella ha celle unite: modifica le righe a mano.")
            if op == "delete" and len(rows) < 2:
                raise RuntimeError("La tabella deve avere almeno una riga.")
            spans = self._spans(page)
            texts = [[self._cell_text(spans, c) for c in row] for row in rows]
            styles = [[self._cell_style(spans, c, t) for c in row] for row in rows]
            last = fitz.Rect(rows[-1][0].x0, rows[-1][0].y0, rows[-1][-1].x1, rows[-1][0].y1)
            h = last.height
            box = t["bbox"]
            self._checkpoint(index)
            if op == "add":
                self._shift_below(index, page, t, h)
                color, lw = self._table_lines(page, t)
                # shading of the last row repeats on the new one
                try:
                    for d in page.get_drawings():
                        fr = _shown(page, d["rect"])
                        if d.get("fill") and fr.y0 >= last.y0 - 1 and fr.y1 <= last.y1 + 1 and fr.width > 3:
                            page.draw_rect(_unrot(page, fitz.Rect(fr.x0, fr.y0 + h, fr.x1, fr.y1 + h)),
                                           color=None, fill=d["fill"], overlay=True)
                except Exception:
                    pass
                y0, y1 = box.y1, box.y1 + h
                xs = sorted({round(c.x0, 2) for c in rows[-1]} | {round(rows[-1][-1].x1, 2)})
                shape = page.new_shape()
                for x in xs:
                    a, b = fitz.Point(x, y0) * page.derotation_matrix, fitz.Point(x, y1) * page.derotation_matrix
                    shape.draw_line(a, b)
                shape.draw_line(fitz.Point(box.x0, y1) * page.derotation_matrix, fitz.Point(box.x1, y1) * page.derotation_matrix)
                shape.finish(color=color, width=lw)
                shape.commit(overlay=True)
                new_rows = [list(x) for x in rows] + [[fitz.Rect(c.x0, c.y0 + h, c.x1, c.y1 + h) for c in rows[-1]]]
                moved = list(range(r + 1, len(rows)))           # these rows go one down
                cleared = sum(self._clear_cell(page, spans, c) for j in moved for c in rows[j])
                if cleared:
                    page.apply_redactions(**REDACT_KW)
                for j in moved:
                    for c, cell in enumerate(new_rows[j + 1]):
                        self._write_cell(page, cell, texts[j][c], styles[j][c])
            else:
                moved = list(range(r + 1, len(rows)))           # these rows go one up
                cleared = sum(self._clear_cell(page, spans, c) for j in [r] + moved for c in rows[j])
                if cleared:
                    page.apply_redactions(**REDACT_KW)
                for j in moved:
                    for c, cell in enumerate(rows[j - 1]):
                        self._write_cell(page, cell, texts[j][c], styles[j][c])
                # the last row goes: its own lines are removed, what is left of the borders
                # crossing it is covered with the paper
                _, lw = self._table_lines(page, t)
                band = fitz.Rect(box.x0 - lw * 2 - 1, last.y0 + lw * 0.75 + 0.2, box.x1 + lw * 2 + 1, last.y1 + lw * 2 + 0.5)
                paper = self._paper_colour(page, last)
                page.add_redact_annot(_to_text(page, band), fill=False)
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                      graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
                # a picture of the paper, not a filled rectangle: a rectangle would draw the
                # edges of a new row for whoever looks for the table's lines
                from PIL import Image
                import io as _io
                buf = _io.BytesIO()
                Image.new("RGB", (2, 2), tuple(int(v * 255) for v in paper)).save(buf, "PNG")
                xref = page.insert_image(_unrot(page, band), stream=buf.getvalue(), keep_proportion=False, overlay=True)
                from .graphics import tag_image
                tag_image(self.doc, xref, "Cover")
                self._shift_below(index, page, t, -h)
            self._touch(index)
            ts = self._find_tables(index)
            # the same table, found again (its id may change if tables moved)
            best = min(range(len(ts)), key=lambda k: abs(ts[k]["bbox"].x0 - box.x0) + abs(ts[k]["bbox"].y0 - box.y0)) \
                if ts else None
            res = self.table_cells(index, best) if best is not None else {"id": None, "cells": []}
            return {**res, "version": self.page_versions.get(index, 0)}

    def _paper_colour(self, page, rect):
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=rect & page.rect, alpha=False)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        if not a.size:
            return (1, 1, 1)
        flat = a.reshape(-1, 3)
        light = flat[flat.mean(axis=1) >= np.percentile(flat.mean(axis=1), 50)]
        return tuple(float(v) / 255 for v in np.median(light, axis=0))

    # ================================================================== translation
    def translate_into(self, target, translator, progress=None, cancelled=None):
        """Translate every text of the document in place (call it on a copy): digital text
        is rewritten in its own box with its font (smaller if the translation is longer),
        writing read on scans is rewritten with the same hand."""
        done = 0
        n = len(self.doc)
        for i in range(n):
            if cancelled and cancelled():
                raise RuntimeError("Traduzione interrotta.")
            with self.lock:
                data = self.blocks(i)
            blocks = [b for b in data["blocks"] if re.search(r"[^\W\d_]{2}", b["text"])]
            ink = [l for l in data["ink"] if l.get("source") != "unread" and re.search(r"[^\W\d_]{2}", l.get("text") or "")]
            texts = [b["text"] for b in blocks] + [l["text"] for l in ink]
            out = translator(texts, target) if texts else []
            for b, new in zip(blocks, out[:len(blocks)]):
                if not new or new.strip() == b["text"].strip():
                    continue
                ratio = len(new) / max(len(b["text"]), 1)
                style = {"size": round(b["size"] / min(ratio ** 0.5, 1.6), 2)} if ratio > 1.08 else None
                try:
                    self.edit_block(i, b["bbox"], new, style, checkpoint=False)
                    done += 1
                except Exception:
                    pass
            for l, new in zip(ink, out[len(blocks):]):
                if not new or new.strip() == l["text"].strip():
                    continue
                try:
                    self.edit_ink(i, l["id"], new)
                    done += 1
                except Exception:
                    pass
            if progress:
                progress(i, n)
        return done


# ---------------------------------------------------------------- translation service
LANGS = {"it": "Italiano", "en": "Inglese", "fr": "Francese", "de": "Tedesco", "es": "Spagnolo",
         "pt": "Portoghese", "nl": "Olandese", "ro": "Rumeno", "pl": "Polacco", "ru": "Russo",
         "uk": "Ucraino", "ar": "Arabo", "zh-CN": "Cinese", "ja": "Giapponese", "sq": "Albanese",
         "el": "Greco", "tr": "Turco", "hr": "Croato", "sl": "Sloveno", "sv": "Svedese"}


def _google(text, target):
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&dt=t&tl="
           + urllib.parse.quote(target) + "&q=" + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 CalamoPDF"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _mymemory(text, target):
    url = ("https://api.mymemory.translated.net/get?langpair=" + urllib.parse.quote("autodetect|" + target)
           + "&q=" + urllib.parse.quote(text[:480]))
    req = urllib.request.Request(url, headers={"User-Agent": "CalamoPDF"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["responseData"]["translatedText"]


def translate_texts(texts, target):
    """Translate a list of texts (line breaks kept). Online: Google Translate, MyMemory if
    that does not answer."""
    def one(text):
        parts = text.split("\n")
        out = []
        for p in parts:
            if not p.strip():
                out.append(p)
                continue
            last = None
            for fn in (_google, _mymemory):
                try:
                    out.append(fn(p, target))
                    break
                except Exception as e:      # try the other service
                    last = e
            else:
                raise RuntimeError("Traduzione non disponibile: controlla la connessione a internet. "
                                   f"({last.__class__.__name__})")
        return "\n".join(out)
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(one, texts))
