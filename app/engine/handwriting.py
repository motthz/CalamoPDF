"""Handwriting style capture and synthesis.

The app learns the writer's own letter shapes from the handwritten words on the
page (using the recognised - and user-corrected - text as labels), then composes
new text from those glyphs, matching size, ink colour and rhythm. Letters never
seen fall back to a handwriting font tinted with the same ink.

The user can also mark letters and digits one by one ("manual" glyphs): those
always win over the automatic ones and are kept across documents.
"""
import base64
import io
import os
import random

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .storage import load_json, save_json

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
    __slots__ = ("alpha", "top", "bottom", "width", "join_left", "join_right", "src", "quality", "manual")

    def __init__(self, alpha, top, bottom, width, join_left, join_right, src=None, quality=1.0, manual=False):
        self.manual = manual        # marked by hand by the user
        self.src = src              # which ink line it was learned from
        self.quality = quality      # 1 = clean gap segmentation, lower = forced split
        self.alpha = alpha          # uint8 array, ink coverage
        self.top = top              # relative to source word box height (0 = top of box)
        self.bottom = bottom
        self.width = width          # relative to word box height
        self.join_left = join_left  # stroke continues into neighbour (cursive)
        self.join_right = join_right


def _png(alpha):
    buf = io.BytesIO()
    Image.fromarray(alpha).save(buf, "PNG")
    return buf.getvalue()


def _data_url(alpha):
    return "data:image/png;base64," + base64.b64encode(_png(255 - alpha)).decode()


MANUAL_FILE = "calligrafia.json"
MANUAL_PER_CHAR = 24


