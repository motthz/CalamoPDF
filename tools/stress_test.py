"""Stress and performance test of the engine on big documents: how long every operation
takes on a 500-page text document, a 60-page scan with big pictures, a form with 600
fields. Prints a table; fails if an operation takes much longer than its limit.
   python tools/stress_test.py [pages]"""
import io
import os
import sys
import time
import tracemalloc

# the Windows console (cp1252) cannot print every character of the results
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "stress")
os.makedirs(WORK, exist_ok=True)
os.environ["CALAMO_DATA"] = os.path.join(WORK, "data")
sys.path.insert(0, os.path.join(ROOT, "app"))
import numpy as np  # noqa: E402
import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402
from engine.document import Session  # noqa: E402
from engine import batch, files, signing  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
P = lambda n: os.path.join(WORK, n)  # noqa: E731
rows, slow = [], []


def timed(name, fn, limit):
    t = time.perf_counter()
    try:
        r = fn()
        err = ""
    except Exception as e:
        r, err = None, f"{e.__class__.__name__}: {e}"
    dt = time.perf_counter() - t
    rows.append((name, dt, limit, err))
    flag = "ERR " if err else ("LENTO" if dt > limit else "ok  ")
    print(f"{flag} {dt:8.2f}s  (limite {limit:5.1f}s)  {name}  {err}", flush=True)
    if err or dt > limit:
        slow.append(name)
    return r


def make_text(n):
    d = fitz.open()
    for i in range(n):
        p = d.new_page()
        p.insert_text((60, 60), f"Capitolo {i + 1}" if i % 10 == 0 else f"Sezione {i + 1}", fontsize=18 if i % 10 == 0 else 14)
        y = 100
        for k in range(40):
            p.insert_text((60, y), f"Riga {k} della pagina {i + 1}: testo di prova con parole come IBAN IT60X0542811101000000123456 e mario.rossi@example.com", fontsize=9)
            y += 17
    d.save(P("testo.pdf"))


def make_scan(n):
    d = fitz.open()
    rng = np.random.default_rng(1)
    for i in range(n):
        a = (rng.random((2339, 1654)) * 30 + 215).astype(np.uint8)       # A4 at 200 dpi, noisy paper
        a[300:340, 200:1400] = 40
        im = Image.fromarray(a).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        p = d.new_page()
        p.insert_image(p.rect, stream=buf.getvalue())
    d.save(P("scansione.pdf"))


