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
def line(t):
    l, tp, r, b = font.getbbox(t)
    img = Image.new("RGB", (r - l + 40, b - tp + 30), "white")
    ImageDraw.Draw(img).text((20 - l, 15 - tp), t, font=font, fill="black")
    return img
texts = ["Consegna entro venerdi", "Firmato Giulia Rossi", "the quick brown fox"]
R = HR.READER
for t in texts:
    print("onnx:", t, "->", R.read(np.asarray(line(t))))
R._load()
print("start", R._start, "eos", R._eos, "size", R._size, R._mean, R._std)

# reference: the original PyTorch model
try:
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    for name in ("microsoft/trocr-small-handwritten", "microsoft/trocr-base-handwritten"):
        proc = TrOCRProcessor.from_pretrained(name)
        model = VisionEncoderDecoderModel.from_pretrained(name)
        for t in texts:
            pv = proc(images=line(t), return_tensors="pt").pixel_values
            t0 = time.time()
            ids = model.generate(pv, max_new_tokens=40)
            print("torch", name, t, "->", proc.batch_decode(ids, skip_special_tokens=True)[0], f"{time.time()-t0:.2f}s")
            if name.endswith("small-handwritten") and t == texts[0]:
                mine = R._pixels(np.asarray(line(t)))
                print("pixel diff vs processor", float(np.abs(mine - pv.numpy()).max()))
                print("first ids torch", ids[0][:8].tolist())
except Exception as e:
    import traceback; traceback.print_exc()
