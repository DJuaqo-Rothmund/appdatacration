"""
Pantallas y widgets de la app (lógica de UI). El layout está en ui/layout.kv.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading

from kivy.clock import Clock, mainthread
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.layout import Layout
from kivy.uix.widget import Widget
from kivy.properties import (BooleanProperty, ColorProperty, ListProperty, NumericProperty,
                             ObjectProperty, StringProperty)
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDRaisedButton, MDRectangleFlatButton
from kivymd.uix.dialog import MDDialog
from kivymd.uix.fitimage import FitImage
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.list import TwoLineListItem
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.toolbar import MDTopAppBar

import phenology as ph
from android_bridge import request_runtime_permissions, store_photo
from notifications import FREQUENCIES, can_schedule_exact, request_exact_alarm_permission
from platform_utils import IS_ANDROID, app_version, data_subdir
from ui import theme
from ui.theme import c


DIALOG_BG = (0.985, 0.992, 0.985, 1)


def app() -> "MDApp":
    return MDApp.get_running_app()


def fast_clear(container) -> None:
    """
    Vacía un contenedor sin pasar por ThemableBehavior.remove_widget de KivyMD 1.2,
    que recorre todos los observadores del tema (lista que crece con cada widget
    creado) y vuelve cada refresco más lento que el anterior. Las suscripciones de
    Kivy a métodos son referencias débiles, así que no quedan widgets retenidos.
    """
    remove = Layout.remove_widget if isinstance(container, Layout) else Widget.remove_widget
    for child in list(container.children):
        remove(container, child)


# ===========================================================================
# Widgets comunes
# ===========================================================================
class GlassCard(BoxLayout):
    """
    Tarjeta de vidrio liviana (BoxLayout + 2 instrucciones de canvas).

    Reemplaza a MDCard, que arrastra comportamientos de elevación, ripple, foco
    y tema: crear una MDCard cuesta ~15-20 ms; esta, <1 ms. Acepta las mismas
    propiedades usadas en las reglas KV (md_bg_color, line_color, radius,
    adaptive_height, elevation, ripple_behavior).
    """
    md_bg_color = ColorProperty(c(theme.GLASS))
    line_color = ColorProperty(c(theme.GLASS_EDGE))
    radius = ListProperty([dp(20)])
    adaptive_height = BooleanProperty(False)
    elevation = NumericProperty(0)            # compatibilidad (sin sombra)
    ripple_behavior = BooleanProperty(False)  # compatibilidad (sin ripple)

    def on_adaptive_height(self, *_):
        if self.adaptive_height:
            self.size_hint_y = None
            self.bind(minimum_height=self.setter("height"))
            self.height = self.minimum_height


class GlassButton(ButtonBehavior, GlassCard):
    """Tarjeta pulsable con realce al tocar (sin animación de ripple)."""

class Tag(MDLabel):
    bg = ColorProperty(c(theme.LEAF_SOFT))
    fg = ColorProperty(c(theme.LEAF_DARK))


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


class PickRow(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    accent = ColorProperty(c(theme.LEAF_SOFT))


def pick_dialog(title: str, items: list[tuple], dark: bool = False):
    """
    Lista de selección en un diálogo CENTRADO y a lo ancho (reemplaza al menú
    desplegable, que se abría desplazado y cortaba los nombres largos).
    items: (título, subtítulo, callback) o (título, subtítulo, callback, color_acento).
    """
    from kivymd.uix.scrollview import MDScrollView
    dialog = None
    box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6),
                      padding=(0, dp(4)))
    for it in items:
        head, sub, cb = it[0], it[1], it[2]
        row = PickRow(title=head, subtitle=sub or "")
        if len(it) > 3:
            row.accent = it[3]

        def run(_w, cb=cb):
            dialog.dismiss()
            cb()

        row.bind(on_release=run)
        box.add_widget(row)
    sv = MDScrollView(size_hint_y=None, height=min(dp(440), dp(64) * len(items) + dp(8)),
                      do_scroll_x=False)
    sv.add_widget(box)
    dialog = MDDialog(title=title, type="custom", content_cls=sv, md_bg_color=DIALOG_BG,
                      buttons=[MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss())])
    dialog.open()
    return dialog


def bbch_pick_items(db, callback, extra: list | None = None) -> list[tuple]:
    items = list(extra or [])
    for r in db.list_bbch():
        bg, _fg = theme.stage_colors(r["code"])
        items.append((f"BBCH {r['code']:02d} · {r['label']}",
                      ph.MACRO_STAGES.get(r["code"] // 10, ""),
                      lambda code=r["code"]: callback(code), bg))
    return items


def thumb_widget(path: str | None, size: int = 320, icon: str = "image-off-outline"):
    """Marcador inmediato; la miniatura se genera en segundo plano y lo reemplaza."""
    box = GlassCard(md_bg_color=c(theme.LEAF_SOFT, .8), line_color=c("#FFFFFF", .9),
                    radius=[dp(10)])
    box.add_widget(MDIcon(icon=icon, halign="center", theme_text_color="Custom",
                          text_color=c(theme.LEAF)))
    if path and os.path.exists(path):
        def ready(src):
            fast_clear(box)
            box.add_widget(FitImage(source=src, radius=[dp(8)]))
        app().thumb_async(path, size, ready)
    return box


def text_dialog(title: str, lines: list[str], highlight: str = "", actions=(), small=False):
    """Diálogo con texto largo que se ajusta (diagnósticos, errores)."""
    from kivymd.uix.scrollview import MDScrollView
    box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                      padding=(0, 0, 0, dp(8)))
    for text in lines:
        box.add_widget(MDLabel(text=text, font_style="Caption" if small else "Body2",
                               adaptive_height=True, theme_text_color="Custom",
                               text_color=c(theme.BERRY if highlight and highlight in text else theme.INK)))
    sv = MDScrollView(size_hint_y=None, height=dp(380))
    sv.add_widget(box)
    dialog = None

    def run(cb):
        return lambda *_: (dialog.dismiss(), cb())

    buttons = [MDFlatButton(text=t.upper(), on_release=run(cb)) for t, cb in actions]
    buttons.append(MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss()))
    dialog = MDDialog(title=title, type="custom", content_cls=sv, md_bg_color=DIALOG_BG,
                      buttons=buttons)
    dialog.open()
    return dialog


def confirm(title: str, text: str, actions: list[tuple[str, callable]]):
    dialog = None

    def wrap(cb):
        def _run(*_):
            dialog.dismiss()
            if cb:
                cb()
        return _run

    buttons = [MDFlatButton(text="CANCELAR" if actions else "CERRAR", on_release=wrap(None))]
    buttons += [MDFlatButton(text=t.upper(), theme_text_color="Custom",
                             text_color=c(theme.BERRY if "ELIMIN" in t.upper() else theme.LEAF_DARK),
                             on_release=wrap(cb)) for t, cb in actions]
    dialog = MDDialog(title=title, text=text, buttons=buttons, md_bg_color=DIALOG_BG)
    dialog.open()
    return dialog


def form_dialog(title: str, content, on_ok, ok_text: str = "GUARDAR"):
    dialog = None

    def _ok(*_):
        if on_ok(content) is not False:
            dialog.dismiss()

    dialog = MDDialog(title=title, type="custom", content_cls=content, md_bg_color=DIALOG_BG, buttons=[
        MDFlatButton(text="CANCELAR", on_release=lambda *_: dialog.dismiss()),
        MDFlatButton(text=ok_text, theme_text_color="Custom", text_color=c(theme.LEAF_DARK),
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
    dialog = MDDialog(title=title, type="custom", content_cls=sv, md_bg_color=DIALOG_BG,
                      buttons=[MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss())])
    dialog.open()


def bbch_tag_colors(code: int | None):
    """Del verde vegetativo al rojo frambuesa de la maduración."""
    return theme.stage_colors(code)


class TopBar(MDTopAppBar):
    """Barra superior translúcida con texto e iconos verde profundo."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Se asigna tras construir los ids internos (en la regla KV falla en KivyMD 1.2).
        Clock.schedule_once(lambda *_: setattr(self, "specific_text_color", c(theme.LEAF_DARK)))


class NavTile(ButtonBehavior, BoxLayout):
    name = StringProperty()
    text = StringProperty()
    active = BooleanProperty(False)


# ===========================================================================
# Home
# ===========================================================================
class SplashScreen(MDScreen):
    """Pantalla de inicio: logo, nombre y firma (idéntica al presplash de Android)."""

    def animate_in(self):
        """Sin animación: la pantalla ya se ve idéntica al presplash de Android, así que
        continúa la misma imagen (una sola pantalla de inicio, no dos)."""


class HomeScreen(MDScreen):
    TITLES = {"sampling": "Muestreo semanal", "reports": "Informes", "preview": "Vista previa"}

    def on_tab(self, name: str):
        self.ids.tabs.current = name
        self.ids.bar.title = self.TITLES[name]
        getattr(self.ids, name).refresh()

    def refresh(self):
        for tab in self.TITLES:
            getattr(self.ids, tab).refresh()

    def refresh_current(self):
        getattr(self.ids, self.ids.tabs.current or "sampling").refresh()


