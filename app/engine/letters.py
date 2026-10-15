"""Telling the letters apart from what is around them, pixel by pixel.

A word on a scan is never alone: it sits on paper that may be shaded or highlighted,
inside the borders of a table, on the rules of a notebook. To rewrite it, only the
pixels of its letters may change:
* the paper's colour is measured around every pixel (so a highlighter is paper, not ink);
* ink is what is much darker than its own paper;
* long thin lines in the ink (table borders, underlines, ruled paper) are not letters;
* the letters' pixels are rebuilt from what surrounds them: lines go on across the gap
  the letters leave, the highlighter and the grain of the paper continue."""
import numpy as np
from PIL import Image

from .writers import rules


def _gray(rgb):
    # coloured ink (blue, red pens) counts as dark as it looks, not as its luminance
    r, g, b = (rgb[:, :, i].astype(np.uint16) for i in range(3))
    mn = np.minimum(np.minimum(r, g), b)
    dark = ((r + g + b) * 51 + mn * 102 >> 8).astype(np.float32)   # 0.6 * mean + 0.4 * min
    # but a bright colour is a highlighter, not ink, however saturated (a yellow marker has
    # almost no blue): above a certain brightness it counts as bright as it looks
    lum = (r * 77 + g * 150 + b * 29 >> 8).astype(np.float32)
    w = np.clip((lum - 110) / 60, 0, 1)
    return dark * (1 - w) + lum * w


