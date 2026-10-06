"""
database.py
===========
Persistencia local SQLite (offline-first) con trazabilidad.

Entidades
---------
varieties        Catálogo dinámico de variedades (CRUD, archivado suave).
variety_metrics  Parámetros biométricos por variedad y temporada.
custom_fields    Campos personalizados llave-valor por variedad y temporada.
variety_attachments  Fotos adjuntas a un registro semanal de la variedad (con descripción).
measures         Mediciones definidas por el usuario: registro de imágenes o planilla.
measure_entries  Imágenes o filas de planilla de cada medición, por semana.
sampling_weeks   Semanas de muestreo (Semana 1 = semana del 7 de septiembre).
observations     Registro fenológico variedad × semana (BBCH, IA, notas).
photos           Fotos asociadas a una observación (canopy / detail).
bbch_stages      Catálogo BBCH editable (enriquecible con documentos técnicos).
ai_references    Base de referencia del clasificador (embeddings etiquetados).
documents        Documentos técnicos cargados en el módulo de calibración.
settings         Preferencias (JSON).
audit_log        Bitácora de cambios (trazabilidad de campo).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
from typing import Any, Iterable

import phenology as ph

SCHEMA_VERSION = 3

DEFAULT_VARIETIES: list[tuple[str, str]] = [
    ("Código 11", "C11"),
    ("Código 31", "C31"),
    ("Código 24", "C24"),
    ("Código 55", "C55"),
    ("Código 81", "C81"),
    ("Cascade Harvest", "CH"),
    ("Lagorai", "LAG"),
    ("Meeker", "MEE"),
    ("Regina", "REG"),
    ("Wakefield", "WAK"),
]

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS varieties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    code        TEXT,
    sector      INTEGER,              -- opcional (1-10)
    irrigation  INTEGER,              -- equipo de riego, opcional (1-4)
    notes       TEXT DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variety_metrics (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    variety_id             INTEGER NOT NULL REFERENCES varieties(id) ON DELETE CASCADE,
    season                 INTEGER NOT NULL,
    historical_yield       REAL,
    historical_yield_unit  TEXT DEFAULT 'kg/planta',
    historical_note        TEXT DEFAULT '',
    projected_yield        REAL,
    projected_yield_unit   TEXT DEFAULT 'kg/planta',
    basal_canes            REAL,
    basal_canes_unit       TEXT DEFAULT 'por planta',
    laterals               REAL,
    laterals_unit          TEXT DEFAULT 'por caña',
    updated_at             TEXT NOT NULL,
    UNIQUE (variety_id, season)
);

CREATE TABLE IF NOT EXISTS custom_fields (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    variety_id  INTEGER NOT NULL REFERENCES varieties(id) ON DELETE CASCADE,
    season      INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT DEFAULT '',
    unit        TEXT DEFAULT '',
    updated_at  TEXT NOT NULL,
    UNIQUE (variety_id, season, key)
);

CREATE TABLE IF NOT EXISTS variety_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    variety_id   INTEGER NOT NULL REFERENCES varieties(id) ON DELETE CASCADE,
    season       INTEGER NOT NULL,
    path         TEXT NOT NULL,
    caption      TEXT DEFAULT '',
    source       TEXT DEFAULT 'camera',
    captured_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS treatments (     -- ensayos de tratamientos × repeticiones
    num          INTEGER PRIMARY KEY,         -- 1, 2, 3… (T1, T2, T3…)
    name         TEXT NOT NULL DEFAULT '',    -- opcional: «Testigo», «N 120 kg/ha»…
    description  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS measures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('images', 'table')),
    columns     TEXT NOT NULL DEFAULT '[]',     -- planilla: nombres de columnas (JSON)
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measure_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    measure_id  INTEGER NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
    week_id     INTEGER NOT NULL REFERENCES sampling_weeks(id) ON DELETE CASCADE,
    variety_id  INTEGER REFERENCES varieties(id) ON DELETE SET NULL,
    path        TEXT,                           -- imagen
    caption     TEXT DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',     -- fila de planilla {columna: valor}
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sampling_weeks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season      INTEGER NOT NULL,
    week_number INTEGER NOT NULL,
    start_date  TEXT NOT NULL,
    label       TEXT NOT NULL,
    UNIQUE (season, week_number)
);

CREATE TABLE IF NOT EXISTS observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    variety_id     INTEGER NOT NULL REFERENCES varieties(id) ON DELETE CASCADE,
    week_id        INTEGER NOT NULL REFERENCES sampling_weeks(id) ON DELETE CASCADE,
    bbch_code      INTEGER,
    bbch_label     TEXT DEFAULT '',
    ai_code        INTEGER,
    ai_confidence  REAL,
    ai_detail      TEXT DEFAULT '',
    ai_accepted    INTEGER,
    notes          TEXT DEFAULT '',
    observed_at    TEXT,
    latitude       REAL,              -- ubicación de la muestra (opcional)
    longitude      REAL,
    gps_accuracy   REAL,              -- metros
    gps_source     TEXT,              -- gps | foto | mapa
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (variety_id, week_id)
);

CREATE TABLE IF NOT EXISTS photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id  INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('canopy', 'detail')),
    path            TEXT NOT NULL,
    source          TEXT DEFAULT 'camera',
    captured_at     TEXT NOT NULL,
    is_primary      INTEGER NOT NULL DEFAULT 0   -- foto que aparece en los informes
);

CREATE TABLE IF NOT EXISTS drive_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id     INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    path         TEXT NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error
    attempts     INTEGER NOT NULL DEFAULT 0,
    drive_id     TEXT,
    error        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    uploaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL,          -- capture | identify
    payload     TEXT NOT NULL,          -- JSON
    status      TEXT NOT NULL DEFAULT 'open',   -- open | done | skipped
    answer      TEXT DEFAULT '',
    correct     INTEGER,
    created_at  TEXT NOT NULL,
    UNIQUE (day, idx)
);

CREATE TABLE IF NOT EXISTS bbch_stages (
    code         INTEGER PRIMARY KEY,
    label        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    source       TEXT DEFAULT 'base'
);

CREATE TABLE IF NOT EXISTS ai_references (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bbch_code    INTEGER NOT NULL,
    extractor    TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    dim          INTEGER NOT NULL,
    image_path   TEXT,
    photo_id     INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    path        TEXT,
    text        TEXT DEFAULT '',
    stages_found INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  INTEGER,
    detail     TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_obs_week ON observations(week_id);
CREATE INDEX IF NOT EXISTS idx_obs_variety ON observations(variety_id);
CREATE INDEX IF NOT EXISTS idx_ref_code ON ai_references(bbch_code);
CREATE INDEX IF NOT EXISTS idx_photos_obs ON photos(observation_id, kind);
CREATE INDEX IF NOT EXISTS idx_drive_status ON drive_queue(status);
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class Database:
    """Acceso thread-safe (una conexión + lock) a la base SQLite local."""

    # Ajustes que valen para todo el teléfono (no para un ensayo): viven en la base
    # común cuando la hay (cuenta de Drive, PIN, recordatorio, informes…).
    SHARED_SETTING_PREFIXES = ("drive_", "report_", "ai_pin", "ai_auto_learn", "reminder",
                               "public_names", "last_workspace", "workspaces_", "user_name")

    def __init__(self, path: str, seed: bool = True, shared: "Database | None" = None,
                 code: str = "", workspace: dict | None = None):
        """shared: base común del teléfono (registro de ensayos, memoria de la IA, escala
        BBCH, documentos y ajustes globales). code / workspace: ensayo o predio de esta base."""
        self.path = path
        self.shared = shared
        self.code = code
        self.workspace = workspace or {}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        # WAL + NORMAL: escrituras mucho más rápidas en memorias flash lentas,
        # sin riesgo de corrupción (solo podría perderse la última transacción ante un corte).
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self.init_schema()
        if seed:
            self.seed_defaults()
        if shared is None and seed:
            self.refresh_bbch_scale()
        self._remap_bbch_codes()

    # ------------------------------------------------------------------ core
    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def init_schema(self) -> None:
        with self._lock:
            self._migrate_photos_v2()
            self.conn.executescript(SCHEMA)
            self._add_missing_columns()
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
            self.conn.commit()

    NEW_COLUMNS = {  # v3: columnas opcionales agregadas a tablas existentes
        "varieties": [("sector", "INTEGER"), ("irrigation", "INTEGER"),
                      ("treatment", "INTEGER"), ("rep", "INTEGER"),   # parcela T1R1…
                      ("cultivar", "TEXT")],                          # predio: variedad de la unidad
        "observations": [("latitude", "REAL"), ("longitude", "REAL"),
                         ("gps_accuracy", "REAL"), ("gps_source", "TEXT")],
        "sampling_weeks": [("skipped", "INTEGER NOT NULL DEFAULT 0")],   # semana no muestreada
        "variety_attachments": [("week_id", "INTEGER")],                # foto adjunta semanal
        "ai_references": [("workspace", "TEXT")],                       # ensayo de la foto
    }

    def _add_missing_columns(self) -> None:
        for table, cols in self.NEW_COLUMNS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        self.conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    def _migrate_photos_v2(self) -> None:
        """
        v1 -> v2: la tabla photos permitía una sola foto por tipo (UNIQUE). Se
        recrea sin esa restricción y con «is_primary», conservando ids (las
        referencias de la IA siguen apuntando a la misma foto).
        """
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='photos'").fetchone()
        if not row or "is_primary" in row[0]:
            return
        c = self.conn
        c.execute("PRAGMA foreign_keys = OFF")
        try:
            c.executescript("""
                BEGIN;
                CREATE TABLE photos_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('canopy', 'detail')),
                    path TEXT NOT NULL,
                    source TEXT DEFAULT 'camera',
                    captured_at TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0);
                INSERT INTO photos_v2(id, observation_id, kind, path, source, captured_at, is_primary)
                    SELECT id, observation_id, kind, path, source, captured_at, 1 FROM photos;
                DROP TABLE photos;
                ALTER TABLE photos_v2 RENAME TO photos;
                UPDATE meta SET value='2' WHERE key='schema_version';
                COMMIT;
            """)
        finally:
            c.execute("PRAGMA foreign_keys = ON")

    def refresh_bbch_scale(self) -> int:
        """Pone al día la escala BASE (versión ph.BBCH_SCALE_VERSION) sin tocar los estados
        editados por el usuario o enriquecidos con documentos. Devuelve cuántos cambió."""
        ai = self.ai_db
        row = ai.query_one("SELECT value FROM meta WHERE key='bbch_scale'")
        if row and int(row["value"] or 0) >= ph.BBCH_SCALE_VERSION:
            return 0
        # Códigos corregidos (553 -> 53): fuera la entrada base antigua y la memoria de la IA
        # pasa al código nuevo.
        for old, new in ph.BBCH_RENAMED.items():
            ai.execute("DELETE FROM bbch_stages WHERE code=? AND source='base'", (old,))
            ai.execute("UPDATE ai_references SET bbch_code=? WHERE bbch_code=?", (new, old))
        have = {r["code"]: r for r in ai.query("SELECT code, source FROM bbch_stages")}
        n = 0
        for code, label, desc, kw in ph.BBCH_RUBUS:
            cur = have.get(code)
            if cur is None or cur["source"] == "base":
                ai.upsert_bbch(code, label, desc, kw, source="base")
                n += 1
        ai.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('bbch_scale', ?)",
                   (str(ph.BBCH_SCALE_VERSION),))
        return n

    def _remap_bbch_codes(self) -> None:
        """Registros guardados con un código corregido de la escala (553 -> 53)."""
        for old, new in ph.BBCH_RENAMED.items():
            for col in ("bbch_code", "ai_code"):
                self.execute(f"UPDATE observations SET {col}=? WHERE {col}=?", (new, old))

    def seed_defaults(self) -> None:
        """Carga variedades y escala BBCH iniciales (solo la primera vez)."""
        seeded = self.query_one("SELECT value FROM meta WHERE key='seeded'")
        if seeded:
            return
        for i, (name, code) in enumerate(DEFAULT_VARIETIES):
            self.add_variety(name, code=code, sort_order=i, log=False)
        for code, label, desc, kw in ph.BBCH_RUBUS:
            self.upsert_bbch(code, label, desc, kw, source="base")
        self.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seeded', ?)", (_now(),))
        self.log("seed", "database", None, "Variedades y escala BBCH iniciales")

    # --------------------------------------------------------------- audit
    def log(self, action: str, entity: str, entity_id: int | None, detail: str = "") -> None:
        self.execute(
            "INSERT INTO audit_log(ts, action, entity, entity_id, detail) VALUES (?,?,?,?,?)",
            (_now(), action, entity, entity_id, detail))

    def audit_trail(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ settings
    def _settings_db(self, key: str) -> "Database":
        if self.shared is not None and key.startswith(self.SHARED_SETTING_PREFIXES):
            return self.shared
        return self

    @property
    def ai_db(self) -> "Database":
        """Base donde vive la memoria de la IA (común a todos los ensayos si la hay)."""
        return self.shared if self.shared is not None else self

    def get_setting(self, key: str, default: Any = None) -> Any:
        if self._settings_db(key) is not self:
            return self.shared.get_setting(key, default)
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        if self._settings_db(key) is not self:
            self.shared.set_setting(key, value)
            return
        self.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                     (key, json.dumps(value)))

    # ------------------------------------------------------------ varieties
    def list_varieties(self, include_archived: bool = False) -> list[dict]:
        sql = "SELECT * FROM varieties"
        if not include_archived:
            sql += " WHERE active = 1"
        sql += " ORDER BY sort_order, name"
        return self.query(sql)

    def get_variety(self, variety_id: int) -> dict | None:
        return self.query_one("SELECT * FROM varieties WHERE id=?", (variety_id,))

    def add_variety(self, name: str, code: str | None = None, notes: str = "",
                    sort_order: int | None = None, log: bool = True,
                    sector: int | None = None, irrigation: int | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("El nombre de la variedad no puede estar vacío.")
        existing = self.query_one("SELECT * FROM varieties WHERE name=?", (name,))
        if existing:
            if not existing["active"]:
                self.execute("UPDATE varieties SET active=1, updated_at=? WHERE id=?",
                             (_now(), existing["id"]))
                self.log("restore", "variety", existing["id"], name)
                return existing["id"]
            raise ValueError(f"La variedad «{name}» ya existe.")
        if sort_order is None:
            row = self.query_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM varieties")
            sort_order = row["n"]
        cur = self.execute(
            "INSERT INTO varieties(name, code, notes, sort_order, sector, irrigation, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, code or "", notes, sort_order, sector, irrigation, _now(), _now()))
        if log:
            self.log("create", "variety", cur.lastrowid, name)
        return cur.lastrowid

    def update_variety(self, variety_id: int, **fields: Any) -> None:
        allowed = {"name", "code", "notes", "sort_order", "active", "sector", "irrigation",
                   "treatment", "rep", "cultivar"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.execute(f"UPDATE varieties SET {cols}, updated_at=? WHERE id=?",
                     (*sets.values(), _now(), variety_id))
        self.log("update", "variety", variety_id, json.dumps(sets, ensure_ascii=False))

    # ---------------------------------------------------- predio: unidades
    @property
    def is_predio(self) -> bool:
        return (self.workspace or {}).get("profile") == "predio"

    def save_unit(self, cultivar: str, code: str = "", sector: int | None = None,
                  irrigation: int | None = None, variety_id: int | None = None) -> int:
        """Unidad de predio: se llama «Equipo de riego n, sector m» y la variedad va aparte
        (la misma variedad puede estar en varios sectores y un sector puede repetirse con
        otra variedad)."""
        cultivar = (cultivar or "").strip()
        if not cultivar:
            raise ValueError("Indique la variedad.")
        if not sector and not irrigation:
            raise ValueError("Elija el sector y/o el equipo de riego.")
        base = ph.unit_name(sector, irrigation)
        dup = self.query_one(
            "SELECT id FROM varieties WHERE active=1 AND IFNULL(sector,0)=? AND IFNULL(irrigation,0)=? "
            "AND lower(IFNULL(cultivar,''))=lower(?) AND id IS NOT ?",
            (sector or 0, irrigation or 0, cultivar, variety_id))
        if dup:
            raise ValueError(f"Ya existe «{base}» con la variedad {cultivar}.")
        name = base
        for cand in (base, f"{base} · {cultivar}"):
            other = self.query_one("SELECT id, active FROM varieties WHERE name=?", (cand,))
            if other is None or other["id"] == variety_id or (not other["active"] and variety_id is None):
                name = cand
                break
        else:
            raise ValueError(f"Ya existe «{base}» con la variedad {cultivar}.")
        if variety_id is None:
            variety_id = self.add_variety(name, code=code, sector=sector, irrigation=irrigation)
        self.update_variety(variety_id, name=name, code=code or "", cultivar=cultivar,
                            sector=sector, irrigation=irrigation, active=1)
        return variety_id

    def migrate_predio_units(self) -> int:
        """Unidades creadas antes con el nombre de la variedad -> «Equipo de riego n, sector m»."""
        import re as _re
        n = 0
        for v in self.query("SELECT * FROM varieties WHERE (cultivar IS NULL OR cultivar='') "
                            "AND (sector IS NOT NULL OR irrigation IS NOT NULL)"):
            cultivar = _re.sub(r"\s+S\d+(ER\d+)?$|\s+ER\d+$", "", v["name"]).strip() or v["name"]
            try:
                self.save_unit(cultivar, v["code"] or "", v["sector"], v["irrigation"], v["id"])
                n += 1
            except ValueError:
                self.update_variety(v["id"], cultivar=cultivar)
        return n

    # --------------------------------------------- tratamientos × repeticiones
    MAX_TREATMENTS, MAX_REPS = 30, 12

    @property
    def is_trial(self) -> bool:
        """Ensayo de tratamientos × repeticiones (sus «variedades» son parcelas T1R1…)."""
        return (self.workspace or {}).get("kind") == "tratamientos" or bool(
            self.get_setting("trial_treatments", 0))

    def trial_size(self) -> tuple[int, int]:
        return int(self.get_setting("trial_treatments", 0) or 0), int(self.get_setting("trial_reps", 0) or 0)

    def setup_trial(self, n_treatments: int, n_reps: int) -> dict:
        """Crea (o ajusta) las parcelas T1R1 … TnRm. Al reducir, las parcelas que sobran se
        ARCHIVAN (sus registros se conservan); al volver a aumentar, reaparecen."""
        nt, nr = int(n_treatments), int(n_reps)
        if not (1 <= nt <= self.MAX_TREATMENTS) or not (1 <= nr <= self.MAX_REPS):
            raise ValueError(f"Tratamientos: 1 a {self.MAX_TREATMENTS} · repeticiones: 1 a {self.MAX_REPS}.")
        added = restored = archived = 0
        for t in range(1, nt + 1):
            self.execute("INSERT OR IGNORE INTO treatments(num, name) VALUES (?, '')", (t,))
            for r in range(1, nr + 1):
                name = f"T{t}R{r}"
                cur = self.query_one("SELECT * FROM varieties WHERE treatment=? AND rep=?", (t, r)) \
                    or self.query_one("SELECT * FROM varieties WHERE name=?", (name,))
                if cur is None:
                    vid = self.add_variety(name, code=name, sort_order=t * 100 + r, log=False)
                    added += 1
                else:
                    vid = cur["id"]
                    if not cur["active"]:
                        restored += 1
                self.execute("UPDATE varieties SET treatment=?, rep=?, active=1, sort_order=?, "
                             "updated_at=? WHERE id=?", (t, r, t * 100 + r, _now(), vid))
        for v in self.query("SELECT id FROM varieties WHERE active=1 AND treatment IS NOT NULL "
                            "AND (treatment>? OR rep>?)", (nt, nr)):
            self.execute("UPDATE varieties SET active=0, updated_at=? WHERE id=?", (_now(), v["id"]))
            archived += 1
        self.set_setting("trial_treatments", nt)
        self.set_setting("trial_reps", nr)
        self.log("update", "trial", None, f"{nt} tratamientos × {nr} repeticiones "
                                          f"(+{added}, restauradas {restored}, archivadas {archived})")
        return {"added": added, "restored": restored, "archived": archived}

    @staticmethod
    def treatment_label(t: dict) -> str:
        return f"T{t['num']} · {t['name']}" if (t.get("name") or "").strip() else f"T{t['num']}"

    def list_treatments(self) -> list[dict]:
        nt, _nr = self.trial_size()
        out = []
        for t in self.query("SELECT * FROM treatments WHERE num<=? ORDER BY num", (nt,)):
            t["label"] = self.treatment_label(t)
            t["parcels"] = self.query("SELECT * FROM varieties WHERE active=1 AND treatment=? "
                                      "ORDER BY rep", (t["num"],))
            out.append(t)
        return out

    def update_treatment(self, num: int, name: str = "", description: str = "") -> None:
        self.execute("INSERT OR IGNORE INTO treatments(num, name) VALUES (?, '')", (num,))
        self.execute("UPDATE treatments SET name=?, description=? WHERE num=?",
                     ((name or "").strip(), (description or "").strip(), num))
        self.log("update", "treatment", num, (name or "").strip())

    def delete_variety(self, variety_id: int, purge: bool = False) -> None:
        """
        purge=False  -> archiva (conserva historial y fotos: trazabilidad).
        purge=True   -> elimina definitivamente con sus registros (cascade).
        """
        v = self.get_variety(variety_id)
        if not v:
            return
        if purge:
            self.execute("DELETE FROM varieties WHERE id=?", (variety_id,))
            self.log("delete", "variety", variety_id, v["name"])
        else:
            self.execute("UPDATE varieties SET active=0, updated_at=? WHERE id=?",
                         (_now(), variety_id))
            self.log("archive", "variety", variety_id, v["name"])

    # --------------------------------------------------------------- metrics
    METRIC_FIELDS = ("historical_yield", "historical_yield_unit", "historical_note",
                     "projected_yield", "projected_yield_unit", "basal_canes",
                     "basal_canes_unit", "laterals", "laterals_unit")

    def get_metrics(self, variety_id: int, season: int) -> dict:
        row = self.query_one(
            "SELECT * FROM variety_metrics WHERE variety_id=? AND season=?",
            (variety_id, season))
        if row:
            return row
        return {"variety_id": variety_id, "season": season, "historical_yield": None,
                "historical_yield_unit": "kg/planta", "historical_note": "",
                "projected_yield": None, "projected_yield_unit": "kg/planta",
                "basal_canes": None, "basal_canes_unit": "por planta",
                "laterals": None, "laterals_unit": "por caña"}

    def save_metrics(self, variety_id: int, season: int, **values: Any) -> None:
        data = {k: values[k] for k in self.METRIC_FIELDS if k in values}
        current = self.get_metrics(variety_id, season)
        current.update(data)
        cols = list(self.METRIC_FIELDS)
        self.execute(
            f"INSERT INTO variety_metrics(variety_id, season, {', '.join(cols)}, updated_at) "
            f"VALUES (?, ?, {', '.join('?' for _ in cols)}, ?) "
            f"ON CONFLICT(variety_id, season) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in cols) + ", updated_at=excluded.updated_at",
            (variety_id, season, *[current.get(c) for c in cols], _now()))
        self.log("update", "metrics", variety_id, json.dumps(data, ensure_ascii=False))

    # ---------------------------------------------------------- custom fields
    def list_custom_fields(self, variety_id: int, season: int) -> list[dict]:
        return self.query(
            "SELECT * FROM custom_fields WHERE variety_id=? AND season=? ORDER BY key",
            (variety_id, season))

    def set_custom_field(self, variety_id: int, season: int, key: str,
                         value: str, unit: str = "") -> None:
        key = key.strip()
        if not key:
            raise ValueError("El nombre del campo no puede estar vacío.")
        self.execute(
            "INSERT INTO custom_fields(variety_id, season, key, value, unit, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(variety_id, season, key) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, updated_at=excluded.updated_at",
            (variety_id, season, key, str(value), unit, _now()))
        self.log("set", "custom_field", variety_id, f"{key}={value} {unit}".strip())

    def delete_custom_field(self, field_id: int) -> None:
        self.execute("DELETE FROM custom_fields WHERE id=?", (field_id,))
        self.log("delete", "custom_field", field_id)

    # ------------------------------------------------- fotos adjuntas (ficha)
    def add_attachment(self, variety_id: int, season: int, path: str, caption: str = "",
                       source: str = "camera", week_id: int | None = None) -> int:
        cur = self.execute(
            "INSERT INTO variety_attachments(variety_id, season, path, caption, source, captured_at, "
            "week_id) VALUES (?,?,?,?,?,?,?)",
            (variety_id, season, path, caption.strip(), source, _now(), week_id))
        self.log("create", "attachment", cur.lastrowid, os.path.basename(path))
        return cur.lastrowid

    def list_attachments(self, variety_id: int, season: int | None = None,
                         week_id: int | None = None, general: bool = False) -> list[dict]:
        """Fotos adjuntas de la variedad: de una semana (week_id), de la temporada o solo
        las generales (sin semana, versiones anteriores)."""
        sql, params = "SELECT * FROM variety_attachments WHERE variety_id=?", [variety_id]
        if season is not None:
            sql += " AND season=?"
            params.append(season)
        if week_id is not None:
            sql += " AND week_id=?"
            params.append(week_id)
        elif general:
            sql += " AND week_id IS NULL"
        return self.query(sql + " ORDER BY captured_at, id", tuple(params))

    def set_attachment_caption(self, attachment_id: int, caption: str) -> None:
        self.execute("UPDATE variety_attachments SET caption=? WHERE id=?",
                     (caption.strip(), attachment_id))

    def delete_attachment(self, attachment_id: int) -> None:
        self.execute("DELETE FROM variety_attachments WHERE id=?", (attachment_id,))
        self.log("delete", "attachment", attachment_id)

    # ------------------------------------------- mediciones personalizadas
    MEASURE_KINDS = {"images": "Registro de imágenes", "table": "Planilla de datos"}

    @staticmethod
    def _measure(row: dict | None) -> dict | None:
        if row is not None:
            row["columns"] = json.loads(row.get("columns") or "[]")
        return row

    def add_measure(self, name: str, kind: str, columns: list[str] | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Póngale un nombre a la medición.")
        if kind not in self.MEASURE_KINDS:
            raise ValueError("Tipo de medición desconocido.")
        cols = [c.strip() for c in (columns or []) if c and c.strip()]
        if kind == "table" and not cols:
            cols = list(self.DEFAULT_COLUMNS)   # planilla nueva: se nombran luego en la grilla
        order = (self.query_one("SELECT MAX(sort_order) AS m FROM measures") or {}).get("m") or 0
        cur = self.execute(
            "INSERT INTO measures(name, kind, columns, sort_order, created_at) VALUES (?,?,?,?,?)",
            (name, kind, json.dumps(list(dict.fromkeys(cols)), ensure_ascii=False), order + 1, _now()))
        self.log("create", "measure", cur.lastrowid, f"{name} ({kind})")
        return cur.lastrowid

    def update_measure(self, measure_id: int, name: str | None = None,
                       columns: list[str] | None = None) -> None:
        if name is not None:
            if not name.strip():
                raise ValueError("Póngale un nombre a la medición.")
            self.execute("UPDATE measures SET name=? WHERE id=?", (name.strip(), measure_id))
        if columns is not None:
            cols = list(dict.fromkeys(c.strip() for c in columns if c and c.strip()))
            if not cols:
                raise ValueError("La planilla necesita al menos una columna.")
            self.execute("UPDATE measures SET columns=? WHERE id=?",
                         (json.dumps(cols, ensure_ascii=False), measure_id))

    DEFAULT_COLUMNS = ["Columna 1", "Columna 2", "Columna 3"]

    def add_measure_column(self, measure_id: int, name: str | None = None) -> str:
        """Agrega una columna al final (nombre automático «Columna N» si no se indica)."""
        m = self.get_measure(measure_id)
        cols = m["columns"]
        if name is None or not name.strip():
            n = len(cols) + 1
            while f"Columna {n}" in cols:
                n += 1
            name = f"Columna {n}"
        name = name.strip()
        if name in cols:
            raise ValueError(f"Ya existe la columna «{name}».")
        self.update_measure(measure_id, columns=cols + [name])
        return name

    def rename_measure_column(self, measure_id: int, old: str, new: str) -> None:
        """Renombra la columna y mueve sus datos en todas las semanas."""
        new = (new or "").strip()
        m = self.get_measure(measure_id)
        if not new:
            raise ValueError("El nombre de la columna no puede quedar vacío.")
        if new == old:
            return
        if new in m["columns"]:
            raise ValueError(f"Ya existe la columna «{new}».")
        cols = [new if col == old else col for col in m["columns"]]
        with self._lock:
            self.update_measure(measure_id, columns=cols)
            for e in self.query("SELECT id, data FROM measure_entries WHERE measure_id=?", (measure_id,)):
                data = json.loads(e["data"] or "{}")
                if old in data:
                    data = {(new if k == old else k): v for k, v in data.items()}
                    self.execute("UPDATE measure_entries SET data=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), e["id"]))

    def delete_measure_column(self, measure_id: int, name: str) -> None:
        """Elimina la columna y sus datos (debe quedar al menos una)."""
        m = self.get_measure(measure_id)
        cols = [col for col in m["columns"] if col != name]
        if not cols:
            raise ValueError("La planilla debe tener al menos una columna.")
        with self._lock:
            self.update_measure(measure_id, columns=cols)
            for e in self.query("SELECT id, data FROM measure_entries WHERE measure_id=?", (measure_id,)):
                data = json.loads(e["data"] or "{}")
                if name in data:
                    data.pop(name)
                    self.execute("UPDATE measure_entries SET data=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), e["id"]))

    def delete_measure(self, measure_id: int) -> None:
        self.execute("DELETE FROM measures WHERE id=?", (measure_id,))
        self.log("delete", "measure", measure_id)

    def get_measure(self, measure_id: int) -> dict | None:
        return self._measure(self.query_one("SELECT * FROM measures WHERE id=?", (measure_id,)))

    def list_measures(self, week_id: int | None = None) -> list[dict]:
        """Mediciones definidas; con week_id agrega «n» = registros de esa semana."""
        rows = self.query(
            "SELECT m.*, (SELECT COUNT(*) FROM measure_entries e WHERE e.measure_id = m.id "
            "AND e.week_id = ? AND (e.path IS NOT NULL OR e.data NOT IN ('', '{}'))) AS n "
            "FROM measures m ORDER BY m.sort_order, m.id", (week_id or 0,))
        return [self._measure(r) for r in rows]

    def add_entry(self, measure_id: int, week_id: int, variety_id: int | None = None,
                  path: str | None = None, caption: str = "", data: dict | None = None) -> int:
        cur = self.execute(
            "INSERT INTO measure_entries(measure_id, week_id, variety_id, path, caption, data, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (measure_id, week_id, variety_id, path, (caption or "").strip(),
             json.dumps(data or {}, ensure_ascii=False), _now()))
        return cur.lastrowid

    def update_entry(self, entry_id: int, **fields) -> None:
        allowed = {"variety_id", "caption", "data"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            if k == "data":
                v = json.dumps(v or {}, ensure_ascii=False)
            elif k == "caption":
                v = (v or "").strip()
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            self.execute(f"UPDATE measure_entries SET {', '.join(sets)} WHERE id=?", (*vals, entry_id))

    def delete_entry(self, entry_id: int) -> None:
        self.execute("DELETE FROM measure_entries WHERE id=?", (entry_id,))

    def list_entries(self, measure_id: int, week_id: int | None = None,
                     season: int | None = None) -> list[dict]:
        """Registros de la medición (de una semana o de toda la temporada), con la
        semana y el nombre de la variedad."""
        sql = ("SELECT e.*, w.season, w.week_number, w.start_date, w.label AS week_label, "
               "v.name AS variety_name FROM measure_entries e "
               "JOIN sampling_weeks w ON w.id = e.week_id "
               "LEFT JOIN varieties v ON v.id = e.variety_id WHERE e.measure_id=?")
        params: list = [measure_id]
        if week_id is not None:
            sql += " AND e.week_id=?"
            params.append(week_id)
        if season is not None:
            sql += " AND w.season=?"
            params.append(season)
        rows = self.query(sql + " ORDER BY w.start_date, e.created_at, e.id", tuple(params))
        for r in rows:
            r["data"] = json.loads(r.get("data") or "{}")
        return rows

    def custom_field_keys(self) -> list[str]:
        return [r["key"] for r in self.query(
            "SELECT DISTINCT key FROM custom_fields ORDER BY key")]

    # ---------------------------------------------------------------- season
    def current_season(self, today: _dt.date | None = None) -> int:
        forced = self.get_setting("active_season")
        if forced:
            return int(forced)
        return ph.season_of(today or _dt.date.today())

    def season_start(self, season: int) -> _dt.date:
        raw = self.get_setting(f"season_start:{season}")
        if raw:
            return _dt.date.fromisoformat(raw)
        return ph.default_season_start(season)

    def set_season_start(self, season: int, start: _dt.date) -> None:
        """Redefine la Semana 1. Re-etiqueta las semanas ya creadas."""
        self.set_setting(f"season_start:{season}", start.isoformat())
        for w in self.query("SELECT * FROM sampling_weeks WHERE season=?", (season,)):
            ws = ph.week_start(start, w["week_number"])
            self.execute("UPDATE sampling_weeks SET start_date=?, label=? WHERE id=?",
                         (ws.isoformat(), ph.week_label(ws), w["id"]))
        self.log("update", "season_start", season, start.isoformat())

    # ---------------------------------------------------------------- weeks
    def ensure_week(self, season: int, number: int) -> dict:
        if number < 1:
            number = 1
        row = self.query_one(
            "SELECT * FROM sampling_weeks WHERE season=? AND week_number=?", (season, number))
        if row:
            return row
        start = ph.week_start(self.season_start(season), number)
        self.execute(
            "INSERT OR IGNORE INTO sampling_weeks(season, week_number, start_date, label) "
            "VALUES (?,?,?,?)", (season, number, start.isoformat(), ph.week_label(start)))
        return self.query_one(
            "SELECT * FROM sampling_weeks WHERE season=? AND week_number=?", (season, number))

    def ensure_weeks(self, season: int, up_to: int) -> list[dict]:
        for n in range(1, max(1, up_to) + 1):
            self.ensure_week(season, n)
        return self.list_weeks(season)

    def list_weeks(self, season: int, include_skipped: bool = True) -> list[dict]:
        """Semanas de la temporada. include_skipped=False quita las marcadas como
        «no muestreada» (los informes de varias semanas no las muestran)."""
        sql = "SELECT * FROM sampling_weeks WHERE season=?"
        if not include_skipped:
            sql += " AND COALESCE(skipped, 0) = 0"
        return self.query(sql + " ORDER BY week_number", (season,))

    def set_week_skipped(self, week_id: int, skipped: bool) -> None:
        self.execute("UPDATE sampling_weeks SET skipped=? WHERE id=?", (int(bool(skipped)), week_id))
        self.log("update", "week", week_id, "omitida" if skipped else "incluida")

    def get_week(self, week_id: int) -> dict | None:
        return self.query_one("SELECT * FROM sampling_weeks WHERE id=?", (week_id,))

    def week_for_date(self, date: _dt.date, season: int | None = None) -> dict:
        season = season if season is not None else ph.season_of(date)
        n = ph.week_number_for(date, self.season_start(season))
        return self.ensure_week(season, max(1, n))

    def current_week(self, today: _dt.date | None = None) -> dict:
        today = today or _dt.date.today()
        season = self.current_season(today)
        week = self.week_for_date(today, season)
        # Garantiza que existan todas las semanas previas (navegación continua).
        self.ensure_weeks(season, week["week_number"])
        return week

    # ----------------------------------------------------------- observations
    def get_observation(self, variety_id: int, week_id: int) -> dict | None:
        return self.query_one(
            "SELECT * FROM observations WHERE variety_id=? AND week_id=?", (variety_id, week_id))

    def get_or_create_observation(self, variety_id: int, week_id: int) -> dict:
        obs = self.get_observation(variety_id, week_id)
        if obs:
            return obs
        now = _now()
        self.execute(
            "INSERT OR IGNORE INTO observations(variety_id, week_id, created_at, updated_at) "
            "VALUES (?,?,?,?)", (variety_id, week_id, now, now))
        obs = self.get_observation(variety_id, week_id)
        self.log("create", "observation", obs["id"], f"variety={variety_id} week={week_id}")
        return obs

    def update_observation(self, observation_id: int, **fields: Any) -> None:
        allowed = {"bbch_code", "bbch_label", "ai_code", "ai_confidence", "ai_detail",
                   "ai_accepted", "notes", "observed_at", "latitude", "longitude",
                   "gps_accuracy", "gps_source"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.execute(f"UPDATE observations SET {cols}, updated_at=? WHERE id=?",
                     (*sets.values(), _now(), observation_id))
        self.log("update", "observation", observation_id,
                 json.dumps({k: v for k, v in sets.items() if k != "ai_detail"},
                            ensure_ascii=False))

    def previous_observation(self, variety_id: int, season: int, week_number: int) -> dict | None:
        """Última observación con BBCH de la variedad antes de `week_number`."""
        return self.query_one(
            "SELECT o.*, w.week_number FROM observations o "
            "JOIN sampling_weeks w ON w.id = o.week_id "
            "WHERE o.variety_id=? AND w.season=? AND w.week_number<? AND o.bbch_code IS NOT NULL "
            "ORDER BY w.week_number DESC LIMIT 1", (variety_id, season, week_number))

    def count_observations(self, season: int) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM observations o JOIN sampling_weeks w ON w.id=o.week_id "
            "WHERE w.season=? AND (o.bbch_code IS NOT NULL OR EXISTS "
            "(SELECT 1 FROM photos p WHERE p.observation_id=o.id))", (season,))
        return row["n"]

    # ----------------------------------------------------------------- photos
    def add_photo(self, observation_id: int, kind: str, path: str, source: str = "camera",
                  captured_at: str | None = None, primary: bool | None = None) -> int:
        """Agrega una foto; la primera de cada tipo queda como principal."""
        with self._lock:
            has_primary = self.query_one(
                "SELECT 1 FROM photos WHERE observation_id=? AND kind=? AND is_primary=1",
                (observation_id, kind))
            make_primary = (not has_primary) if primary is None else primary
            cur = self.execute(
                "INSERT INTO photos(observation_id, kind, path, source, captured_at, is_primary) "
                "VALUES (?,?,?,?,?,0)", (observation_id, kind, path, source, captured_at or _now()))
            if make_primary:
                self.set_primary(cur.lastrowid)
        self.log("add", "photo", cur.lastrowid, f"{kind} <- {os.path.basename(path)} ({source})")
        return cur.lastrowid

    def set_photo(self, observation_id: int, kind: str, path: str,
                  source: str = "camera", captured_at: str | None = None) -> int:
        """Agrega la foto y la deja como principal (compatibilidad con v1)."""
        return self.add_photo(observation_id, kind, path, source, captured_at, primary=True)

    def set_primary(self, photo_id: int) -> None:
        p = self.query_one("SELECT observation_id, kind FROM photos WHERE id=?", (photo_id,))
        if not p:
            return
        with self._lock:
            self.conn.execute("UPDATE photos SET is_primary=0 WHERE observation_id=? AND kind=?",
                              (p["observation_id"], p["kind"]))
            self.conn.execute("UPDATE photos SET is_primary=1 WHERE id=?", (photo_id,))
            self.conn.commit()

    def get_photos(self, observation_id: int) -> dict[str, dict]:
        """Foto PRINCIPAL de cada tipo: {'canopy': fila, 'detail': fila}."""
        return {r["kind"]: r for r in self.query(
            "SELECT * FROM photos WHERE observation_id=? AND is_primary=1", (observation_id,))}

    def list_photos(self, observation_id: int, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM photos WHERE observation_id=?"
        params: tuple = (observation_id,)
        if kind:
            sql += " AND kind=?"
            params += (kind,)
        return self.query(sql + " ORDER BY is_primary DESC, captured_at, id", params)

    def delete_photo(self, photo_id: int) -> None:
        p = self.query_one("SELECT * FROM photos WHERE id=?", (photo_id,))
        # Si aún no se había subido a Drive, ya no hay nada que subir.
        self.execute("UPDATE drive_queue SET status='skipped', error='Foto eliminada en la app' "
                     "WHERE photo_id=? AND status IN ('pending', 'error')", (photo_id,))
        self.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        if p and p["is_primary"]:  # promover la siguiente como principal
            nxt = self.query_one("SELECT id FROM photos WHERE observation_id=? AND kind=? "
                                 "ORDER BY captured_at DESC, id DESC LIMIT 1",
                                 (p["observation_id"], p["kind"]))
            if nxt:
                self.set_primary(nxt["id"])
        self.log("delete", "photo", photo_id)

    def list_detail_photos(self, season: int | None = None) -> list[dict]:
        """Fotos de detalle con su BBCH asignado (para etiquetado en la calibración)."""
        sql = ("SELECT p.*, o.bbch_code, o.variety_id, v.name AS variety_name, "
               "w.week_number, w.start_date, w.label AS week_label, w.season, "
               "0 AS in_reference "
               "FROM photos p JOIN observations o ON o.id = p.observation_id "
               "JOIN varieties v ON v.id = o.variety_id "
               "JOIN sampling_weeks w ON w.id = o.week_id WHERE p.kind='detail'")
        params: tuple = ()
        if season is not None:
            sql += " AND w.season=?"
            params = (season,)
        rows = self.query(sql + " ORDER BY w.week_number DESC, v.sort_order", params)
        in_ref = self.referenced_photo_ids()
        for r in rows:
            r["in_reference"] = int(r["id"] in in_ref)
        return rows

    # ---------------------------------------------------------- aggregations
    def week_overview(self, week_id: int, include_archived: bool = False) -> list[dict]:
        """Una fila por variedad con su observación y fotos para la semana indicada."""
        out = []
        for v in self.list_varieties(include_archived=include_archived):
            obs = self.get_observation(v["id"], week_id)
            photos = self.get_photos(obs["id"]) if obs else {}
            out.append({"variety": v, "observation": obs, "photos": photos})
        return out

    def variety_timeline(self, variety_id: int, season: int) -> list[dict]:
        rows = self.query(
            "SELECT o.*, w.week_number, w.start_date, w.label AS week_label "
            "FROM observations o JOIN sampling_weeks w ON w.id=o.week_id "
            "WHERE o.variety_id=? AND w.season=? AND COALESCE(w.skipped, 0) = 0 "
            "ORDER BY w.week_number",
            (variety_id, season))
        for r in rows:
            r["photos"] = self.get_photos(r["id"])
        return rows

    def phenology_matrix(self, season: int) -> dict[tuple[int, int], dict]:
        """{(variety_id, week_number): observación} para la temporada."""
        rows = self.query(
            "SELECT o.*, w.week_number FROM observations o "
            "JOIN sampling_weeks w ON w.id=o.week_id WHERE w.season=?", (season,))
        return {(r["variety_id"], r["week_number"]): r for r in rows}

    def seasons(self) -> list[int]:
        rows = self.query("SELECT DISTINCT season FROM sampling_weeks ORDER BY season DESC")
        return [r["season"] for r in rows]

    # ------------------------------------------------------------------ BBCH
    def list_bbch(self) -> list[dict]:
        """Escala ordenada por su posición (los subestadios 89-1… van entre 89 y 91)."""
        return ph.bbch_sorted(self.ai_db.query("SELECT * FROM bbch_stages"))

    def bbch_names(self) -> dict[int, str]:
        return {r["code"]: r["label"] for r in self.list_bbch()}

    def upsert_bbch(self, code: int, label: str, description: str = "",
                    keywords: str = "", source: str = "user") -> None:
        self.ai_db.execute(
            "INSERT INTO bbch_stages(code, label, description, keywords, source) "
            "VALUES (?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET label=excluded.label, "
            "description=excluded.description, keywords=excluded.keywords, "
            "source=excluded.source", (int(code), label, description, keywords, source))

    # --------------------------------------------------------- AI references
    def add_reference(self, bbch_code: int, extractor: str, embedding: bytes, dim: int,
                      image_path: str | None = None, photo_id: int | None = None) -> int:
        """La referencia se guarda en la memoria común; photo_id se refiere a una foto de
        ESTE ensayo (se distingue por el código del ensayo)."""
        ai = self.ai_db
        if photo_id is not None:
            # Una foto solo aporta una referencia por extractor (re-etiquetar la reemplaza).
            ai.execute("DELETE FROM ai_references WHERE photo_id=? AND extractor=? "
                       "AND COALESCE(workspace, '')=?", (photo_id, extractor, self.code))
        cur = ai.execute(
            "INSERT INTO ai_references(bbch_code, extractor, embedding, dim, image_path, "
            "photo_id, created_at, workspace) VALUES (?,?,?,?,?,?,?,?)",
            (int(bbch_code), extractor, embedding, dim, image_path, photo_id, _now(), self.code))
        self.log("create", "ai_reference", cur.lastrowid, f"BBCH {bbch_code}")
        return cur.lastrowid

    def list_references(self, extractor: str | None = None) -> list[dict]:
        if extractor:
            return self.ai_db.query("SELECT * FROM ai_references WHERE extractor=? ORDER BY id",
                                    (extractor,))
        return self.ai_db.query("SELECT * FROM ai_references ORDER BY id")

    def referenced_photo_ids(self) -> set[int]:
        """Fotos de este ensayo que ya están en la memoria de la IA."""
        return {r["photo_id"] for r in self.ai_db.query(
            "SELECT photo_id FROM ai_references WHERE photo_id IS NOT NULL "
            "AND COALESCE(workspace, '')=?", (self.code,))}

    def delete_reference(self, ref_id: int) -> None:
        self.ai_db.execute("DELETE FROM ai_references WHERE id=?", (ref_id,))
        self.log("delete", "ai_reference", ref_id)

    def reference_counts(self) -> dict[int, int]:
        return {r["bbch_code"]: r["n"] for r in self.ai_db.query(
            "SELECT bbch_code, COUNT(*) AS n FROM ai_references GROUP BY bbch_code")}

    # -------------------------------------------------------------- documents
    def add_document(self, title: str, path: str | None, text: str, stages_found: int) -> int:
        cur = self.ai_db.execute(
            "INSERT INTO documents(title, path, text, stages_found, created_at) "
            "VALUES (?,?,?,?,?)", (title, path, text, stages_found, _now()))
        self.log("create", "document", cur.lastrowid, title)
        return cur.lastrowid

    def list_documents(self) -> list[dict]:
        return self.ai_db.query("SELECT id, title, path, stages_found, created_at, "
                                "LENGTH(text) AS chars FROM documents ORDER BY id DESC")

    def documents_text(self) -> str:
        return "\n".join(r["text"] for r in self.ai_db.query("SELECT text FROM documents"))

    def delete_document(self, doc_id: int) -> None:
        self.ai_db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.log("delete", "document", doc_id)


def default_db_path() -> str:
    from platform_utils import get_data_dir
    return os.path.join(get_data_dir(), "fenorubus.sqlite3")
