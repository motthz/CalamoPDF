"""How fast the handwriting reader runs on this computer, part by part: the image encoder of
the first download against the fast one, with one or more readers side by side and with
or without the threads waiting actively ("spinning"). Prints a table; changes nothing.
   python tools/reader_bench.py"""
import os
import platform
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from engine.handreader import READER, model_dir  # noqa: E402


def lines():
    fonts = [os.path.join(os.environ.get("WINDIR", ""), "Fonts", f) for f in ("Inkfree.ttf", "segoesc.ttf")]
    fonts = [f for f in fonts if os.path.exists(f)] or [None]
    texts = ["Consegna entro venerdi", "Firmato Giulia Rossi", "Ottimo lavoro del team", "Visto e approvato",
             "Marco Bianchi", "Riunione del 12 maggio", "Chiamare il fornitore domani", "Pagato in contanti"]
    out = []
    for t in texts:
        for f in fonts:
            for size in (50, 58):
                font = ImageFont.truetype(f, size) if f else ImageFont.load_default()
                l, tp, r, b = font.getbbox(t)
                img = Image.new("RGB", (r - l + 40, b - tp + 30), (247, 246, 241))
                ImageDraw.Draw(img).text((20 - l, 15 - tp), t, font=font, fill=(25, 40, 140))
                out.append(np.asarray(img))
    return out


def sessions(path, lanes, threads, spin):
    out = []
    for _ in range(lanes):
        o = ort.SessionOptions()
        o.intra_op_num_threads = threads
        o.inter_op_num_threads = 1
        o.add_session_config_entry("session.intra_op.allow_spinning", "1" if spin else "0")
        out.append(ort.InferenceSession(path, o, providers=["CPUExecutionProvider"]))
    return out


def encode_all(sess, px):
    queue = list(range(len(px)))
    guard = threading.Lock()

    def work(s):
        while True:
            with guard:
                if not queue:
                    return
                i = queue.pop()
            s.run(None, {"pixel_values": px[i]})
    t = time.perf_counter()
    ths = [threading.Thread(target=work, args=(s,)) for s in sess[1:]]
    for th in ths:
        th.start()
    work(sess[0])
    for th in ths:
        th.join()
    return (time.perf_counter() - t) / len(px)


def main():
    cores = os.cpu_count() or 2
    print(f"{platform.platform()} | {platform.processor()} | {cores} core logici | onnxruntime {ort.__version__}")
    if not READER.installed():
        READER.download()
    try:
        READER.download_fast()
    except Exception as e:
        print("lettore veloce non scaricato:", e)
    READER._load()
    imgs = lines()
    t = time.perf_counter()
    px = [READER._pixels(im) for im in imgs]
    print(f"preparazione dell'immagine: {(time.perf_counter() - t) / len(imgs) * 1000:.1f} ms/riga")
    d = model_dir()
    encs = [("encoder.onnx (1.6)", os.path.join(d, "encoder.onnx"))]
    if os.path.exists(os.path.join(d, "encoder_fast.onnx")):
        encs.append(("encoder_fast.onnx (1.7)", os.path.join(d, "encoder_fast.onnx")))
    configs = [(1, cores, True), (1, cores, False), (2, max(1, cores // 2), True), (2, max(1, cores // 2), False),
               (cores, 1, False)]
    for name, path in encs:
        for lanes, threads, spin in configs:
            s = sessions(path, lanes, threads, spin)
            for x in s:
                x.run(None, {"pixel_values": px[0]})
            print(f"{name:26s} {lanes} lettori x {threads} thread, attesa attiva {'si' if spin else 'no'}: "
                  f"{encode_all(s, px) * 1000:6.1f} ms/riga")
    # the decoder: the text of one line
    h = READER._enc.run(None, {READER._enc_in: px[0]})[0]
    lane = READER._lanes[0]
    t = time.perf_counter()
    for _ in range(5):
        READER._decode(lane, h, 48)
    print(f"decodifica del testo: {(time.perf_counter() - t) / 5 * 1000:.1f} ms/riga")


if __name__ == "__main__":
    main()
