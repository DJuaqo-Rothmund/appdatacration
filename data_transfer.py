"""
data_transfer.py
================
Copia de seguridad completa, restauración y rescate de datos de versiones anteriores.

* `full_backup(db)`: ZIP con la base SQLite + todas las fotos + manifest.json.
* `restore(db, path)`: acepta ese ZIP o el «.sqlite3» de «Respaldar base de datos»
  (también el de versiones anteriores; el esquema se migra al abrirlo). Antes de
  reemplazar se guarda una copia automática de los datos actuales.
* `relink_photos(db)`: las fotos cuyo archivo ya no existe (p. ej. tras reinstalar)
  se vuelven a enlazar con los originales de la carpeta pública «Imágenes de
  Fenología», que se nombran «variedad_S03_detalle_AAAA-MM-DD_HHMMSS.jpg».
* `import_reports(db, paths)`: lee informes semanales HTML/ZIP generados por la app
  (variedad, estado BBCH, notas y fotos) y completa lo que falte.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser

import phenology as ph
from android_bridge import PUBLIC_PHOTO_DIR, store_photo
from platform_utils import IS_ANDROID, data_subdir, slugify

KIND_LABEL = {"canopy": "canopia", "detail": "detalle"}
DB_NAME = "fenorubus.sqlite3"


def _stamp() -> str:
    return f"{_dt.datetime.now():%Y%m%d_%H%M%S}"


def public_photo_dirs() -> list[str]:
    if IS_ANDROID:
        try:
            from jnius import autoclass  # type: ignore
            Environment = autoclass("android.os.Environment")
            base = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_PICTURES).getAbsolutePath()
        except Exception:  # noqa: BLE001
            base = "/storage/emulated/0/Pictures"
    else:
        base = os.path.join(os.path.expanduser("~"), "Pictures")
    # «FenoRubus»: carpeta de las primeras versiones (fotos «FenoRubus_AAAAMMDD_HHMMSS.jpg»).
    return [os.path.join(base, PUBLIC_PHOTO_DIR), os.path.join(base, "FenoRubus")]


def _checkpoint_copy(db, dest: str) -> str:
    with db._lock:
        db.conn.commit()
        out = sqlite3.connect(dest)
        try:
            db.conn.backup(out)
        finally:
            out.close()
    return dest


# ------------------------------------------------------------------ respaldo
# Otras imágenes que viajan en la copia: (tabla, clave del manifiesto, carpeta en el ZIP)
_EXTRA_IMAGES = (("variety_attachments", "attachments", "adjuntos"),
                 ("measure_entries", "measures", "mediciones"))


def full_backup(db, dest_dir: str | None = None) -> str:
    """ZIP con la base y las fotos. Devuelve la ruta."""
    dest_dir = dest_dir or data_subdir("backups")
    path = os.path.join(dest_dir, f"PhenoRubus_respaldo_{_stamp()}.zip")
    tmp_db = _checkpoint_copy(db, os.path.join(data_subdir("tmp"), DB_NAME))
    manifest = {"app": "PhenoRubus", "created": _dt.datetime.now().isoformat(timespec="seconds"),
                "photos": {}, "attachments": {}, "measures": {}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_db, DB_NAME)
        for r in db.query("SELECT id, path FROM photos"):
            if os.path.exists(r["path"]):
                arc = f"fotos/{r['id']}_{os.path.basename(r['path'])}"
                zf.write(r["path"], arc, compress_type=zipfile.ZIP_STORED)  # JPEG ya comprimido
                manifest["photos"][str(r["id"])] = arc
        for table, key, folder in _EXTRA_IMAGES:
            for r in db.query(f"SELECT id, path FROM {table} WHERE path IS NOT NULL"):
                if os.path.exists(r["path"]):
                    arc = f"{folder}/{r['id']}_{os.path.basename(r['path'])}"
                    zf.write(r["path"], arc, compress_type=zipfile.ZIP_STORED)
                    manifest[key][str(r["id"])] = arc
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    os.remove(tmp_db)
    db.log("backup", "full", None, f"{os.path.basename(path)} · {len(manifest['photos'])} fotos")
    return path


# -------------------------------------------------------------- restauración
@dataclass
class RestoreResult:
    observations: int = 0
    photos: int = 0
    photos_restored: int = 0      # desde el ZIP
    photos_relinked: int = 0      # desde «Imágenes de Fenología»
    photos_missing: int = 0
    safety_copy: str = ""
    missing: list = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.observations} registros", f"{self.photos} fotos en la base"]
        if self.photos_restored:
            parts.append(f"{self.photos_restored} fotos restauradas del respaldo")
        if self.photos_relinked:
            parts.append(f"{self.photos_relinked} recuperadas de la carpeta de fotos del teléfono")
        if self.photos_missing:
            parts.append(f"{self.photos_missing} sin archivo")
        return " · ".join(parts)


def _validate(path: str) -> None:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("El archivo no es una base de datos válida") from exc
    if not {"varieties", "observations", "photos"} <= names:
        raise ValueError("El archivo no es un respaldo de PhenoRubus")


def restore(db, path: str, progress=None) -> RestoreResult:
    """Reemplaza los datos actuales por los del respaldo (.zip o .sqlite3)."""
    tmp = data_subdir("tmp", "restore")
    manifest, extra = {}, {}
    zf = None
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        names = zf.namelist()
        db_name = next((n for n in names if n.endswith((".sqlite3", ".db"))), None)
        if db_name is None:
            raise ValueError("El ZIP no contiene una base de datos (use «Importar informes» para ZIP de informes)")
        src = os.path.join(tmp, "restore.sqlite3")
        with open(src, "wb") as f:
            f.write(zf.read(db_name))
        if "manifest.json" in names:
            full = json.loads(zf.read("manifest.json"))
            manifest = full.get("photos", {})
            extra = {key: full.get(key, {}) for _t, key, _f in _EXTRA_IMAGES}
    else:
        src = path
    _validate(src)

    res = RestoreResult()
    res.safety_copy = _checkpoint_copy(
        db, os.path.join(data_subdir("backups"), f"antes_de_restaurar_{_stamp()}.sqlite3"))
    # Se conserva la conexión a Drive del teléfono actual.
    keep = {k: db.get_setting(k) for k in ("drive_enabled", "drive_wifi_only", "drive_status",
                                          "drive_folders") if db.get_setting(k) is not None}
    with db._lock:
        s = sqlite3.connect(src)
        try:
            s.backup(db.conn)
        finally:
            s.close()
        db.conn.execute("PRAGMA foreign_keys = ON")
        db.init_schema()   # migra respaldos de versiones anteriores
    for k, v in keep.items():
        db.set_setting(k, v)

    rows = db.query("SELECT id, path FROM photos")
    for i, r in enumerate(rows):
        if progress:
            progress(i, len(rows))
        arc = manifest.get(str(r["id"]))
        if zf is not None and arc and not os.path.exists(r["path"]):
            dest = os.path.join(data_subdir("photos", "restauradas"), os.path.basename(arc))
            with zf.open(arc) as fin, open(dest, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            _set_path(db, r["id"], dest)
            res.photos_restored += 1
    for table, key, folder in _EXTRA_IMAGES:
        for r in db.query(f"SELECT id, path FROM {table} WHERE path IS NOT NULL"):
            arc = extra.get(key, {}).get(str(r["id"]))
            if zf is not None and arc and not os.path.exists(r["path"]):
                dest = os.path.join(data_subdir("photos", folder), os.path.basename(arc))
                with zf.open(arc) as fin, open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                db.execute(f"UPDATE {table} SET path=? WHERE id=?", (dest, r["id"]))
    if zf is not None:
        zf.close()
    rel = relink_photos(db)
    res.photos_relinked, res.photos_missing, res.missing = rel["relinked"], rel["missing"], rel["items"]
    res.observations = db.query_one("SELECT COUNT(*) AS n FROM observations")["n"]
    res.photos = len(rows)
    db.log("restore", "database", None, res.summary())
    return res


def _set_path(db, photo_id: int, path: str) -> None:
    db.execute("UPDATE photos SET path=? WHERE id=?", (path, photo_id))
    db.execute("UPDATE ai_references SET image_path=? WHERE photo_id=?", (path, photo_id))


# ------------------------------------------------------- reenlazar originales
_NAME_RE = re.compile(r"^(?P<prefix>.+_S\d{2}_(?:canopia|detalle))_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{6})\.jpe?g$",
                      re.IGNORECASE)


# Nombres sin variedad (primeras versiones o cámara sin datos del registro): solo la hora.
_TIME_RES = [(re.compile(r"^FenoRubus_(\d{8}_\d{6})\.jpe?g$", re.I), "%Y%m%d_%H%M%S"),
             (re.compile(r"^PhenoRubus_(\d{4}-\d{2}-\d{2}_\d{6})\.jpe?g$", re.I), "%Y-%m-%d_%H%M%S")]
# La foto se nombra al abrir la cámara y el registro se guarda al volver de ella.
TIME_WINDOW_BEFORE = _dt.timedelta(minutes=20)
TIME_WINDOW_AFTER = _dt.timedelta(minutes=2)


# Nombre actual «28092026-C11G.jpg» («-2», «-3»… o « (1)» si Android lo duplicó).
_DATED_RE = re.compile(r"^(?P<key>\d{8}-[A-Za-z0-9]+[GD])(?:-(?P<seq>\d+))?(?: \((?P<dup>\d+)\))?\.jpe?g$",
                       re.IGNORECASE)


def _scan_public(dirs: list[str]):
    index: dict[str, list] = {}
    dated: dict[str, list] = {}
    timed: list[tuple[_dt.datetime, str]] = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            m = _DATED_RE.match(name)
            if m:
                order = (int(m["seq"] or 1), int(m["dup"] or 0))
                dated.setdefault(m["key"].lower(), []).append((order, os.path.join(d, name)))
                continue
            m = _NAME_RE.match(name)
            if m:
                when = _dt.datetime.strptime(m["stamp"], "%Y-%m-%d_%H%M%S")
                index.setdefault(m["prefix"].lower(), []).append((when, os.path.join(d, name)))
                continue
            for rx, fmt in _TIME_RES:
                t = rx.match(name)
                if t:
                    timed.append((_dt.datetime.strptime(t[1], fmt), os.path.join(d, name)))
                    break
    for v in dated.values():
        v.sort()
    return index, dated, timed


def _when(text) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat((text or "")[:19])
    except ValueError:
        return None


def relink_photos(db, dirs: list[str] | None = None) -> dict:
    """Vuelve a enlazar fotos sin archivo con los originales de la carpeta pública."""
    index, dated, timed = _scan_public(dirs if dirs is not None else public_photo_dirs())
    used: set[str] = set()
    relinked = missing = 0
    items = []
    rows = db.query(
        "SELECT p.id, p.path, p.kind, p.source, p.captured_at, v.name AS variety, v.code AS vcode, "
        "v.sector AS vsector, v.irrigation AS virrigation, "
        "w.week_number, w.season, w.start_date "
        "FROM photos p JOIN observations o ON o.id = p.observation_id "
        "JOIN varieties v ON v.id = o.variety_id JOIN sampling_weeks w ON w.id = o.week_id "
        "ORDER BY p.id")

    def base_name(r):
        return ph.photo_basename({"name": r["variety"], "code": r["vcode"], "sector": r["vsector"],
                                  "irrigation": r["virrigation"]}, r["start_date"], r["kind"])

    def link(r, src):
        used.add(src)
        dest_dir = data_subdir("photos", f"T{r['season']}", f"S{r['week_number']:02d}")
        _set_path(db, r["id"], store_photo(src, dest_dir, base_name(r), exact=True))

    pending = []
    for r in rows:
        if os.path.exists(r["path"]):
            continue
        # 1) Nombre actual «28092026-C11G» (fecha de la semana + variedad + G/D).
        cands = [c for c in dated.get(base_name(r).lower(), []) if c[1] not in used]
        if cands:
            link(r, cands[0][1])
            relinked += 1
            continue
        # 2) Nombre descriptivo «variedad_S03_detalle_fecha» (versiones de septiembre).
        prefix = f"{slugify(r['variety'])}_S{r['week_number']:02d}_{KIND_LABEL.get(r['kind'], r['kind'])}"
        cands = [c for c in index.get(prefix.lower(), []) if c[1] not in used]
        if cands:
            ref = _when(r["captured_at"])
            link(r, min(cands, key=lambda c: abs((c[0] - ref).total_seconds()) if ref else 0)[1])
            relinked += 1
        else:
            pending.append(r)
    # 3) Solo fecha y hora (primeras versiones): la foto de cámara más cercana antes de
    #    guardarse el registro; se asignan primero las parejas más cercanas.
    pairs = []
    for r in pending:
        ref = _when(r["captured_at"])
        if ref is None or (r["source"] or "camera") != "camera":
            continue
        for when, path in timed:
            if ref - TIME_WINDOW_BEFORE <= when <= ref + TIME_WINDOW_AFTER:
                pairs.append((abs((ref - when).total_seconds()), r["id"], path))
    done_rows: set[int] = set()
    by_id = {r["id"]: r for r in pending}
    for _gap, rid, path in sorted(pairs):
        if rid in done_rows or path in used:
            continue
        link(by_id[rid], path)
        done_rows.add(rid)
        relinked += 1
    for r in pending:
        if r["id"] not in done_rows:
            missing += 1
            items.append(f"{r['variety']} · S{r['week_number']} · {KIND_LABEL.get(r['kind'], r['kind'])}")
    return {"relinked": relinked, "missing": missing, "items": items}


# --------------------------------------------------- informes semanales (HTML)
class _WeeklyParser(HTMLParser):
    """Extrae semana, temporada y tarjetas por variedad de un informe semanal."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1 = ""
        self.sub = ""
        self.cards: list[dict] = []
        self._stack: list[str] = []
        self._card = None
        self._grab = None   # "h1" | "sub" | "h3" | "chip" | "notes"
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "article" and "vcard" in cls:
            self._card = {"name": "", "chip": "", "notes": "", "photos": {}}
            self.cards.append(self._card)
        elif tag == "h1":
            self._grab = "h1"
        elif tag == "div" and "sub" in cls and not self.sub and self._card is None:
            self._grab = "sub"
        elif self._card is not None:
            if tag == "h3":
                self._grab = "h3"
            elif tag == "span" and "chip" in cls and not self._card["chip"]:
                self._grab, self._depth = "chip", 0
            elif tag == "div" and "notes" in cls:
                self._grab = "notes"
            elif tag == "img" and a.get("alt") in ("Canopia", "Detalle"):
                kind = "canopy" if a["alt"] == "Canopia" else "detail"
                self._card["photos"].setdefault(kind, a.get("src") or "")
        if self._grab == "chip" and tag == "span":
            self._depth += 1

    def handle_endtag(self, tag):
        if tag == "article":
            self._card = None
        if self._grab == "chip" and tag == "span":
            self._depth -= 1
            if self._depth <= 0:
                self._grab = None
        elif self._grab in ("h1", "h3") and tag in ("h1", "h3"):
            self._grab = None
        elif self._grab in ("sub", "notes") and tag == "div":
            self._grab = None

    def handle_data(self, data):
        g = self._grab
        if g == "h1":
            self.h1 += data
        elif g == "sub":
            self.sub += data
        elif g == "h3" and self._card is not None:
            self._card["name"] += data
        elif g == "chip" and self._card is not None:
            self._card["chip"] += data
        elif g == "notes" and self._card is not None:
            self._card["notes"] += data


