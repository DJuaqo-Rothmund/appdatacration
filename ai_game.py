"""
ai_game.py
==========
«Desafío diario»: entrenamiento de la IA como minijuego.

Cada día se generan 5 desafíos (deterministas para esa fecha):

* identify  «¿Qué estado es?»   — una foto de detalle de sus registros y 4 opciones.
  Si la foto ya tiene estado registrado es un quiz (acierto/error); si no, la
  respuesta del usuario la etiqueta. En ambos casos la foto entra a la base de
  referencia del clasificador con el estado correcto.
* capture   «Muéstrame BBCH XX» — la app pide un estado (esperado para la semana
  y con pocas referencias); el usuario aporta una foto, la IA dice qué vio y
  luego aprende con la etiqueta pedida.

Puntos (XP), nivel, racha de días consecutivos y precisión en los quiz se
guardan en settings («ai_game»).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import random

import phenology as ph

N_CHALLENGES = 5
N_IDENTIFY = 3
XP = {"quiz_ok": 15, "quiz_bad": 5, "teach": 10, "capture": 20, "capture_bonus": 10, "day": 25}
RECENT_DAYS = 14


class DailyChallenge:
    SETTING = "ai_game"

    def __init__(self, db, classifier_getter):
        self.db = db
        self._classifier = classifier_getter  # carga diferida (numpy)

    @property
    def classifier(self):
        return self._classifier()

    # ------------------------------------------------------------- estado
    def stats(self) -> dict:
        st = {"xp": 0, "streak": 0, "best": 0, "last_day": None, "answered": 0, "correct": 0}
        st.update(self.db.get_setting(self.SETTING) or {})
        st["level"] = st["xp"] // 100 + 1
        st["level_progress"] = (st["xp"] % 100) / 100
        st["accuracy"] = st["correct"] / st["answered"] if st["answered"] else None
        return st

    def _save(self, st: dict) -> None:
        keep = {k: st[k] for k in ("xp", "streak", "best", "last_day", "answered", "correct")}
        self.db.set_setting(self.SETTING, keep)

    def challenges(self, day: _dt.date | None = None) -> list[dict]:
        day = (day or _dt.date.today()).isoformat()
        rows = self.db.query("SELECT * FROM challenges WHERE day=? ORDER BY idx", (day,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def progress(self, day: _dt.date | None = None) -> tuple[int, int]:
        rows = self.challenges(day)
        return sum(r["status"] != "open" for r in rows), len(rows)

    # --------------------------------------------------------- generación
    def ensure_today(self, day: _dt.date | None = None) -> list[dict]:
        day = day or _dt.date.today()
        if self.challenges(day):
            return self.challenges(day)
        rnd = random.Random(day.toordinal())
        week = self.db.current_week(day)
        photos = self._candidate_photos(day, rnd)
        n_id = min(N_IDENTIFY, len(photos))
        items = [self._identify_payload(p, rnd) for p in photos[:n_id]]
        items += [self._capture_payload(code) for code in
                  self._target_codes(week["week_number"], N_CHALLENGES - n_id, rnd)]
        now = _dt.datetime.now().isoformat(timespec="seconds")
        for i, (kind, payload) in enumerate(items):
            self.db.execute(
                "INSERT OR IGNORE INTO challenges(day, idx, kind, payload, created_at) VALUES (?,?,?,?,?)",
                (day.isoformat(), i, kind, json.dumps(payload, ensure_ascii=False), now))
        return self.challenges(day)

    def _candidate_photos(self, day: _dt.date, rnd: random.Random) -> list[dict]:
        since = (day - _dt.timedelta(days=RECENT_DAYS)).isoformat()
        used = set()
        for r in self.db.query("SELECT payload FROM challenges WHERE kind='identify' AND day>=?",
                               (since,)):
            used.add(json.loads(r["payload"]).get("photo_id"))
        import os
        photos = [p for p in self.db.list_detail_photos()
                  if p["id"] not in used and os.path.exists(p["path"])]
        rnd.shuffle(photos)
        # Primero las que la IA aún no conoce (enseñan más).
        photos.sort(key=lambda p: bool(p["in_reference"]))
        return photos

    def _identify_payload(self, photo: dict, rnd: random.Random):
        codes = [r["code"] for r in self.db.list_bbch()]
        truth = photo["bbch_code"]
        try:
            probs = self.classifier.probabilities(photo["path"])
            ai = sorted(probs, key=probs.get, reverse=True)
        except Exception:
            ai = []
        options = [truth] if truth is not None else []
        for c in ai:
            if c not in options:
                options.append(c)
            if len(options) >= 4:
                break
        pool = [c for c in codes if c not in options]
        while len(options) < 4 and pool:
            options.append(pool.pop(rnd.randrange(len(pool))))
        options = sorted(options[:4])
        return ("identify", {"photo_id": photo["id"], "path": photo["path"], "truth": truth,
                             "ai": ai[0] if ai else None, "options": options,
                             "variety": photo["variety_name"], "week": photo["week_number"]})

    def _target_codes(self, week_number: int, n: int, rnd: random.Random) -> list[int]:
        counts = self.db.reference_counts()
        expected = ph.expected_bbch_for_week(week_number)
        weights = []
        for r in self.db.list_bbch():
            c = r["code"]
            w = math.exp(-((c - expected) / 18.0) ** 2) / (1 + counts.get(c, 0)) + 0.02
            weights.append((c, w))
        chosen: list[int] = []
        while len(chosen) < n and weights:
            total = sum(w for _c, w in weights)
            x = rnd.random() * total
            for i, (c, w) in enumerate(weights):
                x -= w
                if x <= 0:
                    chosen.append(c)
                    weights.pop(i)
                    break
        return chosen

    @staticmethod
    def _capture_payload(code: int):
        return ("capture", {"target": code})

    # ------------------------------------------------------------ respuestas
    MIN_DONE_FOR_STREAK = 3

    def _finish(self, ch: dict, answer: str, correct: int | None, xp: int,
                status: str = "done") -> dict:
        self.db.execute("UPDATE challenges SET status=?, answer=?, correct=? WHERE id=?",
                        (status, answer, correct, ch["id"]))
        st = self.stats()
        st["xp"] += xp
        day_done = False
        rows = self.challenges(_dt.date.fromisoformat(ch["day"]))
        n_open = sum(r["status"] == "open" for r in rows)
        n_done = sum(r["status"] == "done" for r in rows)
        # El día cuenta (racha + bono) sin pendientes y con al menos 3 respondidos:
        # saltar no regala la racha.
        if not n_open and n_done >= self.MIN_DONE_FOR_STREAK and st.get("last_day") != ch["day"]:
            prev = _dt.date.fromisoformat(ch["day"]) - _dt.timedelta(days=1)
            st["streak"] = st["streak"] + 1 if st.get("last_day") == prev.isoformat() else 1
            st["best"] = max(st["best"], st["streak"])
            st["last_day"] = ch["day"]
            st["xp"] += XP["day"]
            day_done = True
        self._save(st)
        return {"xp": xp, "day_done": day_done, "stats": self.stats()}

    def _get(self, ch_id: int) -> dict:
        ch = self.db.query_one("SELECT * FROM challenges WHERE id=?", (ch_id,))
        ch["payload"] = json.loads(ch["payload"])
        return ch

    def answer_identify(self, ch_id: int, code: int) -> dict:
        ch = self._get(ch_id)
        p = ch["payload"]
        truth = p.get("truth")
        st = self.stats()
        if truth is not None:
            ok = int(code) == int(truth)
            st["answered"] += 1
            st["correct"] += ok
            self._save(st)
            label = int(truth)
            xp = XP["quiz_ok"] if ok else XP["quiz_bad"]
        else:
            ok, label, xp = None, int(code), XP["teach"]
            obs = self.db.query_one("SELECT observation_id FROM photos WHERE id=?", (p["photo_id"],))
            if obs:  # la respuesta etiqueta también el registro sin estado
                self.db.update_observation(obs["observation_id"], bbch_code=label,
                                           bbch_label=ph.bbch_label(label, self.db.bbch_names()))
        self.classifier.add_reference(p["path"], label, photo_id=p["photo_id"])
        res = self._finish(ch, str(code), None if ok is None else int(ok), xp)
        res.update({"correct": ok, "truth": truth, "ai": p.get("ai"), "label": label})
        return res

    def complete_capture(self, ch_id: int, image_path: str) -> dict:
        ch = self._get(ch_id)
        target = int(ch["payload"]["target"])
        probs = self.classifier.probabilities(image_path)
        ai = max(probs, key=probs.get)
        agree = ai // 10 == target // 10
        self.classifier.add_reference(image_path, target)
        res = self._finish(ch, image_path, int(agree), XP["capture"] + (XP["capture_bonus"] if agree else 0))
        res.update({"ai": ai, "target": target, "agree": agree})
        return res

    def skip(self, ch_id: int) -> dict:
        return self._finish(self._get(ch_id), "skip", None, 0, status="skipped")
