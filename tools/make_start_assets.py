"""
Genera las imágenes de la pantalla de inicio (fondo con color, burbujas de los íconos,
cerebro celeste y su brillo). Se ejecuta en el computador, no en el teléfono:

    python tools/make_start_assets.py
"""
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "ui")


def tint(im, rgb, op=1.0):
    out = Image.new("RGBA", im.size, rgb + (0,))
    out.putalpha(im.split()[3].point(lambda v: int(v * op)))
    return out


def background(w=824, h=1720):
    y = np.linspace(0, 1, h)[:, None]
    top, mid, bot = np.array((222, 239, 214)), np.array((250, 246, 238)), np.array((246, 214, 226))
    c = np.where(y < .5, top + (mid - top) * (y / .5), mid + (bot - mid) * ((y - .5) / .5))
    bg = Image.fromarray(np.repeat(c[:, None, :], w, axis=1).astype("uint8")).convert("RGBA")
    pred = Image.open(os.path.join(UI, "start_predio.png")).convert("RGBA").resize((440, 440))
    logo = Image.open(os.path.join(UI, "logo_line.png")).convert("RGBA").resize((400, 400))
    for im, cx, cy, rot, op in ((tint(logo, (168, 24, 74), .15), -60, 1520, 18, 1),
                                (tint(pred, (47, 86, 67), .12), 800, 500, -20, 1),
                                (tint(logo, (168, 24, 74), .10), 840, 1640, -12, 1)):
        im = im.rotate(rot, expand=True, resample=Image.BICUBIC)
        bg.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))
    bg.convert("RGB").save(os.path.join(UI, "start_bg.jpg"), quality=86)


def blob(n, rgb, seed, stars=False):
    random.seed(seed)
    m = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(m)
    pts = []
    for k in range(10):
        t = 2 * math.pi * k / 10
        r = n * (0.36 + random.uniform(-.035, .045))
        pts.append((n / 2 + r * math.cos(t), n / 2 + r * math.sin(t)))
    d.polygon(pts, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(n * .05)).point(lambda v: 255 if v > 128 else 0)
    m = m.filter(ImageFilter.GaussianBlur(1.5))
    shadow = Image.new("RGBA", (n, n), (60, 30, 50, 0))
    shadow.putalpha(m.filter(ImageFilter.GaussianBlur(n * .04)).point(lambda v: v * 50 // 255))
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.alpha_composite(shadow, (3, int(n * .03)))
    body = Image.new("RGBA", (n, n), rgb + (0,))
    body.putalpha(m)
    out.alpha_composite(body)
    if stars:
        d = ImageDraw.Draw(out)
        random.seed(3)
        for _ in range(40):
            r = random.uniform(0.05, 0.30) * n
            t = random.uniform(0, 2 * math.pi)
            x, y = n / 2 + r * math.cos(t), n / 2 + r * math.sin(t)
            s = random.choice((1.2, 1.2, 1.8, 2.4))
            d.ellipse([x - s, y - s, x + s, y + s], fill=(255, 255, 255, random.randint(110, 230)))
    return out


def main():
    background()
    blob(420, (250, 214, 228), 115).save(os.path.join(UI, "blob_id.png"))
    blob(420, (214, 236, 220), 297).save(os.path.join(UI, "blob_predio.png"))
    blob(440, (46, 52, 130), 7, stars=True).save(os.path.join(UI, "blob_ai.png"))
    ai = Image.open(os.path.join(UI, "start_ai.png")).convert("RGBA")
    tint(ai, (190, 228, 255)).save(os.path.join(UI, "start_ai_night.png"))
    glow = tint(ai, (120, 190, 255)).filter(ImageFilter.GaussianBlur(14))
    glow.putalpha(glow.split()[3].point(lambda v: min(255, int(v * 2.2))))
    glow.save(os.path.join(UI, "ai_glow.png"))


if __name__ == "__main__":
    main()
