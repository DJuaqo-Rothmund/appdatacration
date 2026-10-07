"""
catalog.py
==========
Catálogo de variedades del teléfono, separado por perfil (I+D y Predio). Vive en la
base común (comun.sqlite3) y se usa como desplegable al crear variedades, sectores y
al asignar la variedad a un ensayo de tratamientos o a cada tratamiento.

Solo entran nombres reales: nunca «Código 11» (las variedades por código son propias
del ensayo «Nuevas variedades»), ni parcelas «T1R1», ni nombres de unidades de predio.
"""
from __future__ import annotations

import datetime as _dt
import re

PROFILES = ("id", "predio")
_NOT_A_VARIETY = re.compile(
    r"^\s*(c[oó]d(igo)?\.?\s*(n[°º.]?\s*)?\d+|t\d+\s*r\d+|equipo de riego\b|sector\s*\d+)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS variety_catalog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile     TEXT NOT NULL,            -- id | predio
    name        TEXT NOT NULL,
    code        TEXT DEFAULT '',          -- abreviatura para el nombre de las fotos
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_name ON variety_catalog(profile, name COLLATE NOCASE);
"""


def is_real_variety(name: str | None) -> bool:
    name = (name or "").strip()
    return len(name) >= 2 and not _NOT_A_VARIETY.match(name)


def ensure(shared) -> None:
    shared.conn.executescript(SCHEMA)


def entries(shared, profile: str) -> list[dict]:
    ensure(shared)
    return shared.query("SELECT name, code FROM variety_catalog WHERE profile=? "
                        "ORDER BY name COLLATE NOCASE", (profile,))


def add(shared, profile: str, name: str, code: str = "") -> bool:
    """Agrega (o completa el código de) una variedad. False si el nombre no vale."""
    name, code = (name or "").strip(), (code or "").strip()
    if profile not in PROFILES or not is_real_variety(name):
        return False
    ensure(shared)
    cur = shared.query_one("SELECT id, code FROM variety_catalog WHERE profile=? AND name=? COLLATE NOCASE",
                           (profile, name))
    if cur is None:
        shared.execute("INSERT INTO variety_catalog(profile, name, code, created_at) VALUES (?,?,?,?)",
                       (profile, name, code, _dt.datetime.now().isoformat(timespec="seconds")))
    elif code and code != (cur["code"] or ""):
        shared.execute("UPDATE variety_catalog SET code=? WHERE id=?", (code, cur["id"]))
    return True


def remove(shared, profile: str, name: str) -> None:
    ensure(shared)
    shared.execute("DELETE FROM variety_catalog WHERE profile=? AND name=? COLLATE NOCASE", (profile, name))


def code_for(shared, profile: str, name: str) -> str:
    ensure(shared)
    row = shared.query_one("SELECT code FROM variety_catalog WHERE profile=? AND name=? COLLATE NOCASE",
                           (profile, (name or "").strip()))
    return (row or {}).get("code") or ""


def seed_from_workspaces(wss) -> int:
    """Una sola vez: las variedades con nombre real que ya existen en los ensayos y predios."""
    shared = wss.shared
    if shared.get_setting("catalog_seeded_v1"):
        return 0
    n = 0
    for ws in wss.list() + wss.list(archived=True):
        try:
            db = wss.open(ws)
        except Exception:  # noqa: BLE001 - un ensayo dañado no impide lo demás
            continue
        try:
            for v in db.query("SELECT name, code, cultivar, treatment FROM varieties"):
                if ws["profile"] == "predio":
                    n += add(shared, "predio", v.get("cultivar") or "", v["code"] or "")
                elif ws["kind"] == "variedades" and not v.get("treatment"):
                    n += add(shared, "id", v["name"], v["code"] or "")
            if ws["kind"] == "tratamientos":
                for t in db.query("SELECT cultivar FROM treatments WHERE cultivar IS NOT NULL"):
                    n += add(shared, "id", t["cultivar"])
        finally:
            db.close()
    shared.set_setting("catalog_seeded_v1", True)
    return n