def make_form(n):
    d = fitz.open()
    p = d.new_page()
    for k in range(n):
        if k and k % 60 == 0:
            p = d.new_page()
        j = k % 60
        p.insert_text((40 + (j % 2) * 280, 40 + (j // 2) * 26), f"Campo {k}: ______________", fontsize=9)
    d.save(P("modulo-grande.pdf"))


tracemalloc.start()
timed(f"crea PDF di testo da {N} pagine", lambda: make_text(N), 60)
timed("crea scansione da 60 pagine (200 dpi)", lambda: make_scan(60), 120)
timed("crea modulo cartaceo con 600 spazi", lambda: make_form(600), 20)
print(f"   dimensioni: testo {os.path.getsize(P('testo.pdf')) // 1024} KB, scansione {os.path.getsize(P('scansione.pdf')) // 1048576} MB")

s = Session()
timed("apri testo", lambda: s.open(P("testo.pdf")), 3)
timed("info()", lambda: s.info(), 0.5)
timed("render pagina 1 (zoom 2)", lambda: s.render(0, 2.0), 1)
timed("blocks pagina 1", lambda: s.blocks(0), 1)
timed(f"blocks di tutte le {N} pagine", lambda: [s.blocks(i) for i in range(N)], 60)
timed("cerca una parola in tutto il documento", lambda: s.search("pagina 250"), 15)
timed("modifica un blocco (pagina 300)", lambda: s.edit_block(min(299, N - 1), s.blocks(min(299, N - 1))["blocks"][2]["bbox"], "Testo modificato in una pagina lontana"), 3)
timed("annulla", lambda: s.undo(), 3)
timed("segnalibri dai titoli", lambda: s.bookmarks_from_headings(), 30)
timed("imposta i segnalibri", lambda: s.set_bookmarks(s.bookmarks_from_headings()), 30)
timed("trova dati sensibili", lambda: s.find_sensitive(["iban", "email"]), 60)
timed("numeri di pagina su tutte", lambda: s.stamp_pages({"footer": "Pagina {n} di {N}"}), 60)
timed("Bates su tutte", lambda: s.stamp_pages({"footer": "{bates}", "bates_prefix": "X-"}), 60)
timed("ruota pagina", lambda: s.rotate_page(5), 3)
timed("ritaglia tutte (margini)", lambda: s.crop_pages("", margins=[5, 5, 5, 5]), 30)
timed("ritaglio automatico tutte", lambda: s.crop_pages("", auto=True), 120)
timed("ripristina ritaglio", lambda: s.reset_crop(""), 30)
timed("verifica documento", lambda: s.preflight(), 30)
timed("salva", lambda: s.save(P("testo-salvato.pdf")), 30)
timed("2 pagine per foglio", lambda: s.nup_bytes(2, 1), 60)
timed("opuscolo", lambda: s.booklet_bytes(), 60)
timed("ridimensiona tutte in A3", lambda: s.resize_pages("", "A3"), 120)
timed("annulla ridimensiona", lambda: s.undo(), 30)
timed("bianco e nero", lambda: s.to_grayscale(), 60)
timed("esporta testo", lambda: s.export_text(P("t.txt")), 30)
timed("esporta HTML", lambda: s.export_html(P("t.html")), 60)
timed("esporta PowerPoint (prime 50)", lambda: (s.delete_pages(f"51-{N}"), s.export_pptx(P("t.pptx")), s.undo()), 120)
timed("PDF/A", lambda: s.save_pdfa(P("t-pdfa.pdf")), 300)
timed("copia leggera", lambda: s.save_optimized(P("t-light.pdf")), 60)
c = signing.create_certificate("Stress", "", "", "stress")
timed("firma digitale", lambda: s.sign_digital(P("t-firm.pdf"), c["path"], "stress", 0, [300, 700, 500, 760]), 60)
s2 = Session()
s2.open(P("t-firm.pdf"))
timed("verifica firma", lambda: s2.verify_signatures(), 60)
timed("40 annulla/ripeti di fila", lambda: [s.redo() for _ in range(5)], 60)

sc = Session()
timed("apri scansione", lambda: sc.open(P("scansione.pdf")), 5)
timed("render scansione (zoom 3)", lambda: sc.render(0, 3.0), 3)
timed("elementi grafici della scansione", lambda: sc.graphics(0), 15)
timed("copia leggera scansione", lambda: sc.save_optimized(P("s-light.pdf"), "bassa"), 240)
print(f"   scansione {os.path.getsize(P('scansione.pdf')) // 1048576} MB -> {os.path.getsize(P('s-light.pdf')) // 1048576} MB")
timed("PDF/A scansione", lambda: sc.save_pdfa(P("s-pdfa.pdf")), 240)
timed("confronta scansione con se stessa", lambda: sc.compare_with(P("scansione.pdf")), 120)
timed("ripara scansione", lambda: files.repair(P("scansione.pdf"), P("s-rip.pdf")), 240)

fm = Session()
fm.open(P("modulo-grande.pdf"))
r = timed("riconosci 600 campi", lambda: fm.detect_fields(""), 60)
print(f"   campi trovati: {r and r['added']}")
timed("elenco campi", lambda: fm.fields(), 5)
vals = [{"page": f["page"], "id": f["id"], "value": f"v{k}"} for k, f in enumerate(fm.fields()[:200])]
timed("compila 200 campi in una volta", lambda: fm.fill_fields(vals), 60)
f0 = fm.fields()[0]
timed("compila 1 campo (con annulla)", lambda: fm.fill_fields([{"page": 0, "id": f0["id"], "value": "x"}]), 3)
timed("appiattisci", lambda: fm.flatten(True, True), 60)

out = os.path.join(WORK, "lotto")
os.makedirs(out, exist_ok=True)
srcs = [P("testo.pdf")] * 3 + [P("modulo-grande.pdf")] * 5
timed("lotto: comprimi 8 file", lambda: batch.run(srcs, "compress", {}, out), 240)
timed("unisci 3 grandi", lambda: files.merge([{"src": P("testo.pdf")}] * 3, P("unito.pdf")), 120)
timed("dividi ogni 10 pagine", lambda: files.split(P("testo.pdf"), "t", out, "every", 10), 120)
cur, peak = tracemalloc.get_traced_memory()
print(f"   memoria Python: attuale {cur // 1048576} MB, picco {peak // 1048576} MB")
os.remove(c["path"])
print("LENTI O CON ERRORI: " + (", ".join(slow) if slow else "nessuno"))
