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
    r4 = rep.matrix(season, package="pdf")          # los informes se entregan en PDF
    with open(r4.path, "rb") as f:
        head = f.read(8)
    assert head.startswith(b"%PDF-") and "fotos de detalle" in open(
        rep.matrix(season).path, encoding="utf-8").read()
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
    # Fotos opcionales: con una sola (detalle) solo falta el estado; sin fotos, «fotos».
    assert any(f"Código 11 · S{ph.week_of_year(w)}: falta estado BBCH" == m for m in sm["missing"])
    assert any(m.endswith(": falta fotos, estado BBCH") for m in sm["missing"])
    assert sm["photos_expected"] == 10 and sm["missing_photos"] == 9
    db.update_observation(obs["id"], bbch_code=65, bbch_label="BBCH 65")
    assert report_summary(db, "weekly", season, week_id=w["id"])["complete"] == 1
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
            assert (b"image/jpeg" in body or b"text/html" in body) and b'"parents"' in body
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
    start = dt.date.fromisoformat(week["start_date"])
    iso_w, iso_y = start.isocalendar()[1], start.isocalendar()[0]
    assert ROOT_FOLDER in names and f"Año {iso_y}" in names
    week_dir = f"Semana {iso_w:02d} · {start:%d-%m-%Y}"
    assert week_dir in names                      # carpeta por semana de muestreo
    wk = next(fid for fid, m in folders.items() if m["name"] == week_dir)
    assert any(f'"parents": ["{wk}"]'.encode() in body for body in files.values())

    # Informes: el semanal va a la carpeta de su semana; los demás a «Informes»
    rep = ReportGenerator(db, str(tmp_path / "out"))
    weekly_path = rep.weekly(week["id"]).path
    remote = drive.enqueue_report(weekly_path, flush=False)   # sin hilos: prueba determinista
    assert os.path.basename(weekly_path).startswith(f"semanal_{iso_y}_S{iso_w:02d}")
    assert remote == f"Año {iso_y}/{week_dir}/{os.path.basename(weekly_path)}"
    other = tmp_path / f"matriz_{week['season']}.html"
    other.write_text("<html></html>", encoding="utf-8")
    assert drive.enqueue_report(str(other), flush=False).endswith(f"/Informes/{other.name}")
    assert drive.flush() == 2 and drive.status()["pending"] == 0
    names = [m["name"] for m in folders.values()]
    assert "Informes" in names
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


def test_import_weekly_reports_html_and_pdf(db, tmp_path):
    from data_transfer import import_reports
    pairs = _fill_week(db, tmp_path)
    rep = ReportGenerator(db, str(tmp_path / "out"))
    weeks = sorted({w["id"] for _v, w in pairs})
    # Informes de versiones anteriores (HTML) y los actuales (PDF con los datos incrustados).
    files = [rep.weekly(weeks[0]).path, rep.weekly(weeks[1], package="pdf").path,
             rep.weekly(weeks[2], package="pdf").path]
    assert files[1].endswith(".pdf")
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


def test_relink_early_version_timestamp_names(db, tmp_path):
    import shutil
    import datetime as _d
    from data_transfer import relink_photos
    db.ensure_weeks(2026, 1)
    w = db.list_weeks(2026)[0]
    folder = tmp_path / "FenoRubus"
    folder.mkdir()
    t0 = _d.datetime(2026, 9, 8, 10, 0, 0)
    expected = {}
    for i, v in enumerate(db.list_varieties()[:4]):
        obs = db.get_or_create_observation(v["id"], w["id"])
        for j, kind in enumerate(("canopy", "detail")):
            shot = t0 + _d.timedelta(minutes=5 * i + 2 * j)          # se abre la cámara
            src = synthetic_photo(30 + i, kind, str(tmp_path / f"o{i}{j}.jpg"), i * 2 + j)
            name = f"FenoRubus_{shot:%Y%m%d_%H%M%S}.jpg"
            shutil.copyfile(src, folder / name)
            saved = shot + _d.timedelta(seconds=40)                     # se guarda el registro
            pid = db.add_photo(obs["id"], kind, str(tmp_path / "borrada.jpg"), "camera",
                               captured_at=saved.isoformat())
            expected[pid] = os.path.getsize(folder / name)
    # Una foto de galería no debe «robar» fotos de cámara.
    gobs = db.get_or_create_observation(db.list_varieties()[5]["id"], w["id"])
    db.add_photo(gobs["id"], "detail", str(tmp_path / "x.jpg"), "gallery",
                 captured_at=(t0 + _d.timedelta(minutes=1)).isoformat())
    res = relink_photos(db, [str(tmp_path / "vacia"), str(folder)])
    assert res["relinked"] == 8 and res["missing"] == 1
    from PIL import Image
    for pid in expected:  # cada registro recibió su propia foto (comparando el contenido)
        path = db.query_one("SELECT path FROM photos WHERE id=?", (pid,))["path"]
        assert os.path.exists(path)
    paths = [db.query_one("SELECT path FROM photos WHERE id=?", (p,))["path"] for p in expected]
    assert len(set(paths)) == 8
    # Contenido correcto: la miniatura de cada foto coincide con su original.
    import numpy as np
    for pid, (i, j) in zip(expected, [(i, j) for i in range(4) for j in range(2)]):
        got = np.asarray(Image.open(db.query_one("SELECT path FROM photos WHERE id=?", (pid,))["path"])
                         .convert("L").resize((16, 16)), float)
        ref = np.asarray(Image.open(tmp_path / f"o{i}{j}.jpg").convert("L").resize((16, 16)), float)
        assert np.abs(got - ref).mean() < 8


