import datetime as dt
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import phenology as ph  # noqa: E402
from ai_classifier import (PhenologyClassifier, PinGuard, HandcraftedExtractor,  # noqa: E402
                           parse_bbch_entries)
from database import Database  # noqa: E402
from notifications import ReminderConfig, ReminderManager, next_trigger  # noqa: E402
from reporter import ReportGenerator  # noqa: E402
from tools.demo_data import synthetic_photo  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.sqlite3"))
    yield d
    d.close()


# ------------------------------------------------------------------ semanas
def test_week_labels_start_on_september_7():
    start = ph.default_season_start(2026)
    weeks = ph.generate_weeks(start, 3)
    assert [w.label for w in weeks] == ["Semana del 7 de septiembre",
                                        "Semana del 14 de septiembre",
                                        "Semana del 21 de septiembre"]
    assert ph.week_number_for(dt.date(2026, 9, 13), start) == 1
    assert ph.week_number_for(dt.date(2026, 9, 14), start) == 2
    assert ph.season_of(dt.date(2027, 2, 1)) == 2026


def test_current_week_and_season_start_relabel(db):
    w = db.current_week(dt.date(2026, 9, 25))
    assert w["week_number"] == 3 and w["label"] == "Semana del 21 de septiembre"
    assert len(db.list_weeks(2026)) == 3
    db.set_season_start(2026, dt.date(2026, 9, 1))
    assert db.list_weeks(2026)[0]["label"] == "Semana del 1 de septiembre"


# --------------------------------------------------------------- variedades
def test_default_varieties_and_crud(db):
    names = [v["name"] for v in db.list_varieties()]
    assert names[:5] == ["Código 11", "Código 31", "Código 24", "Código 55", "Código 81"]
    assert len(names) == 10 and "Wakefield" in names
    vid = db.add_variety("Heritage")
    with pytest.raises(ValueError):
        db.add_variety("heritage")
    db.delete_variety(vid)
    assert "Heritage" not in [v["name"] for v in db.list_varieties()]
    assert db.add_variety("Heritage") == vid  # restaurada
    db.delete_variety(vid, purge=True)
    assert db.get_variety(vid) is None


def test_metrics_and_custom_fields(db):
    vid = db.list_varieties()[0]["id"]
    db.save_metrics(vid, 2026, historical_yield=1.8, basal_canes=7)
    db.save_metrics(vid, 2026, projected_yield=2.1)
    m = db.get_metrics(vid, 2026)
    assert (m["historical_yield"], m["projected_yield"], m["basal_canes"]) == (1.8, 2.1, 7)
    db.set_custom_field(vid, 2026, "Diámetro de caña", "9.5", "mm")
    db.set_custom_field(vid, 2026, "Diámetro de caña", "10.1", "mm")
    fields = db.list_custom_fields(vid, 2026)
    assert len(fields) == 1 and fields[0]["value"] == "10.1"


# ----------------------------------------------------------- recordatorios
def test_next_trigger_friday_default():
    cfg = ReminderConfig()
    thu = dt.datetime(2026, 9, 24, 12, 0)
    assert next_trigger(cfg, thu) == dt.datetime(2026, 9, 25, 9, 0)
    fri_after = dt.datetime(2026, 9, 25, 9, 0)
    assert next_trigger(cfg, fri_after) == dt.datetime(2026, 10, 2, 9, 0)


def test_next_trigger_biweekly_anchor(db):
    mgr = ReminderManager(db)
    cfg = ReminderConfig(weekday=0, hour=8, minute=30, every_weeks=2)
    first = mgr.save(cfg, now=dt.datetime(2026, 9, 25, 10, 0))
    assert first == dt.datetime(2026, 9, 28, 8, 30)
    after = mgr.next_time(now=dt.datetime(2026, 9, 28, 9, 0))
    assert after == dt.datetime(2026, 10, 12, 8, 30)


# ------------------------------------------------------------------- IA
def test_classifier_priors_and_knn(db, tmp_path):
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    flower = synthetic_photo(65, "detail", str(tmp_path / "f.jpg"), 1)
    berry = synthetic_photo(87, "detail", str(tmp_path / "b.jpg"), 2)
    # Sin referencias: priors de color + calendario deben distinguir flor y fruto maduro.
    s_flower = clf.suggest(flower, week_number=10)
    s_berry = clf.suggest(berry, week_number=18)
    assert s_flower.code // 10 == 6, s_flower
    assert s_berry.code // 10 == 8, s_berry
    # No retroceso fenológico respecto del registro previo.
    s = clf.suggest(flower, week_number=12, previous=(71, 11))
    assert s.code >= 69
    # k-NN con referencias etiquetadas.
    for i, code in enumerate([65, 65, 87, 87, 51, 51]):
        p = synthetic_photo(code, "detail", str(tmp_path / f"r{i}.jpg"), 100 + i)
        clf.add_reference(p, code)
    ev = clf.evaluate()
    assert ev["n"] == 6 and ev["macro"] >= 0.5
    assert clf.suggest(berry, week_number=18).code // 10 == 8


