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
    return {"ink": ink_colour(rgb, mask), "stroke": stroke_width(mask) / h, "slant": slant(mask),
            "height": float(height_pt), "hand": float(hand_score)}


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
    sw = abs(math.log(max(f["stroke"], 1e-3) / max(g["stroke"], 1e-3))) / 0.45
    sl = abs(f["slant"] - g["slant"]) / 14.0
    hs = abs(math.log(max(f["height"], 1) / max(g["height"], 1))) / 0.5
    hand = abs(f["hand"] - g["hand"]) / 0.6
    return c / 45.0 + sw * 0.6 + sl * 0.6 + hs * 0.5 + hand * 0.6


def _mean(feats):
    return {"ink": np.mean([f["ink"] for f in feats], axis=0).tolist(),
            "stroke": float(np.median([f["stroke"] for f in feats])),
            "slant": float(np.median([f["slant"] for f in feats])),
            "height": float(np.median([f["height"] for f in feats])),
            "hand": float(np.mean([f["hand"] for f in feats]))}


def group(feats, threshold=1.0):
    """Greedy agglomerative grouping. Returns a group index for every line."""
    n = len(feats)
    groups = [[i] for i in range(n)]
    cents = [dict(f) for f in feats]
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


def unread_lines(rgb, zoom, known_rects):
    """Text-like ink regions (page points) not covered by any known rect.
    rgb: page image rendered at `zoom` px per point."""
    mask = ink_mask(rgb)
    H, W = mask.shape
    # forget ink already explained by OCR words or digital text
    for r in known_rects:
        x0, y0, x1, y1 = (int(max(v * zoom, 0)) for v in r)
        mask[max(y0 - 2, 0):min(y1 + 2, H), max(x0 - 2, 0):min(x1 + 2, W)] = False
    # remove long rules (table lines, underlines)
    for axis in (0, 1):
        runs_len = int(60 * zoom)
        m = mask if axis == 1 else mask.T
        for i in range(m.shape[0]):
            row = m[i]
            if row.sum() < runs_len:
                continue
            d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                if e - s >= runs_len:
                    row[s:e] = False
    # join letters into words and lines: smear horizontally
    gap = max(int(6 * zoom), 2)
    cs = np.cumsum(np.pad(mask.astype(np.int32), ((0, 0), (gap, gap))), axis=1)
    smeared = (cs[:, 2 * gap:] - cs[:, :-2 * gap]) > 0
    smeared = smeared[:, :W] if smeared.shape[1] >= W else np.pad(smeared, ((0, 0), (0, W - smeared.shape[1])))
    out = []
    for x0, y0, x1, y1, n in _components(smeared):
        w, h = (x1 - x0) / zoom, (y1 - y0) / zoom
        ink = mask[y0:y1, x0:x1].sum()
        if h < 5 or h > 70 or w < 12 or w < h * 0.8:
            continue                                   # dots, noise, pictures, vertical strokes
        density = ink / max((x1 - x0) * (y1 - y0), 1)
        if density < 0.03 or density > 0.6:
            continue                                   # too faint, or a filled shape
        out.append([x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom])
    return out