def test_photo_names_date_variety_kind(db, tmp_path):
    import shutil
    from android_bridge import store_photo
    from data_transfer import relink_photos
    assert ph.photo_basename({"name": "Código 11", "code": "C11"}, "2026-09-28", "canopy") == "20260928-C11G"
    assert ph.photo_basename({"name": "Meeker", "code": "MEE"}, "2026-09-21", "detail") == "20260921-MeeD"
    assert ph.photo_basename({"name": "Tulameen", "code": ""}, "2026-09-21", "detail", 2) == "20260921-TulD-2"
    src = synthetic_photo(55, "detail", str(tmp_path / "s.jpg"), 0)
    out = tmp_path / "out"
    names = [os.path.basename(store_photo(src, str(out), "20260921-MeeD", exact=True)) for _ in range(3)]
    assert names == ["20260921-MeeD.jpg", "20260921-MeeD-2.jpg", "20260921-MeeD-3.jpg"]

    # Restaurar: se reconocen los nombres nuevos en la carpeta pública.
    db.ensure_weeks(2026, 3)
    w = db.list_weeks(2026)[2]
    v = next(x for x in db.list_varieties() if x["name"] == "Meeker")
    obs = db.get_or_create_observation(v["id"], w["id"])
    public = tmp_path / "pub"
    public.mkdir()
    base = ph.photo_basename(v, w["start_date"], "detail")
    ids = []
    for i, suffix in enumerate(["", "-2"]):
        shutil.copyfile(synthetic_photo(60 + i, "detail", str(tmp_path / f"p{i}.jpg"), i),
                        public / f"{base}{suffix}.jpg")
        ids.append(db.add_photo(obs["id"], "detail", str(tmp_path / f"borrada{i}.jpg"), "camera"))
    res = relink_photos(db, [str(public)])
    assert res["relinked"] == 2 and res["missing"] == 0
    got = [os.path.basename(db.query_one("SELECT path FROM photos WHERE id=?", (i,))["path"]) for i in ids]
    assert got == [f"{base}.jpg", f"{base}-2.jpg"]
    # Galería con el nombre antiguo (ddmmaaaa, hasta la 1.1.30): también se reconoce.
    legacy = ph.photo_basename(v, w["start_date"], "canopy", legacy=True)
    assert legacy != ph.photo_basename(v, w["start_date"], "canopy")
    shutil.copyfile(synthetic_photo(30, "canopy", str(tmp_path / "c.jpg"), 5), public / f"{legacy}.jpg")
    cid = db.add_photo(obs["id"], "canopy", str(tmp_path / "borrada_c.jpg"), "camera")
    assert relink_photos(db, [str(public)])["relinked"] == 1
    assert os.path.basename(db.query_one("SELECT path FROM photos WHERE id=?", (cid,))["path"]) == \
        ph.photo_basename(v, w["start_date"], "canopy") + ".jpg"            # guardada con el nuevo


def test_drive_connect_errors_are_explained_and_logged(db):
    import threading
    from drive_backup import DriveBackup, DriveError, explain
    assert "SHA-1" in explain("com.google.android.gms.common.api.ApiException: 10: ")

    class Auth:
        def get_token(self, interactive):
            raise DriveError("com.google.android.gms.common.api.ApiException: 10: ")

    drive = DriveBackup(db, authorizer=Auth(), transport=lambda *a: (200, b"{}"), metered=lambda: False)
    got, ev = {}, threading.Event()
    drive.connect(lambda ok, msg: (got.update(ok=ok, msg=msg), ev.set()))
    assert ev.wait(5)
    assert got["ok"] is False and "SHA-1" in got["msg"]
    log = drive.diagnostics()
    assert "✗" in log[0] and "SHA-1" in log[0] and "Conectando" in log[-1]
    assert drive.status()["enabled"] is False and "SHA-1" in drive.status()["message"]


def test_v3_sector_irrigation_gps_and_names(db, tmp_path):
    import sqlite3
    import geo
    # Base v2 sin columnas nuevas → se agregan sin perder datos.
    old = tmp_path / "v2.sqlite3"
    d = Database(str(old))
    vid = d.list_varieties()[0]["id"]
    d.close()
    con = sqlite3.connect(old)
    con.executescript("""
        CREATE TABLE v2 AS SELECT id, name, code, notes, active, sort_order, created_at, updated_at FROM varieties;
        PRAGMA foreign_keys=OFF;
        DROP TABLE varieties; ALTER TABLE v2 RENAME TO varieties;""")
    con.close()
    d = Database(str(old))
    cols = {r[1] for r in d.conn.execute("PRAGMA table_info(varieties)")}
    assert {"sector", "irrigation"} <= cols
    d.update_variety(vid, sector=1, irrigation=2)
    v = d.get_variety(vid)
    assert ph.photo_basename(v, "2026-09-28", "canopy") == f"20260928-{ph.variety_tag(v['name'], v['code'])}S1ER2G"
    d.close()

    # Solo equipo de riego / sin nada
    assert ph.photo_basename({"name": "Meeker", "code": "MEE", "irrigation": 3}, "2026-09-21", "detail") == "20260921-MeeER3D"
    new_id = db.add_variety("Tulameen", code="TUL", sector=10, irrigation=4)
    assert ph.location_tag(db.get_variety(new_id)) == "S10ER4"

    # GPS: EXIF de una foto y guardado en el registro
    from PIL import Image
    img = Image.new("RGB", (64, 64), (40, 120, 40))
    exif = Image.Exif()
    exif[0x8825] = {1: "S", 2: (33.0, 27.0, 4.428), 3: "W", 4: (70.0, 39.0, 44.676)}
    p = str(tmp_path / "gps.jpg")
    img.save(p, exif=exif)
    lat, lon = geo.exif_gps(p)
    assert abs(lat + 33.45123) < 1e-5 and abs(lon + 70.66241) < 1e-5
    assert geo.exif_gps(synthetic_photo(55, "detail", str(tmp_path / "n.jpg"), 0)) is None

    week = db.current_week()
    obs = db.get_or_create_observation(new_id, week["id"])
    db.update_observation(obs["id"], latitude=lat, longitude=lon, gps_accuracy=6.0, gps_source="gps")
    rep = ReportGenerator(db, str(tmp_path / "out"))
    html = open(rep.weekly(week["id"]).path, encoding="utf-8").read()
    assert "-33.451230, -70.662410" in html and "openstreetmap.org/?mlat=-33.451230" in html

    # Matemática de teselas: ida y vuelta
    x, y = geo.latlon_to_tile(lat, lon, 17)
    lat2, lon2 = geo.tile_to_latlon(x, y, 17)
    assert abs(lat2 - lat) < 1e-9 and abs(lon2 - lon) < 1e-9


def test_import_with_coordinates(db, tmp_path):
    from importer import import_file, write_template
    path = write_template(str(tmp_path / "plantilla.csv"))
    import_file(db, path, train_ai=False)
    v = next(x for x in db.list_varieties() if x["name"] == "Meeker")
    rows = db.query("SELECT latitude, longitude, gps_source FROM observations WHERE variety_id=? "
                    "AND latitude IS NOT NULL", (v["id"],))
    assert rows and abs(rows[0]["latitude"] + 33.45123) < 1e-6 and rows[0]["gps_source"] == "histórico"