def test_pin_guard(db):
    guard = PinGuard(db)
    assert not guard.verify("0000")
    assert guard.verify("1234")
    assert guard.change("1234", "2468")
    assert guard.verify("2468") and not guard.verify("1234")


def test_document_parsing(db, tmp_path):
    doc = tmp_path / "clave.txt"
    doc.write_text("Clave fenológica\nBBCH 65: Plena floración, 50% de flores abiertas\n"
                   "BBCH 58 - Botones con pétalos rosados visibles\n", encoding="utf-8")
    assert set(parse_bbch_entries(doc.read_text(encoding="utf-8"))) == {65, 58}
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    _doc_id, n = clf.import_document(str(doc))
    assert n == 2 and 58 in db.bbch_names()


# --------------------------------------------------------------- reportes
def test_reports(db, tmp_path):
    season = 2026
    db.ensure_weeks(season, 4)
    for vi, v in enumerate(db.list_varieties()[:3]):
        for w in db.list_weeks(season):
            obs = db.get_or_create_observation(v["id"], w["id"])
            code = [9, 15, 31, 51][w["week_number"] - 1] + vi
            p = synthetic_photo(code, "detail", str(tmp_path / f"{vi}_{w['week_number']}.jpg"), vi)
            db.set_photo(obs["id"], "detail", p)
            db.update_observation(obs["id"], bbch_code=code, notes="obs <b>")
    rep = ReportGenerator(db, str(tmp_path / "out"))
    wk = db.list_weeks(season)[1]
    r1 = rep.weekly(wk["id"])
    html = open(r1.path, encoding="utf-8").read()
    assert "data:image/jpeg;base64," in html and "obs &lt;b&gt;" in html
    assert "Semana del 14 de septiembre" in html
    r2 = rep.monthly(season, 2026, 9)
    r3 = rep.variety(db.list_varieties()[0]["id"], season)
    assert "<svg" in open(r3.path, encoding="utf-8").read()
    r4 = rep.matrix(season, package="zip")
    with zipfile.ZipFile(r4.path) as zf:
        names = zf.namelist()
    assert "index.html" in names and any(n.startswith("img/") for n in names)
    assert all(os.path.exists(r.path) for r in (r1, r2, r3, r4))


def test_suggestion_detail_is_json(db, tmp_path):
    import json
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    for i, code in enumerate([65, 87, 51]):
        clf.add_reference(synthetic_photo(code, "detail", str(tmp_path / f"r{i}.jpg"), i), code)
    s = clf.suggest(synthetic_photo(65, "detail", str(tmp_path / "q.jpg"), 9), week_number=10)
    assert json.loads(s.as_detail_json())["top"][0][0] == s.code


def test_preview_mode_and_summary(db, tmp_path):
    from reporter import report_summary
    season = 2026
    db.ensure_weeks(season, 2)
    v = db.list_varieties()[0]
    w = db.list_weeks(season)[0]
    obs = db.get_or_create_observation(v["id"], w["id"])
    db.set_photo(obs["id"], "detail", synthetic_photo(65, "detail", str(tmp_path / "d.jpg"), 1))
    sm = report_summary(db, "weekly", season, week_id=w["id"])
    assert sm["cells"] == 10 and sm["photos"] == 1 and sm["complete"] == 0
    assert any("Código 11 · S1: falta foto canopia, estado BBCH" == m for m in sm["missing"])
    rep = ReportGenerator(db, str(tmp_path / "reports"))
    res = rep.weekly(w["id"], "preview")
    html = open(res.path, encoding="utf-8").read()
    assert res.path.endswith("vista_previa.html") and "file://" in html
    assert "base64" not in html                      # imágenes livianas, no embebidas
    assert os.listdir(str(tmp_path / "reports")) == []  # no se guarda como informe


def test_any_start_week_and_reset(db):
    db.set_season_start(2026, dt.date(2026, 8, 20))
    w = db.current_week(dt.date(2026, 9, 25))
    assert w["label"] == "Semana del 24 de septiembre" and w["week_number"] == 6
    db.set_season_start(2026, ph.default_season_start(2026))
    assert db.list_weeks(2026)[0]["label"] == "Semana del 7 de septiembre"


