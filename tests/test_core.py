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