def test_gps_keeps_best_reading_and_stops_at_10m():
    import geo

    class Loc:
        def __init__(self, lat, lon, acc):
            self.lat, self.lon, self.acc = lat, lon, acc
        def hasAccuracy(self): return True
        def getAccuracy(self): return self.acc
        def getLatitude(self): return self.lat
        def getLongitude(self): return self.lon

    queue = []                              # hace de reloj de Kivy (sin Kivy en las pruebas)

    def run_queue():
        while queue:
            queue.pop(0)()

    got, prog = [], []
    req = geo.LocationRequest(lambda *a: got.append(a), lambda m: got.append(("error", m)),
                              lambda *a: prog.append(a[2]), schedule=queue.append)
    for acc in (48, 30, 35, 22):          # 35 es peor que 30: se ignora
        req._offer(Loc(-33.45, -70.66, acc))
    run_queue()
    assert prog == [48, 30, 22] and not got
    req._offer(Loc(-33.4512, -70.6624, 8))  # alcanza el objetivo (±10 m) → termina solo
    run_queue()
    assert got == [(-33.4512, -70.6624, 8.0)]
    req._offer(Loc(0, 0, 3))                # después de terminar no cambia nada
    run_queue()
    assert len(got) == 1

    # «Usar ahora» o fin del tiempo: entrega la mejor lectura aunque no llegue a 10 m
    got2 = []
    req2 = geo.LocationRequest(lambda *a: got2.append(a), lambda m: got2.append(("error", m)))
    req2._offer(Loc(-33.1, -70.1, 25))
    req2.finish()
    assert got2 == [(-33.1, -70.1, 25.0)]
    req3 = geo.LocationRequest(lambda *a: None, lambda m: got2.append(("error", m)))
    req3.finish()
    assert got2[-1][0] == "error"


def test_skipped_week_excluded_from_reports(db, tmp_path):
    from reporter import report_summary
    season = 2026
    db.ensure_weeks(season, 3)
    ws = db.list_weeks(season)
    v = db.list_varieties()[0]
    for w in ws:
        obs = db.get_or_create_observation(v["id"], w["id"])
        db.update_observation(obs["id"], bbch_code=10 + w["week_number"], bbch_label="x")
    db.set_week_skipped(ws[1]["id"], True)
    assert [w["week_number"] for w in db.list_weeks(season, include_skipped=False)] == [1, 3]
    assert len(db.list_weeks(season)) == 3                         # no se borra nada
    assert [r["week_number"] for r in db.variety_timeline(v["id"], season)] == [1, 3]
    assert report_summary(db, "period", season, week_from=1, week_to=3)["weeks"] == 2
    rep = ReportGenerator(db, str(tmp_path / "out"))
    html = open(rep.period(season, 1, 3).path, encoding="utf-8").read()
    import re
    heads = re.findall(r'<th class="w"[^>]*>(S\d+)</th>', html)   # columnas de la tabla
    assert heads == ["S37", "S39"]                                 # semana del año
    db.set_week_skipped(ws[1]["id"], False)
    assert len(db.list_weeks(season, include_skipped=False)) == 3


def test_each_phone_keeps_its_own_configuration(tmp_path):
    """Teléfono B: se borran las 10 variedades de fábrica y se crean otras. Al reabrir la
    app (reinicio o actualización con migraciones) NO vuelven las de fábrica."""
    path = str(tmp_path / "telefono_b.sqlite3")
    d = Database(path)
    for v in d.list_varieties(include_archived=True):
        d.delete_variety(v["id"], purge=True)
    d.add_variety("Heritage", code="HER", sector=3, irrigation=1)
    d.add_variety("Tulameen", code="TUL")
    d.set_setting("reminder", {"weekday": 2})
    d.close()
    d = Database(path)                       # «actualización»: init_schema + seed_defaults
    assert [v["name"] for v in d.list_varieties()] == ["Heritage", "Tulameen"]
    assert d.get_variety(d.list_varieties()[0]["id"])["sector"] == 3
    assert d.get_setting("reminder") == {"weekday": 2}
    d.close()


def test_week_of_year_titles():
    # Semana del año ISO-8601 y año calendario: «Semana xx, año 202x».
    assert ph.week_title("2026-08-10") == "Semana 33, año 2026"
    assert ph.week_title("2026-09-07") == "Semana 37, año 2026"
    assert ph.week_title("2026-12-28") == "Semana 53, año 2026"
    assert ph.week_title("2027-01-04") == "Semana 1, año 2027"
    assert ph.week_short({"start_date": "2026-09-28"}) == "S40"


def test_variety_attachments_in_report_and_backup(db, tmp_path):
    from data_transfer import full_backup, restore
    season = 2026
    v = db.list_varieties()[0]
    p = synthetic_photo(55, "canopy", str(tmp_path / "adj.jpg"), 1)
    aid = db.add_attachment(v["id"], season, p, "Daño por helada", source="gallery")
    db.add_attachment(v["id"], season + 1, p, "otra temporada")
    assert [a["caption"] for a in db.list_attachments(v["id"], season)] == ["Daño por helada"]
    db.set_attachment_caption(aid, "  Daño por helada en yemas ")
    assert db.list_attachments(v["id"], season)[0]["caption"] == "Daño por helada en yemas"
    assert ph.photo_basename(v, "2026-10-02", "attachment").startswith("20261002-") \
        and ph.photo_basename(v, "2026-10-02", "attachment").endswith("A")
    html = open(ReportGenerator(db, str(tmp_path / "out")).variety(v["id"], season).path,
                encoding="utf-8").read()
    assert "Fotos adjuntas" in html and "Daño por helada en yemas" in html and "otra temporada" not in html

    zpath = full_backup(db, str(tmp_path))
    os.remove(p)
    fresh = Database(str(tmp_path / "nuevo.sqlite3"))
    restore(fresh, zpath)
    rows = fresh.query("SELECT path FROM variety_attachments")
    assert len(rows) == 2 and all(os.path.exists(r["path"]) for r in rows)
    fresh.close()

    db.delete_attachment(aid)
    assert db.list_attachments(v["id"], season) == []