def test_multiple_photos_primary_average_and_report(db, tmp_path):
    season = 2026
    db.ensure_weeks(season, 1)
    v, w = db.list_varieties()[0], db.list_weeks(season)[0]
    obs = db.get_or_create_observation(v["id"], w["id"])
    paths = [synthetic_photo(65, "detail", str(tmp_path / f"d{i}.jpg"), i) for i in range(3)]
    ids = [db.add_photo(obs["id"], "detail", p, "gallery") for p in paths]
    assert db.get_photos(obs["id"])["detail"]["id"] == ids[0]      # la primera es principal
    db.set_primary(ids[2])
    assert db.get_photos(obs["id"])["detail"]["id"] == ids[2]
    assert [p["id"] for p in db.list_photos(obs["id"], "detail")][0] == ids[2]
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    s = clf.suggest(paths, week_number=10)
    assert s.components["fotos"] == 3 and s.code // 10 == 6
    rep = ReportGenerator(db, str(tmp_path / "out"))
    rep.include_all_photos = True
    html = open(rep.weekly(w["id"]).path, encoding="utf-8").read()
    assert html.count('alt="Foto adicional"') == 2
    db.delete_photo(ids[2])
    assert db.get_photos(obs["id"])["detail"]["id"] in ids[:2]


def test_migrates_v1_photos_table(tmp_path):
    import sqlite3
    path = str(tmp_path / "v1.sqlite3")
    Database(path).close()
    con = sqlite3.connect(path)
    con.executescript("""
        DROP TABLE photos;
        CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, observation_id INTEGER NOT NULL,
            kind TEXT NOT NULL, path TEXT NOT NULL, source TEXT DEFAULT 'camera',
            captured_at TEXT NOT NULL, UNIQUE (observation_id, kind));
        INSERT INTO sampling_weeks(season, week_number, start_date, label) VALUES (2026, 1, '2026-09-07', 'x');
        INSERT INTO observations(variety_id, week_id, created_at, updated_at) VALUES (1, 1, 'x', 'x');
        INSERT INTO photos(observation_id, kind, path, captured_at) VALUES (1, 'detail', '/a.jpg', 'x');
    """)
    con.commit(); con.close()
    db = Database(path)
    assert db.get_photos(1)["detail"]["path"] == "/a.jpg"
    db.add_photo(1, "detail", "/b.jpg")                    # ya no hay UNIQUE
    assert len(db.list_photos(1, "detail")) == 2
    db.close()


def test_daily_challenge_game(db, tmp_path):
    from ai_game import DailyChallenge
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    season = 2026
    db.ensure_weeks(season, 3)
    for i, v in enumerate(db.list_varieties()[:4]):          # fotos para «¿qué estado es?»
        obs = db.get_or_create_observation(v["id"], db.list_weeks(season)[1]["id"])
        db.add_photo(obs["id"], "detail", synthetic_photo(65, "detail", str(tmp_path / f"p{i}.jpg"), i))
        if i < 2:
            db.update_observation(obs["id"], bbch_code=65)
    game = DailyChallenge(db, lambda: clf)
    day = dt.date(2026, 9, 28)
    chs = game.ensure_today(day)
    assert len(chs) == 5 and [c["kind"] for c in chs].count("identify") == 3
    assert game.ensure_today(day) == chs                       # determinista / idempotente
    for ch in chs:
        if ch["kind"] == "identify":
            res = game.answer_identify(ch["id"], ch["payload"]["options"][0])
            assert res["label"] in ch["payload"]["options"] + ([ch["payload"]["truth"]] if ch["payload"]["truth"] is not None else [])
        else:
            res = game.complete_capture(ch["id"], synthetic_photo(ch["payload"]["target"], "detail",
                                                                  str(tmp_path / f"c{ch['id']}.jpg"), 9))
            assert res["target"] == ch["payload"]["target"]
    st = game.stats()
    assert st["streak"] == 1 and st["last_day"] == "2026-09-28" and st["xp"] > 0
    assert sum(db.reference_counts().values()) == 5
    # Día siguiente: racha continúa; saltar todo no la suma.
    chs2 = game.ensure_today(day + dt.timedelta(days=1))
    for ch in chs2:
        game.skip(ch["id"])
    assert game.stats()["streak"] == 1


