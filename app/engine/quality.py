"""How good a rewritten line is, measured instead of judged by eye: the handwriting
reader reads back what Calamo wrote, and the reading is compared with the text asked for
(character error rate, 0 = read perfectly, 1 = nothing right).

Used to pick, among a few compositions of the same text, the one that reads best, and by
tools/eval_handwriting.py to measure the whole pipeline on real pages."""
import io

import numpy as np


def _norm(t):
    return " ".join((t or "").lower().split())


def distance(a, b):
    """Levenshtein distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(got, want):
    """Character error rate of got against want (case and spacing ignored)."""
    g, w = _norm(got), _norm(want)
    if not w:
        return 0.0 if not g else 1.0
    return min(distance(g, w) / len(w), 1.0)


def on_paper(png, pad=0.25):
    """A transparent rendering (RGBA PNG) laid on white paper, as an RGB array with a
    margin around it, the way a scanned line looks to the reader."""
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    m = int(img.height * pad) + 4
    paper = Image.new("RGB", (img.width + 2 * m, img.height + 2 * m), "white")
    paper.paste(img, (m, m), img)
    return np.asarray(paper)


def legibility(png, text, reader):
    """(error rate, what the reader read) for a rendered line."""
    try:
        got, _ = reader.read(on_paper(png))
    except Exception:
        return 1.0, ""
    return cer(got, text), got