def test_ai_knowledge_shared_between_phones(tmp_path):
    """Dos teléfonos independientes intercambian lo aprendido sin duplicar ni borrar."""
    from ai_share import export_knowledge, import_knowledge
    a = Database(str(tmp_path / "a.sqlite3"))
    b = Database(str(tmp_path / "b.sqlite3"))
    ca, cb = PhenologyClassifier(a, HandcraftedExtractor()), PhenologyClassifier(b, HandcraftedExtractor())
    for i, code in enumerate((15, 15, 61, 85)):
        ca.add_reference(synthetic_photo(code, "detail", str(tmp_path / f"a{i}.jpg"), i), code)
    cb.add_reference(synthetic_photo(71, "detail", str(tmp_path / "b0.jpg"), 9), 71)
    a.upsert_bbch(61, "Inicio de floración", "10 % de flores abiertas", "flor petalo blanco",
                  source="user")
    a.add_document("Guía BBCH frambueso", None, "61 Inicio de floración ...", 1)

    pkg_a, n = export_knowledge(a, str(tmp_path))
    assert n == 4
    res = import_knowledge(b, pkg_a, cb)
    assert res.added == 4 and res.known == 0 and res.documents == 1 and res.stages >= 1
    assert b.reference_counts() == {15: 2, 61: 1, 71: 1, 85: 1}
    assert "petalo" in next(s for s in b.list_bbch() if s["code"] == 61)["keywords"]
    again = import_knowledge(b, pkg_a, cb)                  # mismo paquete: nada nuevo
    assert again.added == 0 and again.known == 4 and again.documents == 0

    pkg_b, _ = export_knowledge(b, str(tmp_path))           # y de vuelta: ambos iguales
    back = import_knowledge(a, pkg_b, ca)
    assert back.added == 1 and a.reference_counts() == b.reference_counts()
    # Las miniaturas viajan: el otro teléfono puede re-entrenar con sus propias fotos.
    assert all(r["image_path"] and os.path.exists(r["image_path"])
               for r in b.list_references() if r["photo_id"] is None)
    assert cb.suggest(str(tmp_path / "a2.jpg"), week_number=10).code is not None
    a.close()
    b.close()


def test_weekly_attachments_and_custom_measures(db, tmp_path):
    """Fotos adjuntas por semana y mediciones propias (imágenes o planilla) → informes,
    Excel y copia de seguridad."""
    import re
    from data_transfer import full_backup, restore
    from measures_export import as_number, export_measures
    week = db.current_week(dt.date(2026, 9, 30))
    v = db.list_varieties()[0]
    db.get_or_create_observation(v["id"], week["id"])
    adj = synthetic_photo(55, "canopy", str(tmp_path / "adj.jpg"), 1)
    db.add_attachment(v["id"], week["season"], adj, "Daño por helada en yemas", week_id=week["id"])
    assert len(db.list_attachments(v["id"], week_id=week["id"])) == 1
    assert db.list_attachments(v["id"], week["season"], general=True) == []

    blank = db.add_measure("Planilla nueva", "table")              # columnas por defecto
    assert db.get_measure(blank)["columns"] == ["Columna 1", "Columna 2", "Columna 3"]
    assert db.add_measure_column(blank) == "Columna 4"
    db.add_entry(blank, week["id"], data={"Columna 1": "5"})
    db.add_entry(blank, week["id"], data={})                          # fila vacía: no cuenta
    db.rename_measure_column(blank, "Columna 1", "Altura (cm)")
    assert db.get_measure(blank)["columns"][0] == "Altura (cm)"
    assert db.list_entries(blank)[0]["data"] == {"Altura (cm)": "5"}  # el dato se mueve
    with pytest.raises(ValueError):
        db.rename_measure_column(blank, "Columna 2", "Altura (cm)")   # nombre repetido
    db.delete_measure_column(blank, "Altura (cm)")
    assert db.list_entries(blank)[0]["data"] == {}
    db.delete_measure(blank)
    tab = db.add_measure("Largo de laterales", "table", ["Planta", "Largo (cm)", "Planta"])
    assert db.get_measure(tab)["columns"] == ["Planta", "Largo (cm)"]      # sin repetir
    db.add_entry(tab, week["id"], v["id"], data={"Planta": "1", "Largo (cm)": "23,5"})
    db.add_entry(tab, week["id"], None, data={"Planta": "2", "Largo (cm)": "19"})
    db.add_entry(tab, week["id"], None, data={})                      # fila agregada y vacía
    img = db.add_measure("Plagas", "images")
    pimg = synthetic_photo(70, "detail", str(tmp_path / "plaga.jpg"), 2)
    e = db.add_entry(img, week["id"], path=pimg)
    db.update_entry(e, caption="Arañita roja", variety_id=v["id"])
    counts = {m["name"]: m["n"] for m in db.list_measures(week["id"])}
    assert counts == {"Largo de laterales": 2, "Plagas": 1}
    db.update_measure(tab, columns=["Planta", "Largo (cm)", "N° frutos"])   # columna nueva

    rep = ReportGenerator(db, str(tmp_path / "out"))
    weekly = open(rep.weekly(week["id"]).path, encoding="utf-8").read()
    assert "Daño por helada en yemas" in weekly and "Mediciones de la semana" in weekly
    assert "Largo de laterales" in weekly and "23,5" in weekly and "Arañita roja" in weekly
    variety = open(rep.variety(v["id"], week["season"]).path, encoding="utf-8").read()
    assert "Daño por helada en yemas" in variety and "23,5" in variety and "19" not in \
        re.sub(r"<[^>]+>", " ", variety.split("<h2>Mediciones</h2>")[1])   # solo su variedad

    assert as_number("23,5") == 23.5 and as_number("12") == 12 and as_number("12 cm") is None
    path = export_measures(db, [tab, img], week["season"], str(tmp_path))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        s1 = zf.read("xl/worksheets/sheet1.xml").decode()
        book = zf.read("xl/workbook.xml").decode()
    assert {"[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml",
            "xl/worksheets/sheet2.xml"} <= set(names)
    assert 'name="Largo de laterales"' in book and 'name="Plagas"' in book
    assert "<v>23.5</v>" in s1 and "N° frutos" in s1 and "Semana del año" in s1 and "<v>40</v>" in s1

    zpath = full_backup(db, str(tmp_path))
    os.remove(adj)
    os.remove(pimg)
    fresh = Database(str(tmp_path / "nuevo.sqlite3"))
    restore(fresh, zpath)
    for table in ("variety_attachments", "measure_entries"):
        paths = [r["path"] for r in fresh.query(f"SELECT path FROM {table} WHERE path IS NOT NULL")]
        assert paths and all(os.path.exists(p) for p in paths)
    fresh.close()
    db.delete_measure(tab)
    assert db.list_entries(tab) == []


