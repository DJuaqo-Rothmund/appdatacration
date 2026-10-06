"""
workspaces.py
=============
Perfiles, ensayos y predios.

* Perfil **I+D**: ensayos (de variedades; luego también tratamientos × repeticiones).
* Perfil **Predio**: un espacio por predio (unidades = variedad + sector + equipo de riego).

Cada ensayo o predio tiene su PROPIA base SQLite (variedades, semanas, registros,
fotos, mediciones, informes): no se mezclan nunca. La base común «comun.sqlite3»
guarda lo que vale para todo el teléfono: el registro de ensayos, la memoria de la IA,
la escala BBCH, los documentos técnicos y los ajustes globales (Drive, PIN, recordatorio).

Al instalar esta versión, lo que había pasa a I+D › «Nuevas variedades» (código NV)
y se crean los predios «El Amanecer» (AM) y «La Esperanza 2» (LE).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import unicodedata

import phenology as ph
from database import Database

COMMON_DB = "comun.sqlite3"
LEGACY_DB = "fenorubus.sqlite3"          # base de las versiones anteriores (pasa a NV)
PROFILES = {"id": "I+D", "predio": "Predio"}
KINDS = {"variedades": "Ensayo de variedades",
         "tratamientos": "Tratamientos × repeticiones",
         "predio": "Predio"}
INITIAL = [  # (perfil, tipo, nombre, código, archivo)
    ("id", "variedades", "Nuevas variedades", "NV", LEGACY_DB),
    ("predio", "predio", "El Amanecer", "AM", "ensayos/AM.sqlite3"),
    ("predio", "predio", "La Esperanza 2", "LE", "ensayos/LE.sqlite3"),
]
CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile     TEXT NOT NULL,              -- id | predio
    kind        TEXT NOT NULL,              -- variedades | tratamientos | predio
    name        TEXT NOT NULL,
    code        TEXT NOT NULL UNIQUE,
    file        TEXT NOT NULL,              -- relativo a la carpeta de datos
    archived    INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def suggest_code(name: str, taken=()) -> str:
    """«Nuevas variedades» -> «NV»; «Fertilización nitrogenada 2026» -> «FN26»…"""
    t = unicodedata.normalize("NFKD", name or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    words = re.findall(r"[A-Za-z]+|\d+", t)
    letters = "".join(w[0] for w in words if w.isalpha()).upper()[:3]
    digits = next((w[-2:] for w in words if w.isdigit()), "")
    base = (letters + digits)[:4] or "EN"
    if len(base) < 2:
        base = (base + (words[0][1:2].upper() if words and len(words[0]) > 1 else "X"))[:4]
    code, n = base, 2
    while code in taken:
        code = f"{base[:3]}{n}" if n < 10 else f"{base[:2]}{n}"
        n += 1
    return code


class Workspaces:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.shared = Database(os.path.join(data_dir, COMMON_DB), seed=False)
        # En la base común, photo_id de la IA apunta a fotos de OTRA base (la del ensayo):
        # sin claves foráneas aquí.
        self.shared.conn.execute("PRAGMA foreign_keys = OFF")
        self.shared.conn.executescript(SCHEMA)
        if not self.shared.query_one("SELECT 1 FROM bbch_stages LIMIT 1"):
            for code, label, desc, kw in ph.BBCH_RUBUS:
                self.shared.upsert_bbch(code, label, desc, kw, source="base")
        self.shared.refresh_bbch_scale()   # escala BBCH del frambueso al día (cañas y laterales)
        self.ensure_initial()

    # ----------------------------------------------------------- registro
    def ensure_initial(self) -> None:
        if self.shared.query_one("SELECT 1 FROM workspaces LIMIT 1"):
            return
        legacy = os.path.join(self.data_dir, LEGACY_DB)
        had_data = os.path.exists(legacy)
        for i, (profile, kind, name, code, file) in enumerate(INITIAL):
            self.shared.execute(
                "INSERT INTO workspaces(profile, kind, name, code, file, sort_order, created_at) "
                "VALUES (?,?,?,?,?,?,?)", (profile, kind, name, code, file, i, _now()))
        nv = self.by_code("NV")
        if had_data:
            # La memoria de la IA, la escala y los documentos de NV pasan a la base común.
            local = Database(legacy, seed=False)
            try:
                migrate_ai_to_shared(local, self.shared, "NV")
                # Ajustes del teléfono (Drive, PIN, recordatorio, informes) -> base común.
                for r in local.query("SELECT key, value FROM settings"):
                    if r["key"].startswith(Database.SHARED_SETTING_PREFIXES):
                        self.shared.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)",
                                            (r["key"], r["value"]))
            finally:
                local.close()
        self.shared.set_setting("last_workspace", nv["id"])
        self.shared.log("create", "workspaces", None,
                        "Nuevas variedades (NV), El Amanecer (AM), La Esperanza 2 (LE)")

    def list(self, profile: str | None = None, archived: bool = False) -> list[dict]:
        sql, params = "SELECT * FROM workspaces WHERE archived=?", [int(archived)]
        if profile:
            sql += " AND profile=?"
            params.append(profile)
        return self.shared.query(sql + " ORDER BY sort_order, id", tuple(params))

    def get(self, ws_id: int) -> dict | None:
        return self.shared.query_one("SELECT * FROM workspaces WHERE id=?", (ws_id,))

    def by_code(self, code: str) -> dict | None:
        return self.shared.query_one("SELECT * FROM workspaces WHERE code=?", (code,))

    def codes(self) -> set[str]:
        return {r["code"] for r in self.shared.query("SELECT code FROM workspaces")}

    def create(self, profile: str, kind: str, name: str, code: str) -> dict:
        name, code = (name or "").strip(), (code or "").strip().upper()
        if profile not in PROFILES or kind not in KINDS:
            raise ValueError("Tipo de ensayo desconocido.")
        if not name:
            raise ValueError("Póngale un nombre.")
        if not CODE_RE.match(code):
            raise ValueError("El código debe tener de 2 a 4 letras o números (ej.: NV, FE, AM).")
        if code in self.codes():
            raise ValueError(f"El código «{code}» ya está en uso.")
        order = (self.shared.query_one("SELECT MAX(sort_order) AS m FROM workspaces") or {}).get("m") or 0
        self.shared.execute(
            "INSERT INTO workspaces(profile, kind, name, code, file, sort_order, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (profile, kind, name, code, f"ensayos/{code}.sqlite3",
                                       order + 1, _now()))
        self.shared.log("create", "workspace", None, f"{PROFILES[profile]} · {name} ({code})")
        return self.by_code(code)

    def rename(self, ws_id: int, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("Póngale un nombre.")
        self.shared.execute("UPDATE workspaces SET name=? WHERE id=?", (name, ws_id))

    def set_archived(self, ws_id: int, archived: bool) -> None:
        self.shared.execute("UPDATE workspaces SET archived=? WHERE id=?", (int(bool(archived)), ws_id))

    # ------------------------------------------------------------ abrir
    def path(self, ws: dict) -> str:
        return os.path.join(self.data_dir, ws["file"])

    def open(self, ws: dict) -> Database:
        """Base del ensayo/predio, enlazada a la base común."""
        # Solo NV conserva las variedades de ejemplo de una instalación nueva; los ensayos
        # y predios nuevos parten vacíos.
        return Database(self.path(ws), seed=(ws["code"] == "NV"), shared=self.shared,
                        code=ws["code"], workspace=dict(ws))

    def last(self) -> dict:
        ws = self.get(self.shared.get_setting("last_workspace") or 0)
        if not ws or ws["archived"]:
            ws = self.by_code("NV") or self.list()[0]
        return ws

    def remember(self, ws: dict) -> None:
        self.shared.set_setting("last_workspace", ws["id"])

    @staticmethod
    def title(ws: dict) -> str:
        return f"{PROFILES.get(ws['profile'], '')} · {ws['name']}"

    @staticmethod
    def drive_prefix(ws: dict) -> str:
        """Carpeta del ensayo en Drive: «I+D/Nuevas variedades», «Predio/El Amanecer»."""
        safe = ws["name"].replace("/", "-").strip()
        return f"{PROFILES.get(ws['profile'], 'Otros')}/{safe}"

    def close(self) -> None:
        self.shared.close()


def _emb_key(blob) -> str:
    return hashlib.sha1(bytes(blob)).hexdigest()


def migrate_ai_to_shared(local: Database, shared: Database, code: str) -> dict:
    """Suma a la base común la memoria de la IA, la escala BBCH editada y los documentos
    de una base de ensayo (de una versión anterior o de una copia restaurada).
    No duplica: las referencias se reconocen por la huella de su descriptor."""
    out = {"references": 0, "stages": 0, "documents": 0}
    have = {_emb_key(r["embedding"]) for r in shared.query("SELECT embedding FROM ai_references")}
    for r in local.query("SELECT * FROM ai_references ORDER BY id"):
        key = _emb_key(r["embedding"])
        if key in have:
            continue
        shared.execute(
            "INSERT INTO ai_references(bbch_code, extractor, embedding, dim, image_path, photo_id, "
            "created_at, workspace) VALUES (?,?,?,?,?,?,?,?)",
            (r["bbch_code"], r["extractor"], r["embedding"], r["dim"], r["image_path"],
             r["photo_id"], r["created_at"], code))
        have.add(key)
        out["references"] += 1
    mine = {r["code"]: r for r in shared.query("SELECT * FROM bbch_stages")}
    for s in local.query("SELECT * FROM bbch_stages"):
        cur = mine.get(s["code"])
        if cur is None or (s["source"] != "base" and cur["source"] == "base"):
            shared.upsert_bbch(s["code"], s["label"], s["description"] or "", s["keywords"] or "",
                               source=s["source"] or "user")
            out["stages"] += 1
    known = {(d["title"], len(d["text"] or "")) for d in shared.query("SELECT title, text FROM documents")}
    for d in local.query("SELECT * FROM documents ORDER BY id"):
        if (d["title"], len(d["text"] or "")) in known:
            continue
        shared.execute("INSERT INTO documents(title, path, text, stages_found, created_at) "
                       "VALUES (?,?,?,?,?)", (d["title"], d["path"], d["text"], d["stages_found"],
                                             d["created_at"]))
        out["documents"] += 1
    if any(out.values()):
        shared.log("import", "ai", None, f"{code}: {out}")
    return out
