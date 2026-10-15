"""Who wrote what: groups the lines of a page into "writings" (one per person, or per
typeface for scanned print) so that each line is learned from and rewritten with the
letters of its own writer only. Also finds ink the OCR did not read at all.

Features of a line (all measured on the page image):
* ink colour        - different pens are the strongest cue
* stroke width      - pen tip / font weight, relative to the line height
* slant             - handwriting leans, print is upright
* size              - line height
* hand score        - handwritten vs printed (see ocr.hand_score)
"""
import math

import numpy as np

PALETTE = ["#4F8EF7", "#E5534B", "#3FB27F", "#C678DD", "#E0A030", "#2BB3C0", "#D4609A", "#8C9A2E"]
PRINT_COLOR = "#8E93A3"


# --------------------------------------------------------------------------- measurements
def ink_mask(rgb):
    gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])
    bg = float(np.percentile(gray, 92)) if gray.size else 255.0
    hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    p = hist / max(hist.sum(), 1)
    omega, mu = np.cumsum(p), np.cumsum(p * np.arange(256))
    otsu = int(np.argmax((mu[-1] * omega - mu) ** 2 / np.maximum(omega * (1 - omega), 1e-12)))
    return gray < min(otsu, bg - 25)


def _runs(mask, axis):
    """Lengths of the ink runs along rows (axis=1) or columns (axis=0)."""
    m = mask if axis == 1 else mask.T
    padded = np.pad(m.astype(np.int8), ((0, 0), (1, 1)))
    d = np.diff(padded, axis=1)
    starts, ends = np.where(d == 1), np.where(d == -1)
    return ends[1] - starts[1]


def stroke_width(mask):
    """Typical pen width in pixels: the shorter of the horizontal and vertical run medians."""
    h, v = _runs(mask, 1), _runs(mask, 0)
    if not len(h) or not len(v):
        return 0.0
    return float(min(np.median(h), np.median(v)))