def test_photo_names_year_month_day_and_rename_existing(db, tmp_path):
    """Nombres aaaammdd (20260928-C11G) y paso de las fotos ya existentes (28092026-C11G):
    archivos de la app + base, cola de Drive y archivos ya subidos a Drive."""
    import json
    from drive_backup import DriveBackup
    from photo_rename import migrate_local, new_name
    assert new_name("28092026-C11G.jpg") == "20260928-C11G.jpg"
    assert new_name("28092026-C11S1ER2G-2.jpg") == "20260928-C11S1ER2G-2.jpg"
    assert new_name("20260928-C11G.jpg") is None          # ya está en el formato nuevo
    assert new_name("20261231-MeeD.jpg") is None and new_name("semanal_2026_S40.html") is None
    assert ph.photo_basename({"name": "Meeker", "code": "MEE"}, "2026-09-21", "detail",
                             legacy=True) == "21092026-MeeD"

    week = db.current_week(dt.date(2026, 9, 30))
    v = db.list_varieties()[0]
    obs = db.get_or_create_observation(v["id"], week["id"])
    old = synthetic_photo(55, "canopy", str(tmp_path / "28092026-C11G.jpg"), 1)
    keep = synthetic_photo(55, "detail", str(tmp_path / "20260928-C11D.jpg"), 2)
    pid = db.add_photo(obs["id"], "canopy", old)
    db.add_photo(obs["id"], "detail", keep)
    att = synthetic_photo(40, "canopy", str(tmp_path / "28092026-C11A.jpg"), 3)
    db.add_attachment(v["id"], 2026, att, "x", week_id=week["id"])
    # Ya subida a Drive con el nombre antiguo, y otra en cola.
    db.execute("INSERT INTO drive_queue(photo_id, path, name, status, drive_id, created_at) "
               "VALUES (?,?,?,?,?,?)", (pid, old, "Año 2026/Semana 40/28092026-C11G.jpg", "done",
                                        "f9", "2026-09-28T10:00:00"))
    db.execute("INSERT INTO drive_queue(photo_id, path, name, created_at) VALUES (NULL,?,?,?)",
               (att, "Año 2026/Semana 40/Adjuntas/28092026-C11A.jpg", "2026-09-28T10:00:00"))

    res = migrate_local(db)
    assert res == {"renamed": 2, "missing": 0}
    names = sorted(os.path.basename(r["path"]) for r in db.query("SELECT path FROM photos"))
    assert names == ["20260928-C11D.jpg", "20260928-C11G.jpg"]
    assert os.path.exists(tmp_path / "20260928-C11G.jpg") and not os.path.exists(old)
    assert os.path.basename(db.query_one("SELECT path FROM variety_attachments")["path"]) == "20260928-C11A.jpg"
    pending = db.query_one("SELECT * FROM drive_queue WHERE status='pending'")
    assert pending["name"].endswith("/Adjuntas/20260928-C11A.jpg") and os.path.exists(pending["path"])
    assert migrate_local(db) == {"renamed": 0, "missing": 0}            # idempotente

    patched = []

    def transport(method, url, headers, body):
        if method == "PATCH":
            patched.append((url, json.loads(body)))
            return 200, b'{"id": "f9"}'
        if method == "GET":
            return 200, b'{"files": []}'
        return 200, json.dumps({"id": f"x{len(patched)}"}).encode()

    class Auth:
        def get_token(self, interactive):
            return "t"

    db.set_setting("drive_enabled", True)
    drive = DriveBackup(db, authorizer=Auth(), transport=transport, metered=lambda: False)
    drive.flush()
    assert patched == [("https://www.googleapis.com/drive/v3/files/f9?fields=id",
                        {"name": "20260928-C11G.jpg"})]
    assert db.query_one("SELECT name FROM drive_queue WHERE drive_id='f9'")["name"].endswith("/20260928-C11G.jpg")
    drive.flush()
    assert len(patched) == 1                                           # una sola vez


def test_pdf_reports_all_kinds_and_photo_mode(db, tmp_path):
    """Los cuatro informes salen en PDF válido (texto legible, fotos incrustadas) y se
    puede elegir qué foto representativa incluir: detalle, general o ambas."""
    import pypdf
    pairs = _fill_week(db, tmp_path)
    v, w = pairs[0]
    rep = ReportGenerator(db, str(tmp_path / "out"))
    week = db.get_week(w["id"])
    paths = [rep.weekly(w["id"], "pdf").path, rep.period(2026, 1, 3, "pdf").path,
             rep.variety(v["id"], 2026, "pdf").path, rep.matrix(2026, package="pdf").path]
    for p in paths:
        assert p.endswith(".pdf")
        reader = pypdf.PdfReader(p)
        text = "".join(pg.extract_text() for pg in reader.pages)
        assert "PhenoRubus" in text and "Página 1 de" in text
    weekly = pypdf.PdfReader(paths[0])
    text = "".join(pg.extract_text() for pg in weekly.pages)
    assert ph.week_title(week) in text and v["name"] in text and "Registro fotográfico" in text

    def jpeg_count(path):
        xo = pypdf.PdfReader(path).pages[0]["/Resources"].get("/XObject") or {}
        return len(xo) - 1      # sin contar el logo de la app (primera hoja)

    full = jpeg_count(paths[0])
    rep.photo_mode = "detail"
    only_detail = jpeg_count(rep.weekly(w["id"], "pdf").path)
    rep.photo_mode = "canopy"
    canopy_pdf = rep.weekly(w["id"], "pdf").path
    assert full == 2 * only_detail == 2 * jpeg_count(canopy_pdf)    # 2 variedades × 2 tipos
    data = pypdf.PdfReader(canopy_pdf).attachments["phenorubus_semanal.json"][0]
    import json
    cards = [c for c in json.loads(data)["cards"] if c["photos"]]
    assert len(cards) == 2 and all(set(c["photos"]) == {"canopy"} for c in cards)