class SettingsScreen(MDScreen):
    """Ajustes con dos pestañas: «General» y «Variedades»."""

    def on_pre_enter(self, *_):
        self.show(self.ids.pages.current or "general", defer=False)

    def on_enter(self, *_):
        # La lista de variedades se arma en un momento libre, no al tocar la pestaña.
        if not getattr(self.ids.varieties, "_rows", None):
            Clock.schedule_once(lambda *_: self.ids.varieties.refresh(), 0.4)

    def show(self, page: str, defer: bool = True):
        self.ids.pages.current = page   # el cambio de pestaña se ve al instante
        tab = self.ids.settings if page == "general" else self.ids.varieties
        if defer:
            Clock.schedule_once(lambda *_: tab.refresh(), 0)
        else:
            tab.refresh()


class VarietyRow(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    photos = NumericProperty(0)
    photo_tag = StringProperty()
    code_tag = StringProperty()
    code_bg = ColorProperty(c(theme.BERRY_SOFT))
    code_fg = ColorProperty(c(theme.LEAF_DARK))
    done = BooleanProperty(False)
    variety_id = NumericProperty()
    thumb_src = StringProperty("")


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
        # Las filas se reutilizan: crear/destruir widgets KivyMD es lo más costoso.
        cache = getattr(self, "_rows", {})
        if list(cache) != [r["variety"]["id"] for r in rows]:
            fast_clear(box)
            cache = {}
            for r in rows:
                row = VarietyRow(variety_id=r["variety"]["id"])
                row.bind(on_release=lambda w: a.open_observation(w.variety_id, a.week["id"]))
                cache[r["variety"]["id"]] = row
                box.add_widget(row)
            self._rows = cache
        complete = 0
        names = a.db.bbch_names()
        for r in rows:
            obs, photos, v = r["observation"], r["photos"], r["variety"]
            code = obs["bbch_code"] if obs else None
            n = len(photos)
            complete += code is not None and n == 2
            bg, fg = bbch_tag_colors(code)
            row = cache[v["id"]]
            row.title = v["name"]
            row.subtitle = (obs["bbch_label"] or ph.bbch_label(code, names)) if code is not None \
                else ("Foto sin estado asignado" if n else "Pendiente de registro")
            row.photos, row.photo_tag = n, f"FOTOS {n}/2"
            row.code_tag = f"BBCH {code:02d}" if code is not None else "BBCH —"
            row.code_bg, row.code_fg, row.done = bg, fg, code is not None
            thumb = photos.get("detail") or photos.get("canopy")
            src = thumb["path"] if thumb else ""
            if src != row.thumb_src or not row.ids.thumb_box.children:
                row.thumb_src = src
                fast_clear(row.ids.thumb_box)
                row.ids.thumb_box.add_widget(thumb_widget(src or None, icon="sprout-outline"))
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
class VarietyCatalogRow(GlassButton):
    title = StringProperty()
    code = StringProperty()
    summary = StringProperty()
    variety_id = NumericProperty()
    tab = ObjectProperty()


def pick_number(title: str, values, current, callback):
    """Diálogo centrado para elegir un número opcional («Sin definir» = None)."""
    items = [("Sin definir", "No se incluye en el nombre de las fotos", lambda: callback(None))]
    items += [(f"{title} {n}", "Seleccionado" if n == current else "", lambda n=n: callback(n))
              for n in values]
    pick_dialog(title, items)


def map_dialog(start: tuple, zoom: int, on_pick):
    """Mapa a pantalla completa con pin central. on_pick(lat, lon)."""
    from kivy.uix.floatlayout import FloatLayout
    from kivy.uix.modalview import ModalView
    from kivymd.uix.button import MDIconButton
    import geo
    from ui.mapview import MapPicker

    a = app()
    view = ModalView(size_hint=(1, 1), auto_dismiss=True, background_color=(0, 0, 0, .6))
    root = MDBoxLayout(orientation="vertical", md_bg_color=DIALOG_BG)
    head = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(dp(16), dp(12)),
                       spacing=dp(2))
    head.add_widget(MDLabel(text="Marque la ubicación de la muestra", font_style="H6", bold=True,
                            adaptive_height=True))
    coords = MDLabel(text="", font_style="Caption", adaptive_height=True, theme_text_color="Custom",
                     text_color=c(theme.MUTED))
    head.add_widget(coords)
    root.add_widget(head)

    area = FloatLayout()
    mp = MapPicker(start[0], start[1], zoom, workers=a.workers, size_hint=(1, 1),
                   pos_hint={"x": 0, "y": 0})
    area.add_widget(mp)
    pin = MDIcon(icon="map-marker", theme_text_color="Custom", text_color=c(theme.BERRY),
                 halign="center", valign="bottom", size_hint=(None, None), size=(dp(52), dp(52)),
                 pos_hint={"center_x": .5, "y": .5})   # la punta del pin marca el centro
    area.add_widget(pin)
    Clock.schedule_once(lambda *_: setattr(pin, "font_size", dp(52)), 0)  # tras el estilo de KivyMD
    zoom_box = MDBoxLayout(orientation="vertical", size_hint=(None, None), size=(dp(52), dp(112)),
                           pos_hint={"right": .98, "top": .98}, spacing=dp(4))
    for icon, step in (("plus", 1), ("minus", -1)):
        zoom_box.add_widget(MDIconButton(icon=icon, md_bg_color=(1, 1, 1, .92),
                                         on_release=lambda *_, s=step: mp.zoom_by(s)))
    area.add_widget(zoom_box)
    attribution = MDLabel(text="© OpenStreetMap", font_style="Caption", halign="right",
                          size_hint=(None, None), size=(dp(130), dp(18)),
                          pos_hint={"right": .99, "y": .01}, theme_text_color="Custom",
                          text_color=(0.2, 0.2, 0.2, .9))
    area.add_widget(attribution)
    root.add_widget(area)

    def update(*_):
        lat, lon = mp.center_latlon
        coords.text = f"{geo.fmt(lat, lon)} · arrastre el mapa y deje el pin sobre la planta"
    mp.bind(cx=update, cy=update, zoom=update)
    update()

    state = {"req": None}

    def here(*_):
        def progress(lat, lon, acc):
            mp.center_on(lat, lon, max(int(mp.zoom), 17))
            coords.text = f"{geo.fmt(lat, lon)} · GPS ±{acc:.0f} m (afinando…)"

        def done(lat, lon, acc):
            state["req"] = None
            mp.center_on(lat, lon, max(int(mp.zoom), 18))
            a.toast(f"Centrado en su ubicación (±{acc:.0f} m)")

        def fail(msg):
            state["req"] = None
            a.toast(msg)

        def granted(ok):
            if not ok:
                a.toast("Sin permiso de ubicación")
                return

            def go(*_):
                state["req"] = geo.LocationRequest(done, fail, progress)
                state["req"].start()
            Clock.schedule_once(go, 0)
        geo.request_location_permission(granted)

    def stop_gps(*_):
        if state["req"] is not None:
            state["req"].cancel()
    view.bind(on_dismiss=stop_gps)

    def use(*_):
        lat, lon = mp.center_latlon
        view.dismiss()
        on_pick(lat, lon)
        a.toast("Ubicación marcada en el mapa")

    bar = MDBoxLayout(adaptive_height=True, padding=(dp(12), dp(10)), spacing=dp(8))
    bar.add_widget(MDRectangleFlatButton(text="Cancelar", on_release=lambda *_: view.dismiss()))
    bar.add_widget(MDRectangleFlatButton(text="Mi ubicación", on_release=here))
    bar.add_widget(MDRaisedButton(text="Usar este punto", md_bg_color=c(theme.BERRY), on_release=use))
    root.add_widget(bar)
    view.add_widget(root)
    view.open()
    return view


class SectorIrrigationMixin:
    """Botones «Sector (1-10)» y «Equipo de riego (1-4)» de la identificación."""
    sector = NumericProperty(0)
    irrigation = NumericProperty(0)

    def _sync_location_buttons(self):
        self.ids.sector_btn.text = f"Sector: {self.sector or '—'}"
        self.ids.irrigation_btn.text = f"Riego: {'ER' + str(self.irrigation) if self.irrigation else '—'}"
        if hasattr(self, "update_photo_hint"):
            self.update_photo_hint()

    def pick_sector(self):
        def done(n):
            self.sector = n or 0
            self._sync_location_buttons()
        pick_number("Sector", ph.SECTORS, self.sector, done)

    def pick_irrigation(self):
        def done(n):
            self.irrigation = n or 0
            self._sync_location_buttons()
        pick_number("Equipo de riego", ph.IRRIGATION_UNITS, self.irrigation, done)


class VarietyForm(SectorIrrigationMixin, MDBoxLayout):
    pass


