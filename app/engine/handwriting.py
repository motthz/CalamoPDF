"""Handwriting style capture and synthesis.

The app learns the writer's own letter shapes from the handwritten words on the
page (using the recognised - and user-corrected - text as labels), then composes
new text from those glyphs, matching size, ink colour and rhythm. Letters never
seen fall back to a handwriting font tinted with the same ink.
"""
import base64
import io
import os
import random

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DPI = 300
S = DPI / 72.0
FALLBACK_FONTS = ["Inkfree.ttf", "segoepr.ttf", "segoesc.ttf"]
SAME_SHAPE = set("cosuvwxzpk")
ACCENTS = {"à": ("a", "grave"), "è": ("e", "grave"), "é": ("e", "acute"), "ì": ("i", "grave"),
           "ò": ("o", "grave"), "ù": ("u", "grave"), "á": ("a", "acute"), "í": ("i", "acute"),
           "ó": ("o", "acute"), "ú": ("u", "acute"), "È": ("E", "grave"), "À": ("A", "grave")}


def _otsu(gray):
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    sum_all = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thr = 0.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


class Glyph:
    __slots__ = ("alpha", "top", "bottom", "width", "join_left", "join_right", "src", "quality")

    def __init__(self, alpha, top, bottom, width, join_left, join_right, src=None, quality=1.0):
        self.src = src              # which ink line it was learned from
        self.quality = quality      # 1 = clean gap segmentation, lower = forced split
        self.alpha = alpha          # uint8 array, ink coverage
        self.top = top              # relative to source word box height (0 = top of box)
        self.bottom = bottom
        self.width = width          # relative to word box height
        self.join_left = join_left  # stroke continues into neighbour (cursive)
        self.join_right = join_right