def test_workspaces_migration_isolation_and_backup(tmp_path):
    """Al actualizar: lo existente pasa a I+D › Nuevas variedades (NV), se crean los
    predios AM y LE, la IA y los ajustes del teléfono quedan en la base común, cada
    ensayo está aislado y el respaldo completo devuelve todo."""
    import json
    from data_transfer import full_backup, restore
    from drive_backup import DriveBackup
    from photo_rename import migrate_local
    from workspaces import Workspaces, suggest_code
    data = tmp_path / "datos"
    data.mkdir()
    # --- teléfono con la versión anterior: una sola base con datos, IA y Drive conectado
    old = Database(str(data / "fenorubus.sqlite3"))
    week = old.current_week(dt.date(2026, 9, 30))
    v = old.list_varieties()[0]
    obs = old.get_or_create_observation(v["id"], week["id"])
    photo = synthetic_photo(40, "detail", str(data / "20260928-C11D.jpg"), 1)
    pid = old.add_photo(obs["id"], "detail", photo)
    clf = PhenologyClassifier(old, HandcraftedExtractor())
    clf.add_reference(photo, 19, photo_id=pid)
    old.set_setting("drive_enabled", True)
    old.set_setting("report_photo_mode", "detail")
    n_var = len(old.list_varieties())
    old.close()

    wss = Workspaces(str(data))
    assert [(w["code"], w["name"], w["profile"]) for w in wss.list()] == [
        ("NV", "Nuevas variedades", "id"), ("AM", "El Amanecer", "predio"), ("LE", "La Esperanza 2", "predio")]
    assert wss.last()["code"] == "NV"
    nv = wss.open(wss.by_code("NV"))
    am = wss.open(wss.by_code("AM"))
    assert len(nv.list_varieties()) == n_var and am.list_varieties() == []      # aislados
    assert nv.get_setting("drive_enabled") is True and am.get_setting("drive_enabled") is True
    assert am.get_setting("report_photo_mode") == "detail"                       # ajustes del teléfono
    assert nv.reference_counts() == {19: 1} == am.reference_counts()              # IA compartida
    assert nv.list_detail_photos()[0]["in_reference"] == 1
    am.set_setting("season_start:2026", "2026-09-01")                            # ajuste del ensayo
    assert nv.get_setting("season_start:2026") is None

    # --- código del ensayo en los nombres de foto (las existentes se renombran)
    assert ph.photo_basename(v, "2026-09-28", "canopy", trial="AM").startswith("20260928-AM-")
    assert migrate_local(nv)["renamed"] == 1
    new_path = nv.query_one("SELECT path FROM photos")["path"]
    assert os.path.basename(new_path) == "20260928-NV-C11D.jpg" and os.path.exists(new_path)
    assert nv.list_references()[0]["image_path"] == new_path
    assert migrate_local(nv)["renamed"] == 0

    # --- nuevos ensayos: validaciones y código sugerido
    assert suggest_code("Fertilización nitrogenada", wss.codes()) == "FN"
    with pytest.raises(ValueError):
        wss.create("id", "variedades", "Otro", "NV")
    fe = wss.create("id", "tratamientos", "Fertilización", "FE")
    assert os.path.basename(wss.path(fe)) == "FE.sqlite3"

    # --- Drive: carpeta por ensayo y lo antiguo se mueve a la carpeta de NV
    assert DriveBackup(am).week_path("2026-09-28") == "Predio/El Amanecer/Año 2026/Semana 40 · 28-09-2026"
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url))
        if method == "GET" and "in+parents" in url and "Nuevas" not in url and "I%2BD" not in url:
            if "%27root%27" in url or "root" not in url:
                pass
            return 200, json.dumps({"files": [{"id": "y26", "name": "Año 2026"},
                                              {"id": "bk", "name": "Respaldos"},
                                              {"id": "x", "name": "I+D"}]}).encode()
        if method == "GET":
            return 200, b'{"files": []}'
        return 200, json.dumps({"id": f"n{len(calls)}"}).encode()

    class Auth:
        def get_token(self, interactive):
            return "t"

    drive = DriveBackup(nv, authorizer=Auth(), transport=transport, metered=lambda: False)
    assert drive.relocate_legacy() == 2
    moved = [u for m, u in calls if m == "PATCH"]
    assert len(moved) == 2 and all("removeParents" in u for u in moved)
    assert drive.relocate_legacy() == 0                                           # una sola vez

    # --- informes con el ensayo en el subtítulo y su código en el nombre
    rep = ReportGenerator(nv, str(tmp_path / "out"))
    r = rep.weekly(week["id"])
    assert os.path.basename(r.path).startswith("semanal_NV_2026_S40")
    assert "I+D · Nuevas variedades" in open(r.path, encoding="utf-8").read()

    # --- respaldo completo de todo el teléfono y restauración
    zpath = full_backup(nv, str(tmp_path), workspaces=wss)
    os.remove(new_path)
    nv.execute("DELETE FROM observations")
    res = restore(nv, zpath, workspaces=wss)
    assert res.photos_restored >= 1 and nv.query_one("SELECT COUNT(*) AS n FROM observations")["n"] == 1
    assert os.path.exists(nv.query_one("SELECT path FROM photos")["path"])
    assert nv.reference_counts() == {19: 1}
    assert {w["code"] for w in wss.list()} >= {"NV", "AM", "LE", "FE"}
    wss.close()