class VarietiesTab(MDScreen):
    def refresh(self):
        a = app()
        season = a.season
        self.ids.season_caption.text = (f"Temporada {season}-{season + 1} · toque una variedad "
                                        f"para editar sus parámetros biométricos")
        box = self.ids.rows
        varieties = a.db.list_varieties()
        cache = getattr(self, "_rows", {})
        if list(cache) != [v["id"] for v in varieties]:  # filas reutilizables
            fast_clear(box)
            cache = {}
            for v in varieties:
                row = VarietyCatalogRow(variety_id=v["id"], tab=self)
                row.bind(on_release=lambda w: self.open_variety(w.variety_id))
                cache[v["id"]] = row
                box.add_widget(row)
            self._rows = cache
        for v in varieties:
            m = a.db.get_metrics(v["id"], season)
            parts = []
            if v["sector"]:
                parts.append(f"Sector {v['sector']}")
            if v["irrigation"]:
                parts.append(f"Riego ER{v['irrigation']}")
            if m["historical_yield"] is not None:
                parts.append(f"hist. {m['historical_yield']:g} {m['historical_yield_unit']}")
            if m["projected_yield"] is not None:
                parts.append(f"proy. {m['projected_yield']:g} {m['projected_yield_unit']}")
            if m["basal_canes"] is not None:
                parts.append(f"{m['basal_canes']:g} cañas {m['basal_canes_unit']}")
            n_custom = len(a.db.list_custom_fields(v["id"], season))
            if n_custom:
                parts.append(f"{n_custom} campo(s) extra")
            row = cache[v["id"]]
            row.title, row.code = v["name"], v["code"] or ""
            row.summary = " · ".join(parts) or "Sin parámetros cargados"

    def open_variety(self, variety_id: int):
        app().open_variety(variety_id)

    def add_dialog(self):
        def ok(form):
            try:
                app().db.add_variety(form.ids.name.text, code=form.ids.code.text.strip(),
                                     sector=form.sector or None, irrigation=form.irrigation or None)
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
            self.refresh()
            a.refresh_home()

        def purge():
            a.db.delete_variety(variety_id, purge=True)
            a.toast(f"«{v['name']}» eliminada")
            self.refresh()
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


class VarietyScreen(SectorIrrigationMixin, MDScreen):
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
        self.sector, self.irrigation = v["sector"] or 0, v["irrigation"] or 0
        self._sync_location_buttons()
        self.ids.metrics_title.text = f"PARÁMETROS BIOMÉTRICOS · TEMPORADA {a.season}-{a.season + 1}"
        m = a.db.get_metrics(variety_id, a.season)
        for k in self.METRICS:
            self.ids[k].text = "" if m[k] is None else f"{m[k]:g}"
        for k in self.UNITS:
            if m.get(k):
                self.ids[k].text = m[k]
        self.ids.historical_note.text = m.get("historical_note") or ""
        self.load_custom()

    def update_photo_hint(self):
        if "photo_hint" not in self.ids:
            return
        v = {"name": self.ids.name.text, "code": self.ids.code.text,
             "sector": self.sector, "irrigation": self.irrigation}
        a = app()
        g = ph.photo_basename(v, a.week["start_date"], "canopy")
        d = ph.photo_basename(v, a.week["start_date"], "detail")
        self.ids.photo_hint.text = f"Nombre de las fotos: {g}.jpg (general) · {d}.jpg (detalle)"

    def load_custom(self):
        a = app()
        box = self.ids.custom
        fast_clear(box)
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
                                notes=self.ids.notes.text, sector=self.sector or None,
                                irrigation=self.irrigation or None)
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
                text_color=c(theme.LEAF_DARK), on_release=lambda b, k=k: setattr(form.ids.key, "text", k)))

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
class StripThumb(GlassButton):
    primary = BooleanProperty(False)
    photo_id = NumericProperty(0)


