"""
Pantallas y widgets de la app (lógica de UI). El layout está en ui/layout.kv.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import threading

from kivy.clock import Clock, mainthread
from kivy.metrics import dp
from kivy.properties import (BooleanProperty, ColorProperty, ListProperty, NumericProperty,
                             ObjectProperty, StringProperty)
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDRaisedButton, MDRectangleFlatButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.fitimage import FitImage
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.list import OneLineListItem, TwoLineListItem
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.pickers import MDDatePicker, MDTimePicker
from kivymd.uix.screen import MDScreen

import phenology as ph
from android_bridge import store_photo
from notifications import FREQUENCIES, can_schedule_exact, request_exact_alarm_permission
from platform_utils import IS_ANDROID, data_subdir
from reporter import slugify
from ui import theme
from ui.theme import c


def app() -> "MDApp":
    return MDApp.get_running_app()


# ===========================================================================
# Widgets comunes
# ===========================================================================
class Tag(MDLabel):
    bg = ColorProperty(c(theme.OLIVE_SOFT))
    fg = ColorProperty(c(theme.OLIVE_DARK))


class UnitButton(MDRectangleFlatButton):
    options = ListProperty([])

    def on_options(self, *_):
        if self.options and self.text not in self.options:
            self.text = self.options[0]

    def on_release(self):
        if self.options:
            i = self.options.index(self.text) if self.text in self.options else -1
            self.text = self.options[(i + 1) % len(self.options)]


class SegButton(MDRaisedButton):
    selected = BooleanProperty(False)


def open_menu(caller, items: list[tuple[str, callable]], width_mult: int = 5):
    menu = None

    def make(cb):
        def _run(*_):
            menu.dismiss()
            cb()
        return _run

    menu = MDDropdownMenu(
        caller=caller, width_mult=width_mult, max_height=dp(340),
        items=[{"viewclass": "OneLineListItem", "text": t, "height": dp(48),
                "on_release": make(cb)} for t, cb in items])
    menu.open()
    return menu


def thumb_widget(path: str | None, size: int = 56, icon: str = "image-off-outline"):
    if path and os.path.exists(path):
        return FitImage(source=app().thumb(path), radius=[dp(8)])
    box = MDCard(md_bg_color=c(theme.OLIVE_SOFT), radius=[dp(8)], elevation=0)
    box.add_widget(MDIcon(icon=icon, halign="center", theme_text_color="Custom",
                          text_color=c(theme.OLIVE)))
    return box


def confirm(title: str, text: str, actions: list[tuple[str, callable]]):
    dialog = None

    def wrap(cb):
        def _run(*_):
            dialog.dismiss()
            if cb:
                cb()
        return _run

    buttons = [MDFlatButton(text="CANCELAR", on_release=wrap(None))]
    buttons += [MDFlatButton(text=t.upper(), theme_text_color="Custom",
                             text_color=c(theme.BERRY if "ELIMIN" in t.upper() else theme.OLIVE_DARK),
                             on_release=wrap(cb)) for t, cb in actions]
    dialog = MDDialog(title=title, text=text, buttons=buttons)
    dialog.open()
    return dialog


def form_dialog(title: str, content, on_ok, ok_text: str = "GUARDAR"):
    dialog = None

    def _ok(*_):
        if on_ok(content) is not False:
            dialog.dismiss()

    dialog = MDDialog(title=title, type="custom", content_cls=content, buttons=[
        MDFlatButton(text="CANCELAR", on_release=lambda *_: dialog.dismiss()),
        MDFlatButton(text=ok_text, theme_text_color="Custom", text_color=c(theme.OLIVE_DARK),
                     on_release=_ok)])
    dialog.open()
    return dialog


def list_dialog(title: str, rows: list[tuple[str, str]]):
    box = MDBoxLayout(orientation="vertical", adaptive_height=True)
    for a, b in rows:
        box.add_widget(TwoLineListItem(text=a, secondary_text=b))
    from kivymd.uix.scrollview import MDScrollView
    sv = MDScrollView(size_hint_y=None, height=dp(420))
    sv.add_widget(box)
    dialog = MDDialog(title=title, type="custom", content_cls=sv,
                      buttons=[MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss())])
    dialog.open()


def bbch_tag_colors(code: int | None):
    if code is None:
        return c(theme.SLATE_SOFT), c(theme.SLATE)
    from reporter import seq_color
    bg, fg = seq_color(code)
    return c(bg), c(fg)


# ===========================================================================
# Home
# ===========================================================================
class HomeScreen(MDScreen):
    def on_tab(self, name: str, title: str):
        self.ids.bar.title = title
        getattr(self.ids, name).refresh()

    def refresh(self):
        for tab in ("sampling", "varieties", "reports", "settings"):
            getattr(self.ids, tab).refresh()


class VarietyRow(MDCard):
    title = StringProperty()
    subtitle = StringProperty()
    photos = NumericProperty(0)
    photo_tag = StringProperty()
    code_tag = StringProperty()
    code_bg = ColorProperty(c(theme.SLATE_SOFT))
    code_fg = ColorProperty(c(theme.SLATE))
    done = BooleanProperty(False)
    variety_id = NumericProperty()


class SamplingTab(MDBoxLayout):
    def refresh(self):
        a = app()
        week = a.week
        start = _dt.date.fromisoformat(week["start_date"])
        end = start + _dt.timedelta(days=6)
        self.ids.week_kicker.text = f"SEMANA {week['week_number']} · TEMPORADA {week['season']}-{week['season'] + 1}"
        self.ids.week_title.text = week["label"]
        self.ids.week_range.text = (f"{start.day} {ph.MESES[start.month - 1][:3]} – "
                                    f"{end.day} {ph.MESES[end.month - 1][:3]} {end.year}")
        rows = a.db.week_overview(week["id"])
        box = self.ids.rows
        box.clear_widgets()
        complete = 0
        names = a.db.bbch_names()
        for r in rows:
            obs, photos, v = r["observation"], r["photos"], r["variety"]
            code = obs["bbch_code"] if obs else None
            n = len(photos)
            done = code is not None and n == 2
            complete += done
            bg, fg = bbch_tag_colors(code)
            subtitle = (obs["bbch_label"] or ph.bbch_label(code, names)) if code is not None \
                else ("Foto sin estado asignado" if n else "Pendiente de registro")
            row = VarietyRow(title=v["name"], subtitle=subtitle, photos=n, photo_tag=f"FOTOS {n}/2",
                             code_tag=f"BBCH {code:02d}" if code is not None else "BBCH —",
                             code_bg=bg, code_fg=fg, done=code is not None, variety_id=v["id"])
            thumb = photos.get("detail") or photos.get("canopy")
            row.ids.thumb_box.add_widget(thumb_widget(thumb["path"] if thumb else None,
                                                      icon="sprout-outline"))
            row.bind(on_release=lambda w: a.open_observation(w.variety_id, a.week["id"]))
            box.add_widget(row)
        total = max(1, len(rows))
        self.ids.progress.value = complete / total
        self.ids.progress_text.text = f"{complete}/{len(rows)} completas"

    def shift_week(self, delta: int):
        a = app()
        n = a.week["week_number"] + delta
        if n < 1:
            a.toast("La Semana 1 es la semana de referencia inicial.")
            return
        a.week = a.db.ensure_week(a.week["season"], n)
        self.refresh()

    def go_current(self):
        a = app()
        a.week = a.db.current_week()
        self.refresh()


# ===========================================================================
# Variedades
# ===========================================================================
class VarietyCatalogRow(MDCard):
    title = StringProperty()
    code = StringProperty()
    summary = StringProperty()
    variety_id = NumericProperty()
    tab = ObjectProperty()


class VarietyForm(MDBoxLayout):
    pass


class VarietiesTab(MDScreen):
    def refresh(self):
        a = app()
        season = a.season
        self.ids.season_caption.text = (f"Temporada {season}-{season + 1} · toque una variedad "
                                        f"para editar sus parámetros biométricos")
        box = self.ids.rows
        box.clear_widgets()
        for v in a.db.list_varieties():
            m = a.db.get_metrics(v["id"], season)
            parts = []
            if m["historical_yield"] is not None:
                parts.append(f"hist. {m['historical_yield']:g} {m['historical_yield_unit']}")
            if m["projected_yield"] is not None:
                parts.append(f"proy. {m['projected_yield']:g} {m['projected_yield_unit']}")
            if m["basal_canes"] is not None:
                parts.append(f"{m['basal_canes']:g} cañas {m['basal_canes_unit']}")
            n_custom = len(a.db.list_custom_fields(v["id"], season))
            if n_custom:
                parts.append(f"{n_custom} campo(s) extra")
            row = VarietyCatalogRow(title=v["name"], code=v["code"] or "",
                                    summary=" · ".join(parts) or "Sin parámetros cargados",
                                    variety_id=v["id"], tab=self)
            row.bind(on_release=lambda w: self.open_variety(w.variety_id))
            box.add_widget(row)

    def open_variety(self, variety_id: int):
        app().open_variety(variety_id)

    def add_dialog(self):
        def ok(form):
            try:
                app().db.add_variety(form.ids.name.text, code=form.ids.code.text.strip())
            except ValueError as exc:
                form.ids.name.error = True
                form.ids.name.helper_text = str(exc)
                form.ids.name.helper_text_mode = "on_error"
                return False
            app().toast("Variedad agregada")
            self.refresh()
        form_dialog("Nueva variedad", VarietyForm(), ok, "AGREGAR")

    def delete_dialog(self, variety_id: int):
        a = app()
        v = a.db.get_variety(variety_id)

        def archive():
            a.db.delete_variety(variety_id)
            a.toast(f"«{v['name']}» archivada (historial conservado)")
            a.refresh_home()

        def purge():
            a.db.delete_variety(variety_id, purge=True)
            a.toast(f"«{v['name']}» eliminada")
            a.refresh_home()

        confirm(f"Quitar «{v['name']}»",
                "Archivar la oculta del muestreo pero conserva fotos y registros (recomendado). "
                "Eliminar borra definitivamente todos sus registros.",
                [("Archivar", archive), ("Eliminar", purge)])


class CustomFieldRow(MDBoxLayout):
    key = StringProperty()
    value = StringProperty()
    field_id = NumericProperty()
    field = ObjectProperty(None, allownone=True)
    screen = ObjectProperty()


class CustomFieldForm(MDBoxLayout):
    pass


class VarietyScreen(MDScreen):
    variety_id = NumericProperty(0)
    METRICS = ("historical_yield", "projected_yield", "basal_canes", "laterals")
    UNITS = ("historical_yield_unit", "projected_yield_unit", "basal_canes_unit", "laterals_unit")

    def load(self, variety_id: int):
        a = app()
        self.variety_id = variety_id
        v = a.db.get_variety(variety_id)
        self.ids.bar.title = v["name"]
        self.ids.name.text = v["name"]
        self.ids.code.text = v["code"] or ""
        self.ids.notes.text = v["notes"] or ""
        self.ids.metrics_title.text = f"PARÁMETROS BIOMÉTRICOS · TEMPORADA {a.season}-{a.season + 1}"
        m = a.db.get_metrics(variety_id, a.season)
        for k in self.METRICS:
            self.ids[k].text = "" if m[k] is None else f"{m[k]:g}"
        for k in self.UNITS:
            if m.get(k):
                self.ids[k].text = m[k]
        self.ids.historical_note.text = m.get("historical_note") or ""
        self.load_custom()

    def load_custom(self):
        a = app()
        box = self.ids.custom
        box.clear_widgets()
        fields = a.db.list_custom_fields(self.variety_id, a.season)
        for f in fields:
            box.add_widget(CustomFieldRow(key=f["key"], value=f"{f['value']} {f['unit']}".strip(),
                                          field_id=f["id"], field=f, screen=self))
        if not fields:
            box.add_widget(MDLabel(text="Sin campos adicionales.", font_style="Caption",
                                   adaptive_height=True, theme_text_color="Custom",
                                   text_color=c(theme.MUTED)))

    @staticmethod
    def _float(text: str):
        text = (text or "").strip().replace(",", ".")
        try:
            return float(text) if text else None
        except ValueError:
            return None

    def save(self):
        a = app()
        name = self.ids.name.text.strip()
        if not name:
            a.toast("El nombre no puede quedar vacío.")
            return
        try:
            a.db.update_variety(self.variety_id, name=name, code=self.ids.code.text.strip(),
                                notes=self.ids.notes.text)
        except Exception:
            a.toast("Ya existe otra variedad con ese nombre.")
            return
        values = {k: self._float(self.ids[k].text) for k in self.METRICS}
        values.update({k: self.ids[k].text for k in self.UNITS})
        values["historical_note"] = self.ids.historical_note.text
        a.db.save_metrics(self.variety_id, a.season, **values)
        a.toast("Ficha guardada")
        self.ids.bar.title = name

    def custom_dialog(self, field: dict | None = None):
        a = app()
        form = CustomFieldForm()
        if field:
            form.ids.key.text, form.ids.value.text, form.ids.unit.text = \
                field["key"], field["value"], field["unit"]
        existing = [k for k in a.db.custom_field_keys()][:4] or \
            ["Diámetro de caña", "Grados Brix", "Incidencia fitosanitaria"]
        for k in existing[:3]:
            form.ids.suggestions.add_widget(MDFlatButton(
                text=k[:18], font_size="11sp", theme_text_color="Custom",
                text_color=c(theme.SLATE), on_release=lambda b, k=k: setattr(form.ids.key, "text", k)))

        def ok(f):
            try:
                a.db.set_custom_field(self.variety_id, a.season, f.ids.key.text,
                                      f.ids.value.text.strip(), f.ids.unit.text.strip())
            except ValueError as exc:
                a.toast(str(exc))
                return False
            self.load_custom()

        form_dialog("Campo personalizado", form, ok)

    def delete_custom(self, field_id: int):
        app().db.delete_custom_field(field_id)
        self.load_custom()

    def report(self):
        self.save()
        a = app()
        a.run_report(lambda: a.reports.variety(self.variety_id, a.season))


# ===========================================================================
# Registro fenológico (variedad × semana)
# ===========================================================================
class PhotoSlot(MDCard):
    kind = StringProperty()
    caption = StringProperty()
    meta = StringProperty("Sin foto")
    has_photo = BooleanProperty(False)
    screen = ObjectProperty()

    def show(self, photo: dict | None):
        box = self.ids.image_box
        box.clear_widgets()
        self.has_photo = bool(photo)
        if photo:
            box.add_widget(FitImage(source=app().thumb(photo["path"], 640), radius=[dp(8)]))
            origin = {"camera": "cámara", "gallery": "galería"}.get(photo["source"], photo["source"])
            self.meta = f"{origin} · {photo['captured_at'][:16].replace('T', ' ')}"
        else:
            box.add_widget(thumb_widget(None, icon="camera-plus-outline"))
            self.meta = "Sin foto"


class ObservationScreen(MDScreen):
    variety_id = NumericProperty(0)
    week_id = NumericProperty(0)
    suggestion = ObjectProperty(None, allownone=True)

    def load(self, variety_id: int, week_id: int):
        a = app()
        self.variety_id, self.week_id = variety_id, week_id
        self.variety = a.db.get_variety(variety_id)
        self.week = a.db.get_week(week_id)
        self.obs = a.db.get_or_create_observation(variety_id, week_id)
        self.ids.bar.title = self.variety["name"]
        self.ids.week_caption.text = (f"Semana {self.week['week_number']} · {self.week['label']} · "
                                      f"Temporada {self.week['season']}-{self.week['season'] + 1}")
        photos = a.db.get_photos(self.obs["id"])
        self.ids.canopy.show(photos.get("canopy"))
        self.ids.detail.show(photos.get("detail"))
        self.ids.bbch.text = self.obs["bbch_label"] or (
            ph.bbch_label(self.obs["bbch_code"], a.db.bbch_names())
            if self.obs["bbch_code"] is not None else "")
        self.ids.notes.text = self.obs["notes"] or ""
        self.ids.stamp.text = (f"Última modificación: {self.obs['updated_at'].replace('T', ' ')}"
                               if self.obs["bbch_code"] is not None else "Registro nuevo")
        self.suggestion = None
        self._show_ai_from_obs()

    def _show_ai_from_obs(self):
        o = self.obs
        self.ids.alternatives.clear_widgets()
        if o["ai_code"] is None:
            self.ids.ai_label.text = "Suba la foto de detalle para obtener una sugerencia."
            self.ids.ai_conf.value = 0
            self.ids.ai_conf_text.text = ""
            self.ids.ai_explain.text = ""
            self.ids.accept_btn.disabled = True
            return
        self.ids.ai_label.text = ph.bbch_label(o["ai_code"], app().db.bbch_names())
        conf = o["ai_confidence"] or 0
        self.ids.ai_conf.value = conf
        self.ids.ai_conf_text.text = f"{conf:.0%} confianza"
        try:
            import json
            detail = json.loads(o["ai_detail"] or "{}")
        except ValueError:
            detail = {}
        self.ids.ai_explain.text = detail.get("explanation", "")
        self._alternatives(detail.get("top", []))
        self.ids.accept_btn.disabled = False

    def _alternatives(self, top):
        box = self.ids.alternatives
        box.clear_widgets()
        for code, p in top[1:3]:
            box.add_widget(MDRectangleFlatButton(
                text=f"BBCH {code:02d} · {p:.0%}", theme_text_color="Custom",
                text_color=c(theme.SLATE), line_color=c(theme.RULE),
                on_release=lambda b, code=code: self._set_bbch(code)))

    def _set_bbch(self, code: int):
        self.ids.bbch.text = ph.bbch_label(code, app().db.bbch_names())

    # -------------------------------------------------------------- fotos
    def capture(self, kind: str, source: str):
        a = app()

        def done(tmp_path, origin):
            if not tmp_path:
                if origin == "error":
                    a.toast("No se pudo obtener la foto.")
                return
            season, n = self.week["season"], self.week["week_number"]
            dest_dir = data_subdir("photos", f"T{season}", f"S{n:02d}")
            path = store_photo(tmp_path, dest_dir, f"{slugify(self.variety['name'])}_{kind}")
            a.db.set_photo(self.obs["id"], kind, path, source=origin)
            self.ids[kind].show(a.db.get_photos(self.obs["id"]).get(kind))
            if kind == "detail":
                self.analyze()

        if source == "camera":
            a.media.take_photo(done)
        else:
            a.media.pick_image(done)

    def remove_photo(self, kind: str):
        a = app()
        photo = a.db.get_photos(self.obs["id"]).get(kind)
        if not photo:
            return

        def do():
            a.db.delete_photo(photo["id"])
            self.ids[kind].show(None)

        confirm("Quitar foto", "La foto se desvincula del registro (el archivo se conserva).",
                [("Quitar", do)])

    # ----------------------------------------------------------------- IA
    def analyze(self):
        a = app()
        photo = a.db.get_photos(self.obs["id"]).get("detail")
        if not photo:
            a.toast("Primero agregue la foto de detalle (Foto 2).")
            return
        self.ids.spinner.active = True
        self.ids.ai_label.text = "Analizando foto de detalle…"
        prev = a.db.previous_observation(self.variety_id, self.week["season"],
                                         self.week["week_number"])
        previous = (prev["bbch_code"], prev["week_number"]) if prev else None
        notes = self.ids.notes.text
        obs_id = self.obs["id"]

        def work():
            try:
                s = a.classifier.suggest(photo["path"], self.week["week_number"], previous, notes)
                a.db.update_observation(obs_id, ai_code=s.code, ai_confidence=s.confidence,
                                        ai_detail=s.as_detail_json())
                self._analysis_done(s, None)
            except Exception as exc:  # noqa: BLE001
                self._analysis_done(None, exc)

        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _analysis_done(self, s, error):
        self.ids.spinner.active = False
        if error:
            self.ids.ai_label.text = "No se pudo analizar la imagen."
            self.ids.ai_explain.text = str(error)
            return
        self.suggestion = s
        self.obs = app().db.get_observation(self.variety_id, self.week_id)
        self._show_ai_from_obs()
        if not self.ids.bbch.text.strip():  # sugerencia pre-cargada en el campo editable
            self.ids.bbch.text = s.label

    def accept_ai(self):
        if self.obs["ai_code"] is not None:
            self._set_bbch(self.obs["ai_code"])

    def open_scale(self, caller):
        a = app()
        items = [(ph.bbch_label(r["code"], {r["code"]: r["label"]}),
                  lambda code=r["code"]: self._set_bbch(code)) for r in a.db.list_bbch()]
        open_menu(caller, items, width_mult=6)

    # -------------------------------------------------------------- guardar
    def save(self):
        a = app()
        text = self.ids.bbch.text.strip()
        code = ph.parse_bbch_code(text)
        if text and code is None:
            a.toast("Indique un código BBCH (ej. «BBCH 65: Plena floración»).")
            return
        ai_code = self.obs["ai_code"]
        a.db.update_observation(
            self.obs["id"], bbch_code=code, bbch_label=text, notes=self.ids.notes.text,
            observed_at=_dt.date.today().isoformat(),
            ai_accepted=None if ai_code is None or code is None else int(code == ai_code))
        if code is not None and a.db.get_setting("ai_auto_learn", False):
            photo = a.db.get_photos(self.obs["id"]).get("detail")
            if photo:
                a.classifier.add_reference(photo["path"], code, photo_id=photo["id"])
        a.toast("Registro guardado")
        a.back()


# ===========================================================================
# Informes
# ===========================================================================
class ReportRow(MDCard):
    title = StringProperty()
    meta = StringProperty()
    path = StringProperty()


class ReportsTab(MDScreen):
    sel_week = ObjectProperty(None, allownone=True)
    sel_from = ObjectProperty(None, allownone=True)
    sel_to = ObjectProperty(None, allownone=True)
    sel_variety = ObjectProperty(None, allownone=True)
    sel_month = ObjectProperty(None, allownone=True)

    def refresh(self):
        a = app()
        weeks = a.db.list_weeks(a.season)
        if not weeks:
            weeks = [a.db.current_week()]
        if self.sel_week is None or self.sel_week["season"] != a.season:
            self.sel_week = a.week
            self.sel_from, self.sel_to = weeks[0], a.week
        if self.sel_variety is None:
            vs = a.db.list_varieties()
            self.sel_variety = vs[0] if vs else None
        self._labels()
        self.list_recent()

    def _labels(self):
        ids = self.ids
        ids.week_btn.text = f"Semana {self.sel_week['week_number']} · {self.sel_week['label']}"
        ids.from_btn.text = f"Desde S{self.sel_from['week_number']}"
        ids.to_btn.text = f"Hasta S{self.sel_to['week_number']}"
        ids.month_btn.text = (f"Mes: {ph.MESES[self.sel_month[1] - 1].capitalize()} {self.sel_month[0]}"
                              if self.sel_month else "Elegir mes (o rango abajo)")
        ids.variety_btn.text = self.sel_variety["name"] if self.sel_variety else "Sin variedades"

    def pick_week(self, caller, target: str):
        a = app()
        weeks = a.db.list_weeks(a.season)

        def choose(w):
            if target == "week":
                self.sel_week = w
            elif target == "from":
                self.sel_from, self.sel_month = w, None
            else:
                self.sel_to, self.sel_month = w, None
            self._labels()

        open_menu(caller, [(f"S{w['week_number']} · {w['label']}", lambda w=w: choose(w))
                           for w in reversed(weeks)])

    def pick_month(self, caller):
        a = app()
        months = []
        for w in a.db.list_weeks(a.season):
            d = _dt.date.fromisoformat(w["start_date"])
            if (d.year, d.month) not in months:
                months.append((d.year, d.month))

        def choose(ym):
            self.sel_month = ym
            ws = [w for w in a.db.list_weeks(a.season)
                  if _dt.date.fromisoformat(w["start_date"]).timetuple()[:2] == ym]
            self.sel_from, self.sel_to = ws[0], ws[-1]
            self._labels()

        open_menu(caller, [(f"{ph.MESES[m - 1].capitalize()} {y}", lambda ym=(y, m): choose(ym))
                           for y, m in months])

    def pick_variety(self, caller):
        def choose(v):
            self.sel_variety = v
            self._labels()
        open_menu(caller, [(v["name"], lambda v=v: choose(v)) for v in app().db.list_varieties()])

    def generate(self, kind: str, package: str):
        a = app()
        r = a.reports
        if kind == "weekly":
            job = lambda: r.weekly(self.sel_week["id"], package)  # noqa: E731
        elif kind == "period":
            if self.sel_month:
                job = lambda: r.monthly(a.season, *self.sel_month, package=package)  # noqa: E731
            else:
                job = lambda: r.period(a.season, self.sel_from["week_number"],  # noqa: E731
                                       self.sel_to["week_number"], package)
        elif kind == "variety":
            if not self.sel_variety:
                return
            job = lambda: r.variety(self.sel_variety["id"], a.season, package)  # noqa: E731
        else:
            job = lambda: r.matrix(a.season, package=package)  # noqa: E731
        a.run_report(job, on_done=lambda _res: self.list_recent())

    def list_recent(self):
        box = self.ids.recent
        box.clear_widgets()
        folder = app().reports.out_dir
        files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                        if f.endswith((".html", ".zip"))), key=os.path.getmtime, reverse=True)
        for p in files[:10]:
            ts = _dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d-%m-%Y %H:%M")
            box.add_widget(ReportRow(title=os.path.basename(p), path=p,
                                     meta=f"{ts} · {max(1, os.path.getsize(p) // 1024)} KB"))
        if not files:
            box.add_widget(MDLabel(text="Aún no se han generado informes.", font_style="Caption",
                                   adaptive_height=True, theme_text_color="Custom",
                                   text_color=c(theme.MUTED)))


# ===========================================================================
# Ajustes
# ===========================================================================
class SettingsTab(MDScreen):
    _loading = False

    def refresh(self):
        a = app()
        cfg = a.reminders.config()
        self._loading = True
        self.ids.enabled.active = cfg.enabled
        self._loading = False
        self.ids.day_btn.text = ph.DIAS[cfg.weekday].capitalize()
        self.ids.time_btn.text = f"{cfg.hour:02d}:{cfg.minute:02d}"
        self.ids.freq_btn.text = FREQUENCIES.get(cfg.every_weeks, f"Cada {cfg.every_weeks} semanas")
        nxt = a.reminders.next_time()
        self.ids.next_text.text = (f"Próximo aviso: {ph.DIAS[nxt.weekday()]} "
                                   f"{ph.format_date_es(nxt.date())}, {nxt:%H:%M}"
                                   if nxt else "Recordatorios desactivados.")
        box = self.ids.exact_box
        box.clear_widgets()
        if IS_ANDROID and cfg.enabled and not can_schedule_exact():
            box.add_widget(MDRectangleFlatButton(
                text="Permitir alarmas exactas", theme_text_color="Custom",
                text_color=c(theme.WARN), line_color=c(theme.WARN),
                on_release=lambda *_: request_exact_alarm_permission()))
        start = a.db.season_start(a.season)
        self.ids.season_text.text = f"Temporada activa: {a.season}-{a.season + 1}"
        self.ids.start_btn.text = f"Semana 1: semana del {ph.format_date_es(start)}"
        ext = a.classifier.extractor
        n = sum(a.db.reference_counts().values())
        self.ids.ai_text.text = (f"Extractor: {ext.name} ({ext.dim} dim.) · {n} fotos de referencia. "
                                 "Todo el análisis se ejecuta en el teléfono, sin conexión.")
        self.ids.data_text.text = f"Datos locales: {a.db.path}"

    def _save(self, **changes):
        a = app()
        cfg = a.reminders.config()
        for k, v in changes.items():
            setattr(cfg, k, v)
        a.reminders.save(cfg)
        self.refresh()

    def on_toggle(self, active: bool):
        if not self._loading:
            self._save(enabled=active)

    def pick_day(self, caller):
        open_menu(caller, [(d.capitalize(), lambda i=i: self._save(weekday=i))
                           for i, d in enumerate(ph.DIAS)])

    def pick_freq(self, caller):
        open_menu(caller, [(t, lambda k=k: self._save(every_weeks=k)) for k, t in FREQUENCIES.items()])

    def pick_time(self):
        cfg = app().reminders.config()
        picker = MDTimePicker(primary_color=c(theme.OLIVE), accent_color=c(theme.PAPER),
                              text_button_color=c(theme.OLIVE_DARK))
        picker.set_time(_dt.time(cfg.hour, cfg.minute))
        picker.bind(on_save=lambda inst, t: self._save(hour=t.hour, minute=t.minute))
        picker.open()

    def pick_start(self):
        a = app()
        start = a.db.season_start(a.season)
        picker = MDDatePicker(year=start.year, month=start.month, day=start.day,
                              primary_color=c(theme.OLIVE), selector_color=c(theme.OLIVE),
                              text_button_color=c(theme.OLIVE_DARK))

        def save(inst, value, _range):
            a.db.set_season_start(ph.season_of(value), value)
            a.season = ph.season_of(value)
            a.week = a.db.current_week()
            a.refresh_home()
            a.toast(f"Semana 1 = semana del {ph.format_date_es(value)}")

        picker.bind(on_save=save)
        picker.open()

    def show_audit(self):
        rows = [(f"{r['action']} · {r['entity']}" + (f" #{r['entity_id']}" if r["entity_id"] else ""),
                 f"{r['ts'].replace('T', ' ')} · {r['detail'][:80]}")
                for r in app().db.audit_trail(60)]
        list_dialog("Bitácora de cambios", rows)

    def backup(self):
        a = app()
        dest = os.path.join(data_subdir("backups"),
                            f"fenorubus_{_dt.datetime.now():%Y%m%d_%H%M%S}.sqlite3")
        with a.db._lock:
            a.db.conn.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copyfile(a.db.path, dest)
        a.db.log("backup", "database", None, os.path.basename(dest))
        a.share_file(dest, "application/x-sqlite3")


# ===========================================================================
# Calibración IA (protegida por PIN)
# ===========================================================================
class PinForm(MDBoxLayout):
    pass


class LabelPhotoRow(MDCard):
    title = StringProperty()
    subtitle = StringProperty()
    in_reference = BooleanProperty(False)
    photo = ObjectProperty()


class AILabScreen(MDScreen):
    def on_pre_enter(self, *_):
        self.refresh()

    def show(self, name: str):
        self.ids.seg.current = name
        self.refresh()

    def refresh(self):
        name = self.ids.seg.current
        getattr(self, f"refresh_{name}")()

    # ----------------------------------------------------------- documentos
    def refresh_docs(self):
        box = self.ids.docs
        box.clear_widgets()
        for d in app().db.list_documents():
            item = TwoLineListItem(
                text=d["title"],
                secondary_text=f"{d['stages_found']} claves BBCH · {d['chars']} caracteres · "
                               f"{d['created_at'][:10]}",
                on_release=lambda w, d=d: self._doc_menu(d))
            box.add_widget(item)

    def _doc_menu(self, doc):
        def delete():
            app().db.delete_document(doc["id"])
            self.refresh_docs()
        confirm(doc["title"], "¿Quitar este documento de la base de conocimiento? "
                              "(la escala BBCH enriquecida se mantiene)", [("Eliminar", delete)])

    def import_document(self):
        a = app()

        def done(path, name):
            if not path:
                return
            try:
                _id, n = a.classifier.import_document(path, title=name or os.path.basename(path))
            except Exception as exc:  # noqa: BLE001
                a.toast(f"No se pudo leer el documento: {exc}")
                return
            a.toast(f"Documento cargado: {n} claves BBCH detectadas")
            self.refresh_docs()

        a.media.pick_document(done)

    def show_scale(self):
        rows = [(f"BBCH {r['code']:02d}: {r['label']}", f"[{r['source']}] {r['description']}")
                for r in app().db.list_bbch()]
        list_dialog("Escala BBCH · frambueso", rows)

    # ------------------------------------------------------------ etiquetado
    def refresh_label(self):
        a = app()
        self.ids.auto_learn.active = bool(a.db.get_setting("ai_auto_learn", False))
        box = self.ids.photos
        box.clear_widgets()
        photos = a.db.list_detail_photos(a.season)
        for p in photos[:80]:
            code = p["bbch_code"]
            row = LabelPhotoRow(
                title=f"{p['variety_name']} · S{p['week_number']}",
                subtitle=(f"BBCH {code:02d}" if code is not None else "Sin estado")
                + f" · {p['week_label']}", in_reference=bool(p["in_reference"]), photo=p)
            row.ids.thumb_box.add_widget(thumb_widget(p["path"]))
            row.bind(on_release=lambda w: self.label_dialog(w, w.photo))
            box.add_widget(row)
        if not photos:
            box.add_widget(MDLabel(text="No hay fotos de detalle en la temporada.",
                                   font_style="Caption", adaptive_height=True))

    def set_auto_learn(self, active: bool):
        app().db.set_setting("ai_auto_learn", bool(active))

    def label_dialog(self, caller, photo):
        a = app()

        def assign(code):
            a.classifier.add_reference(photo["path"], code, photo_id=photo["id"])
            obs = a.db.query_one("SELECT * FROM observations WHERE id=?", (photo["observation_id"],))
            if obs and obs["bbch_code"] is None:
                a.db.update_observation(obs["id"], bbch_code=code,
                                        bbch_label=ph.bbch_label(code, a.db.bbch_names()))
            a.toast(f"Referencia agregada: BBCH {code:02d}")
            self.refresh_label()

        items = []
        if photo["bbch_code"] is not None:
            items.append((f"✓ Usar registro: BBCH {photo['bbch_code']:02d}",
                          lambda: assign(photo["bbch_code"])))
        items += [(ph.bbch_label(r["code"], {r["code"]: r["label"]}), lambda code=r["code"]: assign(code))
                  for r in a.db.list_bbch()]
        open_menu(caller, items, width_mult=6)

    def add_all_labeled(self):
        a = app()
        todo = [p for p in a.db.list_detail_photos() if p["bbch_code"] is not None
                and not p["in_reference"] and os.path.exists(p["path"])]
        if not todo:
            a.toast("No hay fotos etiquetadas pendientes.")
            return

        def work():
            for p in todo:
                a.classifier.add_reference(p["path"], p["bbch_code"], photo_id=p["id"])
            Clock.schedule_once(lambda *_: (a.toast(f"{len(todo)} referencias agregadas"),
                                            self.refresh_label()))

        threading.Thread(target=work, daemon=True).start()
        a.toast(f"Procesando {len(todo)} fotos…")

    # ---------------------------------------------------------------- modelo
    def refresh_model(self):
        a = app()
        ext = a.classifier.extractor
        counts = a.db.reference_counts()
        self.ids.model_text.text = (f"Extractor: {ext.name} · {ext.dim} dimensiones\n"
                                    f"k-NN coseno (k={a.classifier.K}) + priors temporal, "
                                    f"cromático y textual · {sum(counts.values())} referencias")
        box = self.ids.counts
        box.clear_widgets()
        names = a.db.bbch_names()
        for code in sorted(counts):
            box.add_widget(OneLineListItem(text=f"{ph.bbch_label(code, names)} — {counts[code]}"))
        if not counts:
            box.add_widget(MDLabel(text="Sin referencias: la IA usa solo los priors agronómicos.",
                                   font_style="Caption", adaptive_height=True))

    def evaluate(self):
        ev = app().classifier.evaluate()
        if ev["exact"] is None:
            self.ids.eval_text.text = "Se necesitan al menos 3 referencias para evaluar."
        else:
            self.ids.eval_text.text = (f"Validación leave-one-out (n={ev['n']}): exactitud código "
                                       f"{ev['exact']:.0%} · estadio principal {ev['macro']:.0%}")

    def retrain(self):
        a = app()

        def progress(i, n):
            Clock.schedule_once(lambda *_: setattr(self.ids.train_progress, "value", i / max(1, n)))

        def work():
            n = a.classifier.rebuild(progress)
            Clock.schedule_once(lambda *_: (a.toast(f"{n} embeddings recalculados"),
                                            self.refresh_model()))

        threading.Thread(target=work, daemon=True).start()

    def change_pin(self):
        a = app()
        form = PinForm()
        form.ids.pin.hint_text = "PIN actual"
        new = PinForm()
        new.ids.pin.hint_text = "Nuevo PIN (4-8 dígitos)"
        box = MDBoxLayout(orientation="vertical", adaptive_height=True)
        box.add_widget(form)
        box.add_widget(new)

        def ok(_):
            try:
                if not a.pin.change(form.ids.pin.text, new.ids.pin.text):
                    form.ids.msg.text = "PIN actual incorrecto."
                    return False
            except ValueError as exc:
                new.ids.msg.text = str(exc)
                return False
            a.toast("PIN actualizado")

        form_dialog("Cambiar PIN", box, ok)
