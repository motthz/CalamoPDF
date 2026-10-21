"""Builds the fast form of the handwriting model, published once as the assets of a GitHub
release (tools/../.github/workflows/model.yml) and downloaded by the app next to the files
of the first download (app/engine/handreader.py, FAST).

From the full-precision TrOCR small handwritten (Microsoft, MIT licence, ONNX by Xenova):
encoder_fast.onnx, the image encoder with the operations of each layer fused into one
(attention, layer norm, GELU) and its weights quantized to 8 bits per channel: about 40%
less time than the encoder of the first download, as accurate on the test forms. (The
text decoder already takes a fifth of the time of the encoder: it stays as it is.)

Usage: python tools/make_fast_model.py OUT_DIR   (needs: onnxruntime, onnx, sympy)"""
import os
import sys
import urllib.request

import onnx
from onnxruntime.quantization import QuantType, quantize_dynamic
from onnxruntime.transformers import optimizer

REPO = "https://huggingface.co/Xenova/trocr-small-handwritten/resolve/main/onnx/"
FLOAT = {"DefaultTensorType": onnx.TensorProto.FLOAT}


def fetch(name, out):
    path = os.path.join(out, "src-" + name)
    if not os.path.exists(path):
        print("download", name, flush=True)
        urllib.request.urlretrieve(REPO + name, path + ".part")
        os.replace(path + ".part", path)
    return path


def quantize(src, dst, exclude=()):
    quantize_dynamic(src, dst, weight_type=QuantType.QInt8, per_channel=True,
                     nodes_to_exclude=list(exclude), extra_options=FLOAT)
    print("written", dst, os.path.getsize(dst) // 1_000_000, "MB", flush=True)


def main(out):
    os.makedirs(out, exist_ok=True)
    enc = fetch("encoder_model.onnx", out)
    fused = optimizer.optimize_model(enc, model_type="vit", num_heads=6, hidden_size=384, opt_level=0)
    print(fused.get_fused_operator_statistics(), flush=True)
    fused_path = os.path.join(out, "src-encoder-fused.onnx")
    fused.save_model_to_file(fused_path)
    quantize(fused_path, os.path.join(out, "encoder_fast.onnx"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/fast-model")