class PhotoSlot(GlassCard):
    kind = StringProperty()
    caption = StringProperty()
    meta = StringProperty("Sin foto")
    has_photo = BooleanProperty(False)
    count = NumericProperty(0)
    screen = ObjectProperty()

    def show_busy(self, text: str = "Procesando foto…"):
        from kivymd.uix.spinner import MDSpinner
        box = self.ids.image_box
        fast_clear(box)
        box.add_widget(MDSpinner(size_hint=(None, None), size=(dp(32), dp(32)),
                                 pos_hint={"center_x": .5, "center_y": .5},
                                 color=c(theme.BERRY)))
        self.meta = text

    def show(self, photos: list[dict]):
        """photos: todas las fotos de este tipo, la principal primero."""
        box, strip = self.ids.image_box, self.ids.strip
        fast_clear(box)
        fast_clear(strip)
        self.count = len(photos)
        self.has_photo = bool(photos)
        if not photos:
            box.add_widget(thumb_widget(None, icon="camera-plus-outline"))
            self.meta = "Sin foto · la cámara suma una foto por toma"
            return
        main = photos[0]
        box.add_widget(thumb_widget(main["path"], 640))
        origin = {"camera": "cámara", "gallery": "galería"}.get(main["source"], main["source"])
        self.meta = (f"Principal: {origin} · {main['captured_at'][11:16]}"
                     + (" · toque una miniatura para cambiarla" if len(photos) > 1 else ""))
        if len(photos) > 1:
            for ph_ in photos:
                t = StripThumb(primary=bool(ph_["is_primary"]), photo_id=ph_["id"])
                t.add_widget(thumb_widget(ph_["path"], 160))
                t.bind(on_release=lambda w: self.screen.make_primary(self.kind, w.photo_id))
                strip.add_widget(t)


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
        self._refresh_slot("canopy")
        self._refresh_slot("detail")
        self.ids.bbch.text = self.obs["bbch_label"] or (
            ph.bbch_label(self.obs["bbch_code"], a.db.bbch_names())
            if self.obs["bbch_code"] is not None else "")
        self.ids.notes.text = self.obs["notes"] or ""
        self.ids.stamp.text = (f"Última modificación: {self.obs['updated_at'].replace('T', ' ')}"
                               if self.obs["bbch_code"] is not None else "Registro nuevo")
        self.suggestion = None
        self._show_ai_from_obs()
        self.refresh_location()

    # ------------------------------------------------ ubicación (opcional)
    GPS_SOURCES = {"gps": "GPS del teléfono", "foto": "desde la foto", "mapa": "marcada en el mapa"}

    def refresh_location(self):
        o = self.obs
        has = o["latitude"] is not None and o["longitude"] is not None
        self.ids.gps_icon.icon = "map-marker-check" if has else "map-marker-off-outline"
        self.ids.gps_icon.text_color = c(theme.BERRY if has else theme.MUTED)
        if has:
            import geo
            self.ids.gps_text.text = geo.fmt(o["latitude"], o["longitude"])
            parts = [self.GPS_SOURCES.get(o["gps_source"] or "", o["gps_source"] or "")]
            if o["gps_accuracy"]:
                parts.append(f"precisión ±{o['gps_accuracy']:.0f} m")
            self.ids.gps_detail.text = " · ".join(p for p in parts if p)
        else:
            self.ids.gps_text.text = "Sin ubicación"
            self.ids.gps_detail.text = ("Opcional: tome la posición actual, márquela en el mapa o "
                                        "se completa sola si la foto trae GPS.")
        self.ids.gps_clear.opacity = 1 if has else 0
        self.ids.gps_clear.disabled = not has

    def set_location(self, lat, lon, accuracy=None, source="gps"):
        a = app()
        a.db.update_observation(self.obs["id"], latitude=lat, longitude=lon,
                                gps_accuracy=accuracy, gps_source=source)
        a.db.set_setting("last_location", [lat, lon])
        self.obs = a.db.get_observation(self.variety_id, self.week_id)
        self.refresh_location()

    def clear_location(self):
        self.set_location(None, None, None, None)

    def locate_gps(self):
        a = app()
        import geo
        gps = getattr(self, "_gps", None)
        if gps is not None and not gps._done:
            gps.finish()   # «Usar ahora»: se queda con la mejor lectura obtenida
            return

        def granted(ok):
            if not ok:
                a.toast("Sin permiso de ubicación: puede marcar el punto en el mapa.")
                return
            Clock.schedule_once(lambda *_: start(), 0)

        def reset_button():
            self.ids.gps_btn.text = "Mi ubicación"
            self.ids.gps_btn.icon = "crosshairs-gps"

        def start():
            obs_id = self.obs["id"]
            self.ids.gps_text.text = "Buscando señal GPS…"
            self.ids.gps_detail.text = (f"Objetivo ±{geo.LocationRequest.TARGET_M:.0f} m · mejor al aire "
                                        "libre, con el teléfono quieto")
            self.ids.gps_btn.text = "Usar ahora"
            self.ids.gps_btn.icon = "check"

            def progress(lat, lon, acc):
                if self.obs["id"] != obs_id:
                    return
                self.ids.gps_text.text = f"{geo.fmt(lat, lon)}"
                self.ids.gps_detail.text = (f"Precisión actual ±{acc:.0f} m · buscando ±"
                                            f"{geo.LocationRequest.TARGET_M:.0f} m…")
                self.ids.gps_btn.text = f"Usar ahora (±{acc:.0f} m)"

            def done(lat, lon, acc):
                self._gps = None
                reset_button()
                if self.obs["id"] != obs_id:
                    return
                self.set_location(lat, lon, acc, "gps")
                if acc <= geo.LocationRequest.TARGET_M:
                    a.toast(f"Ubicación guardada (±{acc:.0f} m)")
                else:
                    a.toast(f"Ubicación guardada con ±{acc:.0f} m (no se logró ±10 m: "
                            "pruebe al aire libre y reintente)")

            def fail(msg):
                self._gps = None
                reset_button()
                self.refresh_location()
                a.toast(msg)

            try:
                self._gps = geo.LocationRequest(done, fail, progress)
                self._gps.start()
            except Exception as exc:  # noqa: BLE001
                fail(f"No se pudo leer el GPS: {exc}")

        geo.request_location_permission(granted)

    def on_leave(self, *_):
        gps = getattr(self, "_gps", None)
        if gps is not None:
            gps.cancel()   # no dejar el GPS encendido al salir del registro
            self._gps = None
            self.ids.gps_btn.text = "Mi ubicación"
            self.ids.gps_btn.icon = "crosshairs-gps"

    def pick_on_map(self):
        a = app()
        o = self.obs
        if o["latitude"] is not None:
            start, zoom = (o["latitude"], o["longitude"]), 18
        elif a.db.get_setting("last_location"):
            start, zoom = tuple(a.db.get_setting("last_location")), 17
        else:
            import geo
            start, zoom = geo.DEFAULT_CENTER, 6
        map_dialog(start, zoom, lambda lat, lon: self.set_location(lat, lon, None, "mapa"))

    def _show_ai_from_obs(self):
        o = self.obs
        fast_clear(self.ids.alternatives)
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
        fast_clear(box)
        for code, p in top[1:3]:
            box.add_widget(MDRectangleFlatButton(
                text=f"BBCH {code:02d} · {p:.0%}", theme_text_color="Custom",
                text_color=c(theme.LEAF_DARK), line_color=c(theme.LINE),
                on_release=lambda b, code=code: self._set_bbch(code)))

    def _set_bbch(self, code: int):
        self.ids.bbch.text = ph.bbch_label(code, app().db.bbch_names())

    # -------------------------------------------------------------- fotos
    def _refresh_slot(self, kind: str):
        self.ids[kind].show(app().db.list_photos(self.obs["id"], kind))

    def capture(self, kind: str, source: str):
        """Cada toma (o cada foto elegida de la galería) SE SUMA al registro."""
        a = app()

        def done(result, origin):
            paths = [p for p in (result if isinstance(result, list) else [result]) if p]
            if not paths:
                if origin == "error":
                    a.toast("No se pudo obtener la foto.")
                return
            season, n = self.week["season"], self.week["week_number"]
            obs_id = self.obs["id"]
            self.ids[kind].show_busy("Procesando foto…" if len(paths) == 1
                                     else f"Procesando {len(paths)} fotos…")

            def work():  # normalizar y guardar fuera del hilo de la interfaz
                error = None
                try:
                    dest_dir = data_subdir("photos", f"T{season}", f"S{n:02d}")
                    base = ph.photo_basename(self.variety, self.week["start_date"], kind)
                    for tmp in paths:
                        if self.obs["latitude"] is None:
                            import geo
                            gps = geo.exif_gps(tmp)
                            if gps:
                                Clock.schedule_once(lambda *_, g=gps: (
                                    self.set_location(g[0], g[1], None, "foto"),
                                    a.toast("Ubicación tomada de la foto")))
                        path = store_photo(tmp, dest_dir, base, exact=True)
                        pid = a.db.add_photo(obs_id, kind, path, source=origin)
                        a.thumb(path, 640)
                        a.thumb(path, 160)
                        a.backup_photo(pid, path)
                except Exception as exc:  # noqa: BLE001
                    error = exc
                Clock.schedule_once(lambda *_: stored(error))

            def stored(error):
                if self.obs["id"] != obs_id:  # el usuario ya cambió de registro
                    return
                self._refresh_slot(kind)
                if error:
                    a.toast(f"No se pudo guardar la foto: {error}")
                    return
                if origin == "camera":
                    a.toast("Foto agregada · toque la cámara otra vez para sumar más")
                if kind == "detail":
                    self.analyze()

            a.workers.submit(work)

        if source == "camera":
            # Mismo nombre que en la app y en Drive: 28092026-C11G, 28092026-C11G-2…
            seq = len(a.db.list_photos(self.obs["id"], kind)) + 1
            hint = ph.photo_basename(self.variety, self.week["start_date"], kind, seq)
            a.media.take_photo(done, hint, exact=True)
        else:
            a.media.pick_images(done)

    def make_primary(self, kind: str, photo_id: int):
        app().db.set_primary(photo_id)
        self._refresh_slot(kind)
        app().toast("Foto principal actualizada (es la que aparece en los informes)")

    def remove_photo(self, kind: str):
        a = app()
        photo = a.db.get_photos(self.obs["id"]).get(kind)
        if not photo:
            return

        def do():
            a.db.delete_photo(photo["id"])
            self._refresh_slot(kind)

        confirm("Quitar la foto principal",
                "La foto se desvincula del registro (el archivo se conserva en el teléfono). "
                "Si hay otras fotos, la más reciente pasa a ser la principal.", [("Quitar", do)])

    # ----------------------------------------------------------------- IA
    def analyze(self):
        a = app()
        details = [p["path"] for p in a.db.list_photos(self.obs["id"], "detail")]
        if not details:
            a.toast("Primero agregue la foto de detalle (Foto 2).")
            return
        self.ids.spinner.active = True
        self.ids.ai_label.text = ("Analizando foto de detalle…" if len(details) == 1
                                  else f"Analizando {len(details)} fotos de detalle…")
        prev = a.db.previous_observation(self.variety_id, self.week["season"],
                                         self.week["week_number"])
        previous = (prev["bbch_code"], prev["week_number"]) if prev else None
        notes = self.ids.notes.text
        obs_id = self.obs["id"]

        def work():
            try:
                s = a.classifier.suggest(details, self.week["week_number"], previous, notes)
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
        pick_dialog("Escala BBCH · frambueso", bbch_pick_items(app().db, self._set_bbch))

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
# Widgets gráficos de la vista previa
# ===========================================================================
class ProgressRing(Widget):
    """Anillo de avance (0..1) con el porcentaje al centro."""
    value = NumericProperty(0)
    thickness = NumericProperty(dp(9))
    color = ColorProperty(c(theme.BERRY))
    track = ColorProperty(c(theme.BERRY_SOFT))


class CompletenessGrid(Widget):
    """Mini-matriz variedad × semana: 2 completo, 1 parcial, 0 vacío."""
    rows = ListProperty([])
    weeks = ListProperty([])
    COLORS = {2: theme.LEAF, 1: theme.BERRY_LIGHT, 0: "#FFFFFF"}

    def on_rows(self, *_):
        self.height = dp(18) + len(self.rows) * dp(15)
        self._redraw()

    def on_size(self, *_):
        self._redraw()

    on_pos = on_size

    def _redraw(self):
        from kivy.core.text import Label as CoreLabel
        from kivy.graphics import Color, Rectangle, RoundedRectangle
        self.canvas.clear()
        if not self.rows or not self.weeks:
            return
        label_w = dp(40)
        n = len(self.weeks)
        cw = max(dp(6), (self.width - label_w) / n)
        ch = dp(12)
        gap = dp(3)
        top = self.top - dp(16)
        with self.canvas:
            for j, wk in enumerate(self.weeks):   # encabezados de semana (cada 2 si son muchas)
                if n > 10 and j % 2:
                    continue
                t = CoreLabel(text=f"S{wk}", font_size=dp(9))
                t.refresh()
                Color(*c(theme.MUTED))
                Rectangle(texture=t.texture, size=t.texture.size,
                          pos=(self.x + label_w + j * cw + (cw - t.texture.size[0]) / 2, top + dp(2)))
            for i, r in enumerate(self.rows):
                y = top - (i + 1) * (ch + gap)
                t = CoreLabel(text=str(r["code"])[:6], font_size=dp(9), bold=True)
                t.refresh()
                Color(*c(theme.INK_2))
                Rectangle(texture=t.texture, size=t.texture.size,
                          pos=(self.x, y + (ch - t.texture.size[1]) / 2))
                for j, st in enumerate(r["cells"]):
                    Color(*c(self.COLORS[st], .95 if st else .55))
                    RoundedRectangle(pos=(self.x + label_w + j * cw + gap / 2, y),
                                     size=(cw - gap, ch), radius=[dp(3)])


