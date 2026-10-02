"""
photo_rename.py
===============
Paso único de nombres de foto «ddmmaaaa-…» (hasta la 1.1.30) a «aaaammdd-…»:
28092026-C11G.jpg -> 20260928-C11G.jpg (el resto del nombre no cambia).

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


def new_name(name: str) -> str | None:
    """«28092026-C11G.jpg» -> «20260928-C11G.jpg»; None si no es el formato antiguo."""
    m = _LEGACY.match(name or "")
    if not m:
        return None
    try:
        day = _dt.date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return None
    if not 2000 <= day.year <= 2099:
        return None
    return f"{day:%Y%m%d}{m['rest']}"


def _free(path: str) -> str:
    """Si ya existe un archivo con el nombre nuevo, agrega «-2», «-3»…"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}-{n}{ext}"):
        n += 1
    return f"{stem}-{n}{ext}"


def migrate_local(db) -> dict:
    """Renombra los archivos de la app con nombre antiguo y actualiza todas las rutas.
    Idempotente: se puede ejecutar en cada inicio (lo ya renombrado no se toca)."""
    moved: dict[str, str] = {}      # ruta antigua -> ruta nueva
    renamed = missing = 0
    for table, col in PATH_COLUMNS:
        try:
            rows = db.query(f"SELECT id, {col} AS p FROM {table} WHERE {col} IS NOT NULL")
        except Exception:  # noqa: BLE001 - tabla de una versión sin esa columna
            continue
        for r in rows:
            old = r["p"]
            nn = new_name(os.path.basename(old))
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
        nn = new_name(base)
        if nn:
            db.execute("UPDATE drive_queue SET name=? WHERE id=?", (f"{head}/{nn}" if head else nn, r["id"]))
    if renamed or missing:
        db.log("rename", "photos", None, f"{renamed} fotos al formato aaaammdd")
    return {"renamed": renamed, "missing": missing}
