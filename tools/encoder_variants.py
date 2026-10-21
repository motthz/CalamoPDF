"""Which form of the handwriting model is fastest on this computer, and does it still read
the same? Builds the encoder and the decoder in a few forms (full precision, fused, 8-bit
weights signed or unsigned, per channel or per tensor), times each one the way the app
uses it (cores/2 readers side by side) and reads synthetic lines with it. Changes nothing.
   python tools/encoder_variants.py WORK_DIR   (needs: onnxruntime, onnx, sympy)"""
import os
import platform
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import numpy as np  # noqa: E402
import onnx  # noqa: E402
import onnxruntime as ort  # noqa: E402
from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: E402
from onnxruntime.transformers import optimizer  # noqa: E402
from engine.handreader import READER, model_dir  # noqa: E402
from engine.quality import cer  # noqa: E402
from make_fast_model import fetch  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

TEXTS = ["Consegna entro venerdi", "Firmato Giulia Rossi", "Ottimo lavoro del team", "Visto e approvato",
         "Marco Bianchi", "Riunione del 12 maggio", "Chiamare il fornitore domani", "Pagato in contanti",
         "Analisi dei requisiti", "Revisione del codice", "Rinviato a giugno", "Preparare il preventivo"]


def lines():
    win = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    fonts = [os.path.join(win, f) for f in ("Inkfree.ttf", "segoesc.ttf", "segoepr.ttf")]
    fonts = [f for f in fonts if os.path.exists(f)] or [None]
    out = []
    for t in TEXTS:
        for f in fonts:
            for size in (48, 58):
                font = ImageFont.truetype(f, size) if f else ImageFont.load_default()
                l, tp, r, b = font.getbbox(t)
                img = Image.new("RGB", (r - l + 40, b - tp + 30), (247, 246, 241))
                ImageDraw.Draw(img).text((20 - l, 15 - tp), t, font=font, fill=(25, 40, 140))
                out.append((t, np.asarray(img)))
    return out


def build(work):
    enc = fetch("encoder_model.onnx", work)
    dec = fetch("decoder_model.onnx", work)
    fused = os.path.join(work, "src-encoder-fused.onnx")
    if not os.path.exists(fused):
        optimizer.optimize_model(enc, model_type="vit", num_heads=6, hidden_size=384,
                                 opt_level=0).save_model_to_file(fused)
    d = model_dir()
    encs = {"fp32": enc, "1.6 (Xenova 8 bit)": os.path.join(d, "encoder.onnx"), "fuso fp32": fused}
    decs = {"fp32": dec, "1.6 (Xenova 8 bit)": os.path.join(d, "decoder.onnx")}
    forms = {"s8 canale": (QuantType.QInt8, True, False), "u8 canale": (QuantType.QUInt8, True, False),
             "s8 tensore": (QuantType.QInt8, False, False), "u8 tensore": (QuantType.QUInt8, False, False),
             "s8 canale rr": (QuantType.QInt8, True, True)}
    for name, (wt, pc, rr) in forms.items():
        for src, label, into in ((fused, "fuso " + name, encs), (dec, name, decs)):
            out = os.path.join(work, label.replace(" ", "_") + ("-enc" if into is encs else "-dec") + ".onnx")
            if not os.path.exists(out):
                quantize_dynamic(src, out, weight_type=wt, per_channel=pc, reduce_range=rr,
                                 extra_options={"DefaultTensorType": onnx.TensorProto.FLOAT})
            into[label] = out
    return encs, decs


def sessions(path, lanes, threads):
    out = []
    for _ in range(lanes):
        o = ort.SessionOptions()
        o.intra_op_num_threads = threads
        o.inter_op_num_threads = 1
        out.append(ort.InferenceSession(path, o, providers=["CPUExecutionProvider"]))
    return out


def side_by_side(sess, items, job):
    """Runs job(session, item) over all items, one reader per session; returns
    (seconds per item, results in order)."""
    queue = list(reversed(range(len(items))))
    res = [None] * len(items)
    guard = threading.Lock()

    def work(s):
        while True:
            with guard:
                if not queue:
                    return
                i = queue.pop()
            res[i] = job(s, items[i])
    t = time.perf_counter()
    ths = [threading.Thread(target=work, args=(s,)) for s in sess[1:]]
    for th in ths:
        th.start()
    work(sess[0])
    for th in ths:
        th.join()
    return (time.perf_counter() - t) / len(items), res


def main(work):
    os.makedirs(work, exist_ok=True)
    cores = os.cpu_count() or 2
    lanes = max(1, min(4, cores // 2))
    threads = max(1, cores // lanes)
    print(f"{platform.processor()} | {cores} core | onnxruntime {ort.__version__} | {lanes} lettori x {threads} thread",
          flush=True)
    if not READER.installed():
        READER.download()
    READER._load()
    encs, decs = build(work)
    data = lines()
    px = [READER._pixels(im) for _, im in data]
    truth = [t for t, _ in data]
    ref_dec = READER._lanes[0].dec

    def decode(dec_sess, h):
        return READER._decode(types.SimpleNamespace(dec=dec_sess), h, 48)[0]

    print("\ncodificatore (immagine)       ms/riga   errore (CER)   righe uguali a fp32", flush=True)
    hidden, base = {}, None
    for name, path in encs.items():
        s = sessions(path, lanes, threads)
        for x in s:
            x.run(None, {"pixel_values": px[0]})
        side_by_side(s, px[:4], lambda x, p: x.run(None, {"pixel_values": p}))
        ms, hs = side_by_side(s, px, lambda x, p: x.run(None, {"pixel_values": p})[0])
        hidden[name] = hs
        texts = [decode(ref_dec, h) for h in hs]
        if name == "fp32":
            base = texts
        err = sum(cer(a, b) for a, b in zip(texts, truth)) / len(truth)
        same = sum(a == b for a, b in zip(texts, base)) if base else "-"
        print(f"{name:28s} {ms * 1000:7.1f}   {err:10.3f}   {same}/{len(texts)}", flush=True)

    print("\ndecodificatore (testo)        ms/riga   errore (CER)   righe uguali a fp32", flush=True)
    hs = hidden["fp32"]
    base = None
    for name, path in decs.items():
        s = sessions(path, lanes, threads)
        decode(s[0], hs[0])
        ms, texts = side_by_side(s, hs, decode)
        if name == "fp32":
            base = texts
        err = sum(cer(a, b) for a, b in zip(texts, truth)) / len(truth)
        same = sum(a == b for a, b in zip(texts, base)) if base else "-"
        print(f"{name:28s} {ms * 1000:7.1f}   {err:10.3f}   {same}/{len(texts)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/variants")
