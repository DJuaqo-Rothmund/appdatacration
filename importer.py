"""
importer.py
===========
Importación de la base de datos de temporadas anteriores.

Formato: CSV (coma o punto y coma, UTF-8) o un ZIP que contenga el CSV y las
fotos. Columnas (sin importar mayúsculas ni tildes; solo «variedad» y «fecha»
o «semana» son obligatorias):

    temporada, fecha, semana, variedad, bbch, foto_canopia, foto_detalle, notas,
    rendimiento_historico, unidad_rendimiento, canas_basales, laterales, latitud, longitud

* temporada: 2023 o «2023-2024» (si falta, se deduce de la fecha).
* fecha: AAAA-MM-DD o DD-MM-AAAA (se asigna a su semana de muestreo).
* semana: número de semana de muestreo (alternativa a la fecha).
* bbch: 65, «BBCH 65» o «65 - Plena floración».
* foto_*: nombre del archivo dentro del ZIP (varias separadas por «|»).

Las fotos de detalle con estado BBCH pueden usarse para entrenar la IA.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field

import phenology as ph
from android_bridge import store_photo
from platform_utils import data_subdir

TEMPLATE_HEADER = ["temporada", "fecha", "semana", "variedad", "bbch", "foto_canopia",
                   "foto_detalle", "notas", "rendimiento_historico", "unidad_rendimiento",
                   "canas_basales", "laterales", "latitud", "longitud"]
TEMPLATE_ROWS = [
    ["2024", "2024-10-18", "", "Meeker", "57", "meeker_s07_canopia.jpg",
     "meeker_s07_detalle.jpg", "Botones con sépalos abiertos", "1.6", "kg/planta", "7", "18",
     "-33.451230", "-70.662410"],
    ["2024", "", "10", "Regina", "BBCH 65", "", "regina_s10_detalle.jpg|regina_s10_detalle2.jpg",
     "Plena floración", "", "", "", "", "", ""],
    ["2023-2024", "12-12-2023", "", "Código 11", "81", "", "", "Primeros frutos rosados", "",
     "", "", "", "", ""],
]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z0-9]+", "_", "".join(ch for ch in text if not unicodedata.combining(ch))
                  .lower()).strip("_")


ALIASES = {"ano": "temporada", "season": "temporada", "fecha_muestreo": "fecha", "date": "fecha",
           "semana_muestreo": "semana", "week": "semana", "variety": "variedad", "cultivar": "variedad",
           "estado": "bbch", "estado_bbch": "bbch", "foto_general": "foto_canopia",
           "foto_planta": "foto_canopia", "foto_macro": "foto_detalle", "observaciones": "notas",
           "comentarios": "notas", "canas": "canas_basales", "rendimiento": "rendimiento_historico",
           "lat": "latitud", "latitude": "latitud", "lon": "longitud", "lng": "longitud",
           "longitude": "longitud"}


@dataclass
class ImportResult:
    rows: int = 0
    observations: int = 0
    photos: int = 0
    references: int = 0
    varieties_created: list = field(default_factory=list)
    seasons: set = field(default_factory=set)
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.rows} filas", f"{self.observations} registros", f"{self.photos} fotos",
                 f"{self.references} referencias IA"]
        if self.varieties_created:
            parts.append(f"variedades nuevas: {', '.join(self.varieties_created)}")
        if self.seasons:
            parts.append("temporadas: " + ", ".join(f"{s}-{s + 1}" for s in sorted(self.seasons)))
        return " · ".join(parts)


def write_template(path: str) -> str:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(TEMPLATE_HEADER)
        w.writerows(TEMPLATE_ROWS)
    return path


def _parse_date(text: str) -> _dt.date | None:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _num(text: str):
    text = (text or "").strip().replace(",", ".")
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _read_rows(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for r in reader:
        rows.append({ALIASES.get(_norm(k), _norm(k)): (v or "").strip()
                     for k, v in r.items() if k})
    return rows


def import_file(db, path: str, classifier=None, train_ai: bool = True, progress=None) -> ImportResult:
    """Importa un CSV o ZIP. `classifier` (opcional) recibe las fotos etiquetadas."""
    res = ImportResult()
    images: dict[str, bytes | str] = {}
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                raise ValueError("El ZIP no contiene ningún archivo .csv")
            rows = _read_rows(zf.read(csvs[0]))
            for n in zf.namelist():
                if n.lower().endswith((".jpg", ".jpeg", ".png")):
                    images[os.path.basename(n).lower()] = zf.read(n)
    else:
        with open(path, "rb") as f:
            rows = _read_rows(f.read())
        base = os.path.dirname(os.path.abspath(path))
        for n in os.listdir(base):
            if n.lower().endswith((".jpg", ".jpeg", ".png")):
                images[n.lower()] = os.path.join(base, n)

    varieties = {v["name"].lower(): v for v in db.list_varieties(include_archived=True)}
    tmp_dir = data_subdir("tmp", "import")
    for i, r in enumerate(rows, start=2):  # fila 1 = encabezado
        res.rows += 1
        if progress:
            progress(res.rows, len(rows))
        name = r.get("variedad", "")
        if not name:
            res.errors.append(f"Fila {i}: falta la variedad")
            continue
        date = _parse_date(r.get("fecha", ""))
        season_txt = re.match(r"\d{4}", r.get("temporada", ""))
        season = int(season_txt.group()) if season_txt else (ph.season_of(date) if date else None)
        if season is None:
            res.errors.append(f"Fila {i}: falta temporada o fecha")
            continue
        if r.get("semana", "").isdigit():
            week = db.ensure_week(season, int(r["semana"]))
        elif date:
            week = db.week_for_date(date, season)
        else:
            res.errors.append(f"Fila {i}: falta la fecha o la semana")
            continue
        v = varieties.get(name.lower())
        if v is None:
            vid = db.add_variety(name)
            v = db.get_variety(vid)
            varieties[name.lower()] = v
            res.varieties_created.append(name)
        res.seasons.add(season)
        obs = db.get_or_create_observation(v["id"], week["id"])
        code = ph.parse_bbch_code(r.get("bbch", ""))
        fields = {}
        if code is not None:
            fields.update(bbch_code=code, bbch_label=ph.bbch_label(code, db.bbch_names()))
        if r.get("notas"):
            fields["notes"] = r["notas"]
        if date:
            fields["observed_at"] = date.isoformat()
        lat, lon = _num(r.get("latitud", "")), _num(r.get("longitud", ""))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            fields.update(latitude=lat, longitude=lon, gps_source="histórico")
        if fields:
            db.update_observation(obs["id"], **fields)
        res.observations += 1
        metrics = {k: _num(r.get(src, "")) for k, src in (("historical_yield", "rendimiento_historico"),
                                                         ("basal_canes", "canas_basales"),
                                                         ("laterals", "laterales"))}
        metrics = {k: val for k, val in metrics.items() if val is not None}
        if r.get("unidad_rendimiento"):
            metrics["historical_yield_unit"] = r["unidad_rendimiento"]
        if metrics:
            db.save_metrics(v["id"], season, **metrics)
        for kind, col in (("canopy", "foto_canopia"), ("detail", "foto_detalle")):
            for fname in [x.strip() for x in r.get(col, "").split("|") if x.strip()]:
                src = images.get(os.path.basename(fname).lower())
                if src is None:
                    res.errors.append(f"Fila {i}: no se encontró la foto «{fname}»")
                    continue
                if isinstance(src, bytes):
                    tmp = os.path.join(tmp_dir, "import.jpg")
                    with open(tmp, "wb") as f:
                        f.write(src)
                    src = tmp
                dest_dir = data_subdir("photos", f"T{season}", f"S{week['week_number']:02d}")
                stored = store_photo(src, dest_dir, ph.photo_basename(v, week["start_date"], kind, trial=db.code),
                                     exact=True)
                pid = db.add_photo(obs["id"], kind, stored, source="histórico",
                                   captured_at=(date.isoformat() + "T12:00:00") if date else None)
                res.photos += 1
                if train_ai and classifier is not None and kind == "detail" and code is not None:
                    classifier.add_reference(stored, code, photo_id=pid)
                    res.references += 1
    db.log("import", "historical", None, res.summary())
    return res