# ===========================================================================
# Vista previa de informes (sin enviar)
# ===========================================================================
class PreviewTab(MDScreen):
    kind = StringProperty("weekly")
    KIND_NAMES = {"weekly": "Reporte semanal inter-varietal",
                  "period": "Evolución mensual / por período",
                  "variety": "Ficha completa por variedad",
                  "matrix": "Matriz comparativa global"}

    def refresh(self):
        a = app()
        weeks = a.db.list_weeks(a.season) or [a.db.current_week()]
        if getattr(self, "_season", None) != a.season:
            self._season = a.season
            self.sel_week = a.week
            self.sel_from, self.sel_to, self.sel_month = weeks[0], a.week, None
            vs = a.db.list_varieties()
            self.sel_variety = vs[0] if vs else None
        self._build_params()
        self._summarize()

    def set_kind(self, kind: str):
        self.kind = kind
        self.refresh()

    # ------------------------------------------------------------ parámetros
    def _param_button(self, icon, text, callback):
        from kivy.factory import Factory
        btn = Factory.GhostButton(icon=icon, text=text)
        btn.bind(on_release=callback)
        return btn

    def _build_params(self):
        box = self.ids.params
        fast_clear(box)
        if self.kind == "weekly":
            box.add_widget(self._param_button(
                "calendar-week", f"Semana {self.sel_week['week_number']} · {self.sel_week['label']}",
                lambda b: self._pick_week(b, "week")))
        elif self.kind == "period":
            month = (f"{ph.MESES[self.sel_month[1] - 1].capitalize()} {self.sel_month[0]}"
                     if self.sel_month else "Elegir mes")
            box.add_widget(self._param_button("calendar-month", month, self._pick_month))
            row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
            row.add_widget(self._param_button("ray-start", f"Desde S{self.sel_from['week_number']}",
                                              lambda b: self._pick_week(b, "from")))
            row.add_widget(self._param_button("ray-end", f"Hasta S{self.sel_to['week_number']}",
                                              lambda b: self._pick_week(b, "to")))
            box.add_widget(row)
        elif self.kind == "variety":
            name = self.sel_variety["name"] if self.sel_variety else "Sin variedades"
            box.add_widget(self._param_button("fruit-cherries", name, self._pick_variety))
        else:
            box.add_widget(MDLabel(text=f"Toda la temporada {app().season}-{app().season + 1}",
                                   font_style="Caption", adaptive_height=True,
                                   theme_text_color="Custom", text_color=c(theme.MUTED)))

    def _pick_week(self, caller, target):
        a = app()

        def choose(w):
            if target == "week":
                self.sel_week = w
            elif target == "from":
                self.sel_from, self.sel_month = w, None
            else:
                self.sel_to, self.sel_month = w, None
            self.refresh()

        pick_dialog("Semana de muestreo", [(f"Semana {w['week_number']}", w["label"], lambda w=w: choose(w))
                                            for w in reversed(a.db.list_weeks(a.season))])

    def _pick_month(self, caller):
        a = app()
        months = []
        for w in a.db.list_weeks(a.season):
            d = _dt.date.fromisoformat(w["start_date"])
            if (d.year, d.month) not in months:
                months.append((d.year, d.month))

        def choose(ym):
            ws = [w for w in a.db.list_weeks(a.season)
                  if _dt.date.fromisoformat(w["start_date"]).timetuple()[:2] == ym]
            self.sel_month, self.sel_from, self.sel_to = ym, ws[0], ws[-1]
            self.refresh()

        open_menu(caller, [(f"{ph.MESES[m - 1].capitalize()} {y}", lambda ym=(y, m): choose(ym))
                           for y, m in months])

    def _pick_variety(self, caller):
        def choose(v):
            self.sel_variety = v
            self.refresh()
        open_menu(caller, [(v["name"], lambda v=v: choose(v)) for v in app().db.list_varieties()])

    # --------------------------------------------------------------- resumen
    def _args(self) -> dict:
        a = app()
        if self.kind == "weekly":
            return {"week_id": self.sel_week["id"]}
        if self.kind == "period":
            lo, hi = sorted((self.sel_from["week_number"], self.sel_to["week_number"]))
            return {"week_from": lo, "week_to": hi}
        if self.kind == "variety":
            return {"variety_id": self.sel_variety["id"] if self.sel_variety else None}
        return {"season": a.season}

    def _summarize(self):
        from reporter import report_summary
        a = app()
        args = self._args()
        args.pop("season", None)
        sm = report_summary(a.db, self.kind, a.season, **args)
        ids = self.ids
        ids.sum_title.text = self.KIND_NAMES[self.kind]
        ratio = sm["complete"] / sm["cells"] if sm["cells"] else 0
        ids.ring.value = ratio
        ids.ring_text.text = f"{ratio:.0%}"
        ids.metric_photos.text = f"{sm['photos']}/{sm['photos_expected']}"
        ids.metric_bbch.text = f"{sm['bbch']}/{sm['cells']}"
        ids.metric_notes.text = str(sm["notes"])
        ids.grid.weeks = sm["grid"]["weeks"]
        ids.grid.rows = sm["grid"]["rows"]
        lack = []
        if sm["missing_photos"]:
            lack.append(f"{sm['missing_photos']} foto" + ("s" if sm["missing_photos"] != 1 else ""))
        if sm["missing_bbch"]:
            lack.append(f"{sm['missing_bbch']} estado" + ("s" if sm["missing_bbch"] != 1 else ""))
        ids.lack_text.text = ("Faltan " + " y ".join(lack)) if lack else "Completo"
        ids.lack_text.text_color = c(theme.BERRY) if lack else c(theme.LEAF)
        mb = sm["est_kb"] / 1024
        ids.size_text.text = f"≈ {mb:.1f} MB" if mb >= 1 else f"≈ {sm['est_kb']} KB"

    # ----------------------------------------------------------- vista previa
    def open_preview(self):
        a = app()
        r = a.reports
        r.include_all_photos = bool(a.db.get_setting("report_all_photos", False))
        args = self._args()
        kind = self.kind
        if kind == "weekly":
            job = lambda: r.weekly(args["week_id"], "preview")  # noqa: E731
        elif kind == "period":
            if self.sel_month:
                job = lambda: r.monthly(a.season, *self.sel_month, package="preview")  # noqa: E731
            else:
                job = lambda: r.period(a.season, args["week_from"], args["week_to"], "preview")  # noqa: E731
        elif kind == "variety":
            if not args["variety_id"]:
                return
            job = lambda: r.variety(args["variety_id"], a.season, "preview")  # noqa: E731
        else:
            job = lambda: r.matrix(a.season, package="preview")  # noqa: E731
        self.ids.preview_btn.disabled = True
        self.ids.preview_btn.text = "Preparando vista previa…"

        def work():
            try:
                res = job()
                Clock.schedule_once(lambda *_: self._ready(res, None))
            except Exception as exc:  # noqa: BLE001
                error = exc
                Clock.schedule_once(lambda *_: self._ready(None, error))

        a.workers.submit(work)

    def _ready(self, res, error):
        self.ids.preview_btn.disabled = False
        self.ids.preview_btn.text = "Ver vista previa completa"
        if error:
            app().toast(f"No se pudo preparar la vista previa: {error}")
            return
        app().media.preview(res.path, res.title)


# ===========================================================================
# Informes
# ===========================================================================
class ReportRow(GlassButton):
    title = StringProperty()
    meta = StringProperty()
    path = StringProperty()


