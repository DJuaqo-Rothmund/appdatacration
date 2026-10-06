"""
photo_rename.py
===============
Puesta al día de los nombres de foto ya existentes:

* fecha «ddmmaaaa-…» (hasta la 1.1.30) -> «aaaammdd-…»;
* código del ensayo (desde la 1.1.34): 20260928-C11G.jpg -> 20260928-NV-C11G.jpg.

Los dos formatos no se confunden: en el nuevo, los dígitos 3-4 son el año («26»),
que nunca es un mes válido; en el antiguo, «2809» nunca es un año válido.

* `migrate_local(db)`: renombra los archivos guardados por la app y actualiza sus
  rutas en la base (fotos, adjuntas, mediciones, referencias de la IA y cola de Drive).
* `new_name(nombre)`: nombre nuevo o None si no tiene el formato antiguo (lo usan
  también la galería del teléfono y Google Drive).
"""
from __future__ import annotations

import datetime as _dt
import os
import re

_LEGACY = re.compile(r"^(?P<d>\d{2})(?P<m>\d{2})(?P<y>\d{4})(?P<rest>-.+)$")

# (tabla, columna con la ruta del archivo)
PATH_COLUMNS = (("photos", "path"), ("variety_attachments", "path"), ("measure_entries", "path"),
                ("ai_references", "image_path"), ("drive_queue", "path"))


_DATED = re.compile(r"^(?P<date>\d{8})-(?P<rest>.+)$")


def _fix_date(name: str) -> str:
    m = _LEGACY.match(name or "")
    if not m:
        return name
    try:
        day = _dt.date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return name
    if not 2000 <= day.year <= 2099:
        return name
    return f"{day:%Y%m%d}{m['rest']}"


def new_name(name: str, code: str = "", known=()) -> str | None:
    """Nombre puesto al día o None si ya está bien.
    «28092026-C11G.jpg» -> «20260928-C11G.jpg» (y con code="NV": «20260928-NV-C11G.jpg»).
    known: códigos de ensayo existentes (un nombre que ya empieza con uno no se toca)."""
    out = _fix_date(name or "")
    if code:
        m = _DATED.match(out)
        if m and m["date"][:2] == "20" and not any(m["rest"].startswith(f"{k}-")
                                                   for k in set(known) | {code}):
            out = f"{m['date']}-{code}-{m['rest']}"
    return out if out != name else None


def known_codes(db) -> set[str]:
    """Códigos de todos los ensayos y predios del teléfono (base común)."""
    try:
        return {r["code"] for r in db.ai_db.query("SELECT code FROM workspaces")}
    except Exception:  # noqa: BLE001 - base sin registro de ensayos (pruebas, versiones previas)
        return set()


def _free(path: str) -> str:
    """Si ya existe un archivo con el nombre nuevo, agrega «-2», «-3»…"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}-{n}{ext}"):
        n += 1
    return f"{stem}-{n}{ext}"


def migrate_local(db, code: str | None = None) -> dict:
    """Renombra los archivos de la app con nombre antiguo y actualiza todas las rutas.
    Idempotente: se puede ejecutar en cada inicio (lo ya renombrado no se toca)."""
    code = db.code if code is None else code
    known = known_codes(db)
    moved: dict[str, str] = {}      # ruta antigua -> ruta nueva
    renamed = missing = 0
    for table, col in PATH_COLUMNS:
        try:
            rows = db.query(f"SELECT id, {col} AS p FROM {table} WHERE {col} IS NOT NULL")
        except Exception:  # noqa: BLE001 - tabla de una versión sin esa columna
            continue
        for r in rows:
            old = r["p"]
            nn = new_name(os.path.basename(old), code, known)
            if not nn:
                continue
            dest = moved.get(old)
            if dest is None:
                if os.path.exists(old):
                    dest = _free(os.path.join(os.path.dirname(old), nn))
                    try:
                        os.replace(old, dest)
                    except OSError:
                        continue
                    renamed += 1
                else:   # el archivo no está (se recuperará con su nombre nuevo)
                    dest = os.path.join(os.path.dirname(old), nn)
                    missing += 1
                moved[old] = dest
            db.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (dest, r["id"]))
    # Cola de Drive: los pendientes se suben ya con el nombre nuevo.
    for r in db.query("SELECT id, name FROM drive_queue WHERE status != 'done'"):
        head, _, base = (r["name"] or "").rpartition("/")
        nn = new_name(base, code, known)
        if nn:
            db.execute("UPDATE drive_queue SET name=? WHERE id=?", (f"{head}/{nn}" if head else nn, r["id"]))
    # Memoria de la IA (base común): rutas de las fotos de este ensayo.
    if moved and db.ai_db is not db:
        for r in db.ai_db.query("SELECT id, image_path FROM ai_references WHERE image_path IS NOT NULL"):
            if r["image_path"] in moved:
                db.ai_db.execute("UPDATE ai_references SET image_path=? WHERE id=?",
                                 (moved[r["image_path"]], r["id"]))
    if renamed or missing:
        db.log("rename", "photos", None, f"{renamed} fotos con fecha aaaammdd y código {code or '—'}")
    return {"renamed": renamed, "missing": missing}