def test_bbch_scale_canes_laterals_and_substages(db, tmp_path):
    """Escala de la tabla (cañas anuales / brotes laterales) con subestadios 89-1 … 89-9:
    se muestran «89-1», se ordenan entre 89 y 91 y funcionan en IA e informes."""
    assert ph.code_str(891) == "89-1" and ph.code_str(7) == "07" and ph.code_str(553) == "55-3"
    assert ph.parse_bbch_code("BBCH 89-3: 30 % cosechado") == 893 and ph.parse_bbch_code("891") == 891
    assert ph.macro_of(891) == 8 and ph.bbch_value(891) == 89.1
    codes = [r["code"] for r in db.list_bbch()]
    assert codes.index(89) < codes.index(891) < codes.index(899) < codes.index(91)
    names = db.bbch_names()
    assert names[9].startswith("Cañas: ") and "Laterales: " in names[9]
    assert names[31].startswith("Cañas anuales: 10 %") and names[7].startswith("Brotes laterales")
    assert ph.bbch_label(891, names) == "BBCH 89-1: 10 % de los frutos cosechados"
    assert 15 in codes                                     # estados anteriores se conservan
    assert codes.index(51) < codes.index(53) < codes.index(55) and 553 not in codes

    # Una base con la escala anterior se actualiza sin pisar lo editado por el usuario.
    db.execute("DELETE FROM meta WHERE key='bbch_scale'")
    db.upsert_bbch(65, "Plena floración", "antigua", "", source="base")
    db.upsert_bbch(61, "Mi nombre", "editado", "", source="user")
    db.execute("DELETE FROM bbch_stages WHERE code=893")
    db.upsert_bbch(553, "Los tallos florales se estiran (capullos juntos)", "", "", source="base")
    db.add_reference(553, "x", b"\0" * 8, 2, None, None)          # versión con «55-3»
    assert db.refresh_bbch_scale() > 0
    names = db.bbch_names()
    assert names[65].startswith("Final de la floración") and names[61] == "Mi nombre" and 893 in names
    assert 553 not in names and names[53].startswith("Los tallos florales se estiran (capullos juntos)")
    assert 553 not in db.reference_counts() and db.reference_counts()[53] == 1
    db.execute("DELETE FROM ai_references")

    # IA: referencias en 89-1 -> sugiere 89-1 (código real) y explica con «89-1».
    clf = PhenologyClassifier(db, HandcraftedExtractor())
    for i in range(4):
        clf.add_reference(synthetic_photo(87, "detail", str(tmp_path / f"h{i}.jpg"), i), 891)
    s = clf.suggest(str(tmp_path / "h0.jpg"))
    assert s.code in {r["code"] for r in db.list_bbch()}
    assert all(isinstance(c, int) for c, _p in s.top)
    assert clf.evaluate()["n"] == 4

    # Informes: 89-1 en la tabla y en el heatmap; hito «Inicio cosecha» lo reconoce.
    w = db.current_week(dt.date(2026, 10, 30))
    v = db.list_varieties()[0]
    obs = db.get_or_create_observation(v["id"], w["id"])
    db.update_observation(obs["id"], bbch_code=891, bbch_label=ph.bbch_label(891, names))
    rep = ReportGenerator(db, str(tmp_path / "out"))
    html = open(rep.weekly(w["id"]).path, encoding="utf-8").read()
    assert "BBCH 89-1" in html
    matrix = open(rep.matrix(w["season"]).path, encoding="utf-8").read()
    assert ">89-1<" in matrix and "≥89-1" in matrix
    assert rep.matrix(w["season"], package="pdf").path.endswith(".pdf")

    # Registros guardados con 553 pasan a 53 al abrir la base.
    db.update_observation(obs["id"], bbch_code=553, ai_code=553)
    db._remap_bbch_codes()
    o = db.query_one("SELECT bbch_code, ai_code FROM observations WHERE id=?", (obs["id"],))
    assert (o["bbch_code"], o["ai_code"]) == (53, 53)


def test_treatments_by_replicates_trial_and_report(tmp_path):
    """Ensayo de tratamientos × repeticiones: parcelas T1R1…, ANOVA, letras e informe PDF."""
    import trial_stats as st
    from workspaces import Workspaces
    wss = Workspaces(str(tmp_path / "data"))
    ws = wss.create("id", "tratamientos", "Fertilización", "FE")
    db = wss.open(ws)
    assert db.is_trial and db.list_varieties() == []
    assert db.setup_trial(3, 4) == {"added": 12, "restored": 0, "archived": 0}
    names = [v["name"] for v in db.list_varieties()]
    assert names[:5] == ["T1R1", "T1R2", "T1R3", "T1R4", "T2R1"] and len(names) == 12
    db.update_treatment(1, "Testigo", "Sin fertilizar")
    trts = db.list_treatments()
    assert [t["label"] for t in trts] == ["T1 · Testigo", "T2", "T3"] and len(trts[0]["parcels"]) == 4
    # Foto de una parcela: lleva el código del ensayo y la parcela.
    p = db.list_varieties()[0]
    assert ph.photo_basename(p, "2026-10-05", "detail", trial="FE").startswith("20261005-FE-T1R1")

    # Reducir archiva (no borra) y aumentar restaura.
    assert db.setup_trial(2, 4)["archived"] == 4 and len(db.list_varieties()) == 8
    assert db.setup_trial(3, 4)["restored"] == 4 and len(db.list_varieties()) == 12

    # Datos: T2 adelantado; T1 y T3 parecidos.
    weeks = [db.current_week(dt.date(2026, 10, 5) + dt.timedelta(days=7 * i)) for i in range(3)]
    base = {1: [55, 61, 65], 2: [61, 65, 71], 3: [55, 59, 65]}
    m = db.add_measure("Largo de brotes", "table", ["Largo (cm)", "Obs."])
    for v in db.list_varieties():
        for i, w in enumerate(weeks):
            o = db.get_or_create_observation(v["id"], w["id"])
            code = base[v["treatment"]][i] + (2 if v["rep"] in (2, 4) and i == 1 else 0)
            db.update_observation(o["id"], bbch_code=code)
        for sub in (0, 1):   # dos submuestras por parcela
            db.add_entry(m, weeks[2]["id"], v["id"],
                         data={"Largo (cm)": f"{10 * v['treatment'] + v['rep'] * .3 + sub:.1f}".replace(".", ","),
                               "Obs.": "ok"})
    a = st.anova({(t, r): float(base[t][0] + r * .1) for t in (1, 2, 3) for r in (1, 2, 3, 4)})
    assert a["design"] == "bloques completos al azar" and a["p"] < 0.001
    assert a["letters"][2] == "a" and a["letters"][1] != "a"

    rep = ReportGenerator(db, str(tmp_path / "out"))
    html = open(rep.treatments(weeks[0]["season"]).path, encoding="utf-8").read()
    assert "T1 · Testigo" in html and "ANOVA por semana" in html and "Largo de brotes" in html
    assert "Bloques completos" in html and "<sup>a</sup>" in html and "Inicio floración" in html
    res = rep.treatments(weeks[0]["season"], package="pdf")
    assert res.path.endswith(".pdf") and "_FE_" in os.path.basename(res.path)
    assert open(res.path, "rb").read(5) == b"%PDF-"
    db.close()
    wss.close()


