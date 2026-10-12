"""Draws the Calamo app icon (assets/icon.ico + assets/icon.png)."""
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = 1024


def gradient(size, c0, c1, diagonal=True):
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = ((x + y) / (2 * size)) if diagonal else y / size
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
    return g


small = 256  # draw gradients small then upscale (fast + smooth)
bg = gradient(small, (34, 36, 46), (10, 11, 15)).resize((N, N), Image.LANCZOS)
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, N - 40, N - 40), radius=220, fill=255)
icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
icon.paste(bg, (0, 0), mask)

# subtle gold rim
rim = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(rim).rounded_rectangle((40, 40, N - 40, N - 40), radius=220, outline=(226, 182, 97, 90), width=6)
icon = Image.alpha_composite(icon, rim)

# nib shape (same geometry as the SVG logo, viewBox 64)
s = N / 64 * 0.78
ox, oy = (N - 64 * s) / 2, (N - 64 * s) / 2 + 10


def P(x, y):
    return (ox + x * s, oy + y * s)


import math  # noqa: E402
pts = []
# left curve from top (32,4) to (16,35) to (25,50) ; approximated with polyline
for t in [i / 40 for i in range(41)]:
    x = 32 - 16 * math.sin(t * math.pi / 2) ** 0.9
    y = 4 + 31 * t
    pts.append(P(x, y))
for t in [i / 20 for i in range(1, 21)]:
    pts.append(P(16 + 9 * t, 35 + 15 * t ** 1.4))
pts += [P(29, 60), P(35, 60)]
right = [(64 - x / s + 2 * ox / s, y) for (x, y) in []]
mirror = [(2 * (ox + 32 * s) - x, y) for (x, y) in reversed(pts[:-2])]
nib = pts + mirror
nib_mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(nib_mask).polygon(nib, fill=255)
gold = gradient(small, (246, 223, 164), (168, 115, 42)).resize((N, N), Image.LANCZOS).convert("RGBA")
glow = nib_mask.filter(ImageFilter.GaussianBlur(40))
glow_layer = Image.new("RGBA", (N, N), (226, 182, 97, 0))
glow_layer.putalpha(glow.point(lambda v: int(v * 0.45)))
icon = Image.alpha_composite(icon, glow_layer)
nib_img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
nib_img.paste(gold, (0, 0), nib_mask)
icon = Image.alpha_composite(icon, nib_img)
d = ImageDraw.Draw(icon)
d.line([P(32, 18), P(32, 41)], fill=(20, 21, 27, 255), width=int(2.8 * s))
cx, cy = P(32, 42)
r = 3.8 * s
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(20, 21, 27, 255))

os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
icon.save(os.path.join(ROOT, "assets", "icon.png"))
icon.save(os.path.join(ROOT, "assets", "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon ok")
