"""
Genera los recursos gráficos de la interfaz (assets/ui/*.png, icono y presplash).

Estilo: iconos de línea fina verde profundo con rellenos verde hoja, sobre
azulejos redondeados (el azulejo lo dibuja Kivy para poder animar el estado
activo). Versión «active» en tono frambuesa.

    python tools/make_ui_assets.py
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "ui")

SS = 4          # supersampling
SIZE = 192      # tamaño final de los iconos

PALETTES = {
    "idle": {"stroke": (30, 74, 69), "fill": (169, 214, 160), "berry": (255, 255, 255, 0),
             "hole": (0, 0, 0, 0)},
    "active": {"stroke": (168, 24, 66), "fill": (150, 205, 140), "berry": (246, 196, 208),
               "hole": (0, 0, 0, 0)},
}


class Pen:
    def __init__(self, size: int, stroke, fill, berry, width: float = 0.034):
        self.n = size * SS
        self.img = Image.new("RGBA", (self.n, self.n), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.stroke = tuple(stroke) + ((255,) if len(stroke) == 3 else ())
        self.fill = tuple(fill) + ((255,) if len(fill) == 3 else ())
        self.berry = tuple(berry) + ((255,) if len(berry) == 3 else ())
        self.w = max(2, int(self.n * width))

    def p(self, x, y):
        return (x * self.n, y * self.n)

    def box(self, x0, y0, x1, y1):
        return [x0 * self.n, y0 * self.n, x1 * self.n, y1 * self.n]

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=True):
        self.d.rounded_rectangle(self.box(x0, y0, x1, y1), radius=r * self.n, fill=fill,
                                 outline=self.stroke if outline else None,
                                 width=self.w if outline else 0)

    def circle(self, cx, cy, r, fill=None, outline=True, width=None):
        self.d.ellipse(self.box(cx - r, cy - r, cx + r, cy + r), fill=fill,
                       outline=self.stroke if outline else None,
                       width=(width or self.w) if outline else 0)

    def line(self, pts, width=None, color=None):
        pts = [self.p(*q) for q in pts]
        w = width or self.w
        self.d.line(pts, fill=color or self.stroke, width=w, joint="curve")
        for q in (pts[0], pts[-1]):  # extremos redondeados
            self.d.ellipse([q[0] - w / 2, q[1] - w / 2, q[0] + w / 2, q[1] + w / 2],
                           fill=color or self.stroke)

    def polygon(self, pts, fill=None, outline=True):
        pts = [self.p(*q) for q in pts]
        self.d.polygon(pts, fill=fill)
        if outline:
            self.d.line(pts + [pts[0]], fill=self.stroke, width=self.w, joint="curve")

    def leaf(self, p0, p1, width, fill=None):
        (x0, y0), (x1, y1) = p0, p1
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        up, down = [], []
        for i in range(41):
            t = i / 40
            bulge = width * math.sin(math.pi * t) ** 0.85
            cx, cy = x0 + dx * t, y0 + dy * t
            up.append((cx + nx * bulge, cy + ny * bulge))
            down.append((cx - nx * bulge, cy - ny * bulge))
        self.polygon(up + down[::-1], fill=fill or self.fill)
        self.line([(x0 + dx * .15, y0 + dy * .15), (x0 + dx * .7, y0 + dy * .7)],
                  width=max(2, self.w // 2))

    def result(self, size):
        return self.img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- iconos
def camera(pen: Pen):
    pen.rrect(.12, .43, .19, .75, .02, fill=pen.fill, outline=False)
    pen.rrect(.21, .29, .35, .38, .025)
    pen.rrect(.09, .35, .89, .80, .085)
    pen.rrect(.12, .43, .19, .74, .02, fill=pen.fill, outline=False)
    pen.circle(.50, .58, .155)
    pen.circle(.50, .58, .085)
    pen.circle(.78, .47, .028, fill=pen.stroke, outline=False)
    pen.line([(.72, .35), (.72, .22)])
    pen.leaf((.72, .25), (.53, .10), .075)
    pen.leaf((.72, .25), (.91, .09), .075)


def raspberry(pen: Pen):
    pen.leaf((.50, .34), (.50, .07), .085)
    pen.leaf((.50, .34), (.26, .19), .075)
    pen.leaf((.50, .34), (.74, .19), .075)
    r = .098
    rows = [(.41, [.37, .50, .63]), (.555, [.30, .435, .565, .70]),
            (.70, [.37, .50, .63]), (.835, [.435, .565])]
    cells = [(x, y) for y, xs in rows for x in xs]
    # las esferas centrales se dibujan al final para quedar «delante»
    cells.sort(key=lambda q: -(abs(q[0] - .5) + abs(q[1] - .6)))
    for x, y in cells:
        pen.circle(x, y, r, fill=pen.berry if pen.berry[3] else (0, 0, 0, 0))


def reports(pen: Pen):
    pen.rrect(.12, .16, .52, .80, .05)
    pen.line([(.19, .29), (.36, .29)], width=int(pen.w * 1.6), color=pen.fill)
    for y in (.38, .45, .52, .59, .66):
        pen.line([(.19, y), (.33, y)], width=max(2, int(pen.w * .8)))
    front = [(.40, .26), (.74, .26), (.88, .40), (.88, .88), (.40, .88)]
    pen.polygon(front, fill=(0, 0, 0, 0), outline=False)   # recorta el documento trasero
    pen.polygon(front, fill=None)
    pen.line([(.74, .26), (.74, .40), (.88, .40)])
    for x0, h in ((.49, .10), (.59, .17), (.69, .25)):
        pen.rrect(x0, .66 - h, x0 + .07, .66, .01, fill=pen.fill)
    pen.line([(.48, .74), (.70, .74)])
    pen.circle(.85, .86, .105, fill=(0, 0, 0, 0), outline=False)
    pen.circle(.85, .86, .088)
    pen.line([(.805, .865), (.84, .90), (.90, .83)])


def vision(pen: Pen):
    for (x, y, sx, sy) in ((.12, .18, 1, 1), (.88, .18, -1, 1), (.12, .82, 1, -1), (.88, .82, -1, -1)):
        pen.line([(x, y + sy * .15), (x, y), (x + sx * .15, y)])
    up, down = [], []
    for i in range(61):
        t = i / 60
        x = .18 + .64 * t
        h = .205 * math.sin(math.pi * t) ** 1.1
        up.append((x, .50 - h))
        down.append((x, .50 + h))
    pen.polygon(up + down[::-1][1:-1], fill=None)
    pen.circle(.50, .50, .135, fill=pen.fill)
    pen.circle(.50, .50, .07, fill=pen.stroke, outline=False)
    pen.circle(.465, .465, .026, fill=(255, 255, 255, 255), outline=False)


ICONS = {"nav_sampling": camera, "nav_varieties": raspberry,
         "nav_reports": reports, "nav_preview": vision}


def make_icons():
    for name, fn in ICONS.items():
        for state, pal in PALETTES.items():
            pen = Pen(SIZE, pal["stroke"], pal["fill"], pal["berry"])
            fn(pen)
            pen.result(SIZE).save(os.path.join(OUT, f"{name}_{state}.png"))


# ---------------------------------------------------------------- fondo
def make_background(w=720, h=1280):
    base = Image.new("RGB", (w, h))
    top, bottom = (241, 247, 240), (251, 243, 245)
    for y in range(h):
        t = y / (h - 1)
        base.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)), (0, y, w, y + 1))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for (cx, cy, r, col) in ((0.05, 0.10, 0.55, (143, 200, 130, 110)),
                             (1.00, 0.42, 0.45, (232, 128, 156, 80)),
                             (0.15, 0.78, 0.40, (164, 214, 150, 70)),
                             (0.95, 0.98, 0.55, (214, 90, 124, 70))):
        g.ellipse([(cx - r) * w, cy * h - r * w, (cx + r) * w, cy * h + r * w], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    base = Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")
    base.save(os.path.join(OUT, "background.jpg"), quality=90)


# ---------------------------------------------------------------- «mente del sistema»
def make_mind_background(w=720, h=1280):
    """Fondo del módulo de IA: azul noche, resplandores y una red neuronal tenue."""
    import random
    base = Image.new("RGB", (w, h))
    top, bottom = (7, 11, 20), (13, 22, 38)
    for y in range(h):
        t = y / (h - 1)
        base.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)), (0, y, w, y + 1))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for (cx, cy, r, col) in ((0.95, 0.10, 0.55, (30, 136, 229, 70)),
                             (0.05, 0.75, 0.50, (91, 95, 239, 60)),
                             (0.60, 0.45, 0.35, (79, 195, 247, 28))):
        g.ellipse([(cx - r) * w, cy * h - r * w, (cx + r) * w, cy * h + r * w], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.alpha_composite(base.convert("RGBA"), glow)
    net = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(net)
    rnd = random.Random(7)
    nodes = [(rnd.uniform(0, w), rnd.uniform(0, h)) for _ in range(70)]
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            dist = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** .5
            if dist < 150:
                d.line([(x1, y1), (x2, y2)], fill=(120, 190, 255, int(38 * (1 - dist / 150))), width=1)
    for x, y in nodes:
        r = rnd.uniform(1.2, 2.8)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(140, 210, 255, 90))
    for gx in range(0, w, 36):
        for gy in range(0, h, 36):
            d.point((gx, gy), fill=(140, 170, 220, 22))
    img = Image.alpha_composite(img, net)
    img.convert("RGB").save(os.path.join(OUT, "mind_bg.jpg"), quality=88)


# ---------------------------------------------------------------- logo / icono
LOGO_SRC = os.path.join(ROOT, "assets", "logo_source.png")
APP_TITLE = "PhenoRubus"
BYLINE = "By DJuaqo, potenciado con Claude AI"


def load_logo(size: int) -> Image.Image:
    """Logo oficial (frambuesa-reloj con ciclo fenológico) centrado en un cuadrado."""
    src = Image.open(LOGO_SRC).convert("RGBA")
    src = src.crop(src.getbbox())
    side = max(src.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(src, ((side - src.width) // 2, (side - src.height) // 2), src)
    return sq.resize((size, size), Image.LANCZOS)


def make_logo_assets():
    load_logo(512).save(os.path.join(OUT, "logo.png"))           # pantalla de inicio
    icon = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    logo = load_logo(472)
    icon.alpha_composite(logo, ((512 - 472) // 2, (512 - 472) // 2))
    icon.save(os.path.join(ROOT, "assets", "icon.png"))


def _fonts():
    fonts = os.path.join(os.path.dirname(__import__("kivy").__file__), "data", "fonts")
    try:
        return (ImageFont.truetype(os.path.join(fonts, "Roboto-Bold.ttf"), 86),
                ImageFont.truetype(os.path.join(fonts, "Roboto-Regular.ttf"), 34))
    except OSError:
        return None, None


SPLASH_BG = (0xF2, 0xF4, 0xEE)   # = ui/theme.py SPLASH_BG y android.presplash_color


def make_presplash():
    """Idéntica a <SplashScreen>: fondo liso y proporción de teléfono actual (≈20:9), para
    que Android no la achique con franjas y se vea una sola pantalla de inicio."""
    w, h = 1080, 2340
    bg = Image.new("RGBA", (w, h), SPLASH_BG + (255,))
    # Mismas proporciones que <SplashScreen> (layout.kv): logo 62 % del ancho centrado
    # al 60 % de la altura, nombre al 33,5 % y firma al pie.
    side = int(w * .62)
    logo = load_logo(side)
    cy = int(h * (1 - .60))
    bg.alpha_composite(logo, ((w - side) // 2, cy - side // 2))
    d = ImageDraw.Draw(bg)
    f1, f2 = _fonts()
    d.text((w // 2, int(h * (1 - .335))), APP_TITLE, fill=(30, 74, 58), anchor="mm", font=f1)
    d.text((w // 2, int(h * (1 - .04)) - 40), BYLINE, fill=(110, 126, 117), anchor="mm", font=f2)
    bg.convert("RGB").save(os.path.join(ROOT, "assets", "presplash.png"))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    make_icons()
    make_background()
    make_mind_background()
    make_logo_assets()
    make_presplash()
    print("Recursos generados en", OUT)
