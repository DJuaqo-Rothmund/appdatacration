"""
Genera una base de datos de demostración con fotos sintéticas (para probar
la UI, la IA y los informes en PC sin salir a terreno).

Uso:
    FENORUBUS_DATA=/tmp/fenorubus-demo python tools/demo_data.py [--weeks 14]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import phenology as ph  # noqa: E402
from ai_classifier import PhenologyClassifier  # noqa: E402
from database import Database, default_db_path  # noqa: E402
from platform_utils import data_subdir  # noqa: E402


def synthetic_photo(code: int, kind: str, path: str, seed: int) -> str:
    """Dibuja una escena esquemática coherente con el estadio BBCH."""
    rnd = random.Random(seed)
    size = (800, 600)
    macro = code // 10
    sky = (205, 214, 190) if kind == "canopy" else (70, 92, 45)
    img = Image.new("RGB", size, sky)
    d = ImageDraw.Draw(img)
    n_leaves = {0: 0, 1: 40, 3: 90}.get(macro, 120 if macro < 9 else 60)
    if macro == 0:
        d.rectangle((0, 0, *size), fill=(120, 95, 70))
    for _ in range(n_leaves if kind == "canopy" else n_leaves // 2):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        r = rnd.randint(25, 70)
        g = (rnd.randint(60, 100), rnd.randint(110, 160), rnd.randint(40, 70))
        if macro == 9:
            g = (rnd.randint(170, 210), rnd.randint(150, 180), rnd.randint(40, 70))
        d.ellipse((x - r, y - r // 2, x + r, y + r // 2), fill=g)
    organs = 8 if kind == "canopy" else 22
    for _ in range(organs):
        x, y = rnd.randrange(60, size[0] - 60), rnd.randrange(60, size[1] - 60)
        r = rnd.randint(10, 22) if kind == "canopy" else rnd.randint(18, 40)
        if macro == 0:
            d.ellipse((x - r // 2, y - r, x + r // 2, y + r), fill=(95, 65, 45))
        elif macro == 5:
            d.ellipse((x - r, y - r, x + r, y + r), fill=(150, 175, 90))
        elif macro == 6:
            for a in range(5):
                import math
                ang = a * 72 * math.pi / 180
                px, py = x + int(r * 0.8 * math.cos(ang)), y + int(r * 0.8 * math.sin(ang))
                d.ellipse((px - r // 2, py - r // 2, px + r // 2, py + r // 2), fill=(245, 245, 238))
            d.ellipse((x - r // 3, y - r // 3, x + r // 3, y + r // 3), fill=(215, 200, 110))
        elif macro == 7:
            d.ellipse((x - r, y - r, x + r, y + r), fill=(170, 200, 110))
        elif macro == 8:
            t = min(1.0, (code - 80) / 9)
            col = (int(235 - 90 * t), int(140 - 120 * t), int(160 - 110 * t))
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    img.save(path, "JPEG", quality=85)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=14)
    ap.add_argument("--season", type=int, default=ph.season_of(_dt.date.today()))
    args = ap.parse_args()

    db = Database(default_db_path())
    clf = PhenologyClassifier(db)
    photo_dir = data_subdir("photos", "demo")
    varieties = db.list_varieties()
    db.ensure_weeks(args.season, args.weeks)
    for vi, v in enumerate(varieties):
        offset = (vi % 5) - 2  # precocidad relativa simulada
        db.save_metrics(v["id"], args.season, historical_yield=round(1.2 + vi * 0.15, 2),
                        projected_yield=round(1.3 + vi * 0.12, 2), basal_canes=6 + vi % 4,
                        laterals=14 + vi)
        db.set_custom_field(v["id"], args.season, "Grados Brix", f"{9.5 + vi * 0.3:.1f}", "°Bx")
        for w in db.list_weeks(args.season):
            n = w["week_number"]
            code = int(round(ph.expected_bbch_for_week(n + offset * 0.6)))
            code = min(ph.BBCH_RUBUS, key=lambda s: abs(s[0] - code))[0]
            obs = db.get_or_create_observation(v["id"], w["id"])
            for kind in ("canopy", "detail"):
                path = os.path.join(photo_dir, f"v{v['id']}_s{n:02d}_{kind}.jpg")
                synthetic_photo(code, kind, path, seed=vi * 1000 + n * 10 + (kind == "detail"))
                db.set_photo(obs["id"], kind, path, source="demo")
            db.update_observation(obs["id"], bbch_code=code, bbch_label=ph.bbch_label(code),
                                  notes=f"Registro de demostración. {ph.bbch_label(code)}.",
                                  observed_at=w["start_date"])
            if vi < 4:  # semillas para el k-NN
                ph_row = db.get_photos(obs["id"])["detail"]
                clf.add_reference(ph_row["path"], code, photo_id=ph_row["id"])
    print(f"Demo creada en {db.path}: {len(varieties)} variedades × {args.weeks} semanas")


if __name__ == "__main__":
    main()
