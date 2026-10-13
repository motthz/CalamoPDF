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
        check(f"rotation {rot}: line found where it is shown", (line & target).get_area() > target.get_area() * 0.5,
              str([round(v) for v in line]))
        before = s.render(0, 1)
        s.edit_ink(0, 0, "Anna")
        page = s.doc[0]
        imgs = [fitz.Rect(i["bbox"]) * _view_matrix(page) for i in page.get_image_info()]
        new = [r for r in imgs if r.width < target.width * 0.8]
        check(f"rotation {rot}: new writing placed on the old line",
              bool(new) and abs(new[-1].x0 - line.x0) < 3 and abs(new[-1].y0 - line.y0) < line.height,
              str([[round(v) for v in r] for r in new]))
        pix = page.get_pixmap(clip=fitz.Rect(line.x0 + line.width * 0.6, line.y0, line.x1, line.y1))
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].mean(axis=2)
        check(f"rotation {rot}: old writing covered", (a < 140).mean() < 0.01, f"{(a < 140).mean():.3f}")
        check(f"rotation {rot}: page changed", s.render(0, 1) != before)


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
    for t in (test_ocr, test_rotated_page_edit, test_portable_swap, test_setup_script, test_release_check):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(t.__name__, False, repr(e))
    print("\n%d failure(s)" % len(failures))
    sys.exit(1 if failures else 0)