@dataclass
class ReportImport:
    reports: int = 0
    observations: int = 0
    bbch: int = 0
    photos: int = 0
    skipped: list = field(default_factory=list)

    def summary(self) -> str:
        s = (f"{self.reports} informes · {self.observations} registros · {self.bbch} estados BBCH · "
             f"{self.photos} fotos")
        if self.skipped:
            s += f" · {len(self.skipped)} omitidos"
        return s


def _report_sources(path: str) -> list[tuple[str, str, zipfile.ZipFile | None]]:
    """[(nombre, html, zip)] de un .html o de un .zip (informe empaquetado o varios)."""
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        out = []
        for n in zf.namelist():
            if n.lower().endswith((".html", ".htm")):
                out.append((n, zf.read(n).decode("utf-8", "replace"), zf))
            elif n.lower().endswith(".zip"):  # ZIP de ZIPs
                inner = os.path.join(data_subdir("tmp", "restore"), os.path.basename(n))
                with open(inner, "wb") as f:
                    f.write(zf.read(n))
                out += _report_sources(inner)
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        return [(os.path.basename(path), f.read(), None)]


def _image_bytes(src: str, zf, html_name: str) -> bytes | None:
    if src.startswith("data:"):
        try:
            return base64.b64decode(src.split(",", 1)[1])
        except Exception:  # noqa: BLE001
            return None
    if zf is not None and src:
        base = os.path.dirname(html_name)
        for cand in (os.path.normpath(os.path.join(base, src)).replace("\\", "/"), src):
            try:
                return zf.read(cand)
            except KeyError:
                continue
    return None


