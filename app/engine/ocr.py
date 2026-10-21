"""Text understanding for content that has no text layer (handwriting, scans).

* Windows built-in OCR (offline, no extra download) produces a first reading.
* Each line is classified as handwritten or printed (scanned print must not be
  learned as handwriting), and digits misread as letters are repaired.
* Every correction the user makes is remembered and re-applied automatically.
"""
import asyncio
import io
import re
import threading

import numpy as np
import pymupdf as fitz

from .storage import load_json, save_json
from . import writers as WR
from .writers import line_features, unread_lines

OCR_DPI = 300
_ocr_lock = threading.Lock()


# --------------------------------------------------------------------------- Windows OCR
async def _recognize_async(png_bytes, lang_tag=None):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = None
    if lang_tag:
        from winrt.windows.globalization import Language
        try:
            engine = OcrEngine.try_create_from_language(Language(lang_tag))
        except Exception:
            engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Nessun motore OCR di Windows disponibile. Installa un pacchetto lingua con OCR.")
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append({"text": w.text, "box": (r.x, r.y, r.x + r.width, r.y + r.height)})
        lines.append(words)
    return lines


def windows_ocr(png_bytes, lang_tag=None):
    with _ocr_lock:
        try:
            return asyncio.run(_recognize_async(png_bytes, lang_tag))
        except ImportError:
            raise RuntimeError("Il riconoscimento del testo usa l'OCR di Windows 10/11, che non è disponibile "
                               "su questo computer.")


def available_languages():
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


# --------------------------------------------------------------------------- learning memory
class Learning:
    """Remembers how the user corrected readings, so the app adapts over time."""

    def __init__(self):
        self.data = load_json("learning.json", {"words": {}, "lines": {}})

    def save(self):
        save_json("learning.json", self.data)

    @staticmethod
    def _split(word):
        m = re.match(r"^(\W*)(.*?)(\W*)$", word, re.S)
        return m.group(1), m.group(2), m.group(3)

    def apply(self, text):
        """Returns (text, changed)."""
        if not text:
            return text, False
        line_hit = self.data["lines"].get(text.strip().lower())
        if line_hit:
            return line_hit["to"], True
        out, changed = [], False
        for tok in text.split(" "):
            pre, core, post = self._split(tok)
            hit = self.data["words"].get(core.lower()) if core else None
            if hit and hit["n"] >= 1:
                out.append(pre + hit["to"] + post)
                changed = True
            else:
                out.append(tok)
        return " ".join(out), changed

    def learn(self, recognized, corrected):
        recognized, corrected = recognized.strip(), corrected.strip()
        if not recognized or recognized == corrected:
            return 0
        a, b = recognized.split(), corrected.split()
        learned = 0
        if len(a) == len(b):
            for x, y in zip(a, b):
                _, cx, _ = self._split(x)
                _, cy, _ = self._split(y)
                if cx and cy and cx != cy:
                    entry = self.data["words"].setdefault(cx.lower(), {"to": cy, "n": 0})
                    if entry["to"] != cy:
                        entry["to"], entry["n"] = cy, 0
                    entry["n"] += 1
                    learned += 1
        else:
            self.data["lines"][recognized.lower()] = {"to": corrected}
            learned = 1
        self.save()
        return learned

    def hints(self, limit=60):
        items = sorted(self.data["words"].items(), key=lambda kv: -kv[1]["n"])[:limit]
        return [f'"{k}" → "{v["to"]}"' for k, v in items]

    def stats(self):
        return {"words": len(self.data["words"]), "lines": len(self.data["lines"])}

    def reset(self):
        self.data = {"words": {}, "lines": {}}
        self.save()


# --------------------------------------------------------------------------- page analysis
def _native_rects(page):
    rects = []
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            # invisible text (scanners' OCR layer) does not hide the ink: it must still be read
            if "".join(s["text"] for s in l["spans"] if s.get("alpha", 255) > 0).strip():
                rects.append(fitz.Rect(l["bbox"]) * page.rotation_matrix)   # as displayed, like the OCR
    return rects


def _gray(rgb):
    """Luminance as uint8, with integer arithmetic (several times faster than floats)."""
    r, g, b = rgb[:, :, 0].astype(np.uint16), rgb[:, :, 1].astype(np.uint16), rgb[:, :, 2].astype(np.uint16)
    return ((r * 77 + g * 150 + b * 29) >> 8).astype(np.uint8)


_GAMMA = (np.linspace(0, 1, 256) ** 1.5 * 255).astype(np.uint8)


