"""Letter shapes of one writing (a person's handwriting, or the typeface of scanned
print) and synthesis of new text with them.

A page can hold several writings: each has its own HandwritingLibrary, learned only
from its own lines (using the recognised - and user-corrected - text as labels), so
rewriting a line never mixes letters of different people. New text is composed from
those glyphs, matching size, ink colour and rhythm:
* handwriting keeps small natural variations; missing letters use a handwriting font;
* scanned print is composed exactly, with the scan's own letters and spacing; missing
  letters use the system font closest to the scan, at the same size, weight and blur.

The user can also mark letters and digits one by one ("manual" glyphs), which win
over the automatic ones.
"""
import base64
import io
import os
import random
import re

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .storage import load_json, save_json

DPI = 300
S = DPI / 72.0
FALLBACK_FONTS = ["Inkfree.ttf", "segoepr.ttf", "segoesc.ttf"]
# (regular, bold) candidates for scanned print; the first ones found on the system are tried
PRINT_FONTS = [("times.ttf", "timesbd.ttf"), ("arial.ttf", "arialbd.ttf"), ("calibri.ttf", "calibrib.ttf"),
               ("georgia.ttf", "georgiab.ttf"), ("verdana.ttf", "verdanab.ttf"), ("tahoma.ttf", "tahomabd.ttf"),
               ("cambria.ttc", "cambriab.ttf"), ("cour.ttf", "courbd.ttf"), ("segoeui.ttf", "segoeuib.ttf"),
               ("garamond.ttf", "garabd.ttf"), ("bookos.ttf", "bookosb.ttf"),
               ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"), ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
               ("FreeSerif.ttf", "FreeSerifBold.ttf"), ("FreeSans.ttf", "FreeSansBold.ttf")]
FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/freefont"]
HAND_FONTS = [("Inkfree.ttf", None), ("segoepr.ttf", "segoeprb.ttf"), ("segoesc.ttf", "segoescb.ttf"),
              ("timesi.ttf", "timesbi.ttf"), ("ariali.ttf", "arialbi.ttf"), ("georgiai.ttf", "georgiaz.ttf"),
              ("calibrii.ttf", "calibriz.ttf"), ("verdanai.ttf", "verdanaz.ttf"),
              ("FreeSansOblique.ttf", "FreeSansBoldOblique.ttf"), ("FreeSerifItalic.ttf", "FreeSerifBoldItalic.ttf"),
              ("DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")]
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


LOWER = set("abcdefghijklmnopqrstuvwxyzàèéìòù")
CAP_TO_X = 0.68          # x-height / cap-height of a typical writing


def _ink_band(page, r):
    """The rows really taken by the writing of a line. The OCR box of a handwritten line
    is sometimes much thinner than the letters (it keeps only their middle): the band of
    ink around it, up to the first empty row above and below, is measured instead."""
    try:
        h = r.height
        ext = fitz.Rect(r.x0, r.y0 - 1.5 * h, r.x1, r.y1 + 1.5 * h) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=ext, alpha=False)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].mean(axis=2)
        ink = gray < min(float(np.percentile(gray, 92)) - 40, 200)
        rows = ink.mean(axis=1) > 0.004
        c0, c1 = int((r.y0 - ext.y0) * 2), int((r.y1 - ext.y0) * 2)
        if not rows[c0:c1].any():
            return r
        top, bot = c0, c1
        while top > 0 and rows[top - 1]:
            top -= 1
        while bot < len(rows) and rows[bot]:
            bot += 1
        y0, y1 = ext.y0 + top / 2, ext.y0 + bot / 2
        # only when the box clearly missed part of the letters, and the band is one line
        if (y1 - y0) > h * 1.25 and (y1 - y0) < h * 4:
            return fitz.Rect(r.x0, min(r.y0, y0), r.x1, max(r.y1, y1))
    except Exception:
        pass
    return r


def measure_line(page, bbox, text=""):
    """Baseline (page y) and x-height (pt) of a line, measured on its ink.
    Letters are stored and composed relative to these two, like type: so letters coming
    from different lines (with or without ascenders and descenders) share one size and
    sit on one baseline, and new text matches the line it replaces."""
    r = _ink_band(page, fitz.Rect(bbox))
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(S, S), clip=r & page.rect, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        gray = rgb.mean(axis=2)
        bg = float(np.percentile(gray, 92))
        ink = gray < min(_otsu(gray.astype(np.uint8)), bg - 25)
        lines = _lines_in(page, r & page.rect, pix)
        if lines is not None:
            ink &= ~lines                      # an underline or a border is not the baseline
        prof = np.convolve(ink.sum(axis=1).astype(np.float64), np.ones(3) / 3, mode="same")
        if prof.max() <= 0:
            raise ValueError
        core = np.where(prof >= prof.max() * 0.45)[0]
        xline, base = core[0], core[-1] + 1
        core_pt = (base - xline) / S
        if not (r.height * 0.2 <= core_pt <= r.height * 0.95):
            raise ValueError
        letters = [c for c in text if c.isalpha()]
        # a line of capitals or digits: its core is the cap height, not the x-height
        has_lower = (not text.strip()) or any(c in LOWER for c in letters)
        xh = core_pt if has_lower else core_pt * CAP_TO_X
        return float(r.y0 + base / S), float(xh)
    except Exception:
        return r.y0 + r.height * 0.78, r.height * 0.45


_LINES = {}          # while a line is being learned: its borders, found once for all its words


