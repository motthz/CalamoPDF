"""Diagnostics for the handwriting reader (temporary, run on CI)."""
import json, os, sys, time, urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFont
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine import handreader as HR

def api(path):
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{path}", timeout=30) as r:
        return json.load(r)

for repo in ("Xenova/trocr-small-handwritten", "Xenova/trocr-base-handwritten"):
    try:
        info = api(repo + "/tree/main/onnx")
        print(repo, [(f["path"], f.get("size")) for f in info])
    except Exception as e:
        print(repo, "ERR", e)

d = HR.model_dir()
for n in os.listdir(d):
    print("file", n, os.path.getsize(os.path.join(d, n)))
for n in ("config.json", "generation_config.json", "preprocessor_config.json"):
    try:
        c = json.load(open(os.path.join(d, n)))
        if n == "config.json":
            c = {k: v for k, v in c.items() if not isinstance(v, dict)} | {"decoder": {k: c["decoder"].get(k) for k in ("decoder_start_token_id", "bos_token_id", "eos_token_id", "pad_token_id", "vocab_size", "scale_embedding", "layernorm_embedding")}}
        print(n, json.dumps(c)[:800])
    except Exception as e:
        print(n, "ERR", e)

import onnxruntime as ort
for n in ("encoder.onnx", "decoder.onnx"):
    s = ort.InferenceSession(os.path.join(d, n))
    print(n, "IN", [(i.name, i.shape, i.type) for i in s.get_inputs()][:6])
    print(n, "OUT", [(o.name, o.shape) for o in s.get_outputs()][:3])

font = ImageFont.truetype(os.path.join(os.environ["WINDIR"], "Fonts", "Inkfree.ttf"), 56)
def line(t, bg="white", fg="black"):
    l, tp, r, b = font.getbbox(t)
    img = Image.new("RGB", (r - l + 40, b - tp + 30), bg)
    ImageDraw.Draw(img).text((20 - l, 15 - tp), t, font=font, fill=fg)
    return img
R = HR.READER
t = "Firmato Giulia Rossi"
print("A black/white:", R.read(np.asarray(line(t))))
print("B blue/cream:", R.read(np.asarray(line(t, (247, 246, 241), (25, 40, 140)))))
import io
from engine.ocr import windows_ocr
buf = io.BytesIO(); line(t).save(buf, "PNG")
print("ocr:", windows_ocr(buf.getvalue()))
print("C after OCR, black/white:", R.read(np.asarray(line(t))))
print("D after OCR, blue/cream:", R.read(np.asarray(line(t, (247, 246, 241), (25, 40, 140)))))
import threading
out = []
th = threading.Thread(target=lambda: out.append(R.read(np.asarray(line(t)))))
th.start(); th.join()
print("E in another thread:", out)
for fg, bg in [((25, 40, 140), (247, 246, 241)), ((170, 25, 25), (247, 246, 241)), ((90, 90, 90), (230, 225, 210)), ((20, 20, 20), (255, 255, 255))]:
    for t2 in ("Firmato Giulia Rossi", "Consegna entro venerdi"):
        print("cleaned", fg, bg, R.read(np.asarray(line(t2, bg, fg))))