class HandwritingLibrary:
    def __init__(self):
        self.glyphs = {}      # char -> [Glyph] learned automatically from the open document
        self.manual = {}      # char -> [Glyph] marked by the user, persistent
        self.inks = []        # observed ink colours
        self.heights = []     # observed line heights (pt)
        self.words_seen = 0
        self._load_manual()

    # ------------------------------------------------------------------ persistence of manual glyphs
    def _load_manual(self):
        for item in load_json(MANUAL_FILE, {"glyphs": []}).get("glyphs", []):
            try:
                img = Image.open(io.BytesIO(base64.b64decode(item["png"])))
                g = Glyph(np.array(img.convert("L")), item["top"], item["bottom"], item["width"],
                          item["jl"], item["jr"], "manual", 1.0, True)
                self.manual.setdefault(item["char"], []).append(g)
            except Exception:
                continue

    def _save_manual(self):
        out = [{"char": ch, "png": base64.b64encode(_png(g.alpha)).decode("ascii"), "top": g.top,
                "bottom": g.bottom, "width": g.width, "jl": g.join_left, "jr": g.join_right}
               for ch, gs in self.manual.items() for g in gs]
        save_json(MANUAL_FILE, {"glyphs": out})

    def _variants(self, ch):
        """Glyphs to draw a character with: the user's own markings come first."""
        return self.manual.get(ch) or self.glyphs.get(ch)

    def _known(self, ch):
        return ch in self.manual or ch in self.glyphs

    # ------------------------------------------------------------------ learning
    def clear(self):
        """Forget what was learned from the current document (manual marks stay)."""
        self.glyphs, self.inks, self.heights, self.words_seen = {}, [], [], 0

    def forget(self, src):
        for ch in list(self.glyphs):
            self.glyphs[ch] = [g for g in self.glyphs[ch] if g.src != src]
            if not self.glyphs[ch]:
                del self.glyphs[ch]

    def learn_line(self, page, line, src=None):
        """line: ink line dict with 'words' (text,bbox) and corrected 'text'."""
        if src is not None:
            self.forget(src)
        if line.get("synthetic") or line.get("kind") == "print" or \
                (line.get("source") == "ocr" and line.get("doubt", 0) > 0.4):
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

    @staticmethod
    def _ink(page, clip):
        """Render clip and return (ink mask, alpha, rgb) at DPI."""
        pix = page.get_pixmap(matrix=fitz.Matrix(S, S), clip=clip, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
        bg = float(np.percentile(gray, 92))
        thr = min(_otsu(gray), bg - 25)
        ink = gray < thr
        if not ink.any():
            return ink, np.zeros_like(gray), rgb
        darkest = float(np.percentile(gray[ink], 5))
        alpha = np.clip((bg - gray.astype(np.float32)) / max(bg - darkest, 1) * 255, 0, 255).astype(np.uint8)
        alpha[~ink & (alpha < 90)] = 0
        return ink, alpha, rgb

    @staticmethod
    def _split(ink, n, Hpx):
        """Cut a word image into n letter columns. Returns ([a, b, join_left, join_right]...), quality."""
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
            return [], 0.0
        # drop specks
        segs = [s for s in segs if ink[:, s[0]:s[1]].sum() > Hpx * 0.15] or segs
        if n <= 0:
            return segs, 1.0
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
        return segs, quality

    def _learn_word(self, page, rect, label, src=None):
        label = "".join(c for c in label if not c.isspace())
        if not label or rect.height < 3:
            return
        Hw = rect.height
        pad = Hw * 0.35
        clip = fitz.Rect(rect.x0 - 1, rect.y0 - pad, rect.x1 + 1, rect.y1 + pad) & page.rect
        ink, alpha, rgb = self._ink(page, clip)
        if ink.mean() < 0.005:
            return
        dark = rgb[ink]
        if len(dark):
            lum = dark.mean(axis=1)
            core = dark[lum <= np.percentile(lum, 35)]
            self.inks.append(core.mean(axis=0).tolist())
        box_top_px = (rect.y0 - clip.y0) * S
        Hpx = Hw * S
        segs, quality = self._split(ink, len(label), Hpx)
        if len(segs) != len(label):
            return
        for ch, (a, b, jl, jr) in zip(label, segs):
            g = self._cut(ink, alpha, a, b, box_top_px, Hpx, jl, jr, src,
                          quality if (jl or jr) else max(quality, 0.8))
            if g is None:
                continue
            bucket = self.glyphs.setdefault(ch, [])
            if len(bucket) < 12:
                bucket.append(g)
        self.words_seen += 1

    @staticmethod
    def _cut(ink, alpha, a, b, box_top_px, Hpx, jl, jr, src=None, quality=1.0, manual=False):
        col_ink = ink[:, a:b]
        rows = np.where(col_ink.any(axis=1))[0]
        if len(rows) == 0:
            return None
        r0, r1 = rows[0], rows[-1] + 1
        return Glyph(alpha[r0:r1, a:b].copy(), (r0 - box_top_px) / Hpx, (r1 - box_top_px) / Hpx, (b - a) / Hpx,
                     jl, jr, src, quality, manual)

    # ------------------------------------------------------------------ marking letters by hand
    @staticmethod
    def _line_clip(page, bbox):
        r = fitz.Rect(bbox)
        pad = r.height * 0.35
        return fitz.Rect(r.x0 - 4, r.y0 - pad, r.x1 + 4, r.y1 + pad) & page.rect

    def propose_segments(self, page, line):
        """Where Calamo thinks every letter of a line is: [{x0, x1, char}] in page points.
        Uses the reading as labels when it matches what is on the page."""
        out = []
        words = line.get("words") or []
        labels = line["text"].split()
        if words and len(words) == len(labels):
            parts = [(fitz.Rect(w["bbox"]), lab) for w, lab in zip(words, labels)]
        else:
            parts = [(fitz.Rect(line["bbox"]), "".join(labels))]
        for rect, label in parts:
            if rect.height < 3 or rect.width < 1:
                continue
            clip = fitz.Rect(rect.x0 - 1, rect.y0 - rect.height * 0.35, rect.x1 + 1, rect.y1 + rect.height * 0.35) & page.rect
            ink, _, _ = self._ink(page, clip)
            segs, _ = self._split(ink, len(label), rect.height * S)
            chars = list(label) if len(segs) == len(label) else [""] * len(segs)
            for (a, b, _, _), ch in zip(segs, chars):
                out.append({"x0": clip.x0 + a / S, "x1": clip.x0 + b / S, "char": ch})
        out.sort(key=lambda s: s["x0"])
        return out

    def learn_manual(self, page, bbox, segments):
        """segments: [{x0, x1, char}] in page points, each one letter or digit marked by the user.
        Returns how many glyphs were stored."""
        line = fitz.Rect(bbox)
        clip = self._line_clip(page, line)
        ink, alpha, rgb = self._ink(page, clip)
        if not ink.any():
            return 0
        Hpx = max(line.height * S, 4)
        box_top_px = (line.y0 - clip.y0) * S
        W = ink.shape[1]
        segs = sorted((s for s in segments if s.get("char") and not s["char"].isspace()), key=lambda s: s["x0"])
        stored = 0
        for s in segs:
            a = int(np.clip(round((s["x0"] - clip.x0) * S), 0, W - 1))
            b = int(np.clip(round((s["x1"] - clip.x0) * S), a + 1, W))
            cols = np.where(ink[:, a:b].any(axis=0))[0]
            if len(cols) == 0:
                continue
            a, b = a + cols[0], a + cols[-1] + 1            # tight to the ink
            rows = np.where(ink[:, a:b].any(axis=1))[0]
            # a stroke crossing the cut means the letter is joined to its neighbour
            jl = a > 0 and bool((ink[rows, a] & ink[rows, a - 1]).any())
            jr = b < W and bool((ink[rows, b - 1] & ink[rows, b]).any())
            g = self._cut(ink, alpha, a, b, box_top_px, Hpx, jl, jr, "manual", 1.0, True)
            if g is None:
                continue
            ch = s["char"][0]
            bucket = self.manual.setdefault(ch, [])
            bucket.insert(0, g)
            del bucket[MANUAL_PER_CHAR:]
            stored += 1
        if stored:
            self.heights.append(line.height)
            dark = rgb[ink]
            lum = dark.mean(axis=1)
            self.inks.append(dark[lum <= np.percentile(lum, 35)].mean(axis=0).tolist())
            self._save_manual()
        return stored

    def variants(self, ch):
        """Every sample of a character, for the review panel."""
        out = [{"kind": "manual", "i": i, "img": _data_url(g.alpha)} for i, g in enumerate(self.manual.get(ch, []))]
        out += [{"kind": "auto", "i": i, "img": _data_url(g.alpha)} for i, g in enumerate(self.glyphs.get(ch, []))]
        return out

    def delete_variant(self, ch, kind, i):
        store = self.manual if kind == "manual" else self.glyphs
        bucket = store.get(ch, [])
        if 0 <= i < len(bucket):
            del bucket[i]
        if not bucket:
            store.pop(ch, None)
        if kind == "manual":
            self._save_manual()

    def forget_manual(self):
        self.manual = {}
        self._save_manual()

    # ------------------------------------------------------------------ info
    def ink_color(self):
        if not self.inks:
            return (0.12, 0.16, 0.35)
        c = np.median(np.array(self.inks), axis=0) / 255.0
        return tuple(float(v) for v in c)

    def typical_height(self):
        return float(np.median(self.heights)) if self.heights else 14.0

    def summary(self):
        chars = sorted(set(self.glyphs) | set(self.manual),
                       key=lambda c: (0 if c.isalpha() else 1 if c.isdigit() else 2, c.lower(), c))
        out = []
        for c in chars:
            manual = self.manual.get(c, [])
            g = (manual or self.glyphs[c])[0]
            out.append({"char": c, "count": len(manual) + len(self.glyphs.get(c, [])), "manual": len(manual),
                        "img": _data_url(g.alpha)})
        return {"glyphs": out, "words": self.words_seen, "manual": sum(len(v) for v in self.manual.values()),
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
        ok = sum(1 for c in chars if self._known(c) or (c.lower() in SAME_SHAPE and self._known(c.swapcase()))
                 or (c in ACCENTS and self._known(ACCENTS[c][0])))
        return ok / len(chars)

    def render(self, text, height_pt, color=None, seed=None, max_width_pt=None):
        """Compose text. Returns dict(png, width_pt, top_rel, height_rel)."""
        rnd = random.Random(seed if seed is not None else hash(text) & 0xFFFF)
        color = color or self.ink_color()
        Hpx = max(height_pt * S, 8)
        placed, x = [], 0.0
        last = {}
        space = None
        widths = [g.width for store in (self.glyphs, self.manual) for v in store.values() for g in v]
        space = (np.median(widths) * 0.75 if widths else 0.38) * Hpx
        prev_join = False
        for ch in text:
            if ch == " ":
                x += space * rnd.uniform(0.9, 1.15)
                prev_join = False
                continue
            accent = None
            variants = self._variants(ch)
            if not variants and ch in ACCENTS and self._known(ACCENTS[ch][0]):
                variants = self._variants(ACCENTS[ch][0])
                accent = ACCENTS[ch][1]
            case_scale = 1.0
            if not variants and ch.lower() in SAME_SHAPE and self._known(ch.swapcase()):
                variants = self._variants(ch.swapcase())
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