def import_reports(db, paths: list[str], progress=None) -> ReportImport:
    """Completa registros a partir de informes semanales (no sobrescribe lo existente)."""
    res = ReportImport()
    varieties = {v["name"].lower(): v for v in db.list_varieties(include_archived=True)}
    sources = []
    for p in paths:
        sources += _report_sources(p)
    tmp = os.path.join(data_subdir("tmp", "restore"), "informe.jpg")
    for i, (name, html, zf) in enumerate(sources):
        if progress:
            progress(i, len(sources))
        parser = _WeeklyParser()
        parser.feed(html)
        wk = re.search(r"Semana\s+(\d+)", parser.h1)
        year = re.search(r"año\s+(\d{4})", parser.h1, flags=re.I)      # «Semana 37, año 2026»
        season = re.search(r"Temporada\s+(\d{4})", parser.sub)         # formato anterior
        week = None
        if "semanal" in parser.h1.lower() and wk and parser.cards:
            if year:
                try:   # domingo de esa semana ISO: siempre cae dentro de la semana de muestreo
                    week = db.week_for_date(_dt.date.fromisocalendar(int(year[1]), int(wk[1]), 7))
                except ValueError:
                    week = None
            elif season:
                week = db.ensure_week(int(season[1]), int(wk[1]))
        if week is None:
            res.skipped.append(f"{name}: no es un informe semanal")
            continue
        res.reports += 1
        for card in parser.cards:
            vname = card["name"].strip()
            if not vname:
                continue
            v = varieties.get(vname.lower())
            if v is None:
                v = db.get_variety(db.add_variety(vname))
                varieties[vname.lower()] = v
            obs = db.get_or_create_observation(v["id"], week["id"])
            fields = {}
            code = ph.parse_bbch_code(card["chip"])
            if code is not None and obs["bbch_code"] is None:
                fields.update(bbch_code=code, bbch_label=ph.bbch_label(code, db.bbch_names()))
                res.bbch += 1
            notes = " ".join(card["notes"].split())
            if notes and not (obs["notes"] or "").strip():
                fields["notes"] = notes
            if fields:
                db.update_observation(obs["id"], **fields)
            have = db.get_photos(obs["id"])
            missing_file = {k: p for k, p in have.items() if not os.path.exists(p["path"])}
            for kind, src in card["photos"].items():
                if kind in have and kind not in missing_file:
                    continue
                data = _image_bytes(src, zf, name)
                if not data:
                    continue
                with open(tmp, "wb") as f:
                    f.write(data)
                dest_dir = data_subdir("photos", f"T{week['season']}", f"S{week['week_number']:02d}")
                stored = store_photo(tmp, dest_dir, ph.photo_basename(v, week["start_date"], kind),
                                     exact=True)
                if kind in missing_file:
                    _set_path(db, missing_file[kind]["id"], stored)
                else:
                    db.add_photo(obs["id"], kind, stored, source="informe")
                res.photos += 1
            if fields or card["photos"]:
                res.observations += 1
    db.log("import", "reports", None, res.summary())
    return res
