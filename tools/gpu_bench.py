"""Does the handwriting model run on the graphics card (DirectML) on this computer, does it
read the same as on the processor, and how fast? Changes nothing.
   python tools/gpu_bench.py   (needs onnxruntime-directml instead of onnxruntime)"""
import os
import sys
import time
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from engine.handreader import READER, model_dir  # noqa: E402
from engine.quality import cer  # noqa: E402

TEXTS = ["Consegna entro venerdi", "Firmato Giulia Rossi", "Ottimo lavoro del team", "Visto e approvato",
         "Marco Bianchi", "Riunione del 12 maggio", "Chiamare il fornitore domani", "Pagato in contanti"]


def lines():
    win = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    fonts = [os.path.join(win, f) for f in ("Inkfree.ttf", "segoesc.ttf")]
    fonts = [f for f in fonts if os.path.exists(f)] or [None]
    out = []
    for t in TEXTS:
        for f in fonts:
            font = ImageFont.truetype(f, 52) if f else ImageFont.load_default()
            l, tp, r, b = font.getbbox(t)
            img = Image.new("RGB", (r - l + 40, b - tp + 30), (247, 246, 241))
            ImageDraw.Draw(img).text((20 - l, 15 - tp), t, font=font, fill=(25, 40, 140))
            out.append((t, np.asarray(img)))
    return out


def gpu_session(path):
    o = ort.SessionOptions()
    o.enable_mem_pattern = False
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(path, o, providers=["DmlExecutionProvider", "CPUExecutionProvider"])


def main():
    print("onnxruntime", ort.__version__, "fornitori:", ort.get_available_providers(), flush=True)
    if not READER.installed():
        READER.download()
    READER._load()
    d = model_dir()
    fp32 = os.path.join(d, "encoder_fp32.onnx")
    if not os.path.exists(fp32):
        urllib.request.urlretrieve("https://huggingface.co/Xenova/trocr-small-handwritten/resolve/main/onnx/"
                                   "encoder_model.onnx", fp32)
    data = lines()
    px = [READER._pixels(im) for _, im in data]
    truth = [t for t, _ in data]
    dec = READER._lanes[0].dec

    def decode(h):
        return READER._decode(types.SimpleNamespace(dec=dec), h, 48)[0]
    cpu = READER._lanes[0].enc
    ref = [cpu.run(None, {"pixel_values": p})[0] for p in px]
    t = time.perf_counter()
    for p in px:
        cpu.run(None, {"pixel_values": p})
    print(f"processore, encoder.onnx: {(time.perf_counter() - t) / len(px) * 1000:.1f} ms/riga "
          f"(un lettore, tutti i core), CER {np.mean([cer(decode(h), x) for h, x in zip(ref, truth)]):.3f}", flush=True)
    if "DmlExecutionProvider" not in ort.get_available_providers():
        print("DirectML non disponibile")
        return
    for name in ("encoder.onnx", "encoder_fast.onnx", "encoder_fp32.onnx"):
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        try:
            t = time.perf_counter()
            s = gpu_session(path)
            s.run(None, {"pixel_values": px[0]})
            first = time.perf_counter() - t
            t = time.perf_counter()
            hs = [s.run(None, {"pixel_values": p})[0] for p in px]
            ms = (time.perf_counter() - t) / len(px) * 1000
            diff = max(float(np.abs(a - b).max()) for a, b in zip(hs, ref))
            texts = [decode(h) for h in hs]
            err = np.mean([cer(a, x) for a, x in zip(texts, truth)])
            print(f"scheda grafica, {name}: {ms:.1f} ms/riga (apertura {first:.1f} s), CER {err:.3f}, "
                  f"differenza massima dal processore {diff:.4f}", flush=True)
        except Exception as e:
            print(f"scheda grafica, {name}: errore {e}", flush=True)


if __name__ == "__main__":
    main()
