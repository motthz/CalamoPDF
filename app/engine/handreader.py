"""Handwriting recognition with a dedicated model, run locally (offline after the first
download): TrOCR "small handwritten" (Microsoft, MIT licence) in ONNX format.

Windows OCR is made for print and reads handwriting poorly; TrOCR reads a whole line
of handwriting at a time. The model (about 60 MB) is downloaded once, on request, into
%APPDATA%\\CalamoPDF\\modelli; without it the app keeps working with Windows OCR only."""
import json
import os
import threading
import types
import urllib.request

import numpy as np

from .storage import path as data_path

REPO = "Xenova/trocr-small-handwritten"
MIRRORS = [f"https://huggingface.co/{REPO}/resolve/main/", f"https://hf-mirror.com/{REPO}/resolve/main/",
           f"https://www.modelscope.cn/models/{REPO}/resolve/master/"]
# (local name, candidates in the repository: the smaller quantized ones first)
FILES = [("config.json", ["config.json"]),
         ("generation_config.json", ["generation_config.json"]),
         ("preprocessor_config.json", ["preprocessor_config.json"]),
         ("tokenizer.json", ["tokenizer.json"]),
         ("encoder.onnx", ["onnx/encoder_model_quantized.onnx", "onnx/encoder_model.onnx"]),
         ("decoder.onnx", ["onnx/decoder_model_quantized.onnx", "onnx/decoder_model.onnx",
                           "onnx/decoder_model_merged_quantized.onnx", "onnx/decoder_model_merged.onnx"])]
UA = "CalamoPDF-handreader"
# the fast encoder (tools/make_fast_model.py), built and published by this project once:
# downloaded after the files above (or in the background by an older installation); a
# reading without it uses encoder.onnx, as before
FAST = ("encoder_fast.onnx", ["https://github.com/motthz/CalamoPDF/releases/download/modello-veloce-1/encoder_fast.onnx"])


def model_dir():
    d = os.environ.get("CALAMO_MODEL_DIR") or data_path(os.path.join("modelli", "trocr-small-handwritten"))
    os.makedirs(d, exist_ok=True)
    return d


class HandReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._enc = self._dec = self._tok = None
        self._lanes = []
        self.error = None
        self.use_fast = True             # the fast encoder when downloaded (tests compare both)
        self._seen = {}                  # line picture -> reading, the most recent 4000
        self._aspects = {}               # line picture -> shape of its cleaned ink
        self._mem = threading.Lock()

    # ------------------------------------------------------------------ installation
    def installed(self):
        d = model_dir()
        return all(os.path.exists(os.path.join(d, name)) for name, _ in FILES)

    def runtime_ok(self):
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
            return True
        except Exception:
            return False

    def status(self):
        return {"installed": self.installed(), "runtime": self.runtime_ok(), "error": self.error}

    def remove(self):
        """Deletes the downloaded files (to download them again after an error)."""
        with self._lock:
            self._enc = self._dec = self._tok = None
            self._lanes = []
            self.error = None
            d = model_dir()
            for name, _ in FILES + [FAST, ("scelta-codificatore.json", [])]:
                for f in (name, name + ".part", name + ".new"):
                    try:
                        os.remove(os.path.join(d, f))
                    except OSError:
                        pass

    def fast_installed(self):
        return os.path.exists(os.path.join(model_dir(), FAST[0]))

    def prepare(self):
        """In the background at start: the fast encoder downloaded if missing, and measured
        against the other one on this computer, so the first reading does not wait for it."""
        self.download_fast()
        import onnxruntime as ort
        self._fast_wins(ort, model_dir())

    def download_fast(self, progress=None):
        """Fetch the fast encoder and check it opens; a reading in progress keeps the one it
        has, the next ones use the new one. Returns True when it is there."""
        if self.fast_installed():
            return True
        target = os.path.join(model_dir(), FAST[0])
        last = None
        for url in FAST[1]:
            try:
                self._fetch(url, target + ".new", lambda f: progress and progress(f))
                import onnxruntime as ort
                sess = ort.InferenceSession(target + ".new", providers=["CPUExecutionProvider"])
                if [i.name for i in sess.get_inputs()][:1] != ["pixel_values"]:
                    raise RuntimeError("file non valido")
                del sess
                os.replace(target + ".new", target)
                last = None
                break
            except Exception as e:
                last = e
                try:
                    os.remove(target + ".new")
                except OSError:
                    pass
        if last is not None:
            raise RuntimeError(f"Download del lettore veloce non riuscito: {last}")
        with self._lock:                        # the next reading opens the fast encoder
            if self._lanes and not any(l.lock.locked() for l in self._lanes):
                self._enc = None
                self._lanes = []
        return True

    def download(self, progress=None):
        """Fetch the model files (resumable per file). progress(fraction)."""
        d = model_dir()
        todo = [(name, cands) for name, cands in FILES if not os.path.exists(os.path.join(d, name))]
        for k, (name, cands) in enumerate(todo):
            last = None
            for base in MIRRORS:
                for cand in cands:
                    try:
                        self._fetch(base + cand, os.path.join(d, name),
                                    lambda f: progress and progress((k + f) / len(todo)))
                        last = None
                        break
                    except Exception as e:     # try the next candidate / mirror
                        last = e
                if last is None:
                    break
            if last is not None:
                raise RuntimeError(f"Download del modello non riuscito ({name}): {last}")
        try:
            self.download_fast()
        except Exception:
            pass                               # the reading works without it, only slower
        if progress:
            progress(1.0)
        return True

    @staticmethod
    def _fetch(url, target, progress):
        part = target + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)
        if os.path.getsize(part) < 64:
            os.remove(part)
            raise RuntimeError("file vuoto")
        os.replace(part, target)

    # ------------------------------------------------------------------ inference
    def _load(self):
        if self.error:
            raise RuntimeError(self.error)
        try:
            self._open()
        except Exception as e:                  # a broken download: don't retry on every line
            self.error = f"Lettore non disponibile: {e}"
            self._enc = None
            raise

    def _open(self):
        with self._lock:
            if self._enc is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer
            d = model_dir()
            # the fast encoder when it is there (see FAST) and when it is really faster on
            # this computer: on some processors the encoder of the first download already is
            fast = self.use_fast and os.path.exists(os.path.join(d, FAST[0])) and self._fast_wins(ort, d)
            enc = FAST[0] if fast else "encoder.onnx"
            # two readers side by side, with half of the cores each, read more lines in the
            # same time than one with all of them (the model does not use many cores well)
            cores = max(1, os.cpu_count() or 2)
            lanes = max(1, min(4, cores // 2))      # 2 on 4 cores, 4 on 8 or more
            self._lanes = []
            for _ in range(lanes):
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = max(1, cores // lanes)
                opts.inter_op_num_threads = 1
                prov = ["CPUExecutionProvider"]
                self._lanes.append(types.SimpleNamespace(
                    lock=threading.Lock(),
                    enc=ort.InferenceSession(os.path.join(d, enc), opts, providers=prov),
                    dec=ort.InferenceSession(os.path.join(d, "decoder.onnx"), opts, providers=prov)))
            self._enc, self._dec = self._lanes[0].enc, self._lanes[0].dec
            self._tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
            cfg = {}
            for name in ("config.json", "generation_config.json"):
                try:
                    with open(os.path.join(d, name), encoding="utf-8") as f:
                        c = json.load(f)
                    cfg.update(c.get("decoder", {}) if isinstance(c.get("decoder"), dict) else {})
                    cfg.update({k: v for k, v in c.items() if not isinstance(v, dict)})
                except Exception:
                    pass
            self._start = int(cfg.get("decoder_start_token_id", 2))
            self._eos = int(cfg.get("eos_token_id", 2))
            try:
                with open(os.path.join(d, "preprocessor_config.json"), encoding="utf-8") as f:
                    pc = json.load(f)
            except Exception:
                pc = {}
            size = pc.get("size", 384)
            self._size = (size.get("height", 384), size.get("width", 384)) if isinstance(size, dict) else (size, size)
            self._mean = np.array(pc.get("image_mean", [0.5, 0.5, 0.5]), np.float32)
            self._std = np.array(pc.get("image_std", [0.5, 0.5, 0.5]), np.float32)
            self._dec_inputs = {i.name: i for i in self._dec.get_inputs()}
            self._logits = [self._dec.get_outputs()[0].name]
            self._enc_in = self._enc.get_inputs()[0].name

    @staticmethod
    def _fast_wins(ort, d):
        """Is the fast encoder faster than the one of the first download on this computer?
        Measured once (a second or two) and remembered with the processor and the version
        of onnxruntime; a new processor or onnxruntime measures again."""
        import platform
        what = f"{platform.processor()}|{os.cpu_count()}|{ort.__version__}"
        memo = os.path.join(d, "scelta-codificatore.json")
        try:
            with open(memo, encoding="utf-8") as f:
                m = json.load(f)
            if m.get("what") == what:
                return bool(m.get("fast"))
        except Exception:
            pass
        times = {}
        try:
            x = np.random.default_rng(0).random((1, 3, 384, 384), np.float32)
            import time
            for name in ("encoder.onnx", FAST[0]):
                o = ort.SessionOptions()
                o.intra_op_num_threads = max(1, os.cpu_count() or 2)
                s = ort.InferenceSession(os.path.join(d, name), o, providers=["CPUExecutionProvider"])
                for _ in range(2):
                    s.run(None, {"pixel_values": x})
                t = time.perf_counter()
                for _ in range(5):
                    s.run(None, {"pixel_values": x})
                times[name] = time.perf_counter() - t
            fast = times[FAST[0]] < times["encoder.onnx"] * 0.95       # clearly faster, or not worth it
        except Exception:
            return False
        try:
            with open(memo, "w", encoding="utf-8") as f:
                json.dump({"what": what, "fast": fast, "seconds": times}, f)
        except OSError:
            pass
        return fast

    @staticmethod
    def clean(rgb):
        """The model was trained on single lines of dark ink on white paper: whatever the
        pen colour and the paper tone, the line becomes black ink on white, with full
        contrast; table borders and pieces of the lines above and below are removed."""
        from PIL import Image
        a = rgb[:, :, :3].astype(np.float32)
        gray = a.min(axis=2) * 0.5 + a.mean(axis=2) * 0.5      # coloured ink counts as dark
        # the paper's own brightness everywhere (shadows, creases, grain): divide it out
        H0, W0 = gray.shape
        k = max(min(H0, W0) // 3, 4)
        hb, wb = max(H0 // k, 1), max(W0 // k, 1)
        small = gray[:hb * k, :wb * k].reshape(hb, k, wb, k).max(axis=(1, 3)) if H0 >= k and W0 >= k else None
        if small is not None:
            bg = np.asarray(Image.fromarray(small.astype(np.uint8)).resize((W0, H0), Image.BILINEAR), np.float32)
            gray = np.clip(gray / np.maximum(bg, 1) * 255, 0, 255)
        paper = float(np.percentile(gray, 90))
        ink = float(np.percentile(gray, 1))
        if paper - ink < 20:
            return np.full(a.shape[:2] + (3,), 255, np.uint8)
        x = np.clip((gray - ink) / (paper - ink), 0, 1)
        x = np.where(x > 0.85, 1.0, x)                           # paper grain and shading go white
        dark = x < 0.5
        H, W = dark.shape
        # borders: columns dark almost top to bottom, rows dark across most of the width
        from .writers import rules
        cols = dark.mean(axis=0) > 0.9            # a border crosses the whole line; an "l" does not
        rows = dark.mean(axis=1) > 0.5
        line = rules(dark, max(H // 10, 2), chain=9, fill=0.75) | cols[None, :] | rows[:, None]
        for k in (1, 2):                                        # their anti-aliased edges too
            line[:, k:] |= line[:, :-k] & cols[None, :-k]
            line[:, :-k] |= line[:, k:] & cols[None, k:]
        x[line] = 1.0
        dark &= ~line
        # keep the band of text in the middle: what the crop caught of the lines around goes
        prof = dark.sum(axis=1)
        if prof.any():
            on = prof > max(prof.max() * 0.04, 0)
            bands, start = [], None
            for i, v in enumerate(on):
                if v and start is None:
                    start = i
                elif not v and start is not None:
                    bands.append((start, i))
                    start = None
            if start is not None:
                bands.append((start, H))
            if len(bands) > 1:
                main = max(bands, key=lambda b: prof[b[0]:b[1]].sum())
                keep = np.zeros(H, bool)
                keep[main[0]:main[1]] = True
                for b in bands:     # accents and dots just above, tails just below, stay
                    if b != main and min(abs(b[1] - main[0]), abs(b[0] - main[1])) < (main[1] - main[0]) * 0.35 \
                            and prof[b[0]:b[1]].sum() < prof[main[0]:main[1]].sum() * 0.25:
                        keep[b[0]:b[1]] = True
                x[~keep] = 1.0
                dark[~keep] = False
        # crop to the ink, with a margin like the training lines
        ys, xs = np.nonzero(dark)
        if len(xs):
            m = max(int((ys.max() - ys.min()) * 0.15), 3)
            x = x[max(ys.min() - m, 0):ys.max() + m + 1, max(xs.min() - m, 0):xs.max() + m + 1]
        g = (x * 255).astype(np.uint8)
        return np.repeat(g[:, :, None], 3, axis=2)

    def aspect(self, rgb):
        """Width / height of the ink of a line, as the reader saw it (cleaned)."""
        rgb = np.ascontiguousarray(rgb)
        a = self._aspects.get(self._key(rgb, 0))
        if a is None:
            c = self.clean(rgb)
            a = c.shape[1] / max(c.shape[0], 1)
        return a

    def _pixels(self, rgb):
        from PIL import Image
        c = self.clean(rgb)
        with self._mem:
            self._aspects[self._key(rgb, 0)] = c.shape[1] / max(c.shape[0], 1)
            while len(self._aspects) > 4000:
                self._aspects.pop(next(iter(self._aspects)))
        img = Image.fromarray(c)
        img = img.resize((self._size[1], self._size[0]), Image.BILINEAR)
        x = (np.asarray(img, np.float32) / 255.0 - self._mean) / self._std
        return x.transpose(2, 0, 1)[None]

    @staticmethod
    def _key(rgb, max_tokens):
        import hashlib
        return hashlib.blake2b(rgb.tobytes(), digest_size=16).hexdigest() + str(rgb.shape) + str(max_tokens)

    def _remember(self, key, value):
        with self._mem:                         # several readers write at the same time
            self._seen[key] = value
            while len(self._seen) > 4000:
                self._seen.pop(next(iter(self._seen)))

    def read(self, rgb, max_tokens=48):
        """Reads one line image (H x W x 3, uint8). Returns (text, confidence 0..1).
        A line already read (the page read again, a writing marked again, a line read ahead
        by read_ahead) is not read twice."""
        rgb = np.ascontiguousarray(rgb)
        key = self._key(rgb, max_tokens)
        hit = self._seen.get(key)
        if hit is None:
            self._load()
            hit = self._read(rgb, max_tokens, self._free_lane())
            self._remember(key, hit)
        return hit

    def _free_lane(self):
        for lane in self._lanes:
            if not lane.lock.locked():
                return lane
        return self._lanes[0]

    def read_ahead(self, crops, max_tokens=48, cancelled=None):
        """Read many lines at once, each reader taking the next one as soon as it is free;
        the results wait in memory for read(). Returns how many were read."""
        self._load()
        todo, keys = [], set()
        for c in crops:
            c = np.ascontiguousarray(c)
            k = self._key(c, max_tokens)
            if k not in self._seen and k not in keys and c.size:
                keys.add(k)
                todo.append((k, c))
        if not todo:
            return 0
        queue = list(reversed(todo))
        guard = threading.Lock()

        def work(lane):
            while True:
                if cancelled and cancelled():
                    return
                with guard:
                    if not queue:
                        return
                    k, c = queue.pop()
                try:
                    self._remember(k, self._read(c, max_tokens, lane))
                except Exception:
                    return
        threads = [threading.Thread(target=work, args=(lane,), daemon=True) for lane in self._lanes[1:]]
        for t in threads:
            t.start()
        work(self._lanes[0])
        for t in threads:
            t.join()
        return len(todo)

    def _read(self, rgb, max_tokens, lane=None):
        self._load()
        lane = lane or self._lanes[0]
        px = self._pixels(rgb)
        with lane.lock:
            hidden = lane.enc.run(None, {self._enc_in: px})[0]
            return self._decode(lane, hidden, max_tokens)

    def _decode(self, lane, hidden, max_tokens):
        ids, logps = [self._start], []
        for _ in range(max_tokens):
            feeds = {}
            for name, inp in self._dec_inputs.items():
                if name == "input_ids":
                    feeds[name] = np.array([ids], dtype=np.int64)
                elif name == "encoder_hidden_states":
                    feeds[name] = hidden
                elif name == "encoder_attention_mask":
                    feeds[name] = np.ones(hidden.shape[:2], dtype=np.int64)
                elif name == "use_cache_branch":
                    feeds[name] = np.array([False])
                elif name.startswith("past_key_values"):
                    shape = [1 if not isinstance(d, int) else d for d in inp.shape]
                    shape[2] = 0 if ".decoder." in name else hidden.shape[1]
                    feeds[name] = np.zeros(shape, dtype=np.float32)
            logits = lane.dec.run(self._logits, feeds)[0][0, -1]     # only the logits are used
            logits = logits - logits.max()
            p = np.exp(logits)
            p /= p.sum()
            nxt = int(np.argmax(p))
            if nxt == self._eos:
                break
            ids.append(nxt)
            logps.append(float(np.log(p[nxt] + 1e-9)))
        text = self._tok.decode(ids[1:], skip_special_tokens=True).strip()
        conf = float(np.exp(np.mean(logps))) if logps else 0.0
        return text, conf


READER = HandReader()