def slant(mask):
    """Dominant slant of the strokes in degrees (positive = leaning right)."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 30:
        return 0.0
    yc = ys - mask.shape[0] / 2.0
    best, best_a = -1.0, 0.0
    for a in range(-36, 37, 3):
        shifted = np.round(xs + yc * math.tan(math.radians(a))).astype(int)
        shifted -= shifted.min()
        prof = np.bincount(shifted)
        score = float((prof.astype(np.float64) ** 2).sum())
        if score > best:
            best, best_a = score, a
    return float(best_a)


def ink_colour(rgb, mask):
    dark = rgb[mask].astype(np.float32)
    if not len(dark):
        return [40.0, 40.0, 40.0]
    lum = dark.mean(axis=1)
    return dark[lum <= np.percentile(lum, 40)].mean(axis=0).tolist()


def line_features(rgb, height_pt, hand_score):
    mask = ink_mask(rgb) if rgb.size else np.zeros((1, 1), bool)
    h = max(rgb.shape[0], 1)
    # how much ink the measures come from: short pieces give noisy slant and stroke
    weight = float(np.clip(rgb.shape[1] / max(h * 6, 1), 0.2, 1.0))
    return {"ink": ink_colour(rgb, mask), "stroke": stroke_width(mask) / h, "slant": slant(mask),
            "height": float(height_pt), "hand": float(hand_score), "w": weight}


# --------------------------------------------------------------------------- grouping
def _colour_dist(a, b):
    a, b = np.array(a), np.array(b)
    # chroma matters more than lightness (scans darken/lighten a whole page)
    da, db = a - a.mean(), b - b.mean()
    return float(np.linalg.norm(da - db) * 1.0 + abs(a.mean() - b.mean()) * 0.35)


def distance(f, g):
    """0 = same writer; > 1 = different."""
    c = _colour_dist(f["ink"], g["ink"])
    if c > 55:
        return 9.0                                   # a different pen
    rel = min(f.get("w", 1.0), g.get("w", 1.0))      # trust shape measures of long lines more
    sw = abs(math.log(max(f["stroke"], 1e-3) / max(g["stroke"], 1e-3))) / 0.45
    sl = abs(f["slant"] - g["slant"]) / 14.0
    hs = abs(math.log(max(f["height"], 1) / max(g["height"], 1))) / 0.5
    hand = abs(f["hand"] - g["hand"]) / 0.6
    return c / 45.0 + (sw * 0.6 + sl * 0.6 + hand * 0.6) * rel + hs * 0.5


def _mean(feats):
    w = np.array([f.get("w", 1.0) for f in feats])
    return {"w": float(min(1.0, w.sum())),
            "ink": np.mean([f["ink"] for f in feats], axis=0).tolist(),
            "stroke": float(np.median([f["stroke"] for f in feats])),
            "slant": float(np.median([f["slant"] for f in feats])),
            "height": float(np.median([f["height"] for f in feats])),
            "hand": float(np.mean([f["hand"] for f in feats]))}


def group(feats, threshold=1.0, boxes=None):
    """Greedy agglomerative grouping. Returns a group index for every line.
    With boxes, pieces on the same text row written with the same ink start together."""
    n = len(feats)
    groups = [[i] for i in range(n)]
    if boxes:
        for i in range(n):
            for j in range(i + 1, n):
                a, b = boxes[i], boxes[j]
                inter = min(a[3], b[3]) - max(a[1], b[1])
                same_row = inter / max(min(a[3] - a[1], b[3] - b[1]), 1e-3) >= 0.55
                near = max(b[0] - a[2], a[0] - b[2], 0) < 3 * max(a[3] - a[1], b[3] - b[1])
                f, g = feats[i], feats[j]
                similar = (_colour_dist(f["ink"], g["ink"]) < 30
                           and abs(math.log(max(f["height"], 1) / max(g["height"], 1))) < 0.35
                           # a printed label next to a signature: same black, but print vs hand
                           and (min(f.get("w", 1), g.get("w", 1)) < 0.6 or abs(f["hand"] - g["hand"]) < 0.35))
                if same_row and near and similar:
                    gi = next(g for g in groups if i in g)
                    gj = next(g for g in groups if j in g)
                    if gi is not gj:
                        gi.extend(gj)
                        groups.remove(gj)
    cents = [_mean([feats[k] for k in g]) for g in groups]
    while len(groups) > 1:
        best, pair = threshold, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                d = distance(cents[i], cents[j])
                if d < best:
                    best, pair = d, (i, j)
        if pair is None:
            break
        i, j = pair
        groups[i] += groups.pop(j)
        cents.pop(j)
        cents[i] = _mean([feats[k] for k in groups[i]])
    out = [0] * n
    # biggest writing first
    for gi, members in enumerate(sorted(groups, key=lambda g: -len(g))):
        for k in members:
            out[k] = gi
    return out


def closest(feat, centroids, kind=None):
    """Index of the closest group (optionally of a given kind) or None."""
    best, idx = None, None
    for i, (c, k) in enumerate(centroids):
        if kind and k != kind:
            continue
        d = distance(feat, c)
        if best is None or d < best:
            best, idx = d, i
    return idx, best


# --------------------------------------------------------------------------- ink the OCR missed
def _components(mask):
    """Connected components of a boolean image via run-length union-find.
    Returns a list of bounding boxes (x0, y0, x1, y1) and pixel counts."""
    parent = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    prev, boxes = [], []
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            prev = []
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        cur = []
        j = 0
        for s, e in zip(starts, ends):
            label = len(parent)
            parent.append(label)
            boxes.append([s, y, e, y + 1, e - s])
            while j < len(prev) and prev[j][1] < s:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < e:
                ra, rb = find(label), find(prev[k][2])
                if ra != rb:
                    parent[rb] = ra
                k += 1
            cur.append((s, e, label))
        prev = cur
    merged = {}
    for i, b in enumerate(boxes):
        r = find(i)
        if r in merged:
            m = merged[r]
            m[0], m[1], m[2], m[3] = min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3])
            m[4] += b[4]
        else:
            merged[r] = list(b)
    return list(merged.values())


def rules(mask, band, chain=3, fill=0.7, chain_h=None, axes=(0, 1)):
    """Pixels of long thin lines: table borders, underlines, ruled paper, also drawn by
    hand or slanting in a photo. The page is cut into strips `band` px long; inside a
    strip a border fills (almost) a whole column (or row); it is a border when that
    continues, give or take a couple of pixels, over `chain` strips in a row - longer
    than any stroke of a letter."""
    out = np.zeros_like(mask)
    for axis in axes:                        # 0: vertical lines, 1: horizontal lines
        m = mask if axis == 0 else mask.T
        o = out if axis == 0 else out.T
        need = chain if axis == 0 or chain_h is None else chain_h
        H, W = m.shape
        nb = H // band
        if nb < (chain if axis == 0 or chain_h is None else chain_h) or W == 0:
            continue
        thick = m.copy()                      # a slanting line spreads over 2 columns
        thick[:, 1:] |= m[:, :-1]
        thick[:, :-1] |= m[:, 1:]
        strips = thick[:nb * band].reshape(nb, band, W).mean(axis=1) >= fill
        near = strips.copy()                  # allow a drift of 2 px between strips
        for k in (1, 2):
            near[:, k:] |= strips[:, :-k]
            near[:, :-k] |= strips[:, k:]
        # length of the chain of strips each (strip, column) belongs to
        run = np.zeros_like(near, dtype=np.int32)
        for i in range(nb):
            run[i] = np.where(near[i], (run[i - 1] if i else 0) + 1, 0)
        total = run.copy()
        for i in range(nb - 2, -1, -1):
            total[i] = np.where(near[i] & near[i + 1], total[i + 1], total[i])
        hit = strips & (total >= need)
        for i in np.nonzero(hit.any(axis=1))[0]:
            cols = np.nonzero(hit[i])[0]
            for c in cols:
                o[i * band:(i + 1) * band, max(c - 1, 0):c + 2] |= m[i * band:(i + 1) * band, max(c - 1, 0):c + 2]
    return out


def split_cols(mask, border, box):
    """A region that leaks through a gap of a table border is cut where the border runs."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    b = border[y0:y1, x0:x1]
    wide = b.copy()                           # a wavy border: look 2 px around each column
    for k in (1, 2):
        wide[:, k:] |= b[:, :-k]
        wide[:, :-k] |= b[:, k:]
    cuts = np.nonzero(wide.sum(axis=0) >= 0.3 * h)[0]
    if not len(cuts):
        return [box]
    out, start = [], 0
    for c in list(cuts) + [x1 - x0]:
        if c - start >= 3:
            sub = mask[y0:y1, x0 + start:x0 + c]
            ys, xs = np.nonzero(sub)
            if len(xs):
                out.append((x0 + start + int(xs.min()), y0 + int(ys.min()),
                            x0 + start + int(xs.max()) + 1, y0 + int(ys.max()) + 1))
        start = c + 1
    return out or [box]


