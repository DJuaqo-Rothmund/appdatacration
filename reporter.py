"""
reporter.py
===========
Generador de informes HTML estáticos y autocontenidos (Jinja2).

Informes
--------
1. ``weekly``   Comparativa inter-varietal de una semana de muestreo.
2. ``period``   Evolución de todas las variedades en un rango de semanas / mes.
3. ``variety``  Timeline longitudinal de una variedad + datos biométricos.
4. ``matrix``   Matriz comparativa global (heatmap BBCH, precocidad relativa,
                hitos fenológicos y galería paralela).

Empaquetado
-----------
* ``package="html"``: un único .html con las imágenes embebidas en Base64
  (redimensionadas/comprimidas para poder enviarse por WhatsApp o correo).
* ``package="zip"``:  .zip portátil con ``index.html`` + carpeta ``img/``
  (imágenes en mayor resolución).
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import json
import os
import zipfile
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps

import phenology as ph
from platform_utils import APP_NAME, resource_path, slugify

PHOTO_KINDS = (("canopy", "Canopia / planta completa"), ("detail", "Detalle / macro"))
MILESTONES = [(51, "Botón floral"), (61, "Inicio floración"), (65, "Plena floración"),
              (71, "Cuajado"), (81, "Pinta"), (87, "Cosecha")]


# ===========================================================================
# Colores del heatmap (secuencial, un solo tono oliva: claro -> oscuro)
# ===========================================================================
_SEQ_LIGHT = (0xEE, 0xF0, 0xE2)
_SEQ_DARK = (0x2B, 0x36, 0x14)


def seq_color(code: int | None) -> tuple[str, str]:
    """(fondo, texto) para un código BBCH en la rampa secuencial."""
    if code is None:
        return "transparent", "inherit"
    t = max(0.0, min(1.0, code / 99.0)) ** 0.85
    rgb = tuple(round(a + (b - a) * t) for a, b in zip(_SEQ_LIGHT, _SEQ_DARK))
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    return "#%02x%02x%02x" % rgb, ("#ffffff" if lum < 0.5 else "#1f2413")


# ===========================================================================
# Imágenes
# ===========================================================================
class ImageStore:
    """
    Convierte fotos a data-URI (modo html), archivos relativos (modo zip) o
    miniaturas locales en caché referenciadas por file:// (modo preview: rápido
    y con poca memoria, para la vista previa dentro de la app).
    """

    def __init__(self, package: str, max_side: int, quality: int, cache_dir: str | None = None):
        self.package = package
        self.cache_dir = cache_dir
        self.max_side = max_side
        self.quality = quality
        self.files: dict[str, bytes] = {}
        self._cache: dict[tuple[str, int], str] = {}

    def _encode(self, path: str, max_side: int) -> bytes:
        img = Image.open(path)
        img.draft("RGB", (max_side, max_side))  # decodificación JPEG reducida
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=self.quality, optimize=True, progressive=True)
        return buf.getvalue()

    def src(self, path: str | None, max_side: int | None = None) -> str | None:
        if not path or not os.path.exists(path):
            return None
        side = max_side or self.max_side
        key = (path, side)
        if key in self._cache:
            return self._cache[key]
        if self.package == "zip":
            side = max(side, 1600)
        if self.package == "preview":
            uri = self._preview_file(path, min(side, 720))
            self._cache[key] = uri
            return uri
        try:
            data = self._encode(path, side)
        except Exception:
            return None
        if self.package == "zip":
            name = f"img/{len(self.files) + 1:04d}_{slugify(os.path.splitext(os.path.basename(path))[0])}.jpg"
            self.files[name] = data
            uri = name
        else:
            uri = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        self._cache[key] = uri
        return uri

    def _preview_file(self, path: str, side: int) -> str | None:
        import hashlib
        key = hashlib.md5(f"{path}|{os.path.getmtime(path)}|{side}".encode()).hexdigest()
        dest = os.path.join(self.cache_dir, key + ".jpg")
        if not os.path.exists(dest):
            try:
                with open(dest, "wb") as f:
                    f.write(self._encode(path, side))
            except Exception:
                return None
        return "file://" + os.path.abspath(dest)


# ===========================================================================
# SVG (gráficos sin JavaScript, 100 % offline)
# ===========================================================================
def svg_progress(points: list[tuple[int, int]], week_min: int, week_max: int,
                 width: int = 640, height: int = 220, spark: bool = False) -> str:
    """Curva escalonada BBCH vs semana para una sola serie (una variedad)."""
    if not points:
        return ""
    pad_l, pad_r, pad_t, pad_b = (4, 4, 4, 4) if spark else (40, 14, 12, 28)
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    span = max(1, week_max - week_min)

    def x(week):
        return pad_l + (week - week_min) / span * w

    def y(code):
        return pad_t + (1 - code / 99.0) * h

    pts = sorted(points)
    path = f"M{x(pts[0][0]):.1f},{y(pts[0][1]):.1f}"
    for (_w0, c0), (w1, c1) in zip(pts, pts[1:]):
        path += f" L{x(w1):.1f},{y(c0):.1f} L{x(w1):.1f},{y(c1):.1f}"
    parts = [f'<svg class="chart{" spark" if spark else ""}" viewBox="0 0 {width} {height}" '
             f'role="img" aria-label="Progresión BBCH por semana">']
    if not spark:
        for code, name in ((0, "0"), (50, "50"), (60, "60"), (70, "70"), (80, "80"), (99, "99")):
            parts.append(f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" '
                         f'y1="{y(code):.1f}" y2="{y(code):.1f}"/>'
                         f'<text class="tick" x="{pad_l - 6}" y="{y(code) + 4:.1f}" '
                         f'text-anchor="end">{name}</text>')
        step = 1 if span <= 12 else (2 if span <= 24 else 4)
        for wk in range(week_min, week_max + 1, step):
            parts.append(f'<text class="tick" x="{x(wk):.1f}" y="{height - 8}" '
                         f'text-anchor="middle">S{wk}</text>')
    parts.append(f'<path class="line" d="{path}"/>')
    for wk, code in pts:
        r = 2.5 if spark else 4.5
        label = ph.bbch_label(code)
        parts.append(f'<circle class="dot" cx="{x(wk):.1f}" cy="{y(code):.1f}" r="{r}">'
                     f'<title>Semana {wk} · {label}</title></circle>')
    if not spark:
        wk, code = pts[-1]
        parts.append(f'<text class="direct" x="{min(x(wk) + 6, width - pad_r - 2):.1f}" '
                     f'y="{y(code) - 8:.1f}" text-anchor="end">BBCH {code}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ===========================================================================
# Generador
# ===========================================================================
@dataclass
class ReportResult:
    path: str
    title: str
    kind: str
    size_kb: int


class ReportGenerator:
    def __init__(self, db, out_dir: str, preview_dir: str | None = None):
        self.db = db
        self.out_dir = out_dir
        self.preview_dir = preview_dir or os.path.join(os.path.dirname(out_dir), "preview")
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.join(self.preview_dir, "img"), exist_ok=True)
        self.env = Environment(loader=FileSystemLoader(resource_path("templates")),
                               autoescape=select_autoescape(["html"]))
        self.env.filters["bbch"] = lambda c: ph.bbch_label(c, self.db.bbch_names())
        self.env.filters["seq"] = seq_color
        self.env.filters["date_es"] = lambda s: ph.format_date_es(_dt.date.fromisoformat(s[:10]))
        self.env.filters["num"] = self._num
        self.env.globals.update(macro_name=lambda c: ph.MACRO_STAGES.get(ph.macro_of(c), ""),
                                macro_color=lambda c: ph.MACRO_COLORS.get(ph.macro_of(c), "#ccc"),
                                app_name=APP_NAME)

    @staticmethod
    def _num(v, digits: int = 1) -> str:
        if v is None or v == "":
            return "—"
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return f"{f:.0f}" if f.is_integer() else f"{f:.{digits}f}".replace(".", ",")

    # -------------------------------------------------------------- común
    def _images(self, package: str, max_side: int = 960) -> ImageStore:
        return ImageStore(package, max_side=max_side, quality=72,
                          cache_dir=os.path.join(self.preview_dir, "img"))

    def _render(self, template: str, filename: str, kind: str, title: str,
                images: ImageStore, package: str, **ctx) -> ReportResult:
        html = self.env.get_template(template).render(
            title=title, generated=_dt.datetime.now().strftime("%d-%m-%Y %H:%M"),
            package=package, **ctx)
        base = os.path.join(self.out_dir, filename)
        if package == "preview":
            # Vista previa: no se guarda como informe ni se registra en la bitácora.
            path = os.path.join(self.preview_dir, "vista_previa.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return ReportResult(path, title, kind, max(1, len(html.encode()) // 1024))
        if package == "zip":
            path = base + ".zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("index.html", html)
                for name, data in images.files.items():
                    zf.writestr(name, data, compress_type=zipfile.ZIP_STORED)
        else:
            path = base + ".html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        self.db.log("create", "report", None, f"{kind}: {os.path.basename(path)}")
        return ReportResult(path, title, kind, max(1, os.path.getsize(path) // 1024))

    def _photo_ctx(self, photos: dict, images: ImageStore, max_side: int | None = None) -> dict:
        return {kind: images.src(photos[kind]["path"], max_side) if kind in photos else None
                for kind, _ in PHOTO_KINDS}

    def _obs_ctx(self, obs: dict | None) -> dict:
        if not obs:
            return {"code": None, "notes": "", "ai_code": None, "ai_conf": None,
                    "ai_accepted": None, "observed_at": None}
        return {"code": obs["bbch_code"], "notes": obs["notes"] or "",
                "ai_code": obs["ai_code"], "ai_conf": obs["ai_confidence"],
                "ai_accepted": obs["ai_accepted"], "observed_at": obs["observed_at"]}

    def _heatmap(self, season: int, weeks: list[dict], varieties: list[dict]) -> list[dict]:
        matrix = self.db.phenology_matrix(season)
        rows = []
        for v in varieties:
            cells = []
            for w in weeks:
                o = matrix.get((v["id"], w["week_number"]))
                cells.append({"week": w, "code": o["bbch_code"] if o else None})
            rows.append({"variety": v, "cells": cells})
        return rows

    # ------------------------------------------------------ 1. semanal
    def weekly(self, week_id: int, package: str = "html") -> ReportResult:
        week = self.db.get_week(week_id)
        images = self._images(package)
        cards = []
        for row in self.db.week_overview(week_id):
            cards.append({"variety": row["variety"], **self._obs_ctx(row["observation"]),
                          "img": self._photo_ctx(row["photos"], images)})
        done = sum(1 for c in cards if c["code"] is not None)
        title = f"Reporte semanal · Semana {week['week_number']}"
        return self._render(
            "weekly.html", f"semanal_T{week['season']}_S{week['week_number']:02d}",
            "semanal", title, images, package, week=week, cards=cards,
            subtitle=f"{week['label']} · Temporada {week['season']}-{week['season'] + 1}",
            stats={"varieties": len(cards), "done": done,
                   "photos": sum(bool(c["img"]["canopy"]) + bool(c["img"]["detail"]) for c in cards)})

    # ---------------------------------------------- 2. periodo / mensual
    def period(self, season: int, week_from: int, week_to: int, package: str = "html",
               title: str | None = None) -> ReportResult:
        if week_to < week_from:
            week_from, week_to = week_to, week_from
        self.db.ensure_weeks(season, week_to)
        weeks = [w for w in self.db.list_weeks(season) if week_from <= w["week_number"] <= week_to]
        varieties = self.db.list_varieties()
        images = self._images(package, max_side=480)
        heat = self._heatmap(season, weeks, varieties)
        strips = []
        for v in varieties:
            tiles = []
            for w in weeks:
                obs = self.db.get_observation(v["id"], w["id"])
                photos = self.db.get_photos(obs["id"]) if obs else {}
                tiles.append({"week": w, **self._obs_ctx(obs),
                              "img": self._photo_ctx(photos, images, 480)})
            first = next((t["code"] for t in tiles if t["code"] is not None), None)
            last = next((t["code"] for t in reversed(tiles) if t["code"] is not None), None)
            strips.append({"variety": v, "tiles": tiles, "first": first, "last": last,
                           "advance": (last - first) if first is not None and last is not None else None})
        start = _dt.date.fromisoformat(weeks[0]["start_date"])
        end = _dt.date.fromisoformat(weeks[-1]["start_date"]) + _dt.timedelta(days=6)
        title = title or f"Evolución · Semanas {week_from}–{week_to}"
        return self._render(
            "period.html", f"periodo_T{season}_S{week_from:02d}-S{week_to:02d}", "periodo",
            title, images, package, weeks=weeks, heat=heat, strips=strips,
            subtitle=f"Del {ph.format_date_es(start, False)} al {ph.format_date_es(end)} · "
                     f"Temporada {season}-{season + 1}")

    def monthly(self, season: int, year: int, month: int, package: str = "html") -> ReportResult:
        weeks = [w for w in self.db.list_weeks(season)
                 if (d := _dt.date.fromisoformat(w["start_date"])).year == year and d.month == month]
        if not weeks:
            raise ValueError("No hay semanas de muestreo que comiencen en ese mes.")
        nums = [w["week_number"] for w in weeks]
        return self.period(season, min(nums), max(nums), package,
                           title=f"Reporte mensual · {ph.MESES[month - 1].capitalize()} {year}")

    # --------------------------------------------------- 3. por variedad
    def variety(self, variety_id: int, season: int, package: str = "html") -> ReportResult:
        v = self.db.get_variety(variety_id)
        images = self._images(package)
        timeline = []
        for o in self.db.variety_timeline(variety_id, season):
            timeline.append({"week_number": o["week_number"], "week_label": o["week_label"],
                             "start_date": o["start_date"], **self._obs_ctx(o),
                             "img": self._photo_ctx(o["photos"], images)})
        pts = [(t["week_number"], t["code"]) for t in timeline if t["code"] is not None]
        chart = svg_progress(pts, min(p[0] for p in pts), max(max(p[0] for p in pts),
                             min(p[0] for p in pts) + 1)) if pts else ""
        milestones = []
        for code, name in MILESTONES:
            hit = next((p for p in sorted(pts) if p[1] >= code), None)
            milestones.append({"code": code, "name": name, "week": hit[0] if hit else None})
        metrics = self.db.get_metrics(variety_id, season)
        custom = self.db.list_custom_fields(variety_id, season)
        title = f"Ficha fenológica · {v['name']}"
        return self._render(
            "variety.html", f"variedad_{slugify(v['name'])}_T{season}", "variedad", title,
            images, package, variety=v, timeline=timeline, chart=chart, metrics=metrics,
            custom=custom, milestones=milestones,
            subtitle=f"Timeline longitudinal · Temporada {season}-{season + 1}")

    # ------------------------------------------------ 4. matriz global
    def matrix(self, season: int, week_from: int | None = None, week_to: int | None = None,
               package: str = "html") -> ReportResult:
        weeks = self.db.list_weeks(season)
        if week_from is not None:
            weeks = [w for w in weeks if w["week_number"] >= week_from]
        if week_to is not None:
            weeks = [w for w in weeks if w["week_number"] <= week_to]
        if not weeks:
            raise ValueError("No hay semanas de muestreo registradas en la temporada.")
        varieties = self.db.list_varieties()
        heat = self._heatmap(season, weeks, varieties)
        images = self._images(package, max_side=360)

        # Precocidad relativa: diferencia media vs. el promedio de las variedades por semana.
        week_means = {}
        for i, w in enumerate(weeks):
            vals = [r["cells"][i]["code"] for r in heat if r["cells"][i]["code"] is not None]
            if len(vals) >= 2:
                week_means[w["week_number"]] = sum(vals) / len(vals)
        rows = []
        wmin, wmax = weeks[0]["week_number"], weeks[-1]["week_number"]
        for r in heat:
            pts = [(c["week"]["week_number"], c["code"]) for c in r["cells"] if c["code"] is not None]
            deltas = [code - week_means[wk] for wk, code in pts if wk in week_means]
            delta = round(sum(deltas) / len(deltas), 1) + 0.0 if deltas else None  # evita «-0.0»
            hits = []
            for code, name in MILESTONES:
                hit = next((p for p in sorted(pts) if p[1] >= code), None)
                hits.append(hit[0] if hit else None)
            gallery = []
            for c in r["cells"]:
                obs = self.db.get_observation(r["variety"]["id"], c["week"]["id"])
                photos = self.db.get_photos(obs["id"]) if obs else {}
                gallery.append({"week": c["week"], "code": c["code"],
                                "src": images.src(photos["detail"]["path"]) if "detail" in photos else None})
            rows.append({"variety": r["variety"], "delta": delta, "hits": hits,
                         "last": pts[-1][1] if pts else None,
                         "spark": svg_progress(pts, wmin, max(wmax, wmin + 1), 160, 40, spark=True)
                         if pts else "", "gallery": gallery})
        max_abs = max([abs(r["delta"]) for r in rows if r["delta"] is not None] or [1.0]) or 1.0
        ranking = sorted([r for r in rows if r["delta"] is not None], key=lambda r: -r["delta"])
        title = "Matriz comparativa inter-varietal"
        return self._render(
            "matrix.html", f"matriz_T{season}", "matriz", title, images, package,
            weeks=weeks, heat=heat, rows=rows, ranking=ranking, max_abs=max_abs,
            milestones=MILESTONES,
            subtitle=f"Avance fenológico relativo · Temporada {season}-{season + 1} · "
                     f"Semanas {wmin}–{wmax}",
            data_json=json.dumps([{"variedad": r["variety"]["name"],
                                   "bbch": [c["code"] for c in h["cells"]]}
                                  for r, h in zip(rows, heat)], ensure_ascii=False).replace("</", "<\\/"))


# ===========================================================================
# Resumen de contenido (para la pestaña «Vista previa»)
# ===========================================================================
EST_KB_PER_PHOTO = 75   # JPEG ~960 px q72 embebido en Base64


def _missing_items(obs, photos) -> list[str]:
    out = []
    if "canopy" not in photos:
        out.append("foto canopia")
    if "detail" not in photos:
        out.append("foto detalle")
    if not obs or obs["bbch_code"] is None:
        out.append("estado BBCH")
    return out


def report_summary(db, kind: str, season: int, week_id: int | None = None,
                   week_from: int | None = None, week_to: int | None = None,
                   variety_id: int | None = None) -> dict:
    """Qué incluirá el informe y qué datos faltan (sin generar nada)."""
    varieties = db.list_varieties()
    weeks = db.list_weeks(season)
    if kind == "weekly":
        weeks = [w for w in weeks if w["id"] == week_id]
    elif kind == "period":
        weeks = [w for w in weeks if week_from <= w["week_number"] <= week_to]
    elif kind == "variety":
        varieties = [v for v in varieties if v["id"] == variety_id]
    cells = complete = photos = bbch = notes = 0
    missing: list[str] = []
    for w in weeks:
        for v in varieties:
            obs = db.get_observation(v["id"], w["id"])
            ph_ = db.get_photos(obs["id"]) if obs else {}
            cells += 1
            photos += len(ph_)
            bbch += bool(obs and obs["bbch_code"] is not None)
            notes += bool(obs and (obs["notes"] or "").strip())
            lack = _missing_items(obs, ph_)
            if not lack:
                complete += 1
            elif len(missing) < 40:
                missing.append(f"{v['name']} · S{w['week_number']}: falta {', '.join(lack)}")
    total_missing = cells - complete
    return {"kind": kind, "varieties": len(varieties), "weeks": len(weeks), "cells": cells,
            "complete": complete, "photos": photos, "photos_expected": cells * 2,
            "bbch": bbch, "notes": notes, "missing": missing,
            "missing_more": max(0, total_missing - len(missing)),
            "est_kb": 40 + photos * EST_KB_PER_PHOTO}