def enhance(rgb):
    """Second look for the OCR: flatten an uneven scan background (shadows of phone scans,
    yellowed paper) and stretch the contrast, so faint pencil and thin pen read better."""
    from PIL import Image
    gray = _gray(rgb)
    k = 16
    h, w = gray.shape
    small = gray[: h // k * k, : w // k * k].reshape(h // k, k, w // k, k).max(axis=(1, 3))
    bg = small.copy()
    for dy in (-1, 0, 1):                                   # 3x3 max filter on the small image
        for dx in (-1, 0, 1):
            bg = np.maximum(bg, np.roll(np.roll(small, dy, 0), dx, 1))
    # paper brightness of every pixel: upscale the small background image smoothly
    bg = np.array(Image.fromarray(bg).resize((w, h), Image.BILINEAR), dtype=np.uint16)
    norm = np.minimum(gray.astype(np.uint16) * 255 // np.maximum(bg, 1), 255).astype(np.uint8)
    hist = np.cumsum(np.bincount(norm.ravel(), minlength=256))
    lo = min(int(np.searchsorted(hist, hist[-1] * 0.005)), 180)
    # darkest ink -> black, paper -> white (on an almost empty page the darkest 0.5% is paper)
    lut = _GAMMA[np.clip((np.arange(256) - lo) * 255 // max(245 - lo, 1), 0, 255)]
    return _png(lut[norm])


# --------------------------------------------------------------------------- straightening
def skew_angle(rgb):
    """Tilt of the text lines in degrees (positive = lines go down to the right), from the
    angle that makes the horizontal ink profile sharpest. 0 when the page is straight."""
    gray = _gray(rgb[::4, ::4])
    ink = gray < min(int(np.percentile(gray, 92)) - 40, 160)
    ys, xs = np.nonzero(ink)
    if len(xs) < 300:
        return 0.0
    if len(xs) > 60000:
        pick = np.random.default_rng(0).choice(len(xs), 60000, replace=False)
        ys, xs = ys[pick], xs[pick]
    xs = xs - xs.mean()

    def sharpness(a):
        t = np.tan(np.radians(a))
        prof = np.bincount(np.round(ys - xs * t - (ys - xs * t).min()).astype(int))
        return float((prof.astype(np.float64) ** 2).sum())
    best = max(np.arange(-6, 6.01, 0.5), key=sharpness)
    best = max(np.arange(best - 0.5, best + 0.501, 0.1), key=sharpness)
    return float(best) if abs(best) >= 0.3 else 0.0


def straighten(rgb, angle):
    """The page image turned so its lines are horizontal, and a function mapping boxes
    found on it back to the original image."""
    from PIL import Image
    h, w = rgb.shape[:2]
    img = Image.fromarray(np.ascontiguousarray(rgb)).rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    cx, cy = w / 2.0, h / 2.0
    c, s_ = np.cos(np.radians(angle)), np.sin(np.radians(angle))

    def back(box):
        x0, y0, x1, y1 = box
        pts = []
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            dx, dy = x - cx, y - cy
            # PIL turns the image counter-clockwise by `angle`: undo it
            pts.append((cx + dx * c - dy * s_, cy + dx * s_ + dy * c))
        xs_, ys_ = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs_), min(ys_), max(xs_), max(ys_))
    return np.array(img), back


def _merge_passes(first, second):
    """Words of the second reading that the first one missed, grouped in their lines."""
    boxes = [w["box"] for line in first for w in line]

    def seen(b):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return any(o[0] - 2 <= cx <= o[2] + 2 and o[1] - 2 <= cy <= o[3] + 2 for o in boxes)
    extra = []
    for line in second:
        new = [w for w in line if not seen(w["box"])]
        if new:
            extra.append(new)
    return first + extra


def prepare_page(page, include_native=False):
    """Everything the reading needs from the document (taken under the document lock):
    the page image and where the digital text is. The reading itself runs without it."""
    zoom = OCR_DPI / 72.0
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > 9800:
        zoom *= 9800 / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    native = [] if include_native else [list(r) for r in _native_rects(page)]
    return {"rgb": rgb, "png": _png(rgb), "zoom": zoom, "native": native}


def analyze_page(page, learning, lang_tag=None, include_native=False, reader=None, lexicon=None, prefer=None):
    return read_page(prepare_page(page, include_native), learning, lang_tag, reader, lexicon, prefer)


def _png(arr):
    """The image handed to Windows OCR. BMP, not PNG: Windows decodes both, and a BMP is
    written in milliseconds where a page-sized PNG takes a quarter of a second."""
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(arr)).convert("RGB").save(buf, "BMP")
    return buf.getvalue()


def read_page(job, learning, lang_tag=None, reader=None, lexicon=None, prefer=None, cancelled=None):
    """Read a prepared page: 'ink' lines, i.e. text that exists only as pixels.
    * a tilted scan is straightened for the OCR (the boxes are mapped back);
    * two OCR readings (as scanned, and with flattened background) are merged;
    * handwritten lines are also read by the dedicated model (reader), and the reading
      that makes most sense in Italian (lexicon) is kept;
    * ink nobody could read comes back as "unread" lines for the user to read.
    Every line carries its measured features ('feat') and a first guess of 'kind'."""
    rgb, zoom = job["rgb"], job["zoom"]
    native = [fitz.Rect(r) for r in job["native"]]
    angle = skew_angle(rgb)
    if angle:
        img, back = straighten(rgb, angle)
        png = _png(img)
    else:
        img, back, png = rgb, None, job["png"]
    raw_lines = windows_ocr(png, lang_tag)
    try:
        raw_lines = _merge_passes(raw_lines, windows_ocr(enhance(img), lang_tag))
    except Exception:
        pass                                   # the second reading is a bonus
    if back:
        for words in raw_lines:
            for w in words:
                w["box"] = back(w["box"])

    def is_native(r):
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        return any(n.contains(c) for n in native)

    lines = []
    for words in raw_lines:
        kept = []
        for w in words:
            x0, y0, x1, y1 = (v / zoom for v in w["box"])
            r = fitz.Rect(x0, y0, x1, y1)
            if not is_native(r):
                kept.append({"text": fix_letters(fix_numbers(w["text"])), "bbox": [r.x0, r.y0, r.x1, r.y1]})
        if not kept:
            continue
        # split lines where a big horizontal gap separates groups of words
        group = [kept[0]]
        for w in kept[1:]:
            prev = group[-1]
            h = max(prev["bbox"][3] - prev["bbox"][1], 4)
            if w["bbox"][0] - prev["bbox"][2] > 3.5 * h:
                lines.append(group)
                group = [w]
            else:
                group.append(w)
        lines.append(group)

    result = []
    for words in lines:
        # "II budget": two capital I's before a lowercase word are the article "Il"
        for w, nxt in zip(words, words[1:]):
            if w["text"] == "II" and nxt["text"][:1].islower():
                w["text"] = "Il"
        raw = " ".join(w["text"] for w in words)
        x0 = min(w["bbox"][0] for w in words)
        y0 = min(w["bbox"][1] for w in words)
        x1 = max(w["bbox"][2] for w in words)
        y1 = max(w["bbox"][3] for w in words)
        result.append({"bbox": [x0, y0, x1, y1], "raw": raw, "text": raw, "words": words,
                       "source": "ocr", "doubt": _doubt(raw), "synthetic": False})
    # ink nobody could read: show it, so the user can type what it says
    half = rgb[::2, ::2]
    try:
        border = WR.rules(WR.ink_mask(half), max(int(10 * zoom / 2), 4), chain=3, chain_h=6)
    except Exception:
        border = None
    try:
        known = [w["bbox"] for l in result for w in l["words"]] + [list(n) for n in native]
        for b in unread_lines(half, zoom / 2, known):
            result.append({"bbox": b, "raw": "", "text": "", "words": [], "source": "unread",
                           "doubt": 1.0, "synthetic": False})
    except Exception:
        pass
    try:
        result = _table_cells(result, rgb, zoom, half)
    except Exception:
        pass                                   # without the table, the lines as the OCR found them
    result = _split_tall(result, rgb, zoom)
    for l in result:
        _measure(l, rgb, zoom)
    if reader is not None:
        # the reader reads whole lines: give it the pieces the OCR split back together
        result = _join_pieces(result, rgb, zoom, border)
    for l in result:
        if cancelled and cancelled():
            raise RuntimeError("Lettura annullata.")
        # handwriting, and any ink nobody read (the OCR may miss print on a poor photo)
        if l["kind"] == "hand" or (l["source"] == "unread" and reader is not None):
            _second_opinion(l, _crop(rgb, l["bbox"], zoom), zoom, reader, lexicon, prefer)
        l["raw"] = tidy(l["raw"])
        text, learned = learning.apply(l["raw"])
        l["text"] = text
        if learned:
            l["source"] = "learned"
    result.sort(key=lambda l: (round(l["bbox"][1] / 6), l["bbox"][0]))
    return result


def read_region(job, rects, learning, lang_tag=None, reader=None, lexicon=None, prefer=None, line_h=None):
    """Read the writing inside areas the user outlined (ink the automatic reading missed).
    Each area may hold several lines; an area with no visible ink still becomes a line,
    so the user can type what it says."""
    rgb, zoom = job["rgb"], job["zoom"]
    H, W = rgb.shape[:2]
    out = []
    for rect in rects:
        x0, y0 = int(max(rect[0] * zoom, 0)), int(max(rect[1] * zoom, 0))
        x1, y1 = int(min(rect[2] * zoom, W)), int(min(rect[3] * zoom, H))
        boxes = []
        if x1 - x0 > 4 and y1 - y0 > 4:
            mask = WR.ink_mask(rgb[y0:y1, x0:x1])
            mask &= ~WR.rules(mask, max(int(10 * zoom), 4), chain=3, chain_h=6)
            ys, xs = np.nonzero(mask)
            if len(xs) > 10:
                tight = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                lh = (line_h or 14.0) * zoom
                for bx0, by0, bx1, by1 in WR.split_rows(mask, tight, lh):
                    boxes.append([(x0 + bx0) / zoom, (y0 + by0) / zoom, (x0 + bx1) / zoom, (y0 + by1) / zoom])
        if not boxes:
            boxes = [list(rect)]
        for b in boxes:
            l = {"bbox": b, "raw": "", "text": "", "words": [], "source": "unread", "doubt": 1.0,
                 "synthetic": False, "marked": True}
            crop = _crop(rgb, b, zoom)
            try:                                       # Windows OCR on the area alone
                words = [w for line in windows_ocr(_png(crop), lang_tag) for w in line
                         if (w["box"][0] + w["box"][2]) / 2 <= crop.shape[1] and (w["box"][1] + w["box"][3]) / 2 <= crop.shape[0]]
            except Exception:
                words = []
            if words:
                ox, oy = b[0] - 2 / zoom, max(b[1] - (b[3] - b[1]) * 0.12, 0)
                l["words"] = [{"text": fix_letters(fix_numbers(w["text"])),
                               "bbox": [ox + w["box"][0] / zoom, oy + w["box"][1] / zoom,
                                        ox + w["box"][2] / zoom, oy + w["box"][3] / zoom]} for w in words]
                l["raw"] = " ".join(w["text"] for w in l["words"])
                l["source"], l["doubt"] = "ocr", _doubt(l["raw"])
            _measure(l, rgb, zoom)
            if crop.size and (l["kind"] == "hand" or l["source"] == "unread"):
                _second_opinion(l, crop, zoom, reader, lexicon, prefer)
            l["raw"] = tidy(l["raw"])
            text, learned = learning.apply(l["raw"])
            l["text"] = text
            if learned:
                l["source"] = "learned"
            out.append(l)
    return out


def _table_cells(lines, rgb, zoom, half):
    """A line the OCR found across a border of a ruled table (printed or drawn by hand) is
    cut where the border runs ("20/12/2025 Da fare" are two cells), each piece with its own
    words; a piece with no words is left for the reader. Every line inside a table keeps
    the cell it is in, so two cells are never joined into one line again."""
    from .layout import scan_tables
    tables = scan_tables(half, zoom / 2)
    if not tables:
        return lines
    out = []
    for l in lines:
        x0, y0, x1, y1 = l["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        t = next((t for t in tables if t["bbox"][0] <= cx <= t["bbox"][2] and t["bbox"][1] <= cy <= t["bbox"][3]), None)
        if t is None:
            out.append(l)
            continue
        z = t["zoom"]
        row = sum(1 for h in t["h"] if h.at(cx * z) / z <= cy) - 1
        xs = [v.at(cy * z) / z for v in t["v"]]
        cuts = [x for x in xs if x0 + 3 < x < x1 - 3]
        edges = [x0] + cuts + [x1]
        ti = tables.index(t)
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1]
            if b - a < 5:
                continue
            col = sum(1 for x in xs if x <= (a + b) / 2) - 1
            if not cuts:
                l["cell"] = [ti, row, col]
                out.append(l)
                break
            mine = [w for w in l["words"] if a <= (w["bbox"][0] + w["bbox"][2]) / 2 < b]
            # the piece tight to its ink, without the border
            X0, X1 = int((a + 1.5) * zoom), int((b - 1.5) * zoom)
            Y0, Y1 = int(y0 * zoom), int(y1 * zoom)
            sub = rgb[max(Y0, 0):Y1, max(X0, 0):X1]
            if sub.size == 0:
                continue
            m = WR.ink_mask(sub)
            cols = np.nonzero(m.mean(axis=0) > 0.02)[0]
            if not mine and len(cols) < 3:
                continue                                 # only the border was there
            if len(cols):
                a, b = (max(X0, 0) + cols[0]) / zoom, (max(X0, 0) + cols[-1] + 1) / zoom
            raw = " ".join(w["text"] for w in mine)
            out.append({"bbox": [a, y0, b, y1], "raw": raw, "text": raw, "words": mine,
                        "source": l["source"] if mine else "unread", "doubt": _doubt(raw) if mine else 1.0,
                        "synthetic": False, "cell": [ti, row, col]})
    return out


def _split_tall(lines, rgb, zoom):
    """A box the OCR drew around two or three lines of a crowded cell (it happens with
    handwriting) is cut into its lines, each with its own words: the reader reads one line
    at a time, and the letters of a line must not mix with those of the next."""
    hs = sorted(l["bbox"][3] - l["bbox"][1] for l in lines)
    if len(hs) < 5:
        return lines
    med = float(np.median(hs))
    out = []
    for l in lines:
        x0, y0, x1, y1 = l["bbox"]
        if y1 - y0 < med * 1.6:
            out.append(l)
            continue
        X0, Y0 = int(x0 * zoom), int(y0 * zoom)
        crop = rgb[Y0:int(y1 * zoom), X0:int(x1 * zoom)]
        if crop.size == 0:
            out.append(l)
            continue
        mask = WR.ink_mask(crop)
        mask &= ~WR.rules(mask, max(int(10 * zoom), 4), chain=3, chain_h=6)
        rows = WR.split_rows(mask, (0, 0, mask.shape[1], mask.shape[0]), med * zoom * 0.8)
        if len(rows) < 2:
            out.append(l)
            continue
        for rx0, ry0, rx1, ry1 in rows:
            top, bot = y0 + ry0 / zoom, y0 + ry1 / zoom
            ws = [w for w in l["words"] if top <= (w["bbox"][1] + w["bbox"][3]) / 2 < bot]
            cols = np.where(mask[ry0:ry1].any(axis=0))[0]
            if not len(cols):
                continue
            box = [x0 + cols[0] / zoom, top, x0 + (cols[-1] + 1) / zoom, bot]
            raw = " ".join(w["text"] for w in ws)
            out.append({"bbox": box, "raw": raw, "text": raw, "words": ws,
                        "source": "ocr" if ws else "unread", "doubt": _doubt(raw) if ws else 1.0,
                        "synthetic": False})
    return out


def _measure(l, rgb, zoom):
    crop = _crop(rgb, l["bbox"], zoom)
    score = hand_score(crop, l["raw"])
    l["handScore"] = round(score, 2)
    l["kind"] = "hand" if score >= PRINT_BELOW else "print"
    l["feat"] = line_features(crop, l["bbox"][3] - l["bbox"][1], score)


def _border_between(rgb, zoom, a, b, border=None):
    """A table border (a dark line from above the row to below it) between two pieces."""
    y0, y1 = min(a[1], b[1]), max(a[3], b[3])
    if border is not None:                    # the borders found on the page (half size)
        z = zoom / 2
        ya, yb = int(y0 * z), int(y1 * z) + 1
        xa, xb = int(a[2] * z) - 1, int(b[0] * z) + 2
        if xb > xa and border[max(ya, 0):yb, max(xa, 0):xb].any(axis=1).mean() >= 0.3:
            return True
    h = y1 - y0
    ya, yb = int(max((y0 - h * 0.3) * zoom, 0)), int(min((y1 + h * 0.3) * zoom, rgb.shape[0]))
    xa, xb = int(a[2] * zoom), int(b[0] * zoom) + 1
    if xb <= xa:
        return False
    g = _gray(rgb[ya:yb, xa:xb])
    if not g.size:
        return False
    dark = g < min(int(np.percentile(_gray(rgb[ya:yb]), 90)) - 60, 160)
    return bool((dark.mean(axis=0) > 0.75).any())


def _join_pieces(lines, rgb, zoom, border=None):
    """Handwriting the OCR cut into pieces of one row ("Visto e" | "approvato", or a piece
    it could not read at all) becomes one line again, if the pieces are close and written
    with the same pen."""
    from .writers import _colour_dist
    out = []
    for l in sorted(lines, key=lambda l: l["bbox"][0]):
        joined = False
        for m in out:
            # only handwriting, or ink nobody read, next to handwriting
            if not all(x["kind"] == "hand" or x["source"] == "unread" for x in (l, m)):
                continue
            if l.get("cell") != m.get("cell"):
                continue                       # two cells of a table: never one line
                continue
            a, b = m["bbox"], l["bbox"]
            inter = min(a[3], b[3]) - max(a[1], b[1])
            h = max(a[3] - a[1], b[3] - b[1])
            if (inter >= 0.5 * min(a[3] - a[1], b[3] - b[1]) and 0 <= b[0] - a[2] < 2.5 * h
                    and _colour_dist(m["feat"]["ink"], l["feat"]["ink"]) < 40
                    and not _border_between(rgb, zoom, a, b, border)):
                m["bbox"] = [a[0], min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                m["words"] = m["words"] + l["words"]
                m["raw"] = (m["raw"] + " " + l["raw"]).strip()
                m["text"] = m["raw"]
                if m["source"] == "unread" and l["source"] != "unread":
                    m["source"] = l["source"]
                m["doubt"] = max(m["doubt"], l["doubt"]) if m["raw"] else 1.0
                _measure(m, rgb, zoom)
                m["kind"] = "hand"
                joined = True
                break
        if not joined:
            out.append(l)
    return out


def _second_opinion(line, crop, zoom, reader, lexicon, prefer):
    """For a handwritten line, compare the OCR reading with the dedicated model's and with
    their dictionary corrections, and keep the one that makes most sense in Italian."""
    options = []
    if line["raw"]:
        options.append((line["raw"], "ocr", None))
    if reader is not None:
        try:
            readings = [reader.read(crop)]
            # as many letters as the ink is long: a reading of another length is the model
            # making things up (it does that on smudges, ruled lines, drawings)
            clean = reader.clean(crop) if hasattr(reader, "clean") else crop
            aspect = clean.shape[1] / max(clean.shape[0], 1)
            def plausible(t):
                if not t or not 0.5 <= len(t) / max(aspect, 0.5) <= 4.5:
                    return False
                toks = t.split()
                if len(toks) >= 4 and max(toks.count(x) for x in set(toks)) >= len(toks) / 2:
                    return False                 # "0 0 0 0 0": the model stuck on a smudge
                if english_share(t) > 0.4 or re.search(r"[a-z][A-Z][a-z]", t):
                    return False                 # a made-up English sentence ("HelpLearn to edit…")
                return "000 000" not in t
            for text, conf in readings:
                if plausible(text):
                    options.append((text, "model", conf))
        except Exception:
            pass
    if not options:
        return
    best, best_q = None, None
    for text, src, conf in options:
        fixed, n = lexicon.correct_text(tidy(text), prefer) if lexicon else (tidy(text), 0)
        ratio = lexicon.ratio(fixed) if lexicon else 0.5
        q = ratio - 0.06 * n + (0.2 * conf if conf is not None else 0.0)
        # the reader was trained on English: on a short or messy Italian line it sometimes
        # makes up English words ("A little", "at the house"); a reading made of them loses
        q -= 0.6 * english_share(fixed)
        if best_q is None or q > best_q:
            best, best_q = (fixed, src, conf, ratio), q
    text, src, conf, ratio = best
    # two readers that disagree: whichever won, the reading is not sure
    first_ocr = next((o for o in options if o[1] == "ocr"), None)
    first_model = next((o for o in options if o[1] == "model"), None)
    if first_ocr and first_model:
        from .quality import cer
        line["agree"] = round(1 - cer(first_ocr[0], first_model[0]), 2)
    if line["source"] == "unread" and src == "model" and (conf or 0) < 0.3:
        return                                  # the model is guessing: leave it unread
    if text != line["raw"]:
        line["words"] = _align_words(text, line, crop, zoom)
        line["raw"] = text
    line["source"] = src if src == "model" else line["source"]
    doubt = _doubt(text)
    if conf is not None:
        doubt = max(doubt, 1 - conf) if src == "model" else doubt
    if lexicon and ratio < 0.5:
        doubt = max(doubt, 0.5)
    if line.get("agree") is not None and line["agree"] < 0.6:
        doubt = max(doubt, 0.5)
    line["doubt"] = round(min(doubt, 1.0), 2)


def _align_words(text, line, crop, zoom):
    """Word boxes for a new reading: the OCR boxes if the word count matches, else the
    gaps between the words found on the image, else the whole line as one word."""
    words = text.split()
    old = line.get("words") or []
    if len(old) == len(words):
        return [dict(w, text=t) for w, t in zip(old, words)]
    x0, y0, x1, y1 = line["bbox"]
    if crop.size and len(words) > 1:
        gray = crop.mean(axis=2)
        ink = (gray < min(np.percentile(gray, 92) - 25, 200)).any(axis=0)
        h = crop.shape[0]
        runs, start = [], None
        for x, on in enumerate(ink):
            if on and start is None:
                start = x
            elif not on and start is not None:
                runs.append([start, x])
                start = None
        if start is not None:
            runs.append([start, len(ink)])
        # merge letters into words until there are as many blocks as words
        while len(runs) > len(words):
            gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
            i = int(np.argmin(gaps))
            runs[i][1] = runs[i + 1][1]
            del runs[i + 1]
        if len(runs) == len(words) and min((runs[i + 1][0] - runs[i][1]) for i in range(len(runs) - 1)) > h * 0.12:
            ox = x0 - 2 / zoom          # _crop adds 2 px on the left
            return [{"text": t, "bbox": [ox + a / zoom, y0, ox + b / zoom, y1]} for t, (a, b) in zip(words, runs)]
    return [{"text": text, "bbox": [x0, y0, x1, y1]}]


# --------------------------------------------------------------------------- digits
# letters the OCR typically reads in place of handwritten digits
DIGIT_LOOKALIKE = {"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "i": "1", "|": "1", "!": "1",
                   "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "G": "6", "b": "6", "g": "9", "q": "9",
                   "T": "7"}
_NUM_SEP = set(".,:/-")
_WORD_RE = re.compile(r"""^([(\["']*)(.*?)([)\]"'.,;:!?%€]*)$""", re.S)


def fix_numbers(word):
    """'2O24' -> '2024', 'l5/O3' -> '15/03': inside a token that is mostly digits,
    letters that look like digits are read as digits."""
    m = _WORD_RE.match(word)
    pre, core, post = m.group(1), m.group(2), m.group(3)
    if not core:
        return word
    digits = sum(c.isdigit() for c in core)
    look = sum(c in DIGIT_LOOKALIKE for c in core)
    other = sum(1 for c in core if not (c.isdigit() or c in DIGIT_LOOKALIKE or c in _NUM_SEP))
    if digits == 0 or other > 0 or digits < look:
        return word
    return pre + "".join(DIGIT_LOOKALIKE.get(c, c) for c in core) + post


# digits the OCR typically reads in place of handwritten letters
LETTER_LOOKALIKE = {"0": "o", "1": "I", "5": "s", "8": "B", "6": "b", "9": "g"}


def fix_letters(word):
    """'Firmat0' -> 'Firmato', 'da+i' -> 'dati': stray digits or '+' inside a word made of letters."""
    letters, digits = sum(c.isalpha() for c in word), sum(c.isdigit() for c in word)
    if "+" in word and letters >= 2 and letters > digits:
        word = word.replace("+", "t")           # a handwritten "t" is often read as "+"
    m = _WORD_RE.match(word)
    pre, core, post = m.group(1), m.group(2), m.group(3)
    digits = [i for i, c in enumerate(core) if c.isdigit()]
    letters = sum(c.isalpha() for c in core)
    if len(digits) != 1 or letters < 3 or letters + 1 != len(core) or core[digits[0]] not in LETTER_LOOKALIKE:
        return word
    i = digits[0]
    ch = LETTER_LOOKALIKE[core[i]]
    neighbours = core[max(i - 1, 0):i] + core[i + 1:i + 2]
    upper = bool(neighbours) and all(c.isupper() for c in neighbours if c.isalpha())
    if core[i] == "1" and not upper:
        return word                     # "1" for "i" or "l"? can't tell: left for the user
    ch = ch.upper() if upper else ch
    if core[i] == "0" and i == 0 and core[1:2].islower():
        ch = "O"                        # capital at the start of a word: "0ttimo" -> "Ottimo"
    return pre + core[:i] + ch + core[i + 1:] + post


def _is_number(tok):
    core = tok.strip("()[]\"'.,;:!?%€+-")
    return bool(core) and any(c.isdigit() for c in core) and all(c.isdigit() or c in _NUM_SEP for c in core)


# --------------------------------------------------------------------------- punctuation and dates
def english_share(text):
    """Share of the words of a reading that are English and not Italian."""
    from .lexicon import ENGLISH
    toks = [t.strip(".,;:!?()\"'«»").lower() for t in text.split()]
    toks = [t for t in toks if t.isalpha() and len(t) > 1]
    if not toks:
        return 0.0
    eng = sum(1 for t in toks if t in ENGLISH)
    return eng / len(toks)


_ELISION = {"l", "un", "dell", "all", "dall", "nell", "sull", "coll", "quest", "quell", "c", "d", "s", "m", "t",
            "v", "n", "tutt", "anch", "dov", "com", "bell", "sant", "nessun", "qual", "cos"}
_SEP = r"\s*[/|Il(),.\\-]\s*"
_DATE = re.compile(r"(?<![\d/])(\d{1,2})" + _SEP + r"(\d{1,2})" + _SEP + r"(\d{4}|\d{2})(?![\d/])")
_DATE_GLUED = re.compile(r"(?<![\d/])(\d{2})(\d{2})" + r"\s*[/|Il1(),.]\s*" + r"(\d{4}|\d{2})(?![\d/])")


def _date(m):
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if 1 <= d <= 31 and 1 <= mo <= 12:
        return f"{m.group(1).zfill(2)}/{m.group(2).zfill(2)}/{y}"
    return m.group(0)


def tidy(text):
    """Punctuation and dates as they are written in Italian, whatever the reading made of
    them: "Gil ," -> "Gil,", "( GET" -> "(GET", "l ' utente" -> "l'utente",
    "27 ( 12 , 2025" -> "27/12/2025", "1812 , 2025" -> "18/12/2025"; quotes and dots
    left alone by themselves (specks of the paper) go."""
    if not text:
        return text
    t = " ".join(text.split())
    t = _DATE.sub(_date, t)
    t = _DATE_GLUED.sub(_date, t)
    t = re.sub(r"(\d)\s*/\s*(\d)", r"\1/\2", t)                     # 05 / 01 -> 05/01
    t = re.sub(r"\s+([,.;:!?%)\]»])", r"\1", t)                       # no space before , . ; : ! ? )
    t = re.sub(r"([(\[«])\s+", r"\1", t)                              # nor after (
    t = re.sub(r"([,;:!?])(?=[^\s\d,.;:!?)\]»\"'/-])", r"\1 ", t)        # a space after , ; : ! ?
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"([,;:!?])\1+", r"\1", t)                               # ",," -> ","
    t = re.sub(r"([,;:])\.|\.([,;:])", lambda m: m.group(1) or m.group(2), t)
    # apostrophes: an elision joins the next word, a stray one goes
    def apo(m):
        w, nxt = m.group(1), m.group(2)
        if w.lower() in _ELISION and nxt and nxt[0].lower() in "aeiouhàèéìòù":
            return f"{w}'{nxt}"
        if w.lower() == "po":
            return f"{w}' {nxt}" if nxt else f"{w}'"
        return f"{w} {nxt}" if nxt else w
    # "I'utente": a capital I read for the l of an elision
    t = re.sub(r"(?<![\w'])I\s*['’`]\s*(?=[aeiouhàèéìòùAEIOU])", "l'", t)
    t = re.sub(r"(\w+)\s*['’`]\s*(\w*)", apo, t)
    # what is left of marks on their own: specks of the paper, not writing
    toks = [w for w in t.split(" ") if re.search(r"[\w€%&@#°()]", w) or w in ("-", "–", "/", "+", "=", "...")]
    t = " ".join(toks)
    if t.count('"') % 2:
        t = t.replace('"', "")                                         # an unpaired quote
    t = re.sub(r"\(\)", "", t)
    return t.strip()


def _doubt(text):
    """Heuristic 0..1: how likely the reading is wrong."""
    if not text:
        return 1.0
    score = 0.0
    toks = text.split()
    # symbols are odd in words (a "+" is often a misread "t"), normal in numbers (+39, 3*4)
    odd = sum(1 for t in toks for c in t
              if not (c.isalnum() or c in ".,;:'\"!?-()€%/°àèéìòùÀÈÉÌÒÙ" or (_is_number(t) or len(t) == 1) and c in "+=*#<>"))
    score += min(odd / max(len(text), 1) * 4, 0.6)
    mixed = sum(1 for t in toks if not _is_number(t) and any(c.isdigit() for c in t) and any(c.isalpha() for c in t))
    score += 0.25 * mixed
    if len(text.strip()) <= 2 and not all(_is_number(t) for t in toks):
        score += 0.3
    return round(min(score, 1.0), 2)


# --------------------------------------------------------------------------- handwritten or printed?
PRINT_BELOW = 0.36      # hand_score under this = printed text


def _crop(rgb, bbox, zoom):
    x0, y0, x1, y1 = bbox
    h = (y1 - y0) * zoom
    a, b = int(max(y0 * zoom - h * 0.12, 0)), int(min(y1 * zoom + h * 0.12, rgb.shape[0]))
    c, d = int(max(x0 * zoom - 2, 0)), int(min(x1 * zoom + 2, rgb.shape[1]))
    return rgb[a:b, c:d]


def _otsu(gray):
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    between = (mu[-1] * omega - mu) ** 2 / np.maximum(omega * (1 - omega), 1e-12)
    return int(np.argmax(between))


def _segments(ink, min_mass):
    cols = ink.any(axis=0)
    segs, start = [], None
    for x, on in enumerate(cols):
        if on and start is None:
            start = x
        elif not on and start is not None:
            segs.append((start, x))
            start = None
    if start is not None:
        segs.append((start, len(cols)))
    return [s for s in segs if ink[:, s[0]:s[1]].sum() >= min_mass]


def hand_score(rgb, text=""):
    """0..1: how much a line crop looks handwritten (1) rather than printed (0).

    Printed text sits on a perfectly straight baseline, its letters are all the
    same height and separate from each other, and it is (almost always) black.
    Handwriting drifts, joins letters, varies in size and often uses coloured ink."""
    if rgb.size == 0 or rgb.shape[0] < 6 or rgb.shape[1] < 6:
        return 1.0
    gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
    bg = float(np.percentile(gray, 92))
    thr = min(_otsu(gray), bg - 25)
    ink = gray < thr
    if ink.mean() < 0.004:
        return 1.0
    H = rgb.shape[0]

    # coloured ink (blue, red, green pens) is a strong handwriting signal
    dark = rgb[ink].astype(np.float32)
    lum = dark.mean(axis=1)
    core = dark[lum <= np.percentile(lum, 40)].mean(axis=0)
    chroma = float(core.max() - core.min())
    colour = float(np.clip((chroma - 22) / 30, 0, 1))

    segs = _segments(ink, max(H * 0.08, 2))
    n_chars = sum(1 for c in text if not c.isspace()) or len(segs)    # unread line: one letter per block
    if len(segs) < 4 or n_chars < 4:
        return max(0.6, colour)           # too little evidence: keep it as handwriting

    tops, bots, heights, xs = [], [], [], []
    for a, b in segs:
        rows = np.where(ink[:, a:b].any(axis=1))[0]
        tops.append(rows[0])
        bots.append(rows[-1] + 1)
        heights.append(rows[-1] + 1 - rows[0])
        xs.append((a + b) / 2)
    tops, bots, xs = np.array(tops, float), np.array(bots, float), np.array(xs, float)
    ref = max(float(np.median(heights)), 4.0)

    def spread(v):
        """robust deviation from a straight line (a printed line may be slightly skewed)"""
        k, q = np.polyfit(xs, v, 1)
        res = np.abs(v - (k * xs + q))
        return float(np.percentile(res, 60)) / ref

    base = float(np.clip((spread(bots) - 0.035) / 0.09, 0, 1))      # baseline drift
    top = float(np.clip((spread(tops) - 0.10) / 0.18, 0, 1))        # x-height irregularity
    # cursive writing joins letters: many characters per connected column block
    per_seg = n_chars / len(segs)
    joined = float(np.clip((per_seg - 1.35) / 1.2, 0, 1))
    shape = 0.5 * base + 0.25 * top + 0.25 * joined
    return float(max(shape, 0.85 * joined, colour))
