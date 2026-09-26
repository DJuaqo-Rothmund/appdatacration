"""
database.py
===========
Persistencia local SQLite (offline-first) con trazabilidad.

Entidades
---------
varieties        Catálogo dinámico de variedades (CRUD, archivado suave).
variety_metrics  Parámetros biométricos por variedad y temporada.
custom_fields    Campos personalizados llave-valor por variedad y temporada.
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

SCHEMA_VERSION = 1

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
    UNIQUE (observation_id, kind)
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
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class Database:
    """Acceso thread-safe (una conexión + lock) a la base SQLite local."""

    def __init__(self, path: str, seed: bool = True):
        self.path = path
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
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
            self.conn.commit()

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
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
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
                    sort_order: int | None = None, log: bool = True) -> int:
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
            "INSERT INTO varieties(name, code, notes, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)", (name, code or "", notes, sort_order, _now(), _now()))
        if log:
            self.log("create", "variety", cur.lastrowid, name)
        return cur.lastrowid

    def update_variety(self, variety_id: int, **fields: Any) -> None:
        allowed = {"name", "code", "notes", "sort_order", "active"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.execute(f"UPDATE varieties SET {cols}, updated_at=? WHERE id=?",
                     (*sets.values(), _now(), variety_id))
        self.log("update", "variety", variety_id, json.dumps(sets, ensure_ascii=False))

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

    def list_weeks(self, season: int) -> list[dict]:
        return self.query(
            "SELECT * FROM sampling_weeks WHERE season=? ORDER BY week_number", (season,))

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
                   "ai_accepted", "notes", "observed_at"}
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
    def set_photo(self, observation_id: int, kind: str, path: str,
                  source: str = "camera", captured_at: str | None = None) -> int:
        old = self.query_one("SELECT * FROM photos WHERE observation_id=? AND kind=?",
                             (observation_id, kind))
        self.execute(
            "INSERT INTO photos(observation_id, kind, path, source, captured_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(observation_id, kind) DO UPDATE SET "
            "path=excluded.path, source=excluded.source, captured_at=excluded.captured_at",
            (observation_id, kind, path, source, captured_at or _now()))
        row = self.query_one("SELECT id FROM photos WHERE observation_id=? AND kind=?",
                             (observation_id, kind))
        detail = f"{kind} <- {os.path.basename(path)} ({source})"
        if old and old["path"] != path:
            detail += f"; reemplaza {os.path.basename(old['path'])}"
        self.log("set", "photo", row["id"], detail)
        return row["id"]

    def get_photos(self, observation_id: int) -> dict[str, dict]:
        return {r["kind"]: r for r in self.query(
            "SELECT * FROM photos WHERE observation_id=?", (observation_id,))}

    def delete_photo(self, photo_id: int) -> None:
        self.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        self.log("delete", "photo", photo_id)

    def list_detail_photos(self, season: int | None = None) -> list[dict]:
        """Fotos de detalle con su BBCH asignado (para etiquetado en la calibración)."""
        sql = ("SELECT p.*, o.bbch_code, o.variety_id, v.name AS variety_name, "
               "w.week_number, w.label AS week_label, w.season, "
               "(SELECT COUNT(*) FROM ai_references r WHERE r.photo_id = p.id) AS in_reference "
               "FROM photos p JOIN observations o ON o.id = p.observation_id "
               "JOIN varieties v ON v.id = o.variety_id "
               "JOIN sampling_weeks w ON w.id = o.week_id WHERE p.kind='detail'")
        params: tuple = ()
        if season is not None:
            sql += " AND w.season=?"
            params = (season,)
        return self.query(sql + " ORDER BY w.week_number DESC, v.sort_order", params)

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
            "WHERE o.variety_id=? AND w.season=? ORDER BY w.week_number",
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
        return self.query("SELECT * FROM bbch_stages ORDER BY code")

    def bbch_names(self) -> dict[int, str]:
        return {r["code"]: r["label"] for r in self.list_bbch()}

    def upsert_bbch(self, code: int, label: str, description: str = "",
                    keywords: str = "", source: str = "user") -> None:
        self.execute(
            "INSERT INTO bbch_stages(code, label, description, keywords, source) "
            "VALUES (?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET label=excluded.label, "
            "description=excluded.description, keywords=excluded.keywords, "
            "source=excluded.source", (int(code), label, description, keywords, source))

    # --------------------------------------------------------- AI references
    def add_reference(self, bbch_code: int, extractor: str, embedding: bytes, dim: int,
                      image_path: str | None = None, photo_id: int | None = None) -> int:
        if photo_id is not None:
            # Una foto solo aporta una referencia por extractor (re-etiquetar la reemplaza).
            self.execute("DELETE FROM ai_references WHERE photo_id=? AND extractor=?",
                         (photo_id, extractor))
        cur = self.execute(
            "INSERT INTO ai_references(bbch_code, extractor, embedding, dim, image_path, "
            "photo_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (int(bbch_code), extractor, embedding, dim, image_path, photo_id, _now()))
        self.log("create", "ai_reference", cur.lastrowid, f"BBCH {bbch_code}")
        return cur.lastrowid

    def list_references(self, extractor: str | None = None) -> list[dict]:
        if extractor:
            return self.query("SELECT * FROM ai_references WHERE extractor=? ORDER BY id",
                              (extractor,))
        return self.query("SELECT * FROM ai_references ORDER BY id")

    def delete_reference(self, ref_id: int) -> None:
        self.execute("DELETE FROM ai_references WHERE id=?", (ref_id,))
        self.log("delete", "ai_reference", ref_id)

    def reference_counts(self) -> dict[int, int]:
        return {r["bbch_code"]: r["n"] for r in self.query(
            "SELECT bbch_code, COUNT(*) AS n FROM ai_references GROUP BY bbch_code")}

    # -------------------------------------------------------------- documents
    def add_document(self, title: str, path: str | None, text: str, stages_found: int) -> int:
        cur = self.execute(
            "INSERT INTO documents(title, path, text, stages_found, created_at) "
            "VALUES (?,?,?,?,?)", (title, path, text, stages_found, _now()))
        self.log("create", "document", cur.lastrowid, title)
        return cur.lastrowid

    def list_documents(self) -> list[dict]:
        return self.query("SELECT id, title, path, stages_found, created_at, "
                          "LENGTH(text) AS chars FROM documents ORDER BY id DESC")

    def documents_text(self) -> str:
        return "\n".join(r["text"] for r in self.query("SELECT text FROM documents"))

    def delete_document(self, doc_id: int) -> None:
        self.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.log("delete", "document", doc_id)


def default_db_path() -> str:
    from platform_utils import get_data_dir
    return os.path.join(get_data_dir(), "fenorubus.sqlite3")