class ReportsTab(MDScreen):
    sel_week = ObjectProperty(None, allownone=True)
    sel_from = ObjectProperty(None, allownone=True)
    sel_to = ObjectProperty(None, allownone=True)
    sel_variety = ObjectProperty(None, allownone=True)
    sel_month = ObjectProperty(None, allownone=True)

    def set_all_photos(self, active: bool):
        app().db.set_setting("report_all_photos", bool(active))

    def refresh(self):
        a = app()
        self.ids.all_photos.active = bool(a.db.get_setting("report_all_photos", False))
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

        pick_dialog("Semana de muestreo", [(f"Semana {w['week_number']}", w["label"], lambda w=w: choose(w))
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
        r.include_all_photos = bool(a.db.get_setting("report_all_photos", False))
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
        fast_clear(box)
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
        fast_clear(box)
        if IS_ANDROID and cfg.enabled and not can_schedule_exact():
            box.add_widget(MDRectangleFlatButton(
                text="Permitir alarmas exactas", theme_text_color="Custom",
                text_color=c(theme.WARN), line_color=c(theme.WARN),
                on_release=lambda *_: request_exact_alarm_permission()))
        start = a.db.season_start(a.season)
        self.ids.season_text.text = (f"Temporada {a.season}-{a.season + 1} · semana en curso: "
                                     f"Semana {a.week['week_number']} ({a.week['label']})")
        self.ids.start_btn.text = f"Semana 1: semana del {ph.format_date_es(start)}"
        n = sum(a.db.reference_counts().values())
        # Sin instanciar el clasificador (evita cargar numpy solo por abrir Ajustes).
        ext = a.__dict__.get("classifier")
        engine = f"Extractor: {ext.extractor.name} · " if ext else ""
        self.ids.ai_text.text = (f"{engine}{n} fotos de referencia. "
                                 "Todo el análisis se ejecuta en el teléfono, sin conexión.")
        self.ids.data_text.text = f"PhenoRubus versión {app_version()} · datos locales: {a.db.path}"
        self.refresh_drive()

    # ------------------------------------------------ respaldo en Google Drive
    def refresh_drive(self, st=None):
        a = app()
        drive = a.drive
        if not getattr(self, "_drive_hooked", False):
            self._drive_hooked = True
            drive.listeners.append(lambda status: Clock.schedule_once(
                lambda *_: self.refresh_drive(status)))
        st = st or drive.status()
        on = st["enabled"]
        total = st["pending"] + st["done"] + st["errors"]
        if not st["available"]:
            title, icon, col = "Disponible en el teléfono", "cloud-off-outline", theme.MUTED
        elif not on:
            title, icon, col = "Sin conectar", "cloud-off-outline", theme.MUTED
        elif st["running"]:
            title, icon, col = "Subiendo fotos…", "cloud-sync-outline", theme.LEAF
        elif st["errors"] and not st["pending"]:
            title, icon, col = "Con errores", "cloud-alert-outline", theme.WARN
        elif st["pending"]:
            title, icon, col = f"{st['pending']} foto(s) en espera", "cloud-clock-outline", theme.WARN
        else:
            title, icon, col = "Todo respaldado", "cloud-check-outline", theme.LEAF
        self.ids.drive_title.text = title
        self.ids.drive_icon.icon = icon
        self.ids.drive_icon.text_color = c(col)
        parts = []
        if on:
            parts.append(f"{st['done']} de {total} fotos en Drive" if total else "Aún no hay fotos en la cola")
            if st["errors"]:
                parts.append(f"{st['errors']} con error")
            if st["last_sync"]:
                parts.append("última sincronización " + st["last_sync"][5:16].replace("T", " "))
        if st["message"] and (on or not st["available"]):
            parts.append(st["message"])
        self.ids.drive_text.text = " · ".join(parts)
        self.ids.drive_bar.value = 100 * st["done"] / total if on and total else 0
        self.ids.drive_bar_box.opacity = 1 if on and total else 0
        self.ids.drive_connect.text = ("Reconectar cuenta" if on else "Conectar Google Drive")
        # Referencia FUERTE: al quitar el bloque del árbol, `ids` (referencia débil) lo
        # perdería y el recolector lo borraría → ReferenceError al volver a «General».
        if not hasattr(self, "_drive_slot"):
            opts = self.ids.drive_opts.__self__   # objeto real, no el proxy débil de `ids`
            self._drive_opts = opts
            self._drive_slot = (opts.parent, opts.parent.children.index(opts))
        opts = self._drive_opts
        parent, index = self._drive_slot
        if on and opts.parent is None:
            parent.add_widget(opts, index=index)
        elif not on and opts.parent is not None:
            parent.remove_widget(opts)
        self._loading = True
        self.ids.drive_wifi.active = st["wifi_only"]
        self._loading = False

    def drive_connect(self):
        a = app()
        if not a.drive.available:
            a.toast("El respaldo en Google Drive funciona en el teléfono (Android).")
            return
        a.toast("Conectando con Google… siga los pasos en pantalla")

        def done(ok, msg):
            Clock.schedule_once(lambda *_: (a.toast(msg), self.refresh_drive()))

        a.drive.connect(done)

    def drive_diagnostics(self):
        from platform_utils import app_version
        lines = app().drive.diagnostics()
        head = f"PhenoRubus {app_version()} · paquete org.rubus.fenorubus"
        text_dialog("Diagnóstico de Google Drive",
                    [head] + (lines or ["Sin registros: toque «Conectar Google Drive» y vuelva aquí."]),
                    highlight="✗")

    def show_errors(self):
        import crashguard
        from platform_utils import app_version
        lines = crashguard.entries()
        extra = [("Borrar registro", lambda: (crashguard.clear(), app().toast("Registro borrado")))] \
            if lines else []
        text_dialog("Registro de errores",
                    [f"PhenoRubus {app_version()}"] + (lines or ["Sin errores registrados."]),
                    highlight="Error", actions=extra, small=True)

    def drive_disconnect(self):
        confirm("Desconectar Google Drive",
                "Las fotos dejarán de subirse. Las que ya están en Drive y en el teléfono se conservan.",
                [("Desconectar", lambda: (app().drive.disconnect(), self.refresh_drive()))])

    def drive_wifi(self, active: bool):
        if not self._loading:
            app().db.set_setting("drive_wifi_only", bool(active))
            if not active:
                app().drive.flush_async()

    def drive_sync(self):
        a = app()
        a.drive.retry_errors()
        a.toast("Sincronizando con Google Drive…")

    def drive_existing(self):
        a = app()

        def work():
            n = a.drive.enqueue_existing()
            Clock.schedule_once(lambda *_: (a.toast(f"{n} foto(s) añadidas a la cola de respaldo"
                                                    if n else "No hay fotos anteriores pendientes"),
                                            self.refresh_drive()))

        a.workers.submit(work)

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
        from kivymd.uix.pickers import MDTimePicker  # import diferido (pesado)
        picker = MDTimePicker(primary_color=c(theme.LEAF), accent_color=c(theme.GLASS),
                              text_button_color=c(theme.LEAF_DARK))
        picker.set_time(_dt.time(cfg.hour, cfg.minute))
        picker.bind(on_save=lambda inst, t: self._save(hour=t.hour, minute=t.minute))
        picker.open()

    def pick_start(self):
        a = app()
        start = a.db.season_start(a.season)
        from kivymd.uix.pickers import MDDatePicker  # import diferido (pesado)
        picker = MDDatePicker(year=start.year, month=start.month, day=start.day,
                              primary_color=c(theme.LEAF), selector_color=c(theme.LEAF),
                              text_button_color=c(theme.LEAF_DARK))
        picker.bind(on_save=lambda inst, value, _range: self._confirm_start(value))
        picker.open()

    def reset_start(self):
        self._confirm_start(ph.default_season_start(app().season))

    def _confirm_start(self, value: _dt.date):
        a = app()
        season = ph.season_of(value)
        if value == a.db.season_start(season):
            a.toast("Esa ya es la semana de inicio.")
            return
        n = a.db.count_observations(season)

        def apply():
            a.db.set_season_start(season, value)
            a.season = season
            a.week = a.db.current_week()
            self.refresh()
            a.toast(f"Semana 1 = semana del {ph.format_date_es(value)}")

        if n:
            confirm("Cambiar la semana de inicio",
                    f"Hay {n} registro(s) en la temporada. Conservarán su número de semana "
                    f"(p. ej. «Semana 3»), pero sus fechas y etiquetas se recalcularán desde el "
                    f"{ph.format_date_es(value)}.", [("Cambiar", apply)])
        else:
            apply()

    def show_audit(self):
        rows = [(f"{r['action']} · {r['entity']}" + (f" #{r['entity_id']}" if r["entity_id"] else ""),
                 f"{r['ts'].replace('T', ' ')} · {r['detail'][:80]}")
                for r in app().db.audit_trail(60)]
        list_dialog("Bitácora de cambios", rows)

    # ---------------------------------------------- copias de seguridad
    def _busy(self, text):
        self.ids.backup_text.text = text

    def full_backup(self):
        a = app()
        self._busy("Creando copia de seguridad…")

        def work():
            try:
                from data_transfer import full_backup
                path = full_backup(a.db)
                a.media.save_public(path, "application/zip")   # una sola copia en Descargas
                if a.db.get_setting("drive_enabled", False):
                    a.drive.enqueue(None, path, remote=f"Respaldos/{os.path.basename(path)}")
                size = os.path.getsize(path) / 1e6
                msg = (f"Copia guardada en Descargas › PhenoRubus: {os.path.basename(path)} "
                       f"({size:.1f} MB)" + (" · también se sube a Drive" if a.db.get_setting(
                           "drive_enabled", False) else ""))
                Clock.schedule_once(lambda *_: (self._busy(msg), a.toast("Copia de seguridad lista"),
                                                self._offer_share(path)))
            except Exception as exc:  # noqa: BLE001
                error = exc
                Clock.schedule_once(lambda *_: (self._busy(f"No se pudo crear la copia: {error}"),))

        a.workers.submit(work)

    def _offer_share(self, path):
        confirm("Copia de seguridad lista",
                "Quedó en Descargas › PhenoRubus. ¿Quiere además enviarla (Drive, correo, WhatsApp…)?",
                [("Compartir", lambda: app().share_file(path, "application/zip"))])

    def restore_backup(self):
        a = app()

        def picked(path, name):
            if not path:
                return
            if not path.lower().endswith((".zip", ".sqlite3", ".db")):
                a.toast("Elija la copia .zip o el archivo .sqlite3")
                return
            confirm("Restaurar copia de seguridad",
                    f"Se reemplazarán los datos actuales por los de «{name or os.path.basename(path)}». "
                    "Antes se guarda automáticamente una copia de lo actual.",
                    [("Restaurar", lambda: self._do_restore(path))])

        a.media.pick_document(picked, exts=(".zip", ".sqlite3", ".db"))

    def _do_restore(self, path):
        a = app()
        self._busy("Restaurando… no cierre la app.")

        def progress(i, n):
            if i % 10 == 0:
                Clock.schedule_once(lambda *_: self._busy(f"Recuperando fotos… {i}/{n}"))

        def work():
            try:
                from data_transfer import restore
                res = restore(a.db, path, progress=progress)
                Clock.schedule_once(lambda *_: self._restored(res, None))
            except Exception as exc:  # noqa: BLE001
                error = exc
                Clock.schedule_once(lambda *_: self._restored(None, error))

        request_runtime_permissions(lambda _ok: a.workers.submit(work))

    def _restored(self, res, error):
        a = app()
        if error:
            self._busy(f"No se pudo restaurar: {error}")
            return
        a.reload_data()
        self.refresh()
        self._busy("Restaurado: " + res.summary())
        text = res.summary()
        if res.missing:
            text += ("\n\nFotos sin archivo (puede recuperarlas con «Importar informes semanales» "
                     "o volver a adjuntarlas desde la galería):\n• " + "\n• ".join(res.missing[:12]))
            if len(res.missing) > 12:
                text += f"\n… y {len(res.missing) - 12} más"
        confirm("Datos restaurados", text, [])

    def import_reports(self):
        a = app()

        def picked(path, name):
            if not path:
                return
            if not path.lower().endswith((".html", ".htm", ".zip")):
                a.toast("Elija un informe semanal (.html o .zip)")
                return
            self._busy(f"Leyendo {name or os.path.basename(path)}…")

            def work():
                try:
                    from data_transfer import import_reports
                    res = import_reports(a.db, [path])
                    Clock.schedule_once(lambda *_: self._reports_done(res, None))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: self._reports_done(None, error))

            a.workers.submit(work)

        a.media.pick_document(picked, exts=(".html", ".htm", ".zip"))

    def _reports_done(self, res, error):
        a = app()
        if error:
            self._busy(f"No se pudo importar: {error}")
            return
        a.reload_data()
        self.refresh()
        self._busy("Importado: " + res.summary() + (" · " + "; ".join(res.skipped[:3]) if res.skipped else ""))
        a.toast("Informe importado" if res.reports else "El archivo no es un informe semanal")


# ===========================================================================
# Calibración IA (protegida por PIN)
# ===========================================================================
class PinForm(MDBoxLayout):
    pass


class LabelPhotoRow(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    in_reference = BooleanProperty(False)
    photo = ObjectProperty()


class MindSeg(MDRaisedButton):
    selected = BooleanProperty(False)


class MindTopBar(MDTopAppBar):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_once(lambda *_: setattr(self, "specific_text_color", c(theme.MIND_TEXT)))


class MindRow(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    value = StringProperty()


class MindOption(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    code = NumericProperty(0)
    result = StringProperty("")   # "", "ok", "bad"
    locked = BooleanProperty(False)  # ya respondida (sin atenuar el texto como «disabled»)


def _mind(cls_name: str, **kw):
    from kivy.factory import Factory
    return getattr(Factory, cls_name)(**kw)


class AILabScreen(MDScreen):
    """«Mente del sistema»: desafío diario, etiquetado, modelo y datos históricos."""

    def on_pre_enter(self, *_):
        self.refresh()

    def show(self, name: str):
        self.ids.seg.current = name
        self.refresh()

    def refresh(self):
        getattr(self, f"refresh_{self.ids.seg.current}")()

    # ======================================================= desafío diario
    @property
    def game(self):
        a = app()
        if not hasattr(self, "_game"):
            from ai_game import DailyChallenge
            self._game = DailyChallenge(a.db, lambda: a.classifier)
        return self._game

    def refresh_challenge(self):
        box = self.ids.task_box
        if not self.game.challenges():
            fast_clear(box)
            box.add_widget(self._mind_note("Preparando los desafíos de hoy…"))

            def work():
                self.game.ensure_today()
                Clock.schedule_once(lambda *_: self._render_challenge())

            app().workers.submit(work)
        else:
            self._render_challenge()

    def _update_header(self):
        st = self.game.stats()
        done, total = self.game.progress()
        self.ids.ch_ring.value = done / total if total else 0
        self.ids.ch_ring_text.text = f"{done}/{total or 5}"
        today = _dt.date.today().isoformat()
        self.ids.ch_title.text = ("¡Desafío completado!" if st.get("last_day") == today
                                  else "Desafíos cerrados" if total and done == total
                                  else "Entrena a la IA")
        self.ids.streak_text.text = f"{st['streak']} día" + ("s" if st["streak"] != 1 else "")
        acc = f" · {st['accuracy']:.0%} aciertos" if st["accuracy"] is not None else ""
        self.ids.xp_text.text = f"Nivel {st['level']} · {st['xp']} XP{acc}"
        self.ids.level_bar.value = st["level_progress"]

    def _mind_note(self, text):
        card = _mind("MindCard")
        card.add_widget(_mind("MindText", text=text))
        return card

    def _render_challenge(self):
        self._update_header()
        box = self.ids.task_box
        fast_clear(box)
        chs = self.game.challenges()
        pending = [ch for ch in chs if ch["status"] == "open"]
        if not pending:
            st = self.game.stats()
            card = _mind("MindCard")
            n_done = sum(ch["status"] == "done" for ch in chs)
            counted = n_done >= self.game.MIN_DONE_FOR_STREAK
            card.add_widget(_mind("MindSection", text="HOY COMPLETADO" if counted else "DESAFÍOS CERRADOS"))
            card.add_widget(MDLabel(text="La IA aprendió de tus respuestas." if counted else
                                    f"Responde al menos {self.game.MIN_DONE_FOR_STREAK} para sumar racha.",
                                    font_style="H6", bold=True,
                                    adaptive_height=True, theme_text_color="Custom",
                                    text_color=c(theme.MIND_TEXT)))
            card.add_widget(_mind("MindMuted", text=f"Racha: {st['streak']} día(s) · mejor racha: {st['best']} · "
                                                    f"{st['xp']} XP. Vuelve mañana por 5 desafíos nuevos."))
            box.add_widget(card)
            return
        ch = pending[0]
        box.add_widget(self._identify_card(ch) if ch["kind"] == "identify" else self._capture_card(ch))

    # ---- «¿Qué estado es?»
    def _identify_card(self, ch):
        p = ch["payload"]
        names = app().db.bbch_names()
        card = _mind("MindCard")
        card.add_widget(_mind("MindSection", text="¿QUÉ ESTADO ES?"))
        card.add_widget(_mind("MindMuted", text=f"{p['variety']} · semana {p['week']}"
                                                + ("" if p.get("truth") is not None
                                                   else " · foto sin estado: tu respuesta la etiqueta")))
        img = MDBoxLayout(size_hint_y=None, height=dp(230))
        img.add_widget(thumb_widget(p["path"], 640))
        card.add_widget(img)
        grid = MDGridLayout(cols=2, spacing=dp(8), adaptive_height=True)
        opts = []
        for code in p["options"]:
            o = MindOption(title=f"BBCH {code:02d}", subtitle=names.get(code, ""), code=code)
            o.bind(on_release=lambda w, ch=ch: self._answer(ch, w, opts))
            opts.append(o)
            grid.add_widget(o)
        card.add_widget(grid)
        self._skip = self._skip_button(ch)
        card.add_widget(self._skip)
        return card

    def _answer(self, ch, option, opts):
        if option.locked:
            return
        for o in opts:
            o.locked = True
        if getattr(self, "_skip", None) is not None and self._skip.parent:
            self._skip.parent.remove_widget(self._skip)
        res = self.game.answer_identify(ch["id"], option.code)
        truth = res["truth"]
        for o in opts:
            if truth is not None and o.code == truth:
                o.result = "ok"
            elif o is option:
                o.result = "ok" if res["correct"] in (True, None) else "bad"
            if not o.result:
                o.opacity = .45
        if res["correct"] is None:
            msg = f"Etiquetada como BBCH {option.code:02d}. +{res['xp']} XP"
        elif res["correct"]:
            msg = f"¡Correcto! +{res['xp']} XP"
        else:
            msg = f"Era BBCH {truth:02d}. La IA lo aprende igual. +{res['xp']} XP"
        if res.get("ai") is not None:
            msg += f" · la IA había pensado BBCH {res['ai']:02d}"
        self._feedback(msg, res)

    # ---- «Muéstrame este estado»
    def _capture_card(self, ch):
        a = app()
        code = ch["payload"]["target"]
        row = next((r for r in a.db.list_bbch() if r["code"] == code), None)
        card = _mind("MindCard")
        card.add_widget(_mind("MindSection", text="MUÉSTRAME ESTE ESTADO"))
        card.add_widget(MDLabel(text=f"BBCH {code:02d}", font_style="H4", bold=True, adaptive_height=True,
                                theme_text_color="Custom", text_color=c(theme.MIND_ACCENT)))
        card.add_widget(MDLabel(text=row["label"] if row else "", font_style="H6", adaptive_height=True,
                                theme_text_color="Custom", text_color=c(theme.MIND_TEXT)))
        if row and row["description"]:
            card.add_widget(_mind("MindMuted", text=row["description"]))
        card.add_widget(_mind("MindMuted", text=ph.MACRO_STAGES.get(code // 10, "")))
        cam = _mind("MindButton", text="Tomar foto")
        cam.bind(on_release=lambda *_: self._capture(ch, "camera"))
        gal = _mind("MindGhost", icon="image-outline", text="Elegir de la galería")
        gal.bind(on_release=lambda *_: self._capture(ch, "gallery"))
        card.add_widget(cam)
        card.add_widget(gal)
        card.add_widget(self._skip_button(ch))
        return card

    def _capture(self, ch, source):
        a = app()

        def done(result, _origin):
            path = result[0] if isinstance(result, list) else result
            if not path:
                return
            fast_clear(self.ids.task_box)
            self.ids.task_box.add_widget(self._mind_note("La IA está mirando la foto…"))

            def work():
                try:
                    stored = store_photo(path, data_subdir("training"), f"desafio_bbch{ch['payload']['target']:02d}")
                    res = self.game.complete_capture(ch["id"], stored)
                    res["path"] = stored
                    Clock.schedule_once(lambda *_: self._capture_done(res))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: (a.toast(f"No se pudo procesar: {error}"),
                                                    self._render_challenge()))

            a.workers.submit(work)

        if source == "camera":
            a.media.take_photo(done, f"entrenamiento_BBCH{ch['payload']['target']:02d}")
        else:
            a.media.pick_image(done)

    def _capture_done(self, res):
        fast_clear(self.ids.task_box)
        card = _mind("MindCard")
        img = MDBoxLayout(size_hint_y=None, height=dp(200))
        img.add_widget(thumb_widget(res["path"], 640))
        card.add_widget(img)
        seen = f"La IA vio BBCH {res['ai']:02d}"
        verdict = " — ¡coincide con el estadio!" if res["agree"] else " — ahora aprende tu ejemplo"
        self.ids.task_box.add_widget(card)
        self._feedback(f"{seen}{verdict}. +{res['xp']} XP", res, container=card)

    # ---- comunes
    def _skip_button(self, ch):
        b = MDFlatButton(text="SALTAR", theme_text_color="Custom", text_color=c(theme.MIND_MUTED),
                         pos_hint={"center_x": .5})
        b.bind(on_release=lambda *_: (self.game.skip(ch["id"]), self._render_challenge()))
        return b

    def _feedback(self, msg, res, container=None):
        self._update_header()
        card = container or _mind("MindCard")
        card.add_widget(MDLabel(text=msg, font_style="Subtitle1", bold=True, adaptive_height=True,
                                theme_text_color="Custom",
                                text_color=c(theme.MIND_BAD if res.get("correct") is False else theme.MIND_OK)))
        if res.get("day_done"):
            card.add_widget(_mind("MindMuted", text=f"¡Desafío del día completado! Racha: "
                                                    f"{res['stats']['streak']} día(s)."))
        nxt = _mind("MindButton", text="Siguiente")
        nxt.bind(on_release=lambda *_: self._render_challenge())
        card.add_widget(nxt)
        if container is None:
            self.ids.task_box.add_widget(card)

    # ============================================================ etiquetado
    def refresh_label(self):
        a = app()
        self.ids.auto_learn.active = bool(a.db.get_setting("ai_auto_learn", False))
        box = self.ids.photos
        fast_clear(box)
        photos = a.db.list_detail_photos(a.season)
        limit = getattr(self, "_label_limit", 12)
        for p in photos[:limit]:
            code = p["bbch_code"]
            row = LabelPhotoRow(
                title=f"{p['variety_name']} · S{p['week_number']}",
                subtitle=(f"BBCH {code:02d}" if code is not None else "Sin estado")
                + f" · {p['week_label']}", in_reference=bool(p["in_reference"]), photo=p)
            row.ids.thumb_box.add_widget(thumb_widget(p["path"]))
            row.bind(on_release=lambda w: self.label_dialog(w, w.photo))
            box.add_widget(row)
        if len(photos) > limit:
            box.add_widget(MDFlatButton(
                text=f"MOSTRAR MÁS ({len(photos) - limit} restantes)", pos_hint={"center_x": .5},
                theme_text_color="Custom", text_color=c(theme.MIND_ACCENT),
                on_release=lambda *_: self._more_labels()))
        if not photos:
            box.add_widget(self._mind_note("No hay fotos de detalle en la temporada."))

    def _more_labels(self):
        self._label_limit = getattr(self, "_label_limit", 12) + 12
        self.refresh_label()

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

        extra = []
        if photo["bbch_code"] is not None:
            extra.append((f"✓ Usar el registro: BBCH {photo['bbch_code']:02d}",
                          "Estado ya asignado en el muestreo", lambda: assign(photo["bbch_code"])))
        pick_dialog("¿Qué estado muestra la foto?", bbch_pick_items(a.db, assign, extra))

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

        a.workers.submit(work)
        a.toast(f"Procesando {len(todo)} fotos…")

    # ================================================================ modelo
    def refresh_model(self):
        a = app()
        ext = a.classifier.extractor
        counts = a.db.reference_counts()
        self.ids.model_text.text = (f"{ext.name} · {ext.dim} dimensiones · k-NN coseno "
                                    f"(k={a.classifier.K}) + priors · {sum(counts.values())} referencias")
        box = self.ids.counts
        fast_clear(box)
        names = a.db.bbch_names()
        for code in sorted(counts):
            box.add_widget(MindRow(title=f"BBCH {code:02d}", subtitle=names.get(code, ""),
                                   value=str(counts[code])))
        if not counts:
            box.add_widget(_mind("MindMuted", text="Sin referencias: la IA usa solo los priors agronómicos."))

    def evaluate(self):
        ev = app().classifier.evaluate()
        if ev["exact"] is None:
            self.ids.eval_text.text = "Se necesitan al menos 3 referencias para evaluar."
        else:
            app().db.set_setting("ai_last_eval", ev)
            self.ids.eval_text.text = (f"Validación leave-one-out (n={ev['n']}): código exacto "
                                       f"{ev['exact']:.0%} · estadio principal {ev['macro']:.0%}")

    def retrain(self):
        a = app()

        def progress(i, n):
            Clock.schedule_once(lambda *_: setattr(self.ids.train_progress, "value", i / max(1, n)))

        def work():
            n = a.classifier.rebuild(progress)
            Clock.schedule_once(lambda *_: (a.toast(f"{n} embeddings recalculados"),
                                            self.refresh_model()))

        a.workers.submit(work)

    def try_photo(self):
        a = app()

        def picked(path, _origin):
            if not path:
                return
            self.ids.spinner.active = True
            self.ids.try_label.text = "Analizando…"
            week = a.week["week_number"]

            def work():
                try:
                    preview = a.thumb(path, 640)
                    s = a.classifier.suggest(path, week_number=week)
                    Clock.schedule_once(lambda *_: self._show(preview, s, None))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: self._show(None, None, error))

            a.workers.submit(work)

        a.media.pick_image(picked)

    def _show(self, preview, s, error):
        self.ids.spinner.active = False
        if error:
            self.ids.try_label.text = "No se pudo analizar la imagen."
            self.ids.try_explain.text = str(error)
            return
        box = self.ids.try_box
        fast_clear(box)
        box.add_widget(FitImage(source=preview, radius=[dp(16)]))
        box.height, box.opacity = dp(190), 1
        self.ids.try_label.text = s.label
        self.ids.try_conf.value = s.confidence
        alts = " · ".join(f"BBCH {code:02d} {p:.0%}" for code, p in s.top[1:])
        self.ids.try_explain.text = f"Confianza {s.confidence:.0%} · alternativas: {alts}\n{s.explanation}"

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

    # ================================================================= datos
    def refresh_data(self):
        box = self.ids.docs
        fast_clear(box)
        for d in app().db.list_documents():
            row = MindRow(title=d["title"], subtitle=f"{d['created_at'][:10]} · {d['chars']} caracteres",
                          value=f"{d['stages_found']}")
            row.bind(on_release=lambda w, d=d: self._doc_menu(d))
            box.add_widget(row)
        last = app().db.get_setting("last_import")
        if last and not self.ids.import_text.text:
            self.ids.import_text.text = f"Última importación: {last}"

    def _doc_menu(self, doc):
        def delete():
            app().db.delete_document(doc["id"])
            self.refresh_data()
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
            self.refresh_data()

        a.media.pick_document(done)

    def show_scale(self):
        rows = [(f"BBCH {r['code']:02d}: {r['label']}", f"[{r['source']}] {r['description']}")
                for r in app().db.list_bbch()]
        list_dialog("Escala BBCH · frambueso", rows)

    def save_template(self):
        from importer import write_template
        a = app()
        path = write_template(os.path.join(data_subdir("tmp"), "plantilla_historico_phenorubus.csv"))
        try:
            a.media.save_public(path, "text/csv")
            a.toast("Plantilla guardada en Descargas/PhenoRubus")
        except Exception as exc:  # noqa: BLE001
            a.toast(f"No se pudo guardar: {exc}")

    def import_history(self):
        a = app()

        def done(path, name):
            if not path:
                return
            if not path.lower().endswith((".csv", ".zip")):
                a.toast("Elija un archivo .csv o .zip")
                return
            train = self.ids.train_import.active
            self.ids.import_btn.disabled = True
            self.ids.import_text.text = f"Importando {name or os.path.basename(path)}…"

            def progress(i, n):
                Clock.schedule_once(lambda *_: setattr(self.ids.import_progress, "value", i / max(1, n)))

            def work():
                from importer import import_file
                try:
                    res = import_file(a.db, path, classifier=a.classifier if train else None,
                                      train_ai=train, progress=progress)
                    Clock.schedule_once(lambda *_: self._import_done(res, None))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: self._import_done(None, error))

            a.workers.submit(work)

        a.media.pick_document(done)

    def _import_done(self, res, error):
        self.ids.import_btn.disabled = False
        if error:
            self.ids.import_text.text = f"No se pudo importar: {error}"
            return
        summary = res.summary()
        app().db.set_setting("last_import", summary)
        errs = (f"\n{len(res.errors)} advertencia(s): " + "; ".join(res.errors[:3])) if res.errors else ""
        self.ids.import_text.text = f"Importado: {summary}{errs}"
        app().toast("Importación terminada")
