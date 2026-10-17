"""Whole PDF files: merge several into one, split one into many. Works on files on disk
(and on the open document, passed as bytes), without opening them in the editor."""
import os
import re

import pymupdf as fitz

from .editing import parse_pages
from .storage import gc


def _open(src, password=None):
    """src: a path, or (name, bytes) for the document open in the editor."""
    if isinstance(src, (tuple, list)):
        doc = fitz.open(stream=src[1], filetype="pdf")
    else:
        doc = fitz.open(src)
    if doc.needs_pass and not (password and doc.authenticate(password)):
        name = src[0] if isinstance(src, (tuple, list)) else os.path.basename(src)
        raise RuntimeError(f"“{name}” è protetto da password: aprilo e salvane una copia senza password.")
    return doc


def describe(path):
    """Name, pages and size of a PDF, for the list of files to merge."""
    try:
        doc = fitz.open(path)
        locked = doc.needs_pass
        pages = 0 if locked else len(doc)
    except Exception:
        raise RuntimeError(f"“{os.path.basename(path)}” non è un PDF valido.")
    return {"path": path, "name": os.path.basename(path), "pages": pages, "locked": bool(locked),
            "size": os.path.getsize(path)}


def merge(items, out_path, bookmarks=True):
    """items: [{src, pages ('' = all), name}] in order. One bookmark per file."""
    if not items:
        raise RuntimeError("Aggiungi almeno un PDF da unire.")
    out = fitz.open()
    toc = []
    for it in items:
        doc = _open(it["src"])
        pages = parse_pages(it.get("pages", ""), len(doc))
        start = len(out)
        # consecutive runs keep links and annotations of each run together
        run = [pages[0]]
        for p in pages[1:] + [None]:
            if p is not None and p == run[-1] + 1:
                run.append(p)
                continue
            out.insert_pdf(doc, from_page=run[0], to_page=run[-1], annots=True, links=True)
            if p is not None:
                run = [p]
        if bookmarks:
            toc.append([1, os.path.splitext(it.get("name") or "Documento")[0], start + 1])
            # the bookmarks of each file go under its own
            for lvl, title, page, *rest in doc.get_toc(simple=True):
                if page - 1 in pages:
                    toc.append([lvl + 1, title, start + pages.index(page - 1) + 1])
    if bookmarks:
        try:
            out.set_toc(toc)
        except Exception:
            out.set_toc([t for t in toc if t[0] == 1])
    out.save(out_path, garbage=gc(out, 3), deflate=True)
    return {"path": out_path, "name": os.path.basename(out_path), "pages": len(out), "files": len(items)}


def _safe(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return name[:60] or "parte"


def split(src, base, folder, mode, value=None):
    """Split a PDF into files in folder. mode:
    'every' (value = pages per file), 'ranges' (value = '1-3; 4-6' or '1-3, 4-6'),
    'single' (one file per page), 'bookmarks' (one file per top-level bookmark),
    'size' (value = MB per file at most)."""
    doc = _open(src)
    n = len(doc)
    groups, names = [], []
    if mode == "every":
        k = max(int(value or 1), 1)
        groups = [list(range(a, min(a + k, n))) for a in range(0, n, k)]
    elif mode == "single":
        groups = [[i] for i in range(n)]
    elif mode == "ranges":
        parts = [p for p in re.split(r"[;,]", value or "") if p.strip()]
        if not parts:
            raise RuntimeError("Scrivi gli intervalli, per esempio: 1-3, 4-6, 7-")
        for p in parts:
            g = parse_pages(p, n)
            groups.append(g)
            names.append(f"pagine {g[0] + 1}-{g[-1] + 1}" if len(g) > 1 else f"pagina {g[0] + 1}")
    elif mode == "bookmarks":
        top = [(t[1], t[2] - 1) for t in doc.get_toc(simple=True) if t[0] == 1 and t[2] >= 1]
        top = sorted({p: t for t, p in top}.items())          # one per start page, in page order
        if not top:
            raise RuntimeError("Questo PDF non ha segnalibri: scegli un altro modo di dividerlo.")
        starts = [p for p, _ in top]
        if starts[0] > 0:
            starts.insert(0, 0)
            top.insert(0, (0, "Inizio"))
        for k, (p, title) in enumerate(top):
            end = starts[k + 1] if k + 1 < len(starts) else n
            if end > p:
                groups.append(list(range(p, end)))
                names.append(_safe(title))
    elif mode == "size":
        limit = max(float(value or 5), 0.2) * 1024 * 1024
        # what each page weighs on its own (fonts shared by pages are counted each time,
        # so the files come out a little under the limit)
        cur, total = [], 0
        for i in range(n):
            one = fitz.open()
            one.insert_pdf(doc, from_page=i, to_page=i)
            w = len(one.tobytes(garbage=gc(one, 3), deflate=True))
            if cur and total + w > limit:
                groups.append(cur)
                cur, total = [], 0
            cur.append(i)
            total += w
        if cur:
            groups.append(cur)
    else:
        raise RuntimeError("Modo di divisione sconosciuto.")
    if not groups:
        raise RuntimeError("Nessuna pagina da dividere.")
    paths = []
    width = len(str(len(groups)))
    for k, g in enumerate(groups):
        out = fitz.open()
        for p in g:
            out.insert_pdf(doc, from_page=p, to_page=p, annots=True, links=True)
        label = names[k] if k < len(names) and names[k] else (
            f"pagina {g[0] + 1}" if mode == "single" else f"parte {str(k + 1).zfill(width)}")
        path = os.path.join(folder, f"{_safe(base)} - {label}.pdf")
        j = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{_safe(base)} - {label} ({j}).pdf")
            j += 1
        out.save(path, garbage=gc(out, 3), deflate=True)
        paths.append(path)
    return {"files": paths, "folder": folder, "pages": n}


def repair(src, out_path):
    """A damaged PDF read as far as possible and written again clean. Pages that cannot be
    read are left out and listed."""
    import pymupdf as fz
    with open(src, "rb") as f:
        data = f.read()
    if not data.lstrip()[:5].startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        raise RuntimeError("Il file non sembra un PDF.")
    fz.TOOLS.mupdf_warnings()                     # forget earlier warnings
    try:
        doc = fz.open(stream=data, filetype="pdf")
    except Exception:
        raise RuntimeError("Il file è troppo danneggiato: non riesco a leggerlo.")
    if doc.needs_pass:
        raise RuntimeError("Il PDF è protetto da password: aprilo con la password e salvane una copia.")
    out = fz.open()
    bad = []
    for i in range(doc.page_count):
        try:
            doc[i].get_pixmap(matrix=fz.Matrix(0.2, 0.2))
            out.insert_pdf(doc, from_page=i, to_page=i, annots=True, links=True)
        except Exception:
            bad.append(i + 1)
    if not len(out):
        raise RuntimeError("Nessuna pagina leggibile in questo file.")
    try:
        toc = [t for t in doc.get_toc(simple=False) if 1 <= t[2] <= len(out)]
        if toc and not bad:
            out.set_toc(toc)
        out.set_metadata({k: v for k, v in doc.metadata.items() if k in ("title", "author", "subject", "keywords") and v})
    except Exception:
        pass
    warnings = fz.TOOLS.mupdf_warnings()
    out.save(out_path, garbage=gc(out, 4), deflate=True, clean=True)
    return {"path": out_path, "name": os.path.basename(out_path), "pages": len(out), "bad": bad,
            "repaired": bool(doc.is_repaired or warnings or bad)}
