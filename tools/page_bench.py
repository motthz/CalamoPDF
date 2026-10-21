"""How long a whole page of handwriting takes to read: a form filled in by hand (made here
with the handwriting fonts of Windows: no real document leaves the user's computer), read
by the version of the app in --app. Run it with the old and the new version on the same
computer and --compare prints the difference.
   python tools/page_bench.py --app OLD/app --out old.json
   python tools/page_bench.py --out new.json
   python tools/page_bench.py --compare old.json new.json"""
import io
import json
import os
import random
import sys
import tempfile
import time


def form_page(seed):
    from PIL import Image, ImageDraw, ImageFont
    win = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    hands = [os.path.join(win, f) for f in ("Inkfree.ttf", "segoesc.ttf", "segoepr.ttf")]
    hands = [f for f in hands if os.path.exists(f)]
    printed = os.path.join(win, "arial.ttf")
    rnd = random.Random(seed)
    W, H = 2480, 3508                                     # A4 at 300 dpi
    img = Image.new("RGB", (W, H), (246, 244, 236))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(printed, 54) if os.path.exists(printed) else ImageFont.load_default()
    small = ImageFont.truetype(printed, 34) if os.path.exists(printed) else ImageFont.load_default()
    d.text((180, 160), "Registro attività di progetto", font=big, fill=(20, 20, 20))
    cols = [180, 420, 1080, 1560, 1940, 2300]
    heads = ["N.", "Attività", "Responsabile", "Scadenza", "Stato"]
    words = ["Analisi dei requisiti", "Riunione con il cliente", "Sviluppo interfaccia", "Verifica dei dati",
             "Consegna documentazione", "Test di sistema", "Revisione del codice", "Preparare il preventivo",
             "Mario Bianchi", "Luca Rossi", "Giulia Verdi", "Anna Neri", "12/03/2026", "30/04/2026",
             "15 maggio", "Completato", "In corso", "Da fare", "Rinviato a giugno", "Approvato"]
    top, rh = 360, 210
    for k in range(13):
        d.line((cols[0], top + k * rh, cols[-1], top + k * rh), fill=(40, 40, 60), width=4)
    for x in cols:
        d.line((x, top, x, top + 12 * rh), fill=(40, 40, 60), width=4)
    for c, h in enumerate(heads):
        d.text((cols[c] + 20, top + 70), h, font=small, fill=(20, 20, 20))
    for r in range(1, 12):
        pen = rnd.choice([(25, 40, 140), (20, 20, 30), (30, 60, 160)])
        for c in range(5):
            text = str(r) if c == 0 else rnd.choice(words[:8] if c == 1 else words[8:12] if c == 2 else
                                                   words[12:15] if c == 3 else words[15:])
            font = ImageFont.truetype(rnd.choice(hands), rnd.choice((44, 50, 56))) if hands else small
            box = font.getbbox(text)
            room = cols[c + 1] - cols[c] - 40
            if box[2] - box[0] > room:                       # too long for the cell: two lines
                half = text.rsplit(" ", 1) if " " in text else [text]
                for k, part in enumerate(half):
                    d.text((cols[c] + 20, top + r * rh + 30 + k * 80), part, font=font, fill=pen)
            else:
                d.text((cols[c] + 20, top + r * rh + 60 + rnd.randint(-15, 15)), text, font=font, fill=pen)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def run(app, out, pages=2):
    sys.path.insert(0, app)
    os.environ.setdefault("CALAMO_DATA", tempfile.mkdtemp())
    import pymupdf as fitz
    from engine.document import Session
    from engine.handreader import READER
    import engine.ocr as ocr
    READER._load()
    spent = {"ocr": 0.0, "reader": 0.0}

    def timed(obj, name, key):                 # where the time goes: Windows OCR, the reader
        f = getattr(obj, name)

        def g(*a, **k):
            t = time.perf_counter()
            try:
                return f(*a, **k)
            finally:
                spent[key] += time.perf_counter() - t
        setattr(obj, name, g)
    timed(ocr, "windows_ocr", "ocr")
    for name in ("read_ahead", "read"):
        if hasattr(READER, name):
            timed(READER, name, "reader")
    res = []
    for seed in range(pages):
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=form_page(seed))
        path = os.path.join(tempfile.mkdtemp(), "modulo.pdf")
        doc.save(path)
        s = Session()
        s.open(path)
        spent.update(ocr=0.0, reader=0.0)
        t = time.perf_counter()
        r = s.analyze(0, reader=READER)
        text = time.perf_counter() - t
        learn = 0.0
        if hasattr(s, "learn_page"):
            t = time.perf_counter()
            s.learn_page(0)
            learn = time.perf_counter() - t
        lines = sum(1 for l in r["ink"] if l["source"] != "unread")
        print(f"   pagina {seed + 1}: testo {text:.1f} s (OCR di Windows {spent['ocr']:.1f}, lettore a mano "
              f"{spent['reader']:.1f}, resto {text - spent['ocr'] - spent['reader']:.1f}), calligrafia {learn:.1f} s, "
              f"{lines} righe lette", flush=True)
        res.append({"text": text, "learn": learn, "lines": lines, **spent})
    with open(out, "w") as f:
        json.dump(res, f)


def compare(old, new):
    a, b = json.load(open(old)), json.load(open(new))
    ta, tb = sum(x["text"] for x in a), sum(x["text"] for x in b)
    fa, fb = ta + sum(x["learn"] for x in a), tb + sum(x["learn"] for x in b)
    n = len(a)
    print(f"testo a schermo: {ta / n:.1f} -> {tb / n:.1f} s a pagina ({(tb / ta - 1) * 100:+.0f}%)")
    print(f"con la calligrafia imparata: {fa / n:.1f} -> {fb / n:.1f} s a pagina ({(fb / fa - 1) * 100:+.0f}%)")
    print(f"righe lette: {sum(x['lines'] for x in a)} -> {sum(x['lines'] for x in b)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--compare"]:
        compare(args[1], args[2])
    else:
        app = args[args.index("--app") + 1] if "--app" in args else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
        out = args[args.index("--out") + 1] if "--out" in args else "page_bench.json"
        run(os.path.abspath(app), out)
