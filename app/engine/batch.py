"""The same operation on many PDF files at once (like the batch tools of the big suites):
compress, repair, PDF/A, remove or add a password, rotate, page numbers, watermark,
flatten, grayscale. Every result is a new file in the chosen folder; the originals stay."""
import os

from . import files as FILES

ACTIONS = {
    "compress": "leggero", "repair": "riparato", "pdfa": "PDF-A", "unlock": "senza password",
    "protect": "protetto", "rotate": "ruotato", "numbers": "numerato", "watermark": "filigrana",
    "flatten": "appiattito", "grayscale": "bianco e nero",
}


def _target(folder, path, suffix):
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(folder, f"{FILES._safe(base)} - {suffix}.pdf")
    k = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{FILES._safe(base)} - {suffix} ({k}).pdf")
        k += 1
    return out


def run(paths, action, opts, folder, progress=None, cancelled=None):
    from .document import Session
    if action not in ACTIONS:
        raise RuntimeError("Operazione sconosciuta.")
    if not paths:
        raise RuntimeError("Scegli almeno un PDF.")
    opts = opts or {}
    results = []
    for k, path in enumerate(paths):
        if cancelled and cancelled():
            break
        if progress:
            progress(k, len(paths))
        name = os.path.basename(path)
        out = _target(folder, path, ACTIONS[action])
        try:
            if action == "repair":
                r = FILES.repair(path, out)
                results.append({"name": name, "ok": True, "out": out,
                                "note": "riparato" if r["repaired"] else "era già integro"})
                continue
            s = Session()
            try:
                s.open(path, opts.get("password") or None)
            except RuntimeError as e:
                if str(e) == "PASSWORD_REQUIRED":
                    raise RuntimeError("protetto da password")
                if str(e) == "PASSWORD_WRONG":
                    raise RuntimeError("password errata")
                raise
            note = ""
            if action == "compress":
                r = s.save_optimized(out, opts.get("quality") or "media")
                note = f"{r['before'] // 1024} KB → {r['after'] // 1024} KB"
            elif action == "pdfa":
                r = s.save_pdfa(out)
                note = "conforme" if r["ok"] else "da controllare: " + ", ".join(i["label"] for i in r["left"])
            elif action == "unlock":
                s.save_unlocked(out)
            elif action == "protect":
                pw = opts.get("newPassword") or ""
                s.save_protected(out, pw, "", True, True, False)
            else:
                if action == "rotate":
                    deg = int(opts.get("angle") or 90)
                    for p in s.doc:
                        p.set_rotation((p.rotation + deg) % 360)
                elif action == "numbers":
                    s.stamp_pages({"footer": opts.get("text") or "Pagina {n} di {N}", "align": opts.get("align") or "center"})
                elif action == "watermark":
                    s.stamp_pages({"watermark": opts.get("text") or "BOZZA"})
                elif action == "flatten":
                    s.flatten(True, True)
                elif action == "grayscale":
                    s.to_grayscale()
                s.save(out)
            results.append({"name": name, "ok": True, "out": out, "note": note})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e) or e.__class__.__name__})
    return {"results": results, "folder": folder, "ok": sum(1 for r in results if r["ok"]), "total": len(paths)}