def test_historical_import_zip(db, tmp_path):
    import zipfile as zf_
    from importer import import_file, write_template
    tpl = write_template(str(tmp_path / "plantilla.csv"))
    z = tmp_path / "historico.zip"
    with zf_.ZipFile(z, "w") as zf:
        zf.write(tpl, "datos/plantilla.csv")
        for n, code in (("meeker_s07_canopia.jpg", 57), ("meeker_s07_detalle.jpg", 57),
                        ("regina_s10_detalle.jpg", 65), ("regina_s10_detalle2.jpg", 65)):
            zf.write(synthetic_photo(code, "detail", str(tmp_path / n), code), f"fotos/{n}")
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    res = import_file(db, str(z), classifier=clf)
    assert res.rows == 3 and res.observations == 3 and res.photos == 4, res.errors
    assert res.references == 3 and res.seasons == {2023, 2024} and not res.errors
    w = db.week_for_date(dt.date(2024, 10, 18), 2024)
    obs = db.get_observation(db.query_one("SELECT id FROM varieties WHERE name='Meeker'")["id"], w["id"])
    assert obs["bbch_code"] == 57 and len(db.list_photos(obs["id"])) == 2
    regina = db.query_one("SELECT id FROM varieties WHERE name='Regina'")["id"]
    assert db.get_metrics(db.query_one("SELECT id FROM varieties WHERE name='Meeker'")["id"], 2024)["historical_yield"] == 1.6
    assert db.get_observation(regina, db.ensure_week(2024, 10)["id"])["bbch_code"] == 65


def test_drive_backup_queue_with_fake_api(db, tmp_path):
    import json
    from drive_backup import DriveBackup, Offline, ROOT_FOLDER

    week = db.current_week()
    v = db.list_varieties()[0]
    obs = db.get_or_create_observation(v["id"], week["id"])
    paths = []
    for i in range(2):
        p = str(tmp_path / f"meeker_detalle_{i}.jpg")
        synthetic_photo(55, "detail", p, i)
        paths.append(p)

    calls, folders, files = [], {}, {}
    state = {"offline": False, "expire": True}

    def transport(method, url, headers, body):
        calls.append((method, url))
        if state["offline"]:
            raise Offline("sin red")
        assert headers["Authorization"].startswith("Bearer ")
        if state["expire"] and headers["Authorization"] == "Bearer t1":
            state["expire"] = False
            return 401, b'{"error": {"message": "expired"}}'
        if method == "GET":  # búsqueda de carpeta
            return 200, json.dumps({"files": []}).encode()
        if "uploadType=multipart" in url:
            assert b"image/jpeg" in body and b'"parents"' in body
            fid = f"f{len(files)}"
            files[fid] = body
            return 200, json.dumps({"id": fid}).encode()
        meta = json.loads(body)
        fid = f"d{len(folders)}"
        folders[fid] = meta
        return 200, json.dumps({"id": fid}).encode()

    tokens = iter(["t1", "t2", "t3"])

    class Auth:
        def get_token(self, interactive):
            return next(tokens)

    drive = DriveBackup(db, authorizer=Auth(), transport=transport, metered=lambda: False)
    assert drive.enqueue(None, paths[0]) is not None  # desactivado: queda en cola
    assert drive.flush() == 0 and drive.status()["pending"] == 1

    db.set_setting("drive_enabled", True)
    state["offline"] = True
    pid = db.add_photo(obs["id"], "detail", paths[1])
    drive.enqueue(pid, paths[1], flush=False)
    assert drive.enqueue(pid, paths[1], flush=False) is None  # sin duplicados
    assert drive.flush() == 0
    st = drive.status()
    assert st["pending"] == 2 and "conexión" in st["message"]

    state["offline"] = False
    assert drive.flush() == 2  # token vencido (401) → se renueva y sigue
    st = drive.status()
    assert st["done"] == 2 and st["pending"] == 0 and st["last_sync"]
    names = [m["name"] for m in folders.values()]
    assert ROOT_FOLDER in names and f"Temporada {week['season']}-{week['season'] + 1}" in names
    row = db.query_one("SELECT * FROM drive_queue WHERE photo_id=?", (pid,))
    assert row["drive_id"] in files and row["name"].endswith("meeker_detalle_1.jpg")

    # Solo Wi-Fi: con datos móviles no sube.
    drive.metered = lambda: True
    drive.enqueue(None, paths[0], flush=False)
    assert drive.flush() == 0 and drive.status()["message"] == "Esperando Wi-Fi"
    # «Subir anteriores» encola las fotos de la base que falten (la de pid ya está).
    assert drive.enqueue_existing() == 0