def test_drive_choose_and_switch_account(db, tmp_path):
    """Conectar abre la lista de cuentas; al cambiar de cuenta lo subido se vuelve a subir
    a la nueva y desconectar revoca el permiso en Google."""
    import threading
    from drive_backup import DriveBackup

    class Auth:
        account = None
        picks = ["ana@gmail.com", "campo@gmail.com"]

        def choose_account(self):
            return self.picks.pop(0)

        def get_token(self, interactive):
            return f"tok-{self.account}"

    calls = []
    auth = Auth()
    drive = DriveBackup(db, authorizer=auth, transport=lambda *a: calls.append(a) or (200, b"{}"),
                        metered=lambda: False)
    drive.flush = lambda: 0      # sin subidas en la prueba

    def connect():
        ev = threading.Event()
        got = {}
        drive.connect(lambda ok, msg: (got.update(ok=ok, msg=msg), ev.set()))
        assert ev.wait(5)
        return got

    got = connect()
    assert got["ok"] and "ana@gmail.com" in got["msg"]
    assert auth.account == "ana@gmail.com" and drive.status()["account"] == "ana@gmail.com"
    db.execute("INSERT INTO drive_queue(path, name, status, drive_id, created_at) "
               "VALUES ('/x.jpg', 'x.jpg', 'done', 'f1', '2026-10-06')")
    drive._sync_account_queue()
    assert db.query_one("SELECT status FROM drive_queue")["status"] == "done"
    db.set_setting("drive_folders", {"a": "b"})

    got = connect()                                       # otra cuenta
    assert got["ok"] and auth.account == "campo@gmail.com"
    assert db.get_setting("drive_folders") == {}           # carpetas de la cuenta anterior
    drive._sync_account_queue()
    row = db.query_one("SELECT status, drive_id FROM drive_queue")
    assert row["status"] == "pending" and row["drive_id"] is None

    drive._token = ("tok-campo", 9e9)
    drive.disconnect()
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(2)
    assert drive.status()["enabled"] is False
    assert any("oauth2.googleapis.com/revoke" in c[1] for c in calls)


def test_predio_units_named_by_irrigation_and_sector(tmp_path):
    """Predio: «Equipo de riego n, sector m» con la variedad aparte (se puede repetir)."""
    from workspaces import Workspaces
    wss = Workspaces(str(tmp_path / "data"))
    am = wss.by_code("AM")
    db = wss.open(am)
    assert db.is_predio
    a = db.save_unit("Meeker", "MEE", sector=1, irrigation=2)
    b = db.save_unit("Meeker", "MEE", sector=3, irrigation=2)          # misma variedad
    c = db.save_unit("Heritage", "HER", sector=1, irrigation=2)        # mismo sector, otra variedad
    names = {v["id"]: v["name"] for v in db.list_varieties()}
    assert names[a] == "Equipo de riego 2, sector 1" and names[b] == "Equipo de riego 2, sector 3"
    assert names[c] == "Equipo de riego 2, sector 1 · Heritage"
    with pytest.raises(ValueError):
        db.save_unit("Meeker", "", sector=1, irrigation=2)
    v = db.get_variety(a)
    assert v["cultivar"] == "Meeker"
    assert ph.photo_basename(v, "2026-10-05", "detail", trial="AM") == "20261005-AM-MeeS1ER2D"
    # Editar: cambia de sector -> cambia el nombre.
    db.save_unit("Meeker", "MEE", sector=4, irrigation=2, variety_id=a)
    assert db.get_variety(a)["name"] == "Equipo de riego 2, sector 4"
    # Unidades de versiones anteriores (nombre = variedad) se renombran al abrir.
    vid = db.add_variety("Tulameen S5ER1", code="TUL", sector=5, irrigation=1)
    db.close()
    db = wss.open(am)
    v = db.get_variety(vid)
    assert v["name"] == "Equipo de riego 1, sector 5" and v["cultivar"] == "Tulameen"
    db.close()
    wss.close()


def test_drive_missing_files_do_not_block_progress(db, tmp_path):
    """Una foto borrada o reemplazada no deja la subida «pegada» en 181 de 182."""
    from drive_backup import DriveBackup
    drive = DriveBackup(db, authorizer=object(), transport=lambda *a: (200, b"{}"), metered=lambda: False)
    ok = tmp_path / "ok.jpg"
    ok.write_bytes(b"x")
    db.execute("INSERT INTO drive_queue(path, name, status, created_at) VALUES (?, 'ok.jpg', 'done', '2026')",
               (str(ok),))
    db.execute("INSERT INTO drive_queue(path, name, status, error, created_at) "
               "VALUES ('/no/existe.jpg', 'existe.jpg', 'error', 'El archivo ya no existe', '2026')")
    st = drive.status()
    assert (st["done"], st["errors"]) == (1, 1)
    assert any("existe.jpg" in line for line in drive.diagnostics())
    assert drive._skip_missing() == 1
    st = drive.status()
    assert (st["done"], st["errors"], st["pending"]) == (1, 0, 0)    # 1 de 1: completo


def test_variety_catalog_separate_profiles_and_trial_variety(tmp_path):
    """Catálogo de variedades por perfil (I+D / Predio); nunca entran «Código n» ni T1R1."""
    import catalog
    from workspaces import Workspaces
    wss = Workspaces(str(tmp_path / "data"))
    sh = wss.shared
    assert not catalog.is_real_variety("Código 11") and not catalog.is_real_variety("código n° 24")
    assert not catalog.is_real_variety("T1R2") and catalog.is_real_variety("Meeker")
    assert catalog.add(sh, "predio", "Heritage", "HER")
    assert not catalog.add(sh, "predio", "Código 31")
    assert catalog.add(sh, "id", "Meeker", "MEE")
    catalog.add(sh, "predio", "heritage", "HE2")          # mismo nombre: actualiza el código
    assert catalog.entries(sh, "predio") == [{"name": "Heritage", "code": "HE2"}]
    ids = [e["name"] for e in catalog.entries(sh, "id")]
    assert "Meeker" in ids and not any(n.lower().startswith("código") for n in ids)
    # Tratamientos: variedad del ensayo por defecto y otra en un tratamiento.
    ws = wss.create("id", "tratamientos", "Ensayo N", "EN")
    db = wss.open(ws)
    db.setup_trial(2, 2)
    db.set_setting("trial_cultivar", "Meeker")
    db.update_treatment(2, "Alto N", "", "Heritage")
    vs = {t["num"]: t["variety"] for t in db.list_treatments()}
    assert vs == {1: "Meeker", 2: "Heritage"}
    db.close()
    wss.close()
