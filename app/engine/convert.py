"""Other files to PDF (Word, Excel, PowerPoint, images, web pages, text) and PDF pages to
images. Office files go through Microsoft Office when it is installed (exact layout), else
LibreOffice, else a simple built-in conversion of their text and tables."""
import html
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

import pymupdf as fitz

IMAGE_EXT = {"jpg", "jpeg", "png", "bmp", "gif", "tif", "tiff", "webp"}
OFFICE_EXT = {"doc", "docx", "rtf", "odt", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp"}
MUPDF_EXT = {"xps", "oxps", "epub", "fb2", "cbz", "mobi", "svg"}
WEB_EXT = {"html", "htm", "xhtml", "mht", "txt", "md"}
ALL_EXT = sorted(IMAGE_EXT | OFFICE_EXT | MUPDF_EXT | WEB_EXT)
A4 = fitz.paper_rect("a4")


def _ext(path):
    return os.path.splitext(path)[1].lower().lstrip(".")


# ---------------------------------------------------------------- images
def images_to_pdf(paths, out_path=None, fit="a4"):
    """One page per image: on A4 (portrait or landscape, as the picture) with a margin,
    or a page as big as the picture."""
    from PIL import Image, ImageOps
    out = fitz.open()
    for p in paths:
        img = Image.open(p)
        frames = getattr(img, "n_frames", 1)
        for k in range(frames):
            img.seek(k)
            im = ImageOps.exif_transpose(img.convert("RGB"))     # phone photos stand up
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            w, h = im.size
            if fit == "a4":
                page = out.new_page(width=A4.width if h >= w else A4.height, height=A4.height if h >= w else A4.width)
                box = page.rect + (24, 24, -24, -24)
            else:
                dpi = (im.info.get("dpi") or (150, 150))[0] or 150
                page = out.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
                box = page.rect
            page.insert_image(box, stream=buf.getvalue(), keep_proportion=True)
    if out_path:
        out.save(out_path, garbage=3, deflate=True)
    return out


# ---------------------------------------------------------------- HTML (web pages, built-in conversions)
CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; color: #111; }
h1 { font-size: 18pt; } h2 { font-size: 15pt; } h3 { font-size: 12.5pt; }
table { border-collapse: collapse; margin: 6pt 0; }
td, th { border: 0.6pt solid #888; padding: 2pt 4pt; font-size: 9pt; vertical-align: top; }
th { background: #eee; } pre { font-family: monospace; font-size: 9pt; white-space: pre-wrap; }
img { max-width: 100%; }
"""


def html_to_pdf(text, out_path=None, base=None):
    """HTML laid out on A4 pages (MuPDF's HTML engine: text, tables, images, simple CSS)."""
    story = fitz.Story(html=text, user_css=CSS, archive=base)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more = 1
    while more:
        dev = writer.begin_page(A4)
        more, _ = story.place(A4 + (40, 46, -40, -46))
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    if out_path:
        doc.save(out_path, garbage=3, deflate=True)
    return doc


def _strip_scripts(text):
    text = re.sub(r"(?is)<(script|style|noscript|iframe|svg)\b.*?</\1>", "", text)
    return re.sub(r"(?is)<link\b[^>]*>", "", text)


def url_to_pdf(url, out_path=None):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) CalamoPDF"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, "replace")
    tmp = tempfile.mkdtemp(prefix="calamo-web-")
    # pictures with an absolute address are fetched; the page keeps text, headings, tables
    base = url.rsplit("/", 1)[0] + "/"

    def fetch(m):
        src = m.group(2)
        full = src if re.match(r"^https?://", src) else urllib.parse.urljoin(base, src)
        try:
            name = f"img{len(os.listdir(tmp))}" + (os.path.splitext(full.split("?")[0])[1][:5] or ".img")
            req2 = urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0 CalamoPDF"})
            with urllib.request.urlopen(req2, timeout=10) as r2, open(os.path.join(tmp, name), "wb") as f:
                f.write(r2.read())
            return f'{m.group(1)}"{name}"'
        except Exception:
            return 'data-x=""'
    text = _strip_scripts(text)
    text = re.sub(r'(<img\b[^>]*?\ssrc=)["\']([^"\']+)["\']', fetch, text, count=40)
    return html_to_pdf(text, out_path, base=tmp)


def text_to_html(text, markdown=False):
    if markdown:
        out = []
        for line in text.splitlines():
            m = re.match(r"^(#{1,3})\s+(.*)", line)
            if m:
                out.append(f"<h{len(m.group(1))}>{html.escape(m.group(2))}</h{len(m.group(1))}>")
            elif line.strip():
                t = html.escape(line)
                t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
                out.append(f"<p>{t}</p>")
        return "\n".join(out)
    return f"<pre>{html.escape(text)}</pre>"


# ---------------------------------------------------------------- Office
def _office_com(path, out_path):
    """Microsoft Office (Word / Excel / PowerPoint) prints the file to PDF: exact layout."""
    if sys.platform != "win32":
        return False
    ext = _ext(path)
    if ext in ("doc", "docx", "rtf", "odt"):
        script = ("$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; "
                  "try { $d = $w.Documents.Open($env:CALAMO_IN, $false, $true); $d.ExportAsFixedFormat($env:CALAMO_OUT, 17); "
                  "$d.Close($false) } finally { $w.Quit() }")
    elif ext in ("xls", "xlsx", "ods", "csv"):
        script = ("$x = New-Object -ComObject Excel.Application; $x.Visible = $false; $x.DisplayAlerts = $false; "
                  "try { $b = $x.Workbooks.Open($env:CALAMO_IN, 0, $true); $b.ExportAsFixedFormat(0, $env:CALAMO_OUT); "
                  "$b.Close($false) } finally { $x.Quit() }")
    elif ext in ("ppt", "pptx", "odp"):
        script = ("$p = New-Object -ComObject PowerPoint.Application; "
                  "try { $s = $p.Presentations.Open($env:CALAMO_IN, $true, $false, $false); $s.SaveAs($env:CALAMO_OUT, 32); "
                  "$s.Close() } finally { $p.Quit() }")
    else:
        return False
    env = dict(os.environ, CALAMO_IN=os.path.abspath(path), CALAMO_OUT=os.path.abspath(out_path))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, timeout=180,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _soffice():
    for p in (shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe", "/usr/bin/soffice", "/usr/bin/libreoffice"):
        if p and os.path.exists(p):
            return p
    return None


def _libreoffice(path, out_path):
    exe = _soffice()
    if not exe:
        return False
    tmp = tempfile.mkdtemp(prefix="calamo-lo-")
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", tmp, path], timeout=180,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        made = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
        if os.path.exists(made):
            shutil.move(made, out_path)
            return True
    except Exception:
        pass
    return False


def docx_to_html(path):
    import docx
    d = docx.Document(path)
    body = d.element.body
    out = []
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            runs = []
            for r in p.runs:
                t = html.escape(r.text)
                if r.bold:
                    t = f"<b>{t}</b>"
                if r.italic:
                    t = f"<i>{t}</i>"
                if r.underline:
                    t = f"<u>{t}</u>"
                runs.append(t)
            text = "".join(runs)
            style = (p.style.name or "").lower() if p.style is not None else ""
            m = re.search(r"(heading|titolo)\s*(\d)", style)
            if m:
                out.append(f"<h{min(int(m.group(2)), 3)}>{text}</h{min(int(m.group(2)), 3)}>")
            elif style.startswith("title"):
                out.append(f"<h1>{text}</h1>")
            elif "list" in style or "elenco" in style:
                out.append(f"<p>• {text}</p>")
            else:
                align = {1: "center", 2: "right", 3: "justify"}.get(int(p.alignment) if p.alignment is not None else 0, "left")
                out.append(f'<p style="text-align:{align}">{text or "&nbsp;"}</p>')
        elif tag == "tbl":
            t = Table(child, d)
            rows = []
            for row in t.rows:
                rows.append("<tr>" + "".join(f"<td>{html.escape(c.text)}</td>" for c in row.cells) + "</tr>")
            out.append("<table>" + "".join(rows) + "</table>")
    return "\n".join(out)


def xlsx_to_html(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [r for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(v is not None and str(v).strip() for v in r)]
        if not rows:
            continue
        width = max(max((k + 1 for k, v in enumerate(r) if v is not None), default=0) for r in rows)
        out.append(f"<h2>{html.escape(ws.title)}</h2><table>")
        for k, r in enumerate(rows):
            cells = "".join(f"<{'th' if k == 0 else 'td'}>{html.escape('' if v is None else _fmt(v))}</{'th' if k == 0 else 'td'}>"
                            for v in list(r)[:width])
            out.append(f"<tr>{cells}</tr>")
        out.append("</table>")
    return "\n".join(out)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v != int(v) else str(int(v))
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    return str(v)


def csv_to_html(path):
    import csv
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except Exception:
            dialect = csv.excel
        rows = list(csv.reader(f, dialect))
    return "<table>" + "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


# ---------------------------------------------------------------- any file
def to_pdf(path, out_path):
    """Convert a file to PDF at out_path. Returns how it was converted."""
    ext = _ext(path)
    if ext == "pdf":
        shutil.copyfile(path, out_path)
        return "pdf"
    if ext in IMAGE_EXT:
        images_to_pdf([path], out_path)
        return "image"
    if ext in MUPDF_EXT:
        d = fitz.open(path)
        fitz.open("pdf", d.convert_to_pdf()).save(out_path, garbage=3, deflate=True)
        return "mupdf"
    if ext in ("html", "htm", "xhtml"):
        with open(path, "rb") as f:
            raw = f.read()
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:2000])
        text = raw.decode(m.group(1).decode() if m else "utf-8", "replace")
        html_to_pdf(_strip_scripts(text), out_path, base=os.path.dirname(os.path.abspath(path)))
        return "html"
    if ext in ("txt", "md"):
        with open(path, encoding="utf-8", errors="replace") as f:
            html_to_pdf(text_to_html(f.read(), ext == "md"), out_path)
        return "text"
    if ext in OFFICE_EXT:
        if _office_com(path, out_path):
            return "office"
        if _libreoffice(path, out_path):
            return "libreoffice"
        if ext == "docx":
            html_to_pdf(docx_to_html(path), out_path)
            return "builtin"
        if ext == "xlsx":
            html_to_pdf(xlsx_to_html(path), out_path)
            return "builtin"
        if ext == "csv":
            html_to_pdf(csv_to_html(path), out_path)
            return "builtin"
        raise RuntimeError(f"Per convertire i file .{ext} serve Microsoft Office o LibreOffice installato.")
    raise RuntimeError(f"Non so convertire i file .{ext} in PDF.")


def files_to_pdf(paths, out_path):
    """Several files (also mixed: images, Word, pages...) in one PDF, in order."""
    if all(_ext(p) in IMAGE_EXT for p in paths):
        images_to_pdf(paths, out_path)
        return {"path": out_path, "how": ["image"] * len(paths)}
    out, how = fitz.open(), []
    tmp = tempfile.mkdtemp(prefix="calamo-conv-")
    for k, p in enumerate(paths):
        one = os.path.join(tmp, f"{k}.pdf")
        how.append(to_pdf(p, one))
        out.insert_pdf(fitz.open(one))
    out.save(out_path, garbage=3, deflate=True)
    return {"path": out_path, "how": how}


# ---------------------------------------------------------------- PDF to images
def pdf_to_images(doc, pages, folder, base, fmt="png", dpi=150):
    fmt = "jpeg" if fmt in ("jpg", "jpeg") else "png"
    paths = []
    width = len(str(max(pages) + 1)) if pages else 1
    for p in pages:
        pix = doc[p].get_pixmap(dpi=int(dpi), alpha=False)
        path = os.path.join(folder, f"{base} - pagina {str(p + 1).zfill(width)}.{'jpg' if fmt == 'jpeg' else 'png'}")
        if fmt == "jpeg":
            pix.save(path, jpg_quality=90)
        else:
            pix.save(path)
        paths.append(path)
    return {"files": paths, "folder": folder}