def _line_borders(page, rect):
    """Borders and rules around a line of text: (pixmap origin, mask) at DPI."""
    from .letters import analyse
    rect = fitz.Rect(rect)
    h = max(rect.height, 4)
    ctx = fitz.Rect(rect.x0 - 4 * h, rect.y0 - h, rect.x1 + 4 * h, rect.y1 + h) & page.rect
    big = page.get_pixmap(matrix=fitz.Matrix(S, S), clip=ctx, alpha=False)
    rgb = np.frombuffer(big.samples, dtype=np.uint8).reshape(big.height, big.width, big.n)[:, :, :3]
    # an OCR box can be much thinner than the writing: a border is told from a letter by
    # being longer than any letter could be, so never assume lines smaller than 12 pt
    letters, vert, horiz, ink = analyse(rgb, max(h, 12.0) * S / 1.3)
    return (big.x, big.y), vert | horiz


def _lines_in(page, clip, pix):
    """Mask (same size as pix) of the borders and rules crossing clip, found on a wider
    piece of the page (a line is told from a letter by how far it goes)."""
    try:
        clip = fitz.Rect(clip)
        cached = _LINES.get(id(page))
        if cached and cached[0].contains(clip):
            (bx, by), lines = cached[1]
        else:
            (bx, by), lines = _line_borders(page, clip)
        dx, dy = pix.x - bx, pix.y - by
        out = np.zeros((pix.height, pix.width), bool)
        part = lines[max(dy, 0):dy + pix.height, max(dx, 0):dx + pix.width]
        out[:part.shape[0], :part.shape[1]] = part
        return out if out.any() else None
    except Exception:
        return None


