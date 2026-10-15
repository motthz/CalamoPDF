"""Font resolution: reuse the PDF's embedded fonts when they can render the new
text, otherwise fall back to the closest installed Windows font."""
import os
import re

import pymupdf as fitz

WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# family keyword -> (regular, bold, italic, bold-italic, css family)
FAMILIES = {
    "times": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf", "Times New Roman"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf", "Arial"),
    "helvetica": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf", "Arial"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf", "Calibri"),
    "cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf", "Cambria"),
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf", "Georgia"),
    "verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf", "Verdana"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf", "Tahoma"),
    "trebuchet": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf", "Trebuchet MS"),
    "courier": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf", "Courier New"),
    "consolas": ("consola.ttf", "consolab.ttf", "consolai.ttf", "consolaz.ttf", "Consolas"),
    "segoe": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf", "Segoe UI"),
    "garamond": ("GARA.TTF", "GARABD.TTF", "GARAIT.TTF", "GARABD.TTF", "Garamond"),
    "bookantiqua": ("BKANT.TTF", "ANTQUAB.TTF", "ANTQUAI.TTF", "ANTQUABI.TTF", "Book Antiqua"),
    "century": ("GOTHIC.TTF", "GOTHICB.TTF", "GOTHICI.TTF", "GOTHICBI.TTF", "Century Gothic"),
    "palatino": ("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf", "Palatino Linotype"),
    "candara": ("Candara.ttf", "Candarab.ttf", "Candarai.ttf", "Candaraz.ttf", "Candara"),
    "constantia": ("constan.ttf", "constanb.ttf", "constani.ttf", "constanz.ttf", "Constantia"),
    "corbel": ("corbel.ttf", "corbelb.ttf", "corbeli.ttf", "corbelz.ttf", "Corbel"),
}
# Names offered in the UI font picker
UI_FAMILIES = ["Originale", "Arial", "Times New Roman", "Calibri", "Cambria", "Georgia",
               "Verdana", "Segoe UI", "Courier New", "Garamond", "Palatino Linotype",
               "Century Gothic", "Ink Free", "Segoe Print", "Segoe Script"]
SCRIPT_FONTS = {"Ink Free": "Inkfree.ttf", "Segoe Print": ("segoepr.ttf", "segoeprb.ttf"),
                "Segoe Script": ("segoesc.ttf", "segoescb.ttf")}

_font_cache = {}


def clean_name(name):
    name = re.sub(r"^[A-Z]{6}\+", "", name or "")
    return name


def style_from(name, flags):
    low = name.lower()
    bold = bool(flags & 16) or "bold" in low or "black" in low or "heavy" in low or "semibold" in low
    italic = bool(flags & 2) or "italic" in low or "oblique" in low
    serif = bool(flags & 4)
    mono = bool(flags & 8)
    return bold, italic, serif, mono


def family_key(name, serif=False, mono=False):
    low = re.sub(r"[^a-z]", "", clean_name(name).lower())
    for key in FAMILIES:
        if key in low:
            return key
    if "roman" in low or "serif" in low and "sans" not in low:
        return "times"
    if mono or "mono" in low or "code" in low:
        return "courier"
    if serif or "minion" in low or "baskerville" in low or "book" in low:
        return "times"
    return "arial"


def css_family(name, flags):
    if name in SCRIPT_FONTS or name in [f[4] for f in FAMILIES.values()]:
        return name
    bold, italic, serif, mono = style_from(clean_name(name), flags)
    return FAMILIES[family_key(name, serif, mono)][4]


def _load(path):
    if path not in _font_cache:
        _font_cache[path] = fitz.Font(fontfile=path)
    return _font_cache[path]


def system_font(family_or_name, bold=False, italic=False, serif=False, mono=False):
    """Return (fitz.Font, css family) for a UI family name or a PDF font name."""
    if family_or_name in SCRIPT_FONTS:
        files = SCRIPT_FONTS[family_or_name]
        if isinstance(files, tuple):
            files = files[1] if bold else files[0]
        path = os.path.join(WIN_FONTS, files)
        if os.path.exists(path):
            return _load(path), family_or_name
    key = None
    for k, v in FAMILIES.items():
        if v[4] == family_or_name:
            key = k
    if key is None:
        key = family_key(family_or_name, serif, mono)
    files = FAMILIES[key]
    idx = (1 if bold else 0) + (2 if italic else 0)
    for candidate in (files[idx], files[0]):
        path = os.path.join(WIN_FONTS, candidate)
        if os.path.exists(path):
            return _load(path), files[4]
    # last resort: built-in Base14
    base = {"times": "tiro", "courier": "cour"}.get(key, "helv")
    if bold:
        base = {"tiro": "tibo", "cour": "cobo", "helv": "hebo"}[base]
    return fitz.Font(base), files[4]


class FontResolver:
    """Per-document font resolver with embedded-font reuse."""

    def __init__(self, doc):
        self.doc = doc
        self._embedded = {}

    def embedded(self, page, fontname):
        key = clean_name(fontname)
        if key in self._embedded:
            return self._embedded[key]
        font = None
        try:
            for f in page.get_fonts(full=True):
                xref, ext, basefont = f[0], f[1], clean_name(f[3])
                # a CID-keyed CFF font has no table from characters to glyphs: writing with
                # it would put the wrong letters in the text (the glyphs look right, the
                # text copied or searched is garbage)
                if basefont == key and ext not in ("n/a", "", "cid"):
                    _, _, _, buf = self.doc.extract_font(xref)
                    if buf:
                        font = fitz.Font(fontbuffer=buf)
                        break
        except Exception:
            font = None
        self._embedded[key] = font
        return font

    def resolve(self, page, fontname, flags, text, family_override=None, bold=None, italic=None):
        b, i, serif, mono = style_from(clean_name(fontname), flags)
        if bold is not None:
            b = bold
        if italic is not None:
            i = italic
        if family_override and family_override != "Originale":
            return system_font(family_override, b, i)[0]
        overridden_style = (bold is not None and bold != style_from(clean_name(fontname), flags)[0]) or \
                           (italic is not None and italic != style_from(clean_name(fontname), flags)[1])
        if not overridden_style:
            font = self.embedded(page, fontname)
            if font is not None:
                try:
                    if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                        return font
                except Exception:
                    pass
        return system_font(fontname, b, i, serif, mono)[0]