def split_rows(mask, box, line_h):
    """A region holding several lines of text (a table cell, a paragraph) cut into lines
    at the valleys of its row profile; lines whose letters touch (descenders, ascenders)
    are cut at the thinnest row. line_h = usual height of one line, in px."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    if h < 1.6 * line_h:
        return [box]
    prof = mask[y0:y1, x0:x1].sum(axis=1).astype(np.float64)
    k = max(int(line_h * 0.15), 1)
    smooth = np.convolve(prof, np.ones(2 * k + 1) / (2 * k + 1), mode="same")
    lo, hi = int(line_h * 0.6), int(h - line_h * 0.6)
    if hi <= lo:
        return [box]
    cut = lo + int(np.argmin(smooth[lo:hi]))
    body = np.median(smooth[smooth > smooth.max() * 0.1]) if (smooth > 0).any() else 0
    if smooth[cut] > body * 0.45:
        return [box]                                # no clear gap: one tall line
    out = []
    for a, b in ((y0, y0 + cut), (y0 + cut, y1)):
        ys, xs = np.nonzero(mask[a:b, x0:x1])
        if len(xs):
            out += split_rows(mask, (x0 + int(xs.min()), a + int(ys.min()), x0 + int(xs.max()) + 1,
                                     a + int(ys.max()) + 1), line_h)
    return out or [box]


def unread_lines(rgb, zoom, known_rects):
    """Text-like ink regions (page points) not covered by any known rect.
    rgb: page image rendered at `zoom` px per point."""
    mask = ink_mask(rgb)
    H, W = mask.shape
    # forget ink already explained by OCR words or digital text
    for r in known_rects:
        # letters sticking out of a word box (ascenders, tails, the last letter) belong to it
        h = (r[3] - r[1]) * zoom
        x0, y0 = int(max(r[0] * zoom - h * 0.6, 0)), int(max(r[1] * zoom - h * 0.3, 0))
        x1, y1 = int(min(r[2] * zoom + h * 0.6, W)), int(min(r[3] * zoom + h * 0.3, H))
        mask[y0:y1, x0:x1] = False
    # remove the rules of tables and underlines (also when a photo makes them slant)
    border = rules(mask, max(int(10 * zoom), 4), chain=3, chain_h=6)
    mask &= ~border
    # join letters into words and lines: smear horizontally, never across a border
    gap = max(int(6 * zoom), 2)
    cs = np.cumsum(np.pad(mask.astype(np.int32), ((0, 0), (gap, gap))), axis=1)
    smeared = (cs[:, 2 * gap:] - cs[:, :-2 * gap]) > 0
    smeared = smeared[:, :W] if smeared.shape[1] >= W else np.pad(smeared, ((0, 0), (0, W - smeared.shape[1])))
    wall = border.copy()
    wall[:, 1:] |= border[:, :-1]
    wall[:, :-1] |= border[:, 1:]
    smeared &= ~wall
    out = []
    parts = []
    for x0, y0, x1, y1, n in _components(smeared):
        parts += split_cols(mask, border, (x0, y0, x1, y1))
    # the usual height of a line on this page: the regions holding a single word or line
    hs = sorted(b[3] - b[1] for b in parts if b[3] - b[1] >= 5 * zoom and b[2] - b[0] >= 12 * zoom)
    line_h = float(np.percentile(hs, 40)) if hs else 14 * zoom
    boxes = []
    for part in parts:
        boxes += split_rows(mask, part, line_h)
    for x0, y0, x1, y1 in boxes:
        w, h = (x1 - x0) / zoom, (y1 - y0) / zoom
        ink = mask[y0:y1, x0:x1].sum()
        if h < 5 or h > 70 or w < 12 or w < h * 1.2:
            continue                                   # dots, noise, pictures, vertical strokes
        if (y1 - y0) < 0.55 * line_h and (x1 - x0) > 3 * (y1 - y0):
            continue                                   # a dash, a piece of a border or underline
        density = ink / max((x1 - x0) * (y1 - y0), 1)
        if density < 0.03 or density > 0.6:
            continue                                   # too faint, or a filled shape
        out.append([x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom])
    return out