class Glyph:
    __slots__ = ("alpha", "top", "bottom", "width", "join_left", "join_right", "src", "quality", "manual",
                 "entry", "exit")

    def __init__(self, alpha, top, bottom, width, join_left, join_right, src=None, quality=1.0, manual=False):
        self.manual = manual        # marked by hand by the user
        self.src = src              # which ink line it was learned from
        self.quality = quality      # 1 = clean gap segmentation, lower = forced split
        self.alpha = alpha          # uint8 array, ink coverage
        self.top = top              # in x-heights from the baseline (negative = above it)
        self.bottom = bottom
        self.width = width          # in x-heights
        self.join_left = join_left  # stroke continues into neighbour (cursive)
        self.join_right = join_right
        # height (x-heights from the baseline) where the pen enters and leaves the letter:
        # in cursive the next letter should start where the previous one ended
        self.entry, self.exit = self._ends()

    def _ends(self):
        a = self.alpha
        if a is None or not a.size or a.shape[0] < 2:
            return None, None
        w = max(a.shape[1] // 6, 1)
        out = []
        for cols in (a[:, :w], a[:, -w:]):
            rows = np.where((cols > 80).any(axis=1))[0]
            if not len(rows):
                out.append(None)
                continue
            r = float(rows[-1])                  # the lowest ink: joins run near the baseline
            out.append(self.top + (r + 0.5) / a.shape[0] * (self.bottom - self.top))
        return out[0], out[1]


def _png(alpha):
    buf = io.BytesIO()
    Image.fromarray(alpha).save(buf, "PNG")
    return buf.getvalue()


def _data_url(alpha):
    return "data:image/png;base64," + base64.b64encode(_png(255 - alpha)).decode()


STYLES_FILE = "calligrafie.json"      # calligraphies the user chose to save, by name
OLD_MANUAL_FILE = "calligrafia.json"  # 1.1.0 kept every marked letter for all documents
MANUAL_PER_CHAR = 24


def _glyph_to_json(ch, g):
    return {"char": ch, "png": base64.b64encode(_png(g.alpha)).decode("ascii"), "top": float(g.top),
            "bottom": float(g.bottom), "width": float(g.width), "jl": bool(g.join_left), "jr": bool(g.join_right)}


def _glyph_from_json(item, src):
    img = Image.open(io.BytesIO(base64.b64decode(item["png"])))
    return Glyph(np.array(img.convert("L")), item["top"], item["bottom"], item["width"],
                 item["jl"], item["jr"], src, 1.0, src == "manual")


class HandwritingLibrary:
    """One writing of ONE page. Different pages, documents and people have different
    writings, so nothing is shared unless the user loads a saved calligraphy."""

    def __init__(self, kind="hand", sid=0, name=None, color=None, feat=None):
        self.kind = kind      # "hand" or "print"
        self.sid = sid
        self.name = name or ("Stampato" if kind == "print" else "Scrittura")
        self.auto_name = name is None
        self.color = color    # colour of its boxes in the UI
        self.feat = feat      # measured features (writers.line_features)
        self.gaps, self.spaces = [], []     # letter / word spacing, in x-heights
        self.xhs = []                       # x-heights of the lines learned (pt)
        self.words = []                     # whole words as written, to reuse words and syllables
        self._font = None
        self.glyphs = {}      # char -> [Glyph] learned automatically from this page
        self.manual = {}      # char -> [Glyph] marked by the user on this page
        self.loaded = {}      # char -> [Glyph] from a saved calligraphy the user chose
        self.loaded_name = None
        self.inks = []        # observed ink colours
        self.heights = []     # observed line heights (pt)
        self.words_seen = 0

    def _variants(self, ch):
        """Glyphs to draw a character with: the user's markings, then this page, then a loaded style."""
        return self.manual.get(ch) or self.glyphs.get(ch) or self.loaded.get(ch)

    def _known(self, ch):
        return ch in self.manual or ch in self.glyphs or ch in self.loaded

    def empty(self):
        return not (self.glyphs or self.manual or self.loaded)

    # ------------------------------------------------------------------ saving / loading by name
    def to_style(self, name):
        """Everything this page knows, as a saved calligraphy."""
        chars = set(self.glyphs) | set(self.manual) | set(self.loaded)
        out = []
        for ch in chars:
            for g in (self.manual.get(ch, []) + self.glyphs.get(ch, []) + self.loaded.get(ch, []))[:MANUAL_PER_CHAR]:
                out.append(_glyph_to_json(ch, g))
        return {"name": name, "glyphs": out, "ink": [float(v) for v in self.ink_color()], "geom": 2,
                "height": float(self.typical_height()) if self.heights else None,
                "xh": float(self.typical_xh()) if self.xhs else None}

    def load_style(self, style):
        """Use a saved calligraphy on this page, for the letters the page itself lacks."""
        self.loaded = {}
        old = style.get("geom") != 2        # before 1.3 letters were measured on the line box
        for item in style.get("glyphs", []):
            try:
                if old:
                    item = dict(item, top=(item["top"] - 0.78) / 0.45, bottom=(item["bottom"] - 0.78) / 0.45,
                                width=item["width"] / 0.45)
                self.loaded.setdefault(item["char"], []).append(_glyph_from_json(item, "loaded"))
            except Exception:
                continue
        self.loaded_xh = style.get("xh")
        self.loaded_name = style.get("name")
        self.loaded_ink = style.get("ink")
        self.loaded_height = style.get("height")

    def unload_style(self):
        self.loaded, self.loaded_name = {}, None

    # ------------------------------------------------------------------ learning
    def clear(self):
        """Forget what was learned automatically from the page (marked letters stay)."""
        self.glyphs, self.inks, self.heights, self.words_seen = {}, [], [], 0
        self.gaps, self.spaces, self._font, self.xhs, self.words = [], [], None, [], []

    def forget(self, src):
        self.words = [w for w in self.words if w["src"] != src]
        for ch in list(self.glyphs):
            self.glyphs[ch] = [g for g in self.glyphs[ch] if g.src != src]
            if not self.glyphs[ch]:
                del self.glyphs[ch]

    def learn_line(self, page, line, src=None):
        """line: ink line dict with 'words' (text,bbox) and corrected 'text'."""
        if src is not None:
            self.forget(src)
        self._trusted_line = line.get("source") == "user"
        if line.get("synthetic") or line.get("source") == "unread" or \
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
        lrect = fitz.Rect(line["bbox"])
        try:                                   # the borders around the line, once for all its words
            area = fitz.Rect(lrect.x0 - 2, lrect.y0 - lrect.height * 0.3, lrect.x1 + 2, lrect.y1 + lrect.height * 0.3)
            _LINES[id(page)] = (area, _line_borders(page, area))
        except Exception:
            _LINES.pop(id(page), None)
        try:
            self._learn_line(page, line, lrect, labels, words, src)
        finally:
            _LINES.pop(id(page), None)

    def _learn_line(self, page, line, lrect, labels, words, src):
        base, xh = measure_line(page, lrect, line["text"])
        self.heights.append(lrect.height)
        self.xhs.append(xh)
        for a, b in zip(words, words[1:]):
            gap = (b["bbox"][0] - a["bbox"][2]) / max(xh, 1)
            if 0 < gap < 5:
                self.spaces.append(gap)
        self._font = None
        for w, label in zip(words, labels):
            try:
                self._learn_word(page, fitz.Rect(w["bbox"]), label, src, lrect, (base, xh))
            except Exception:
                pass

    @staticmethod
    def _ink(page, clip):
        """Render clip and return (ink mask, alpha, rgb) at DPI. Borders, underlines and
        ruled lines crossing the letters are not ink of the letters."""
        pix = page.get_pixmap(matrix=fitz.Matrix(S, S), clip=clip, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        lines = _lines_in(page, clip, pix)
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
        bg = float(np.percentile(gray, 92))
        thr = min(_otsu(gray), bg - 25)
        ink = gray < thr
        if lines is not None:
            ink &= ~lines
        if not ink.any():
            return ink, np.zeros_like(gray), rgb
        darkest = float(np.percentile(gray[ink], 5))
        alpha = np.clip((bg - gray.astype(np.float32)) / max(bg - darkest, 1) * 255, 0, 255).astype(np.uint8)
        alpha[~ink & (alpha < 90)] = 0
        if lines is not None:
            alpha[lines] = 0
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
        # drop specks, but not when the word needs them (the dot of 141.900, a comma)
        big = [s for s in segs if ink[:, s[0]:s[1]].sum() > Hpx * 0.15] or segs
        if n <= 0:
            return big, 1.0
        if len(big) >= n or len(segs) < n:
            segs = big
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

    def _learn_word(self, page, rect, label, src=None, line=None, metrics=None):
        """Letters of one word. Their size and position are measured against the whole LINE
        (not the word box, which is short for "euro" and tall for "Totale"), so all letters
        of a writing share one scale and one baseline."""
        label = "".join(c for c in label if not c.isspace())
        if not label or rect.height < 3:
            return
        ref = line or rect
        Hw = ref.height
        pad = Hw * 0.35
        clip = fitz.Rect(rect.x0 - 1, ref.y0 - pad, rect.x1 + 1, ref.y1 + pad) & page.rect
        ink, alpha, rgb = self._ink(page, clip)
        if ink.mean() < 0.005:
            return
        dark = rgb[ink]
        if len(dark):
            lum = dark.mean(axis=1)
            core = dark[lum <= np.percentile(lum, 35)]
            self.inks.append(core.mean(axis=0).tolist())
        base, xh = metrics or (ref.y0 + Hw * 0.78, Hw * 0.45)
        base_px, X = (base - clip.y0) * S, max(xh * S, 2)
        segs, quality = self._split(ink, len(label), X * 2.2)
        if len(segs) != len(label):
            return
        if quality == 1.0 and not _widths_plausible(label, segs):
            quality = 0.5          # as many pieces as letters, but not those letters
        if self.kind == "print" and quality < 1.0:
            return            # print letters are separate: a forced cut means the labels would be wrong
        if quality == 1.0:
            for s0, s1 in zip(segs, segs[1:]):
                self.gaps.append((s1[0] - s0[1]) / X)
        strokes = self._strokes(ink)
        if len(self.words) < 80 and len(label) >= 2 and self._reusable(label, (segs[-1][1] - segs[0][0]) / X):
            a0 = segs[0][0]
            self.words.append({"text": label, "alpha": _body_only(alpha, ink, strokes, base_px, X)[:, a0:segs[-1][1]].copy(),
                               "base": base_px, "X": X,
                               "segs": [(a - a0, b - a0, jl, jr) for a, b, jl, jr in segs], "src": src,
                               "exact": quality == 1.0})
        for ch, (a, b, jl, jr) in zip(label, segs):
            g = self._cut(ink, alpha, a, b, base_px, X, jl, jr, src,
                          quality if (jl or jr) else max(quality, 0.8), labels=strokes)
            if g is None:
                continue
            if ch.isalnum() and not _solid(g, ch):
                continue           # a speck or a shred, not a letter
            bucket = self.glyphs.setdefault(ch, [])
            if len(bucket) < 12:
                bucket.append(g)
        self.words_seen += 1

    def _reusable(self, label, width_xh):
        """A whole word is reused (and its syllables) only when the reading is surely right:
        corrected by the user, or an Italian word / a number as wide as such a word is. A
        misread word would otherwise come back, with the wrong letters, whenever its text is
        written."""
        exp = fitz.Font("helv").text_length(label, fontsize=1) / 0.52       # in x-heights
        if not (0.5 <= width_xh / max(exp, 0.1) <= 2.0):
            return False
        if getattr(self, "_trusted_line", False):
            return True
        from .lexicon import LEXICON
        core = label.strip(".,;:!?()\"'«»")
        return bool(core) and (bool(re.fullmatch(r"[\d/.,:%-]+", core)) or LEXICON.known(core))

    @staticmethod
    def _cut(ink, alpha, a, b, base_px, X, jl, jr, src=None, quality=1.0, manual=False, labels=None):
        """One letter: its image and where it sits, in x-heights from the baseline.
        labels (the strokes of the word, see _strokes): a stroke that lies mostly outside
        these columns belongs to a neighbouring letter (slanted writing reaches over) and
        is left out; strokes running through several letters (joined writing) stay."""
        col_ink = ink[:, a:b]
        keep = None
        if labels is not None:
            lab, stats = labels
            keep = col_ink.copy()
            band0, band1 = base_px - X * 1.05, base_px + X * 0.15       # where the body of letters is
            for k in np.unique(lab[:, a:b][col_ink]):
                x0, x1, total, y0, y1 = stats[k]
                inside = int((lab[:, a:b] == k).sum())
                if (inside < total * 0.3 and x1 - x0 < (b - a) * 2.2) or y1 < band0 or y0 > band1:
                    # a stroke of the next letter, or something above / below the line
                    # (the small print of a form, a mark): not part of this letter
                    keep &= lab[:, a:b] != k
            if not keep.any():
                keep = None
        rows = np.where((keep if keep is not None else col_ink).any(axis=1))[0]
        if len(rows) == 0:
            return None
        r0, r1 = rows[0], rows[-1] + 1
        img = alpha[r0:r1, a:b].copy()
        if keep is not None:
            # the soft edges of the strokes kept, nothing of the strokes removed
            from PIL import Image as _I, ImageFilter as _F
            grow = np.array(_I.fromarray((keep[r0:r1] * 255).astype(np.uint8)).filter(_F.MaxFilter(5))) > 0
            img = np.where(grow, img, 0).astype(img.dtype)
        return Glyph(img, (r0 - base_px) / X, (r1 - base_px) / X, (b - a) / X,
                     jl, jr, src, quality, manual)

    @staticmethod
    def _strokes(ink):
        """Connected strokes of a word: a label per pixel (0 = paper) and, for each label,
        (first column, last column, pixels, first row, last row)."""
        H, W = ink.shape
        parent = [0]

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        runs, prev = [], []
        for y in range(H):
            row = ink[y]
            if not row.any():
                prev = []
                continue
            d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            cur = []
            j = 0
            for s0, e0 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                lab = len(parent)
                parent.append(lab)
                while j < len(prev) and prev[j][1] < s0:
                    j += 1
                k = j
                while k < len(prev) and prev[k][0] <= e0:      # 8-connected
                    ra, rb = find(lab), find(prev[k][2])
                    if ra != rb:
                        parent[rb] = ra
                    k += 1
                cur.append((s0, e0, lab))
                runs.append((y, s0, e0, lab))
            prev = cur
        out = np.zeros((H, W), np.int32)
        stats = {}
        for y, s0, e0, lab in runs:
            r = find(lab)
            out[y, s0:e0] = r
            st = stats.get(r)
            stats[r] = (min(st[0], s0), max(st[1], e0), st[2] + e0 - s0, min(st[3], y), max(st[4], y + 1)) \
                if st else (s0, e0, e0 - s0, y, y + 1)
        return out, stats

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

    def learn_manual(self, page, bbox, segments, line_id=None, text=""):
        """segments: [{x0, x1, char}] in page points, each one letter or digit marked by the user.
        Returns how many glyphs were stored."""
        line = fitz.Rect(bbox)
        clip = self._line_clip(page, line)
        ink, alpha, rgb = self._ink(page, clip)
        if not ink.any():
            return 0
        base, xh = measure_line(page, line, text)
        base_px, X = (base - clip.y0) * S, max(xh * S, 2)
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
            g = self._cut(ink, alpha, a, b, base_px, X, jl, jr, ("manual", line_id), 1.0, True)
            if g is None:
                continue
            ch = s["char"][0]
            bucket = self.manual.setdefault(ch, [])
            bucket.insert(0, g)
            del bucket[MANUAL_PER_CHAR:]
            stored += 1
        if stored:
            self._font = None
            self.heights.append(line.height)
            self.xhs.append(xh)
            dark = rgb[ink]
            lum = dark.mean(axis=1)
            self.inks.append(dark[lum <= np.percentile(lum, 35)].mean(axis=0).tolist())
        return stored

    def variants(self, ch):
        """Every sample of a character, for the review panel."""
        out = []
        for kind, store in (("manual", self.manual), ("auto", self.glyphs), ("loaded", self.loaded)):
            out += [{"kind": kind, "i": i, "img": _data_url(g.alpha)} for i, g in enumerate(store.get(ch, []))]
        return out

    def delete_variant(self, ch, kind, i):
        store = {"manual": self.manual, "auto": self.glyphs, "loaded": self.loaded}[kind]
        bucket = store.get(ch, [])
        if 0 <= i < len(bucket):
            del bucket[i]
        if not bucket:
            store.pop(ch, None)

    def forget_manual(self):
        self.manual = {}

    def take_manual(self, other, line_id):
        """Move the letters marked on line `line_id` from another writing into this one."""
        for ch in list(other.manual):
            mine = [g for g in other.manual[ch] if g.src == ("manual", line_id)]
            if mine:
                other.manual[ch] = [g for g in other.manual[ch] if g.src != ("manual", line_id)]
                if not other.manual[ch]:
                    del other.manual[ch]
                self.manual.setdefault(ch, [])[:0] = mine
        self._font = other._font = None

    # ------------------------------------------------------------------ info
    def ink_color(self):
        if not self.inks:
            if self.feat and self.feat.get("ink"):        # measured on this writing's lines
                return tuple(float(v) / 255.0 for v in self.feat["ink"])
            if self.loaded and getattr(self, "loaded_ink", None):
                return tuple(self.loaded_ink)
            return (0.12, 0.16, 0.35)
        c = np.median(np.array(self.inks), axis=0) / 255.0
        return tuple(float(v) for v in c)

    def typical_xh(self):
        """x-height (pt) of this writing."""
        if self.xhs:
            return float(np.median(self.xhs))
        if self.loaded and getattr(self, "loaded_xh", None):
            return float(self.loaded_xh)
        return self.typical_height() * 0.45

    def typical_height(self):
        if self.heights:
            return float(np.median(self.heights))
        if self.feat and self.feat.get("height"):
            return float(self.feat["height"])
        return float(getattr(self, "loaded_height", None) or 14.0) if self.loaded else 14.0

    def summary(self):
        chars = sorted(set(self.glyphs) | set(self.manual) | set(self.loaded),
                       key=lambda c: (0 if c.isalpha() else 1 if c.isdigit() else 2, c.lower(), c))
        out = []
        for c in chars:
            manual, own, loaded = self.manual.get(c, []), self.glyphs.get(c, []), self.loaded.get(c, [])
            g = (manual or own or loaded)[0]
            out.append({"char": c, "count": len(manual) + len(own) + len(loaded), "manual": len(manual),
                        "loaded": not (manual or own), "img": _data_url(g.alpha)})
        return {"glyphs": out, "words": self.words_seen, "manual": sum(len(v) for v in self.manual.values()),
                "loadedName": self.loaded_name, "id": self.sid, "name": self.name, "kind": self.kind,
                "color": self.color,
                "ink": "#%02x%02x%02x" % tuple(int(v * 255) for v in self.ink_color())}

    # ------------------------------------------------------------------ synthesis
    def _fallback_font(self, px):
        win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for name in FALLBACK_FONTS:
            p = os.path.join(win, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, max(int(px), 6))
        try:
            return ImageFont.load_default(size=max(int(px), 6))      # Pillow >= 10.1, scalable
        except TypeError:
            return ImageFont.load_default()

    def _font_glyph(self, ch, X):
        """Last resort: a handwriting font, x-height matched (X = x-height in px)."""
        font = self._fallback_font(X * 2.0)
        bbox = font.getbbox(ch)
        w = max(bbox[2] - bbox[0], 1)
        ascent, descent = font.getmetrics()
        img = Image.new("L", (w + 4, ascent + descent + 4), 0)
        d = ImageDraw.Draw(img)
        d.text((2 - bbox[0], 0), ch, fill=255, font=font)
        a = np.array(img)
        rows = np.where(a.max(axis=1) > 0)[0]
        if len(rows) == 0:
            return None
        top = (rows[0] - ascent) / X              # the font baseline is at y = ascent
        bottom = (rows[-1] + 1 - ascent) / X
        return Glyph(a[rows[0]:rows[-1] + 1], top, bottom, a.shape[1] / X, False, False)

    # ------------------------------------------------------------------ fonts for scanned print
    def _softness(self):
        vals = []
        for gs in list(self.manual.values()) + list(self.glyphs.values()):
            for g in gs[:2]:
                a = g.alpha[g.alpha > 30]
                if len(a):
                    vals.append(((a < 215).sum()) / len(a))
        return float(np.median(vals)) if vals else 0.3

    @staticmethod
    def _font_file(name):
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def _font_mask(path, ch, px):
        font = ImageFont.truetype(path, max(int(px), 6))
        img = Image.new("L", (int(px * 2) + 8, int(px * 2) + 8), 0)
        ImageDraw.Draw(img).text((4, 4), ch, fill=255, font=font)
        a = np.array(img)
        ys, xs = np.nonzero(a > 40)
        if not len(xs):
            return None, font
        return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1], font

    def _match_font(self):
        """System font closest to the scanned letters (shape, weight), with size and blur."""
        if self._font is not None:
            return self._font
        samples = []
        for ch in sorted(set(self.manual) | set(self.glyphs)):
            if not ch.isalnum():
                continue
            g = max(self._variants(ch), key=lambda v: v.quality)
            if g.quality >= 0.8 and g.alpha.shape[0] > 6:
                samples.append((ch, g))
        samples = samples[:16]
        best = (None, -1.0, 1.0)
        candidates = PRINT_FONTS if self.kind == "print" else HAND_FONTS
        files = [(self._font_file(r), self._font_file(b) if b else None) for r, b in candidates]
        for reg, bold in files:
            for path in (reg, bold):
                if not path:
                    continue
                penalty = 0.03 if path == bold else 0.0      # bold only when clearly closer
                score, ratios, sizes = 0.0, [], []
                for ch, g in samples:
                    m, _ = self._font_mask(path, ch, 100)
                    if m is None:
                        continue
                    gh, gw = g.alpha.shape
                    fm = np.array(Image.fromarray(m).resize((gw, gh), Image.BILINEAR)) > 110
                    gm = g.alpha > 110
                    inter, union = (fm & gm).sum(), (fm | gm).sum()
                    # same shape, same proportions, same ink weight
                    aspect = min(m.shape[1] / m.shape[0], gw / gh) / max(m.shape[1] / m.shape[0], gw / gh)
                    weight = min(fm.mean(), gm.mean()) / max(fm.mean(), gm.mean(), 1e-6)
                    score += inter / max(union, 1) * 0.5 + aspect * 0.25 + weight * 0.25
                    ratios.append(gh / m.shape[0])
                    sizes.append(100 * (g.bottom - g.top) / m.shape[0])     # font size per unit of line height
                if samples:
                    score = score / len(samples) - penalty
                if score > best[1] and sizes:
                    best = (path, score, (float(np.median(ratios)), float(np.median(sizes))))
        path, score, scale = best
        if path and self.kind != "print" and score < 0.5:
            path = None               # nothing really similar: the handwriting font is a safer guess
        ratio, size_per_h = scale if path else (1.0, 1.9)
        # a font's x-height is 0.38-0.62 of its size: outside that the samples were bad
        size_per_h = float(np.clip(size_per_h, 1.6, 2.6))
        src_h = float(np.median([g.alpha.shape[0] / max(g.bottom - g.top, 1e-3) for _, g in samples])) if samples else 100.0
        self._font = {"path": path, "size_per_h": size_per_h, "blur": 0.0}
        if path and samples:
            soft = self._softness()
            ch, g = samples[0]
            best_r, best_d = 0.0, 9.0
            for r in (0.0, 0.4, 0.7, 1.0, 1.4, 1.8):
                m, _ = self._font_mask(path, ch, 100 * ratio)
                if m is None:
                    break
                img = Image.fromarray(m).filter(ImageFilter.GaussianBlur(r)) if r else Image.fromarray(m)
                a = np.array(img)
                a = a[a > 30]
                d = abs(((a < 215).sum()) / max(len(a), 1) - soft)
                if d < best_d:
                    best_r, best_d = r, d
            self._font["blur"] = best_r / src_h          # blur radius per pixel of line height
        return self._font

    def _print_glyph(self, ch, Hpx):        # Hpx = x-height in px
        """A letter missing from the writing, drawn with the closest font (same shape family,
        size, weight, baseline and blur) so it does not stand out."""
        f = self._match_font()
        if not f["path"]:
            if self.kind != "print":
                return self._font_glyph(ch, Hpx)
            # nothing learned yet: a plain sans serif, sized like a printed line
            path = next((p for p in (self._font_file(r) for r, _ in PRINT_FONTS[1:2] + PRINT_FONTS) if p), None)
            if not path:
                return self._font_glyph(ch, Hpx)
            f = dict(f, path=path, size_per_h=1.9, blur=0.0)      # x-height of a sans serif ~ 0.52 em
        px = f["size_per_h"] * Hpx
        font = ImageFont.truetype(f["path"], max(int(px), 6))
        ascent, descent = font.getmetrics()
        img = Image.new("L", (int(px * 2) + 8, ascent + descent + 8), 0)
        ImageDraw.Draw(img).text((4, 4), ch, fill=255, font=font)
        if f["blur"]:
            img = img.filter(ImageFilter.GaussianBlur(float(f["blur"] * Hpx)))
        a = np.array(img)
        ys, xs = np.nonzero(a > 20)
        if not len(xs):
            return None
        a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        base_px = 4 + ascent                       # the font baseline in the image
        top = (ys.min() - base_px) / Hpx
        return Glyph(a, top, top + a.shape[0] / Hpx, a.shape[1] / Hpx, False, False, quality=0.9)

    def coverage(self, text):
        chars = [c for c in text if not c.isspace()]
        if not chars:
            return 1.0
        ok = sum(1 for c in chars if self._known(c) or (c.lower() in SAME_SHAPE and self._known(c.swapcase()))
                 or (c in ACCENTS and self._known(ACCENTS[c][0])))
        return ok / len(chars)

    def render(self, text, xh_pt, color=None, seed=None, max_width_pt=None):
        """Compose text with an x-height of xh_pt. Returns dict(png, width_pt, height_pt,
        top_offset_pt): the image top is top_offset_pt from the baseline (negative = above)."""
        rnd = random.Random(seed if seed is not None else hash(text) & 0xFFFF)
        printed = self.kind == "print"
        color = color or self.ink_color()
        Hpx = max(xh_pt * S, 4)              # NB: an x-height, every measure below is in x-heights
        placed, x = [], 0.0
        last = {}
        widths = [g.width for store in (self.glyphs, self.manual, self.loaded) for v in store.values() for g in v]
        # spacing measured on the page itself, so new words sit like the old ones
        space = (np.median(self.spaces) if len(self.spaces) >= 2 else
                 (np.median(widths) * 0.75 if widths else 0.7)) * Hpx
        gap_ref = float(np.median(self.gaps)) if len(self.gaps) >= 3 else 0.15
        prev_join, prev = False, None
        for unit in self._units(text, rnd):
            if unit == " ":
                x += space * (1.0 if printed else rnd.uniform(0.9, 1.15))
                prev_join, prev = False, None
                continue
            accent = None
            case_scale = 1.0
            if isinstance(unit, Glyph):          # a whole word or syllable written by this person
                g = unit
            else:
                ch = unit
                g = None
            if g is None:
                g, accent, case_scale = self._letter(ch, rnd, last, printed, Hpx, prev)
                if g is None:
                    x += space
                    continue
            jitter = 0.0 if printed else rnd.uniform(-0.05, 0.05)
            if (not printed and prev_join and g.join_left and prev is not None
                    and prev.exit is not None and g.entry is not None):
                # nudge the letter so its entry stroke meets the previous exit stroke
                jitter = float(np.clip(prev.exit - g.entry, -0.12, 0.12)) * 0.6
            scale = 1.0 if printed else rnd.uniform(0.97, 1.03)
            gh = max(int((g.bottom - g.top) * Hpx * scale * case_scale), 1)
            gw = max(int(g.width * Hpx * scale * case_scale), 1)
            img = Image.fromarray(g.alpha).resize((gw, gh), Image.LANCZOS)
            if printed:
                gap = gap_ref * Hpx
            else:
                gap = -0.06 * Hpx if (prev_join and g.join_left) else gap_ref * Hpx * rnd.uniform(0.7, 1.2)
            if not placed:
                gap = 0
            x += gap
            top = (g.bottom + jitter) * Hpx - gh
            placed.append((img, x, top, accent))
            x += gw
            prev_join, prev = g.join_right, g
        if not placed:
            return None
        return self._finish(placed, x, Hpx, color, max_width_pt)

    def _letter(self, ch, rnd, last, printed, Hpx, prev=None):
        """The glyph for one character: learned, accented, case-swapped or font-drawn.
        After a joined letter, the variant whose stroke starts where the previous one
        ended is preferred, so the join looks written in one go."""
        accent = None
        variants = self._variants(ch)
        if not variants and ch in ACCENTS and self._known(ACCENTS[ch][0]):
            variants = self._variants(ACCENTS[ch][0])
            accent = ACCENTS[ch][1]
        case_scale = 1.0
        if not variants and not printed and ch.lower() in SAME_SHAPE and self._known(ch.swapcase()):
            variants = self._variants(ch.swapcase())
            case_scale = 0.72 if ch.islower() else 1.38
        if not variants:
            return self._print_glyph(ch, Hpx), accent, case_scale
        med = float(np.median([v.width for v in variants]))
        good = [i for i, v in enumerate(variants)
                if v.quality >= max(v2.quality for v2 in variants) - 0.21 and abs(v.width - med) <= med * 0.35]
        good = good or list(range(len(variants)))
        if printed:
            # print: always the same, most typical shape of each letter
            idx = min(good, key=lambda i: abs(variants[i].width - med))
        else:
            choices = [i for i in good if i != last.get(ch)] or good
            if len(good) > 2:
                choices = good          # with many shapes, repetition is unlikely anyway
            if prev is not None and prev.join_right and prev.exit is not None and len(choices) > 1:
                def gap(i):
                    v = variants[i]
                    return abs(v.entry - prev.exit) if v.join_left and v.entry is not None else 0.6
                best = min(choices, key=gap)
                # mostly the best join; sometimes another shape, so repeated letters still vary
                idx = best if rnd.random() < 0.75 else rnd.choice(choices)
            else:
                idx = rnd.choice(choices)
        last[ch] = idx
        return variants[idx], accent, case_scale

    def _units(self, text, rnd):
        """Split text into what to draw: spaces, whole pieces of words this writer already
        wrote (longest first: they keep the real joins between letters) and single letters."""
        out = []
        for k, word in enumerate(text.split(" ")):
            if k:
                out.append(" ")
            i = 0
            while i < len(word):
                piece = self._piece(word, i, rnd)
                if piece:
                    g, n = piece
                    out.append(g)
                    i += n
                else:
                    out.append(word[i])
                    i += 1
        return out

    def _piece(self, word, i, rnd):
        if not self.words:
            return None
        for n in range(len(word) - i, 1, -1):
            sub = word[i:i + n]
            # a piece of a word is only as good as the cuts between its letters: where they
            # were forced (joined letters), only the whole word is reused
            found = [(w, w["text"].find(sub)) for w in self.words
                     if sub in w["text"] and (w.get("exact", True) or sub == w["text"])]
            if not found:
                continue
            w, k = rnd.choice(found)
            segs = w["segs"][k:k + n]
            a, b = segs[0][0], segs[-1][1]
            alpha = w["alpha"][:, a:b]
            rows = np.where((alpha > 40).any(axis=1))[0]
            if not len(rows):
                continue
            r0, r1 = rows[0], rows[-1] + 1
            X = w["X"]
            # measured on a badly sized line, a piece would come out tiny or huge
            if not (0.6 <= (r1 - r0) / X <= 4.0 and 0.3 <= (b - a) / X / n <= 2.6):
                continue
            return Glyph(alpha[r0:r1].copy(), (r0 - w["base"]) / X, (r1 - w["base"]) / X, (b - a) / X,
                         segs[0][2], segs[-1][3], quality=1.0), n
        return None

    def _finish(self, placed, x, Hpx, color, max_width_pt):
        min_top = min(p[2] for p in placed) - 0.5 * Hpx
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
                ay = oy - 0.25 * Hpx
                dx = 0.25 * Hpx
                lw = max(int(Hpx * 0.12), 1)
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


def _body_only(alpha, ink, strokes, base_px, X):
    """The word without what lies wholly above or below its line (the small print of a
    form, a mark in the margin)."""
    lab, stats = strokes
    band0, band1 = base_px - X * 1.05, base_px + X * 0.15
    away = [k for k, st in stats.items() if st[4] < band0 or st[3] > band1]
    if not away:
        return alpha
    drop = np.isin(lab, away)
    keep = ink & ~drop
    grow = np.array(Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    return np.where(grow & ~(drop & ~keep), alpha, 0).astype(alpha.dtype)


def _solid(g, ch):
    """A letter or digit has a body: at least about half an x-height tall (an "x"), and
    some ink. Specks of the scan or shreds of a cut do not."""
    h = g.bottom - g.top                       # in x-heights
    if h < (0.55 if ch.isalpha() and ch.islower() and ch not in "ilj" else 0.75) or h > 4.5:
        return False
    if not 0.12 <= g.width <= 3.0:
        return False
    a = g.alpha
    ink = (a > 100).sum()
    return ink >= a.shape[0] * 1.5 and a.shape[1] >= 2


def _widths_plausible(label, segs):
    """Do the pieces cut from a word have the widths of its letters? An "i" is narrow, an
    "m" wide: when the pieces disagree with that, the letters were not cut where they are."""
    if len(label) < 3:
        return True
    ref = fitz.Font("helv")
    exp = np.array([max(ref.text_length(c, fontsize=1), 0.2) for c in label])
    got = np.array([max(s[1] - s[0], 1) for s in segs], dtype=np.float64)
    ratio = (got / got.sum()) / (exp / exp.sum())
    return bool(ratio.min() > 0.33 and ratio.max() < 2.6)


# --------------------------------------------------------------------------- the writings of a page
class PageWritings:
    """All the writings (people, typefaces) found on one page."""

    def __init__(self):
        self.libs = {}
        self._next = 1

    def new(self, kind="hand", feat=None):
        from .writers import PALETTE, PRINT_COLOR
        sid = self._next
        self._next += 1
        lib = HandwritingLibrary(kind, sid, None, None, feat)
        self.libs[sid] = lib
        self._names()
        hands = [l for l in self.libs.values() if l.kind == "hand"]
        lib.color = PALETTE[(len(hands) - 1) % len(PALETTE)] if kind == "hand" else PRINT_COLOR
        return sid

    def _names(self):
        """Automatic names follow the kind: Scrittura 1, 2… / Stampato 1, 2…"""
        counts = {"hand": 0, "print": 0}
        for sid in sorted(self.libs):
            lib = self.libs[sid]
            counts[lib.kind] += 1
            if lib.auto_name:
                lib.name = ("Scrittura %d" if lib.kind == "hand" else "Stampato %d") % counts[lib.kind]

    def set_kind(self, sid, kind):
        from .writers import PALETTE, PRINT_COLOR
        lib = self.libs[sid]
        if lib.kind == kind:
            return
        lib.kind = kind
        lib._font = None
        hands = [l for l in self.libs.values() if l.kind == "hand"]
        lib.color = PALETTE[(len(hands) - 1) % len(PALETTE)] if kind == "hand" else PRINT_COLOR
        self._names()

    def get(self, sid):
        return self.libs.get(sid)

    def default(self, kind="hand"):
        """The main writing of a kind (the one with most lines learned)."""
        libs = [l for l in self.libs.values() if l.kind == kind]
        if not libs:
            return None
        return max(libs, key=lambda l: (len(l.heights), -l.sid))

    def drop_empty(self, used):
        for sid in [s for s in self.libs if s not in used and not self.libs[s].manual and not self.libs[s].loaded]:
            del self.libs[sid]
        self._names()

    def listing(self, lines):
        out = []
        for sid in sorted(self.libs):
            lib = self.libs[sid]
            n = sum(1 for l in lines if l.get("style") == sid)
            out.append({"id": sid, "name": lib.name, "kind": lib.kind, "color": lib.color, "lines": n,
                        "glyphs": len(set(lib.glyphs) | set(lib.manual) | set(lib.loaded)),
                        "ink": "#%02x%02x%02x" % tuple(int(v * 255) for v in lib.ink_color())})
        return out


# --------------------------------------------------------------------------- saved calligraphies
def saved_styles():
    data = load_json(STYLES_FILE, None)
    if data is None:
        data = {"styles": []}
        # the letters 1.1.0 kept for every document become a saved calligraphy the user can pick
        old = load_json(OLD_MANUAL_FILE, {}).get("glyphs")
        if old:
            data["styles"].append({"name": "Lettere segnate (versione 1.1)", "glyphs": old, "ink": None, "height": None})
        save_json(STYLES_FILE, data)
    return data["styles"]


def style_list():
    return [{"name": st["name"], "chars": len({g["char"] for g in st.get("glyphs", [])})} for st in saved_styles()]


def save_style(style):
    styles = [st for st in saved_styles() if st["name"] != style["name"]]
    styles.append(style)
    save_json(STYLES_FILE, {"styles": styles})


def get_style(name):
    for st in saved_styles():
        if st["name"] == name:
            return st
    raise RuntimeError(f"Calligrafia “{name}” non trovata.")


def delete_style(name):
    save_json(STYLES_FILE, {"styles": [st for st in saved_styles() if st["name"] != name]})