class HandwritingLibrary:
    def __init__(self):
        self.glyphs = {}      # char -> [Glyph]
        self.inks = []        # observed ink colours
        self.heights = []     # observed line heights (pt)
        self.words_seen = 0

    # ------------------------------------------------------------------ learning
    def clear(self):
        self.__init__()

    def forget(self, src):
        for ch in list(self.glyphs):
            self.glyphs[ch] = [g for g in self.glyphs[ch] if g.src != src]
            if not self.glyphs[ch]:
                del self.glyphs[ch]

    def learn_line(self, page, line, src=None):
        """line: ink line dict with 'words' (text,bbox) and corrected 'text'."""
        if src is not None:
            self.forget(src)
        if line.get("synthetic") or (line.get("source") == "ocr" and line.get("doubt", 0) > 0.4):
            return
        words = line["words"]
        labels = line["text"].split()
        if len(labels) != len(words):
            # the user changed the word count: only learn if a single word maps to the whole line
            if len(words) == 1 and len(labels) >= 1:
                labels = ["".join(labels)]
            else:
                return
        self.heights.append(line["bbox"][3] - line["bbox"][1])
        for w, label in zip(words, labels):
            try:
                self._learn_word(page, fitz.Rect(w["bbox"]), label, src)
            except Exception:
                pass

    def _learn_word(self, page, rect, label, src=None):
        label = "".join(c for c in label if not c.isspace())
        if not label or rect.height < 3:
            return
        Hw = rect.height
        pad = Hw * 0.35
        clip = fitz.Rect(rect.x0 - 1, rect.y0 - pad, rect.x1 + 1, rect.y1 + pad) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(S, S), clip=clip, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
        bg = float(np.percentile(gray, 92))
        thr = min(_otsu(gray), bg - 25)
        ink = gray < thr
        if ink.mean() < 0.005:
            return
        dark = rgb[ink]
        if len(dark):
            lum = dark.mean(axis=1)
            core = dark[lum <= np.percentile(lum, 35)]
            self.inks.append(core.mean(axis=0).tolist())
        darkest = float(np.percentile(gray[ink], 5))
        alpha = np.clip((bg - gray.astype(np.float32)) / max(bg - darkest, 1) * 255, 0, 255).astype(np.uint8)
        alpha[~ink & (alpha < 90)] = 0
        box_top_px = (rect.y0 - clip.y0) * S
        Hpx = Hw * S

        profile = ink.sum(axis=0)
        cols = profile > 0
        segs, start = [], None
        for x, on in enumerate(cols):
            if on and start is None:
                start = x
            elif not on and start is not None:
                segs.append([start, x, False, False])
                start = None
        if start is not None:
            segs.append([start, len(cols), False, False])
        if not segs:
            return
        # drop specks
        segs = [s for s in segs if ink[:, s[0]:s[1]].sum() > Hpx * 0.15] or segs
        n = len(label)
        # merge segments closest to each other until they fit
        while len(segs) > n:
            gaps = [segs[i + 1][0] - segs[i][1] for i in range(len(segs) - 1)]
            i = int(np.argmin(gaps))
            segs[i] = [segs[i][0], segs[i + 1][1], segs[i][2], segs[i + 1][3]]
            del segs[i + 1]
        quality = 1.0
        if len(segs) < n:
            quality = 0.5
            widths = np.array([s[1] - s[0] for s in segs], dtype=np.float64)
            alloc = np.maximum(1, np.round(widths / widths.sum() * n)).astype(int)
            while alloc.sum() > n:
                j = int(np.argmax(np.where(alloc > 1, alloc, 0)))
                alloc[j] -= 1
            while alloc.sum() < n:
                alloc[int(np.argmax(widths / alloc))] += 1
            new = []
            for s, k in zip(segs, alloc):
                a, b = s[0], s[1]
                if k == 1:
                    new.append(s)
                    continue
                cuts = [a]
                step = (b - a) / k
                for m in range(1, k):
                    c = a + step * m
                    lo, hi = int(max(cuts[-1] + 2, c - step * 0.35)), int(min(b - 2, c + step * 0.35))
                    if hi <= lo:
                        cut = int(c)
                    else:
                        cut = lo + int(np.argmin(profile[lo:hi]))
                    cuts.append(cut)
                cuts.append(b)
                for m in range(k):
                    new.append([cuts[m], cuts[m + 1], m > 0 or s[2], m < k - 1 or s[3]])
            segs = new
        if len(segs) != n:
            return
        for ch, (a, b, jl, jr) in zip(label, segs):
            col_ink = ink[:, a:b]
            rows = np.where(col_ink.any(axis=1))[0]
            if len(rows) == 0:
                continue
            r0, r1 = rows[0], rows[-1] + 1
            g = Glyph(alpha[r0:r1, a:b].copy(),
                      (r0 - box_top_px) / Hpx, (r1 - box_top_px) / Hpx, (b - a) / Hpx, jl, jr, src, quality if (jl or jr) else max(quality, 0.8))
            bucket = self.glyphs.setdefault(ch, [])
            if len(bucket) < 12:
                bucket.append(g)
        self.words_seen += 1

    # ------------------------------------------------------------------ info
    def ink_color(self):
        if not self.inks:
            return (0.12, 0.16, 0.35)
        c = np.median(np.array(self.inks), axis=0) / 255.0
        return tuple(float(v) for v in c)

    def typical_height(self):
        return float(np.median(self.heights)) if self.heights else 14.0

    def summary(self):
        chars = sorted(self.glyphs.keys(), key=lambda c: (not c.isalpha(), c.lower(), c))
        out = []
        for c in chars:
            g = self.glyphs[c][0]
            img = Image.fromarray(255 - g.alpha)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            out.append({"char": c, "count": len(self.glyphs[c]),
                        "img": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
        return {"glyphs": out, "words": self.words_seen,
                "ink": "#%02x%02x%02x" % tuple(int(v * 255) for v in self.ink_color())}

    # ------------------------------------------------------------------ synthesis
    def _fallback_font(self, px):
        win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for name in FALLBACK_FONTS:
            p = os.path.join(win, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, max(int(px), 6))
        return ImageFont.load_default()

    def _font_glyph(self, ch, Hpx):
        font = self._fallback_font(Hpx * 1.05)
        bbox = font.getbbox(ch)
        w = max(bbox[2] - bbox[0], 1)
        img = Image.new("L", (w + 4, int(Hpx * 2)), 0)
        d = ImageDraw.Draw(img)
        d.text((2 - bbox[0], 0), ch, fill=255, font=font)
        a = np.array(img)
        rows = np.where(a.max(axis=1) > 0)[0]
        if len(rows) == 0:
            return None
        # place font baseline roughly at 80% of the box, like the OCR boxes
        ascent, _ = font.getmetrics()
        top = (rows[0] - ascent) / Hpx + 0.82
        bottom = (rows[-1] + 1 - ascent) / Hpx + 0.82
        return Glyph(a[rows[0]:rows[-1] + 1], top, bottom, a.shape[1] / Hpx, False, False)

    def coverage(self, text):
        chars = [c for c in text if not c.isspace()]
        if not chars:
            return 1.0
        ok = sum(1 for c in chars if c in self.glyphs or c.swapcase() in self.glyphs
                 or (c in ACCENTS and ACCENTS[c][0] in self.glyphs))
        return ok / len(chars)

    def render(self, text, height_pt, color=None, seed=None, max_width_pt=None):
        """Compose text. Returns dict(png, width_pt, top_rel, height_rel)."""
        rnd = random.Random(seed if seed is not None else hash(text) & 0xFFFF)
        color = color or self.ink_color()
        Hpx = max(height_pt * S, 8)
        placed, x = [], 0.0
        last = {}
        space = None
        widths = [g.width for v in self.glyphs.values() for g in v]
        space = (np.median(widths) * 0.75 if widths else 0.38) * Hpx
        prev_join = False
        for ch in text:
            if ch == " ":
                x += space * rnd.uniform(0.9, 1.15)
                prev_join = False
                continue
            accent = None
            variants = self.glyphs.get(ch)
            if not variants and ch in ACCENTS and ACCENTS[ch][0] in self.glyphs:
                variants = self.glyphs[ACCENTS[ch][0]]
                accent = ACCENTS[ch][1]
            case_scale = 1.0
            if not variants and ch.lower() in SAME_SHAPE and ch.swapcase() in self.glyphs:
                variants = self.glyphs[ch.swapcase()]
                case_scale = 0.72 if ch.islower() else 1.38
            if variants:
                med = float(np.median([v.width for v in variants]))
                good = [i for i, v in enumerate(variants)
                        if v.quality >= max(v2.quality for v2 in variants) - 0.21 and abs(v.width - med) <= med * 0.35]
                good = good or list(range(len(variants)))
                choices = [i for i in good if i != last.get(ch)] or good
                idx = rnd.choice(choices)
                last[ch] = idx
                g = variants[idx]
            else:
                g = self._font_glyph(ch, Hpx)
                if g is None:
                    x += space
                    continue
            jitter = rnd.uniform(-0.025, 0.025)
            scale = rnd.uniform(0.97, 1.03)
            gh = max(int((g.bottom - g.top) * Hpx * scale * case_scale), 1)
            gw = max(int(g.width * Hpx * scale * case_scale), 1)
            img = Image.fromarray(g.alpha).resize((gw, gh), Image.LANCZOS)
            gap = -0.03 * Hpx if (prev_join and g.join_left) else 0.07 * Hpx * rnd.uniform(0.7, 1.2)
            if not placed:
                gap = 0
            x += gap
            top = (g.bottom + jitter) * Hpx - gh
            placed.append((img, x, top, accent))
            x += gw
            prev_join = g.join_right
        if not placed:
            return None
        min_top = min(p[2] for p in placed) - 0.25 * Hpx
        max_bot = max(p[2] + p[0].height for p in placed)
        W, Hc = int(x) + 4, int(max_bot - min_top) + 4
        mask = Image.new("L", (W, Hc), 0)
        for img, gx, gy, accent in placed:
            ox, oy = int(gx) + 2, int(gy - min_top) + 2
            mask.paste(Image.fromarray(np.maximum(np.array(mask.crop((ox, oy, ox + img.width, oy + img.height))),
                                                  np.array(img))), (ox, oy))
            if accent:
                d = ImageDraw.Draw(mask)
                cx = ox + img.width * 0.5
                ay = oy - 0.12 * Hpx
                dx = 0.12 * Hpx
                lw = max(int(Hpx * 0.06), 1)
                if accent == "grave":
                    d.line([(cx - dx, ay - dx), (cx + dx * 0.4, ay)], fill=230, width=lw)
                else:
                    d.line([(cx + dx, ay - dx), (cx - dx * 0.4, ay)], fill=230, width=lw)
        rgba = Image.new("RGBA", (W, Hc), tuple(int(c * 255) for c in color) + (0,))
        rgba.putalpha(mask)
        buf = io.BytesIO()
        rgba.save(buf, "PNG")
        width_pt = W / S
        res = {"png": buf.getvalue(), "width_pt": width_pt, "height_pt": Hc / S,
               "top_offset_pt": (min_top - 2) / S}
        if max_width_pt and width_pt > max_width_pt:
            f = max_width_pt / width_pt
            res["width_pt"] *= f
            res["height_pt"] *= f
            res["top_offset_pt"] *= f
        return res
