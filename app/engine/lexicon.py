"""Italian dictionary and OCR-aware spelling correction.

Handwriting OCR makes typical mistakes ("Ottinoo" for "Ottimo", "effro" for "entro",
"teano" for "team"): a word that is not Italian is replaced with the closest word of
the dictionary, where the distance counts letters that look alike (m/rn, n/u, t/f...)
as small mistakes. Words already present elsewhere in the document are preferred."""
import os
import re
import threading

import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "it_words.txt")
WORD_RE = re.compile(r"^([^\wàèéìíòóùú]*)([\wàèéìíòóùú']+?)([^\wàèéìíòóùú]*)$", re.I)

# letters handwriting OCR confuses: substituting them is a small mistake
SIMILAR = {frozenset(p) for p in ("ao", "ec", "nu", "il", "tf", "rn", "mn", "hb", "uv", "ae", "oe", "ij",
                                  "ce", "ft", "lt", "oc", "rv", "sz", "ag", "qg", "yg", "nh", "kh", "ti", "ri")}
# groups of letters read in place of others
MERGES = {("rn", "m"), ("nn", "m"), ("in", "m"), ("ni", "m"), ("ii", "u"), ("cl", "d"), ("li", "h"),
          ("vv", "w"), ("ri", "n"), ("lc", "k"), ("nt", "ff"), ("ff", "nt"), ("tt", "ff"), ("m", "rn"),
          ("m", "nn"), ("u", "ii"), ("d", "cl"), ("h", "li"), ("n", "ri"), ("no", "m"), ("on", "m")}


class Lexicon:
    def __init__(self, path=DATA):
        self._path = path
        self._lock = threading.Lock()
        self.freq = None

    def _load(self):
        with self._lock:
            if self.freq is not None:
                return
            freq, words = {}, []
            try:
                with open(self._path, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        w, n = line.split()
                        freq[w] = int(n)
                        words.append(w)
            except OSError:
                pass
            self.words = words
            self.lens = np.array([len(w) for w in words], dtype=np.int16)
            index = {}
            for i, w in enumerate(words):
                for g in self._grams(w):
                    index.setdefault(g, []).append(i)
            self.index = {g: np.array(v, dtype=np.int32) for g, v in index.items()}
            self.ngrams = np.array([len(self._grams(w)) for w in words], dtype=np.int16)
            # letter trigrams of Italian, to tell a plausible word from OCR garbage
            tri = {}
            for w in words:
                x = "^^" + w + "$"
                for i in range(len(x) - 2):
                    tri[x[i:i + 3]] = tri.get(x[i:i + 3], 0) + 1
            bi = {}
            for t, n in tri.items():
                bi[t[:2]] = bi.get(t[:2], 0) + n
            self.tri, self.bi = tri, bi
            self.freq = freq

    @staticmethod
    def _grams(w):
        w = "^" + w + "$"
        return {w[i:i + 2] for i in range(len(w) - 1)}

    def plausibility(self, word):
        """Lowest letter-trigram probability of the word (log10): Italian-looking words
        stay above about -2.2, OCR garbage ("Consecya", "Firnoato") falls well below."""
        self._load()
        x = "^^" + word.lower() + "$"
        worst = 0.0
        for i in range(len(x) - 2):
            t = x[i:i + 3]
            p = (self.tri.get(t, 0) + 0.1) / (self.bi.get(t[:2], 0) + 3.0)
            worst = min(worst, float(np.log10(p)))
        return worst

    def known(self, word):
        self._load()
        w = word.lower()
        return w in self.freq or not w.isalpha()

    def ratio(self, text):
        """Share of the words of a text that are Italian words (0..1)."""
        toks = [t for t in (self.core(x) for x in text.split()) if t and t.isalpha() and len(t) > 1]
        if not toks:
            return 0.0
        return sum(1 for t in toks if self.known(t)) / len(toks)

    @staticmethod
    def core(token):
        m = WORD_RE.match(token)
        return m.group(2) if m else ""

    # ------------------------------------------------------------------ correction
    @staticmethod
    def distance(a, b):
        """Edit distance where OCR look-alikes cost less."""
        n, m = len(a), len(b)
        d = np.zeros((n + 1, m + 1))
        d[:, 0] = np.arange(n + 1)
        d[0, :] = np.arange(m + 1)
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                x, y = a[i - 1], b[j - 1]
                sub = 0.0 if x == y else (0.4 if frozenset((x, y)) in SIMILAR else 1.0)
                best = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + sub)
                for p, q in MERGES:                      # "rn" read for "m", "ff" for "nt"...
                    if i >= len(p) and j >= len(q) and a[i - len(p):i] == p and b[j - len(q):j] == q:
                        best = min(best, d[i - len(p), j - len(q)] + 0.4)
                d[i, j] = best
        return float(d[n, m])

    def candidates(self, word, k=40):
        self._load()
        grams = self._grams(word)
        hits = [self.index[g] for g in grams if g in self.index]
        if not hits:
            return []
        counts = np.bincount(np.concatenate(hits), minlength=len(self.words))
        L = len(word)
        ok = np.abs(self.lens - L) <= 2
        dice = np.where(ok, 2.0 * counts / (self.ngrams + len(grams)), 0)
        top = np.argpartition(-dice, min(k, len(dice) - 1))[:k]
        return [self.words[i] for i in top if dice[i] > 0.25]

    def correct(self, word, prefer=None, name=False):
        """The Italian word closest to an OCR reading, or None if the word is fine or
        nothing is close enough. prefer: words of the document (Counter-like).
        name: a capitalised word inside a sentence, probably a proper name."""
        self._load()
        w = word.lower()
        if len(w) < 3 or not w.isalpha() or w in self.freq or not self.freq:
            return None
        best, best_score = None, None
        # a word that reads like Italian may simply be missing from the list: only fix it
        # when a dictionary word is very close (one look-alike letter or so)
        plausible = self.plausibility(w) > -2.3
        limit = 0.9 if plausible else max(1.0, 0.3 * len(w))
        if name:
            limit = min(limit, 1.0)
        # a capitalised word may be a name: never turn it into a word with another initial
        name = word[:1].isupper()
        for c in self.candidates(w):
            if name and c[0] != w[0]:
                continue
            dist = self.distance(w, c)
            if dist > limit:
                continue
            # closer first; among equals the more common word, and words of this document
            bonus = 0.6 if prefer and c in prefer else 0.0
            score = dist - bonus - min(np.log10(self.freq.get(c, 1)), 7) * 0.05
            if best_score is None or score < best_score:
                best, best_score = c, score
        if best is None:
            return None
        if word[:1].isupper():
            best = best[:1].upper() + best[1:]
        if word.isupper() and len(word) > 1:
            best = best.upper()
        return best

    def correct_text(self, text, prefer=None):
        """Corrects every word that is not Italian. Returns (text, n_corrected)."""
        out, n = [], 0
        start = True
        for tok in text.split(" "):
            m = WORD_RE.match(tok)
            if not m:
                out.append(tok)
                start = start or tok[-1:] in ".!?:"
                continue
            pre, core, post = m.groups()
            name = not start and core[:1].isupper() and not core.isupper()
            start = post[-1:] in (".", "!", "?", ":")
            fixed = self.correct(core, prefer, name)
            if fixed and fixed.lower() != core.lower():
                out.append(pre + fixed + post)
                n += 1
            else:
                out.append(tok)
        return " ".join(out), n


LEXICON = Lexicon()
