"""Checks that need a real Windows: the built-in OCR on the sample and the
self-update swap script. Usage: python tools/test_windows.py  (exit code 1 on failure)"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["CALAMO_DATA"] = tempfile.mkdtemp(prefix="calamo-data-")
sys.path.insert(0, os.path.join(ROOT, "app"))

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def test_ocr():
    from engine.ocr import available_languages
    from engine.document import Session
    langs = available_languages()
    check("OCR languages available", bool(langs), ", ".join(langs))
    if not langs:
        return
    s = Session()
    s.open(os.path.join(ROOT, "samples", "esempio.pdf"))
    res = s.analyze(0)
    for l in res["ink"]:
        print(f"   [{l['kind']}] {l['source']} doubt={l['doubt']}: {l['text']}")
    hand = [l for l in res["ink"] if l["kind"] == "hand"]
    check("handwriting lines found", len(hand) >= 2, f"{len(hand)} lines")
    check("glyphs learned", len(res["hand"]["glyphs"]) >= 5, f"{len(res['hand']['glyphs'])} glyphs")
    # mark the letters of a line by hand and rewrite it with digits
    if hand:
        L = s.ink_letters(0, hand[0]["id"])
        check("letter boxes proposed", len(L["segments"]) >= 3, f"{len(L['segments'])} boxes")
        segs = [dict(x, char=x["char"] or "x") for x in L["segments"]]
        r = s.teach_letters(0, hand[0]["id"], segs)
        check("letters stored", r["stored"] >= 3, f"{r['stored']} stored")
        s.edit_ink(0, hand[-1]["id"], "Firmato 12/05/2026")
    out = os.path.join(tempfile.mkdtemp(), "out.pdf")
    s.save(out)
    check("save after edits", os.path.getsize(out) > 1000)


def test_rotated_page_edit():
    """Phone scans often have /Rotate: rewriting handwriting must replace it in place."""
    import io
    import numpy as np
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont
    from engine.document import Session, _view_matrix
    font = ImageFont.truetype(os.path.join(os.environ["WINDIR"], "Fonts", "Inkfree.ttf"), 64)
    img = Image.new("RGB", (900, 110), "white")
    ImageDraw.Draw(img).text((10, 10), "Firmato Giulia", font=font, fill=(25, 40, 110))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    for rot in (0, 90, 270):
        doc = fitz.open()
        p = doc.new_page(width=595, height=842)
        p.set_cropbox(fitz.Rect(20, 30, 580, 820))
        p.set_rotation(rot)
        target = fitz.Rect(60, 200, 330, 233)                     # where it appears on screen
        p.insert_image(target * ~_view_matrix(p), stream=buf.getvalue(), rotate=rot)
        path = os.path.join(tempfile.mkdtemp(), "rot.pdf")
        doc.save(path)
        s = Session()
        s.open(path)
        ink = s.analyze(0)["ink"]
        if not ink:
            check(f"rotation {rot}: handwriting found", False)
            continue
        line = fitz.Rect(ink[0]["bbox"])
        check(f"rotation {rot}: line found where it is shown", target.contains((line.tl + line.br) / 2),
              str([round(v) for v in line]))
        s.edit_ink(0, 0, "Anna")
        page = s.doc[0]

        def ink_share(rect):    # dark pixels in a displayed area, as the user sees the page
            pix = page.get_pixmap(clip=rect, dpi=144)
            a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].mean(axis=2)
            return float((a < 140).mean())
        start = fitz.Rect(line.x0, line.y0 - 4, line.x0 + line.width * 0.4, line.y1 + 4)
        tail = fitz.Rect(line.x0 + line.width * 0.6, line.y0, line.x1, line.y1)
        elsewhere = fitz.Rect(page.rect.x0, line.y1 + line.height * 2, page.rect.x1, page.rect.y1)
        check(f"rotation {rot}: new writing on the old line", ink_share(start) > 0.02, f"{ink_share(start):.3f}")
        check(f"rotation {rot}: old writing covered", ink_share(tail) < 0.01, f"{ink_share(tail):.3f}")
        check(f"rotation {rot}: nothing written elsewhere", ink_share(elsewhere) < 0.001, f"{ink_share(elsewhere):.4f}")


def test_writings():
    """A scanned page with print and two people's handwriting: every line must land in
    the writing of its author, and rewriting must keep size, baseline and letters."""
    import io
    import numpy as np
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from engine.document import Session
    from engine.handwriting import measure_line
    fonts = os.path.join(os.environ["WINDIR"], "Fonts")
    rows = [("print", "times.ttf", 44, (20, 20, 20), "Il budget approvato resta invariato"),
            ("print", "times.ttf", 44, (20, 20, 20), "Presenti tutti i responsabili di area"),
            ("print", "times.ttf", 44, (20, 20, 20), "Totale previsto per il trimestre"),
            ("A", "Inkfree.ttf", 60, (25, 40, 140), "Consegna entro venerdi"),
            ("A", "Inkfree.ttf", 60, (25, 40, 140), "Firmato Giulia Rossi"),
            ("B", "segoesc.ttf", 54, (170, 25, 25), "Visto e approvato"),
            ("B", "segoesc.ttf", 54, (170, 25, 25), "Marco Bianchi")]
    W, H = 1654, 2339
    scan = Image.new("RGB", (W, H), (247, 246, 241))
    d = ImageDraw.Draw(scan)
    y, truth = 200, []
    for who, f, size, col, text in rows:
        font = ImageFont.truetype(os.path.join(fonts, f), size)
        d.text((180, y), text, font=font, fill=col)
        truth.append((who, text, y * 72 / 200))
        y += int(size * 2.1)
    scan = scan.filter(ImageFilter.GaussianBlur(0.7))
    buf = io.BytesIO()
    scan.save(buf, "JPEG", quality=85)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    path = os.path.join(tempfile.mkdtemp(), "writings.pdf")
    doc.save(path)
    s = Session()
    s.open(path)
    r = s.analyze(0)
    names = {st["id"]: st for st in r["styles"]}
    found = {}
    for l in r["ink"]:
        who = min(truth, key=lambda t: abs(t[2] - l["bbox"][1]))[0]
        found.setdefault(who, []).append(l)
        print(f"   {who:5} -> {names[l['style']]['name']:12} [{l['kind']}] {l['source']}: {l['text']}")
    for who in ("print", "A", "B"):
        check(f"writings: lines of {who} found", bool(found.get(who)))
    if not all(found.get(w) for w in ("print", "A", "B")):
        return
    sid = {w: {l["style"] for l in found[w]} for w in found}
    check("writings: print kept together and printed", len(sid["print"]) == 1
          and all(l["kind"] == "print" for l in found["print"]), str(sid["print"]))
    check("writings: each person is one writing", len(sid["A"]) == 1 and len(sid["B"]) == 1,
          f"A={sid['A']} B={sid['B']}")
    check("writings: the two people are not mixed", not (sid["A"] & sid["B"]) and not (sid["print"] & (sid["A"] | sid["B"])),
          f"A={sid['A']} B={sid['B']}")
    check("writings: handwriting recognised as handwriting",
          all(l["kind"] == "hand" for l in found["A"] + found["B"]))
    # rewrite a printed line: same baseline and size as the original
    pl = found["print"][0]
    base0, xh0 = measure_line(s.doc[0], pl["bbox"], pl["text"])
    s.edit_ink(0, pl["id"], "Il budget approvato aumenta")
    base1, xh1 = measure_line(s.doc[0], s.ink[0][pl["id"]]["bbox"], "Il budget approvato aumenta")
    check("print rewrite: same baseline", abs(base1 - base0) < xh0 * 0.25, f"{base0:.1f} -> {base1:.1f}")
    check("print rewrite: same size", abs(xh1 - xh0) < xh0 * 0.15, f"{xh0:.1f} -> {xh1:.1f}")
    check("print rewrite: still searchable", bool(s.doc[0].search_for("aumenta")))
    # rewrite a line of B: it must be drawn in B's ink colour (its own letters), not A's
    bl = found["B"][0]
    s.edit_ink(0, bl["id"], "Visto, approvato")
    pix = s.doc[0].get_pixmap(clip=fitz.Rect(bl["bbox"]), dpi=144)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].astype(int)
    ink = a[a.mean(axis=2) < 170]
    red = len(ink) and (ink[:, 0] - ink[:, 2]).mean() > 40
    check("handwriting rewrite: written with its own writer's ink", bool(red), f"{len(ink)} ink px")


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_portable_swap():
    """The update script must wait for the app to exit, then replace the exe."""
    import updater
    tmp = tempfile.mkdtemp(prefix="calamo upd à-")      # space and accent in the path on purpose
    target = os.path.join(tmp, "CalamoPDF.exe")
    shutil.copy(os.path.join(os.environ["WINDIR"], "System32", "where.exe"), target)
    upd_dir = os.path.join(tmp, "update")
    os.makedirs(upd_dir)
    new = os.path.join(upd_dir, "CalamoPDF.exe")
    shutil.copy(os.path.join(os.environ["WINDIR"], "System32", "whoami.exe"), new)
    want = _sha(new)
    # a fake running app: keeps the target open for 4 s, then exits
    holder = subprocess.Popen([sys.executable, "-c",
                               f"import time; f=open({target!r},'rb'); time.sleep(4)"])
    script = os.path.join(upd_dir, "aggiorna-calamo.cmd")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(updater.build_script({"kind": "portable"}, new, False, target, [holder.pid]))
    t0 = time.time()
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=0x08000000 | 0x00000200)
    time.sleep(1.5)
    check("exe untouched while the app runs", _sha(target) != want)
    while time.time() - t0 < 40 and (not os.path.exists(target) or _sha(target) != want):
        time.sleep(0.5)
    check("exe replaced after the app exits", os.path.exists(target) and _sha(target) == want,
          f"{time.time() - t0:.1f}s")
    time.sleep(1)
    check("update script deletes itself", not os.path.exists(script))


def test_setup_script():
    import updater
    text = updater.build_script({"kind": "setup"}, r"C:\T\CalamoPDF-Setup.exe", True,
                                r"C:\Program Files\Calamo PDF Studio\CalamoPDF.exe", [1, 2])
    check("setup script is silent and relaunches", "/VERYSILENT" in text and "/relaunch=1" in text)
    check("setup script keeps the install folder", '/DIR="C:\\Program Files\\Calamo PDF Studio"' in text)


def test_release_check():
    import updater
    try:
        info = updater.check("0.0.1")
        check("GitHub release found", bool(info and info.get("url")), info and info.get("name"))
    except Exception as e:
        check("GitHub release found", False, str(e))


if __name__ == "__main__":
    for t in (test_ocr, test_writings, test_rotated_page_edit, test_portable_swap, test_setup_script, test_release_check):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(t.__name__, False, repr(e))
    print("\n%d failure(s)" % len(failures))
    sys.exit(1 if failures else 0)