def _fill_week(db, tmp_path, season=2026, n=3):
    db.ensure_weeks(season, n)
    out = []
    for w in db.list_weeks(season)[:n]:
        for v in db.list_varieties()[:2]:
            obs = db.get_or_create_observation(v["id"], w["id"])
            code = 10 + w["week_number"] * 5
            db.update_observation(obs["id"], bbch_code=code, bbch_label=f"BBCH {code}",
                                  notes=f"nota {v['name']} S{w['week_number']}")
            for kind in ("canopy", "detail"):
                p = synthetic_photo(code, kind, str(tmp_path / f"{v['id']}_{w['id']}_{kind}.jpg"), 1)
                db.add_photo(obs["id"], kind, p, "camera",
                             captured_at=f"2026-09-{7 + w['week_number']:02d}T10:00:00")
            out.append((v, w))
    return out


def test_full_backup_roundtrip(db, tmp_path):
    from data_transfer import full_backup, restore
    _fill_week(db, tmp_path)
    zpath = full_backup(db, str(tmp_path))
    for r in db.query("SELECT path FROM photos"):
        os.remove(r["path"])             # «reinstalación»: se pierden los archivos
    fresh = Database(str(tmp_path / "nuevo.sqlite3"))
    res = restore(fresh, zpath)
    assert res.observations == 6 and res.photos == 12 and res.photos_restored == 12
    assert all(os.path.exists(r["path"]) for r in fresh.query("SELECT path FROM photos"))
    assert os.path.exists(res.safety_copy)
    fresh.close()


def test_restore_old_sqlite_relinks_public_photos(db, tmp_path, monkeypatch):
    import shutil
    import data_transfer as dt_
    from platform_utils import slugify
    pairs = _fill_week(db, tmp_path)
    # Originales en la carpeta pública con el nombre que les daba la cámara.
    public = tmp_path / "Imagenes de Fenologia"
    public.mkdir()
    for r in db.query("SELECT p.path, p.kind, p.captured_at, v.name, w.week_number FROM photos p "
                      "JOIN observations o ON o.id=p.observation_id JOIN varieties v ON v.id=o.variety_id "
                      "JOIN sampling_weeks w ON w.id=o.week_id"):
        label = {"canopy": "canopia", "detail": "detalle"}[r["kind"]]
        stamp = r["captured_at"][:10] + "_" + r["captured_at"][11:19].replace(":", "")
        shutil.copyfile(r["path"], public / f"{slugify(r['name'])}_S{r['week_number']:02d}_{label}_{stamp}.jpg")
    backup = tmp_path / "fenorubus_20260928.sqlite3"
    dt_._checkpoint_copy(db, str(backup))
    for r in db.query("SELECT path FROM photos"):
        os.remove(r["path"])
    monkeypatch.setattr(dt_, "public_photo_dirs", lambda: [str(public)])
    fresh = Database(str(tmp_path / "nuevo.sqlite3"))
    fresh.set_setting("drive_enabled", True)
    res = dt_.restore(fresh, str(backup))
    assert res.photos_relinked == 12 and res.photos_missing == 0
    assert fresh.get_setting("drive_enabled") is True     # se conserva la conexión del teléfono
    v, w = pairs[0]
    obs = fresh.get_observation(v["id"], w["id"])
    assert obs["notes"].startswith("nota") and obs["bbch_code"] == 15
    fresh.close()


def test_import_weekly_reports_html_and_zip(db, tmp_path):
    from data_transfer import import_reports
    pairs = _fill_week(db, tmp_path)
    rep = ReportGenerator(db, str(tmp_path / "out"))
    weeks = sorted({w["id"] for _v, w in pairs})
    files = [rep.weekly(weeks[0]).path, rep.weekly(weeks[1], package="zip").path,
             rep.weekly(weeks[2]).path]
    fresh = Database(str(tmp_path / "nuevo.sqlite3"))
    res = import_reports(fresh, files)
    assert res.reports == 3 and res.bbch == 6 and res.photos == 12, res.summary()
    v, w = pairs[-1]
    fv = next(x for x in fresh.list_varieties() if x["name"] == v["name"])
    fw = next(x for x in fresh.list_weeks(2026) if x["week_number"] == w["week_number"])
    obs = fresh.get_observation(fv["id"], fw["id"])
    assert obs["bbch_code"] == 10 + w["week_number"] * 5 and obs["notes"].startswith("nota")
    assert set(fresh.get_photos(obs["id"])) == {"canopy", "detail"}
    again = import_reports(fresh, files)                   # no duplica
    assert again.photos == 0 and again.bbch == 0
    fresh.close()
