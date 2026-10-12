"""Text understanding for content that has no text layer (handwriting, scans).

* Windows built-in OCR (offline, no extra download) produces a first reading.
* An optional Claude vision pass refines the reading using page context.
* Every correction the user makes is remembered and re-applied automatically.
"""
import asyncio
import base64
import json
import os
import re
import threading

import pymupdf as fitz

from .storage import load_json, save_json

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
        return asyncio.run(_recognize_async(png_bytes, lang_tag))


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
            if "".join(s["text"] for s in l["spans"]).strip():
                rects.append(fitz.Rect(l["bbox"]))
    return rects


def analyze_page(page, learning, lang_tag=None, include_native=False):
    """OCR a page and return 'ink' lines: text that exists only as pixels."""
    zoom = OCR_DPI / 72.0
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > 9800:
        zoom *= 9800 / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    raw_lines = windows_ocr(pix.tobytes("png"), lang_tag)
    native = [] if include_native else _native_rects(page)

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
                kept.append({"text": w["text"], "bbox": [r.x0, r.y0, r.x1, r.y1]})
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
        raw = " ".join(w["text"] for w in words)
        x0 = min(w["bbox"][0] for w in words)
        y0 = min(w["bbox"][1] for w in words)
        x1 = max(w["bbox"][2] for w in words)
        y1 = max(w["bbox"][3] for w in words)
        text, learned = learning.apply(raw)
        result.append({
            "bbox": [x0, y0, x1, y1], "raw": raw, "text": text, "words": words,
            "source": "learned" if learned else "ocr", "doubt": _doubt(raw),
            "synthetic": False,
        })
    result.sort(key=lambda l: (round(l["bbox"][1] / 6), l["bbox"][0]))
    return result


def _doubt(text):
    """Heuristic 0..1: how likely the reading is wrong."""
    if not text:
        return 1.0
    score = 0.0
    letters = sum(c.isalpha() for c in text)
    odd = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in ".,;:'\"!?-()€%/àèéìòùÀÈÉÌÒÙ"))
    score += min(odd / max(len(text), 1) * 4, 0.6)
    if letters and any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
        mixed = sum(1 for t in text.split() if any(c.isdigit() for c in t) and any(c.isalpha() for c in t))
        score += 0.25 * mixed
    if len(text.strip()) <= 2:
        score += 0.3
    return round(min(score, 1.0), 2)


# --------------------------------------------------------------------------- Claude vision
AI_MODEL = "claude-opus-5-5"


def ai_refine(page, lines, api_key, learning):
    """Ask Claude to re-read the ink lines of a page using the page image as context."""
    import anthropic

    if not lines:
        return lines
    zoom = 2.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img_b64 = base64.standard_b64encode(pix.tobytes("png")).decode("ascii")
    W, H = page.rect.width, page.rect.height
    listing = "\n".join(
        f'{i}: box=({l["bbox"][0] / W:.3f},{l["bbox"][1] / H:.3f},{l["bbox"][2] / W:.3f},{l["bbox"][3] / H:.3f}) '
        f'ocr="{l["text"]}"' for i, l in enumerate(lines))
    hints = learning.hints()
    prompt = (
        "This is a page from a PDF. Some lines are handwritten or scanned, so a basic OCR read them, "
        "often wrongly. For each numbered line below (box = normalized x0,y0,x1,y1 on the page), read the "
        "actual text in that region of the image and return the exact transcription, keeping the original "
        "language, spelling, punctuation and capitalization as written. If a region is unreadable keep the OCR text.\n\n"
        f"{listing}"
    )
    if hints:
        prompt += "\n\nThe user previously corrected these OCR misreadings of their handwriting:\n" + "\n".join(hints)
    client = anthropic.Anthropic(api_key=api_key)
    response = client.beta.messages.create(
        model=AI_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {"lines": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                        "required": ["id", "text"], "additionalProperties": False}}},
                    "required": ["lines"], "additionalProperties": False,
                },
            },
        },
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("L'AI ha rifiutato la richiesta per questa pagina.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    for item in data.get("lines", []):
        i = item.get("id")
        if isinstance(i, int) and 0 <= i < len(lines) and item.get("text", "").strip():
            new = item["text"].strip()
            if new != lines[i]["text"]:
                lines[i]["text"] = new
                lines[i]["source"] = "ai"
            lines[i]["doubt"] = min(lines[i]["doubt"], 0.1)
    return lines