def paper(gray, k):
    """The colour the paper has around every pixel: the brightest nearby, smoothed."""
    H, W = gray.shape
    k = max(int(k), 2)
    hb, wb = -(-H // k), -(-W // k)
    pad = np.pad(gray, ((0, hb * k - H), (0, wb * k - W)), mode="edge")
    small = pad.reshape(hb, k, wb, k).max(axis=(1, 3))
    grow = small.copy()                       # a 3x3 max on the blocks: no block is all ink
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grow = np.maximum(grow, np.roll(np.roll(small, dy, 0), dx, 1))
    up = np.asarray(Image.fromarray(np.clip(grow, 0, 255).astype(np.uint8)).resize((wb * k, hb * k), Image.BILINEAR),
                    np.float32)
    return up[:H, :W]


def _runs(mask, axis):
    """For every pixel of mask, the length of the run of mask it belongs to along axis."""
    m = mask if axis == 0 else mask.T
    H, W = m.shape
    out = np.zeros((H, W), np.int32)
    up = np.zeros(W, np.int32)
    for i in range(H):                       # length so far, going down
        up = np.where(m[i], up + 1, 0)
        out[i] = up
    down = np.zeros(W, np.int32)
    for i in range(H - 1, -1, -1):           # spread the total back up the run
        down = np.where(m[i], np.maximum(out[i], down), 0)
        out[i] = down
    return out if axis == 0 else out.T


def analyse(rgb, letter_h):
    """(letters, vertical lines, horizontal lines, ink) masks of an image.
    letter_h: height of a line of text in px (how far the paper is looked for, how long
    a border must be, how thick a line may be)."""
    gray = _gray(rgb)
    bg = paper(gray, max(letter_h * 0.8, 6))
    ink = (gray < bg * 0.62) & (bg - gray > 35)
    band = max(int(letter_h * 0.35), 3)
    vert = rules(ink, band, chain=4, fill=0.7, axes=(0,))
    horiz = rules(ink, band, chain=12, fill=0.8, axes=(1,))     # about 4 line heights
    # where a letter crosses a line, those pixels are the letter's: they are darker than
    # the line (another pen), or part of a stroke much longer than the line is thick
    thick = max(int(letter_h * 0.22), 3)
    for lines, axis in ((horiz, 0), (vert, 1)):
        if lines.any():
            tone = float(np.median(gray[lines]))
            crossing = lines & ((gray < tone - 40) | (_runs(ink, axis) > thick))
            lines &= ~crossing
    return ink & ~(vert | horiz), vert, horiz, ink


def _dilate(m, r=1):
    out = m.copy()
    for k in range(1, r + 1):
        out[k:] |= m[:-k]
        out[:-k] |= m[k:]
        out[:, k:] |= m[:, :-k]
        out[:, :-k] |= m[:, k:]
    return out


def _bridge(line, hole, axis, max_gap):
    """Pixels of `hole` lying between two pixels of `line` along `axis`, closer than max_gap:
    where the letters crossed a line, the line goes on."""
    m = line if axis == 1 else line.T
    h = hole if axis == 1 else hole.T
    H, W = m.shape
    idx = np.arange(W)[None, :].repeat(H, axis=0)
    left = np.where(m, idx, -10 ** 6)
    left = np.maximum.accumulate(left, axis=1)
    right = np.where(m, idx, 10 ** 6)
    right = np.minimum.accumulate(right[:, ::-1], axis=1)[:, ::-1]
    out = h & (idx - left <= max_gap) & (right - idx <= max_gap)
    return out if axis == 1 else out.T


def _row_fill(out, src, hole, max_dist):
    """Fill hole pixels along their row from the nearest src pixels left and right.
    Returns the mask of the pixels filled (both sides found within max_dist)."""
    H, W = src.shape
    idx = np.arange(W)[None, :].repeat(H, axis=0)
    left = np.maximum.accumulate(np.where(src, idx, -1), axis=1)
    right = np.minimum.accumulate(np.where(src, idx, W)[:, ::-1], axis=1)[:, ::-1]
    ok = hole & (left >= 0) & (right < W) & (idx - left <= max_dist) & (right - idx <= max_dist)
    ys, xs = np.nonzero(ok)
    if not len(ys):
        return ok
    li, ri = left[ys, xs], right[ys, xs]
    cl, cr = out[ys, li], out[ys, ri]
    t = ((xs - li) / np.maximum(ri - li, 1))[:, None]
    lin = cl * (1 - t) + cr * t
    near = np.where(t < 0.5, cl, cr)
    sharp = (np.abs(cl - cr).max(axis=1) > 30)[:, None]
    out[ys, xs] = np.where(sharp, near, lin)
    return ok


def _fill(rgb, valid, holes):
    """Colour of each hole pixel from the valid pixels around it (normalised blur,
    growing until every hole is reached)."""
    img = rgb.astype(np.float32)
    out = img.copy()
    todo = holes.copy()
    w = valid.astype(np.float32)
    r = 2
    while todo.any() and r < max(img.shape[:2]):
        k = 2 * r + 1
        num = np.stack([_box(img[:, :, c] * w, k) for c in range(3)], axis=2)
        den = _box(w, k)[:, :, None]
        ok = (den[:, :, 0] > 0.15) & todo
        out[ok] = num[ok] / den[ok]
        todo &= ~ok
        r *= 2
    return out


def _box(a, k):
    """Box blur (mean over k x k), via cumulative sums."""
    p = k // 2
    c = np.pad(a, ((p + 1, p), (p + 1, p)), mode="edge").cumsum(0).cumsum(1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def erase_letters(rgb, region, letter_h, seed=0):
    """Rebuild the background under the letters inside `region` (x0, y0, x1, y1 in px of
    rgb, which should include some surroundings). Returns (rgb image of the region: the
    original everywhere but on the letters, n rebuilt pixels, the region really used - it
    grows to hold whole letters) or (None, 0, region) without letters.
    The image is opaque on purpose: a transparency mask leaves a dark seam around its
    edges when the page is drawn."""
    letters, vert, horiz, ink = analyse(rgb, letter_h)
    x0, y0, x1, y1 = region
    H, W = letters.shape
    # whole letters: the strokes touching the area, also where they stick out of it (an OCR
    # box often cuts ascenders and tails), but never the letters of the lines around
    start = np.zeros_like(letters)
    start[y0:y1, x0:x1] = letters[y0:y1, x0:x1]
    if not start.any():
        return None, 0, region
    # never further than a little above and below the area (the next line is not ours)
    reach = np.zeros_like(letters)
    m = int((y1 - y0) * 1.2) + 2
    reach[max(y0 - m, 0):y1 + m, max(x0 - m, 0):x1 + m] = True
    grown = start
    for _ in range(int(letter_h * 1.5)):
        nxt = _dilate(grown, 1) & letters & reach
        if (nxt == grown).all():
            break
        grown = nxt
    ys, xs = np.nonzero(grown)
    ext = ys.max() - ys.min() + 1
    if ext > letter_h * 1.6 and ext < (y1 - y0) * 4:      # the box was much smaller than the letters
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return erase_letters(rgb, box, ext / 1.15, seed)
    pad = max(int(letter_h * 0.15), 3)
    x0, y0 = max(min(x0, int(xs.min()) - pad), 0), max(min(y0, int(ys.min()) - pad), 0)
    x1, y1 = min(max(x1, int(xs.max()) + 1 + pad), W), min(max(y1, int(ys.max()) + 1 + pad), H)
    inside = np.zeros_like(letters)
    inside[y0:y1, x0:x1] = True
    # the soft edges of the strokes (anti-aliasing, scanner blur, ink bleeding) go too
    gray = _gray(rgb)
    bg = paper(gray, max(letter_h * 0.8, 6))
    touched = gray < bg * 0.96
    core = grown
    hole = (_dilate(core, 5) & touched) | _dilate(core, 3)
    hole &= inside
    lines = vert | horiz
    hole &= ~(lines & ~_dilate(letters, 1))           # a line's own pixels stay as they are
    if not hole.any():
        return None, 0, region
    rebuilt = hole.copy()
    out = rgb[:, :, :3].astype(np.float32).copy()
    gap = int(letter_h * 2)
    # 1. vertical borders go on where the letters crossed them
    if vert.any():
        across = _bridge(vert, hole, 0, gap)
        if across.any():
            out[across] = np.median(rgb[vert][:, :3], axis=0)
            hole = hole & ~across
    # 2. row by row, from the nearest paper on the left and on the right: horizontal
    #    lines, the edges of a highlighter, shading continue; where the two sides differ
    #    (the end of a highlighter stroke) the nearer one wins, keeping the edge sharp
    src = ~hole & ~_dilate(vert, 1) & ~(_dilate(ink, 1) & ~horiz)
    done = _row_fill(out, src, hole, gap)
    hole_left = hole & ~done
    # 3. what is left: from the paper all around
    valid = ~hole & ~_dilate(ink, 1) & ~(vert | horiz)
    if hole_left.any():
        filled = _fill(out, valid | done, hole_left)
        out[hole_left] = filled[hole_left]
    # 4. the grain of the paper, reused from the paper nearby
    rng = np.random.default_rng(seed)
    base = _fill(rgb, valid, np.ones_like(valid))
    resid = (rgb[:, :, :3].astype(np.float32) - base)[valid]
    resid = resid[np.abs(resid).max(axis=1) < 12]
    if len(resid) and hole.any():
        out[hole] += resid[rng.integers(0, len(resid), size=int(hole.sum()))] * 0.8
    patch = np.clip(out[y0:y1, x0:x1], 0, 255).astype(np.uint8)
    return patch, int(rebuilt[y0:y1, x0:x1].sum()), (x0, y0, x1, y1)


def cell_right(rgb, row, x_from, letter_h):
    """x (px) of the first table border to the right of x_from on the rows `row` (y0, y1):
    new text must not cross it. None when the line is not in a table."""
    y0, y1 = row
    letters, vert, horiz, ink = analyse(rgb, letter_h)
    cols = vert[max(y0, 0):y1].any(axis=0)
    xs = np.nonzero(cols[x_from:])[0]
    return int(x_from + xs[0]) if len(xs) else None
