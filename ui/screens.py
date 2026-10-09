"""
Pantallas y widgets de la app (lógica de UI). El layout está en ui/layout.kv.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading

from kivy.clock import Clock, mainthread
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
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
    action = ObjectProperty(None, allownone=True)   # lo asigna la lista reciclable

    def on_release(self, *_):
        if self.action is not None:
            self.action()


_pick_open = None   # diálogo de selección abierto (evita abrir varios con toques repetidos)


def pick_dialog(title: str, items: list[tuple], dark: bool = False):
    """
    Lista de selección en un diálogo CENTRADO y a lo ancho (reemplaza al menú
    desplegable, que se abría desplazado y cortaba los nombres largos).
    items: (título, subtítulo, callback) o (título, subtítulo, callback, color_acento).

    Usa una lista RECICLABLE (RecycleView): solo se crean las filas visibles (~8) en vez
    de todas (la escala BBCH tiene 34), así abre al instante también en teléfonos básicos.
    """
    global _pick_open
    if _pick_open is not None:
        return _pick_open
    from kivy.uix.recycleboxlayout import RecycleBoxLayout
    from kivy.uix.recycleview import RecycleView
    dialog = None

    def wrap(cb):
        def run():
            dialog.dismiss()
            cb()
        return run

    data = []
    for it in items:
        row = {"title": it[0], "subtitle": it[1] or "", "action": wrap(it[2])}
        row["accent"] = it[3] if len(it) > 3 else c(theme.LEAF_SOFT)
        data.append(row)
    rv = RecycleView(size_hint_y=None, do_scroll_x=False, bar_width=dp(4),
                     height=min(dp(440), dp(64) * len(items) + dp(8)))
    layout = RecycleBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                              padding=(0, dp(4)), default_size=(None, dp(58)),
                              default_size_hint=(1, None))
    layout.bind(minimum_height=layout.setter("height"))
    rv.add_widget(layout)
    rv.viewclass = "PickRow"
    rv.data = data
    dialog = MDDialog(title=title, type="custom", content_cls=rv, md_bg_color=DIALOG_BG,
                      buttons=[MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss())])

    def closed(*_):
        global _pick_open
        _pick_open = None

    dialog.bind(on_dismiss=closed)
    _pick_open = dialog
    dialog.open()
    return dialog


def bbch_pick_items(db, callback, extra: list | None = None) -> list[tuple]:
    items = list(extra or [])
    for r in db.list_bbch():
        bg, _fg = theme.stage_colors(r["code"])
        items.append((f"BBCH {ph.code_str(r['code'])} · {r['label']}",
                      ph.MACRO_STAGES.get(ph.macro_of(r["code"]), ""),
                      lambda code=r["code"]: callback(code), bg))
    return items


class ScaleGroup(ButtonBehavior, GlassCard):
    title = StringProperty()
    count = StringProperty()
    accent = ColorProperty(c(theme.LEAF_SOFT))
    expanded = BooleanProperty(False)
    macro = NumericProperty(0)


class ScaleRow(ButtonBehavior, GlassCard):
    code = StringProperty()
    text = StringProperty()
    accent = ColorProperty(c(theme.LEAF_SOFT))
    current = BooleanProperty(False)


def bbch_dialog(title: str, db, callback, extra: list | None = None, current: int | None = None):
    """Escala BBCH casi a todo el ancho, agrupada por macroestadio en desplegables
    (se abre el del estado actual). Cada estado muestra su texto completo
    (cañas anuales y brotes laterales en líneas separadas)."""
    global _pick_open
    if _pick_open is not None:
        return _pick_open
    from kivy.core.window import Window
    from kivy.uix.scrollview import ScrollView
    from kivy.utils import escape_markup
    dialog = None
    stages = db.list_bbch()
    groups: dict[int, list] = {}
    for r in stages:
        groups.setdefault(ph.macro_of(r["code"]), []).append(r)
    opened = {ph.macro_of(current)} if current is not None else set()
    state = {}
    box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6), padding=(0, dp(2)))
    scroll = ScrollView(size_hint_y=None, height=Window.height * .64, do_scroll_x=False, bar_width=dp(4))
    scroll.add_widget(box)

    def pick(code):
        dialog.dismiss()
        callback(code)

    def toggle(head):
        if head.macro in opened:
            opened.discard(head.macro)
        else:
            opened.clear()          # acordeón: un macroestadio abierto a la vez
            opened.add(head.macro)
        build()
        Clock.schedule_once(lambda *_: scroll.scroll_to(head, padding=dp(4), animate=False), 0)

    def body(r) -> str:
        label = escape_markup(r["label"]).replace(" · Laterales: ", "\n[b]Laterales:[/b] ")
        for k in ("Cañas: ", "Cañas anuales: ", "Brotes laterales: "):
            if label.startswith(k):
                return f"[b]{k.strip()}[/b] " + label[len(k):]
        return label

    def build():
        box.clear_widgets()
        for t, sub, cb, *rest in (extra or []):
            row = PickRow(title=t, subtitle=sub or "", accent=rest[0] if rest else c(theme.LEAF_SOFT))
            row.action = lambda cb=cb: (dialog.dismiss(), cb())
            box.add_widget(row)
        for macro in sorted(groups):
            rows = groups[macro]
            bg, _fg = theme.stage_colors(rows[0]["code"])
            head = ScaleGroup(title=f"{macro} · {ph.MACRO_STAGES.get(macro, '')}", macro=macro,
                              count=f"{len(rows)} estados", accent=bg, expanded=macro in opened)
            head.bind(on_release=toggle)
            box.add_widget(head)
            if macro not in opened:
                continue
            for r in rows:
                row = ScaleRow(code=ph.code_str(r["code"]), text=body(r), accent=bg,
                               current=r["code"] == current)
                row.bind(on_release=lambda _w, code=r["code"]: pick(code))
                box.add_widget(row)
                if row.current:
                    state["current_row"] = row

    build()
    dialog = MDDialog(title=title, type="custom", content_cls=scroll, md_bg_color=DIALOG_BG,
                      size_hint=(.96, None),
                      buttons=[MDFlatButton(text="CERRAR", on_release=lambda *_: dialog.dismiss())])

    def closed(*_):
        global _pick_open
        _pick_open = None

    dialog.bind(on_dismiss=closed)
    _pick_open = dialog
    dialog.open()
    if state.get("current_row") is not None:   # mostrar el estado actual sin buscarlo
        Clock.schedule_once(lambda *_: scroll.scroll_to(state["current_row"], padding=dp(60),
                                                        animate=False), .15)
    return dialog


def current_profile() -> str:
    return (getattr(app(), "workspace", None) or {}).get("profile") or "id"


def pick_catalog(on_pick, profile: str | None = None, allow_none: str = ""):
    """Desplegable con el catálogo de variedades (I+D o Predio) + «Nueva variedad…».
    on_pick(nombre, código). allow_none: texto de la opción «sin variedad» (si se ofrece)."""
    import catalog
    a = app()
    profile = profile or current_profile()
    shared = a.workspaces.shared

    def new():
        from kivy.factory import Factory
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                           padding=(0, dp(8), 0, 0))
        f_name = Factory.Field(hint_text="Nombre de la variedad (ej.: Meeker)")
        f_code = Factory.Field(hint_text="Código para las fotos (opcional, ej.: MEE)")
        form.add_widget(f_name)
        form.add_widget(f_code)

        def ok(_f):
            if not catalog.add(shared, profile, f_name.text, f_code.text.upper()):
                a.toast("Escriba el nombre real de la variedad (no «Código n»).")
                return False
            on_pick(f_name.text.strip(), f_code.text.strip().upper())

        form_dialog("Nueva variedad", form, ok, "AGREGAR")

    items = []
    if allow_none:
        items.append((allow_none, "", lambda: on_pick("", "")))
    items += [(e["name"], f"Código {e['code']}" if e["code"] else "", lambda e=e: on_pick(e["name"], e["code"] or ""))
              for e in catalog.entries(shared, profile)]
    items.append(("+ Nueva variedad…", "Se guarda en el catálogo para la próxima vez", new))
    pick_dialog(f"Variedades · {'Predio' if profile == 'predio' else 'I+D'}", items)


def manage_catalog(on_change=None):
    """Ajustes › Registros: ver, agregar, cambiar el código o quitar variedades del catálogo."""
    import catalog
    a = app()
    profile = current_profile()
    shared = a.workspaces.shared

    def options(e):
        def set_code():
            from kivy.factory import Factory
            form = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(0, dp(8), 0, 0))
            f = Factory.Field(hint_text="Código para las fotos", text=e["code"] or "")
            form.add_widget(f)

            def ok(_f):
                catalog.add(shared, profile, e["name"], f.text.upper())
                if on_change:
                    on_change()
            form_dialog(e["name"], form, ok)

        def remove():
            catalog.remove(shared, profile, e["name"])
            a.toast(f"«{e['name']}» quitada del catálogo (los registros no cambian)")
            if on_change:
                on_change()

        pick_dialog(e["name"], [("Cambiar código", e["code"] or "sin código", set_code),
                                ("Quitar del catálogo", "Los registros existentes no cambian", remove)])

    items = [(e["name"], f"Código {e['code']}" if e["code"] else "sin código", lambda e=e: options(e))
             for e in catalog.entries(shared, profile)]
    items.append(("+ Nueva variedad…", "", lambda: pick_catalog(lambda *_: on_change and on_change(),
                                                                profile)))
    pick_dialog(f"Catálogo de variedades · {'Predio' if profile == 'predio' else 'I+D'}", items)


def remember_variety(name: str, code: str = "", profile: str | None = None) -> None:
    """Variedad escrita a mano al crear un registro: queda en el catálogo (si es un nombre real)."""
    import catalog
    try:
        catalog.add(app().workspaces.shared, profile or current_profile(), name, code)
    except Exception:  # noqa: BLE001
        pass


class RoundThumb(Widget):
    """Miniatura con esquinas redondeadas dibujada con UNA instrucción (RoundedRectangle
    con textura, recortada «cover»). FitImage de KivyMD usa un stencil por imagen, que en
    GPUs modestas se paga en cada cuadro al desplazar listas."""
    source = StringProperty("")
    radius = NumericProperty(dp(10))

    def __init__(self, **kw):
        super().__init__(**kw)
        from kivy.graphics import Color, RoundedRectangle
        with self.canvas:
            self._bg = Color(*c(theme.LEAF_SOFT, .8))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._layout, size=self._layout, source=self._load)
        self._tex = None
        self._load()

    def _load(self, *_):
        self._tex = None
        if self.source:
            try:
                from kivy.core.image import Image as CoreImage
                self._tex = CoreImage(self.source).texture   # Kivy guarda la textura en caché
            except Exception:  # noqa: BLE001 - archivo dañado: queda el fondo suave
                self._tex = None
        self._bg.rgba = (1, 1, 1, 1) if self._tex else c(theme.LEAF_SOFT, .8)
        self._layout()

    def _layout(self, *_):
        r = self._rect
        r.pos, r.size, r.radius = self.pos, self.size, [self.radius]
        tex = self._tex
        if tex is None or not self.width or not self.height:
            r.texture = None
            return
        tw, th = tex.size
        scale = max(self.width / tw, self.height / th)
        cw, ch = self.width / scale, self.height / scale          # recorte centrado
        r.texture = tex.get_region((tw - cw) / 2, (th - ch) / 2, cw, ch)


def thumb_widget(path: str | None, size: int = 320, icon: str = "image-off-outline"):
    """Marcador inmediato (fondo suave con ícono); la miniatura se genera en segundo plano."""
    thumb = RoundThumb()
    from kivy.uix.label import Label
    from kivymd.icon_definitions import md_icons
    from kivymd import fonts_path
    # Ícono chico y nítido (fuente vectorial a su tamaño real, no una imagen estirada).
    mark = Label(text=md_icons.get(icon, ""), font_name=os.path.join(fonts_path, "materialdesignicons-webfont.ttf"),
                 font_size=dp(20), color=c(theme.LEAF, .45), size_hint=(None, None), size=(dp(28), dp(28)))
    thumb.add_widget(mark)
    thumb.bind(pos=lambda w, v: setattr(mark, "center", w.center), size=lambda w, v: setattr(mark, "center", w.center))

    state = {"async": False}

    def ready(src):
        if src:
            thumb.remove_widget(mark)
            thumb.source = src
            if state["async"]:   # recién generada: aparece suave, sin salto
                from kivy.animation import Animation
                thumb.opacity = 0
                Animation(opacity=1, d=.15).start(thumb)

    if path and os.path.exists(path):
        app().thumb_async(path, size, ready)   # en caché: se dibuja ya, sin marcador
        state["async"] = True
    return thumb


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
        self.update_banner()
        getattr(self.ids, self.ids.tabs.current or "sampling").refresh()
        Clock.schedule_once(lambda *_: self.update_drive_icon(), 0)

    # ------------------------------------------------- acceso rápido a Drive
    def _action(self, sources) -> object | None:
        for btn in self.ids.bar.ids.right_actions.children:
            if getattr(btn, "icon", None) in sources:
                return btn
        return None

    def _style_actions(self):
        if getattr(self, "_styled", False):
            return
        ai = self._action((theme.AI_BAR,))
        if ai is not None and ai.ids.lbl_ic._size[0]:   # el cerebro, más grande que el resto
            lbl = ai.ids.lbl_ic
            grow = dp(31) - lbl._size[0]
            lbl._size = [dp(31), dp(31)]
            lbl.y -= grow / 2
            self._styled = True

    def update_drive_icon(self, *_):
        """Drive con flecha (hay algo por subir), con check (al día) o tachado (sin cuenta)."""
        self._style_actions()
        if getattr(self, "_spin_ev", None):
            return   # subiendo: lo actualiza la animación
        btn = self._action([theme.DRIVE_UP, theme.DRIVE_OK, theme.DRIVE_OFF])
        if btn is None:
            return
        try:
            st = app().drive.status()
        except Exception:  # noqa: BLE001
            return
        btn.icon = (theme.DRIVE_OFF if not st["enabled"] else
                    theme.DRIVE_UP if st["pending"] or st["errors"] else theme.DRIVE_OK)

    def drive_quick(self):
        a = app()
        drive = a.drive
        if not drive.available:
            a.toast("El respaldo en Google Drive funciona en el teléfono (Android).")
            return
        if not drive.enabled:
            a.toast("Elija su cuenta de Google para respaldar en Drive")
            drive.connect(lambda ok, msg: Clock.schedule_once(lambda *_: (a.toast(msg), self.update_drive_icon())))
            return
        if getattr(self, "_spin_ev", None):
            a.toast("Subiendo a Google Drive…")
            return
        st = drive.status()
        if not st["pending"] and not st["errors"]:
            a.toast("Todo está respaldado en Google Drive ✓")
        drive.retry_errors()   # reintenta errores y sube lo pendiente (en segundo plano)
        self._start_spin()

    def _start_spin(self):
        import time as _t
        btn = self._action([theme.DRIVE_UP, theme.DRIVE_OK, theme.DRIVE_OFF] + theme.DRIVE_SPIN)
        if btn is None:
            return
        state = {"k": 0, "t0": _t.monotonic()}

        def tick(_dt):
            state["k"] = (state["k"] + 1) % len(theme.DRIVE_SPIN)
            btn.icon = theme.DRIVE_SPIN[state["k"]]
            drive = app().drive
            if _t.monotonic() - state["t0"] > 1.2 and not drive.running:
                self._spin_ev.cancel()
                self._spin_ev = None
                st = drive.status()
                btn.icon = theme.DRIVE_UP if st["pending"] or st["errors"] else theme.DRIVE_OK
                app().toast("Todo respaldado en Google Drive ✓" if btn.icon == theme.DRIVE_OK else
                            st.get("message") or "Quedan archivos por subir")

        self._spin_ev = Clock.schedule_interval(tick, .1)

    def update_banner(self):
        """Franja bajo la barra: en qué ensayo o predio se está trabajando."""
        ws = getattr(app(), "workspace", None)
        if ws:
            from workspaces import Workspaces
            self.ids.ws_banner.text = f"{Workspaces.title(ws)}  ·  {ws['code']}"
            self.ids.ws_banner.icon = "flask-outline" if ws["profile"] == "id" else "barn"


class StartIcon(ButtonBehavior, BoxLayout):
    """Botón de forma libre (ícono a línea sobre su burbuja) con su nombre debajo."""
    source = StringProperty()
    bubble = StringProperty("")
    glow = StringProperty("")
    glow_level = NumericProperty(.55)   # brillo que «respira» (Asistente IA)
    label = StringProperty()
    color = ColorProperty([0, 0, 0, 1])
    diameter = NumericProperty(dp(130))
    lift = NumericProperty(0)           # animación de entrada (desplazamiento hacia abajo)
    reveal = NumericProperty(1)         # animación de entrada (opacidad)


class StatusPill(Widget):
    text = StringProperty()
    color = ColorProperty([0, 0, 0, 1])


class GoogleButton(ButtonBehavior, FloatLayout):
    """Botón «Inicia sesión con Google» (blanco, con la G de colores)."""
    text = StringProperty("Inicia sesión con Google")


class StartScreen(MDScreen):
    """Lo primero que se ve al abrir la app: I+D, Predio, Asistente IA y la cuenta de Google."""
    _animated = False

    def on_pre_enter(self, *_):
        if not StartScreen._animated:      # animación de entrada: solo la primera vez
            StartScreen._animated = True
            self._prepare_intro()
        # La pantalla aparece al instante; los textos (consultas a la base) justo después.
        Clock.schedule_once(lambda *_: self.refresh(), 0)

    def on_enter(self, *_):
        delay = 0
        if getattr(self, "_intro_ready", False):
            self._intro_ready = False
            self._play_intro()
            delay = 1.0
        self._breath_ev = Clock.schedule_once(lambda *_: self._breathe(), delay)

    def on_leave(self, *_):
        if getattr(self, "_breath_ev", None):
            self._breath_ev.cancel()
        if getattr(self, "_breath", None):
            self._breath.cancel(self.ids.ic_ai)
            self._breath = None

    # ------------------------------------------------------------ animación
    def _prepare_intro(self):
        for w in (self.ids.ic_id, self.ids.ic_predio, self.ids.ic_ai):
            w.reveal, w.lift = 0, dp(46)
        self.ids.logo.opacity = 0
        self._intro_ready = True

    def _play_intro(self):
        from kivy.animation import Animation
        logo = self.ids.logo
        h = logo.height
        logo.height = h * .7
        (Animation(opacity=1, height=h * 1.06, d=.32, t="out_cubic")
         + Animation(height=h, d=.16, t="in_out_sine")).start(logo)
        for i, w in enumerate((self.ids.ic_id, self.ids.ic_predio, self.ids.ic_ai)):
            anim = Animation(d=.12 + .11 * i) + Animation(reveal=1, lift=0, d=.38, t="out_back")
            anim.start(w)

    def _breathe(self):
        from kivy.animation import Animation
        if getattr(self, "_breath", None):
            return
        # Tres «respiraciones» y queda quieto: una animación sin fin obliga a redibujar la
        # pantalla completa 60 veces por segundo y resta fluidez al resto.
        step = Animation(glow_level=1, d=1.4, t="in_out_sine") + Animation(glow_level=.5, d=1.4, t="in_out_sine")
        anim = step + step + step + Animation(glow_level=.8, d=.8)
        self._breath = anim
        anim.start(self.ids.ic_ai)

    # --------------------------------------------------------------- textos
    @staticmethod
    def _greeting() -> str:
        h = _dt.datetime.now().hour
        return "Buenos días" if 5 <= h < 12 else "Buenas tardes" if 12 <= h < 20 else "Buenas noches"

    def refresh(self):
        a = app()
        from workspaces import Workspaces
        name = (a.db.get_setting("user_name") or "").strip()
        self.ids.greeting.text = f"{self._greeting()}, {name}" if name else f"{self._greeting()} · toque para poner su nombre"
        ws = getattr(a, "workspace", None)
        try:
            units = a.db.list_varieties()
            done = a.db.query_one(
                "SELECT COUNT(*) AS n FROM observations o JOIN varieties v ON v.id=o.variety_id "
                "WHERE o.week_id=? AND o.bbch_code IS NOT NULL AND v.active=1", (a.week["id"],))["n"]
            what = {"predio": "sectores", "tratamientos": "parcelas"}.get(
                (ws or {}).get("kind") if (ws or {}).get("kind") == "tratamientos" else (ws or {}).get("profile"),
                "registros")
            self.ids.week_lbl.text = (f"Semana {ph.week_of_year(a.week)} · {done} de {len(units)} "
                                      f"{what} · {Workspaces.title(ws)}" if ws and units else
                                      (f"Último usado: {Workspaces.title(ws)} ({ws['code']})" if ws else ""))
            self.ids.week_bar.progress = done / len(units) if units else 0
        except Exception:  # noqa: BLE001 - nunca bloquear el inicio
            self.ids.week_lbl.text = ""
        self._pills()
        drive = a.drive
        st = drive.status()
        if st["enabled"]:
            self.ids.google_btn.text = "Cambiar cuenta de Google"
        else:
            self.ids.google_btn.text = "Inicia sesión con Google"

    def _pills(self):
        a = app()
        box = self.ids.pills
        box.clear_widgets()
        pills = []
        try:
            st = a.drive.status()
            if not st["enabled"]:
                pills.append(("Drive sin conectar", theme.MUTED))
            elif st["errors"]:
                pills.append((f"Drive: {st['errors']} con error", theme.WARN))
            elif st["pending"]:
                pills.append((f"Drive: {st['pending']} por subir", theme.WARN))
            else:
                pills.append(("Drive al día", theme.LEAF))
            last = a.db.query_one("SELECT MAX(observed_at) AS t FROM observations WHERE bbch_code IS NOT NULL")
            if last and last["t"]:
                days = (_dt.date.today() - _dt.date.fromisoformat(last["t"][:10])).days
                when = "hoy" if days <= 0 else "ayer" if days == 1 else f"hace {days} días"
                pills.append((f"Último registro: {when}", theme.BERRY))
            n_ai = sum(a.db.reference_counts().values())
            pills.append((f"IA: {n_ai} fotos", "#3A6BFF"))
        except Exception:  # noqa: BLE001
            pass
        for text, col in pills:
            pill = StatusPill(text=text, color=c(col))
            pill.ids.lbl.texture_update()
            pill.width = dp(26) + pill.ids.lbl.texture_size[0]
            box.add_widget(pill)
        box.width = sum(p.width for p in box.children) + dp(6) * max(0, len(box.children) - 1)

    def ask_name(self):
        from kivy.factory import Factory
        a = app()
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(0, dp(8), 0, 0))
        field = Factory.Field(hint_text="Su nombre (para el saludo)", text=a.db.get_setting("user_name") or "")
        form.add_widget(field)

        def ok(_f):
            a.db.set_setting("user_name", field.text.strip())
            self.refresh()

        form_dialog("¿Cómo se llama?", form, ok)

    def open_profile(self, profile: str):
        app().open_workspaces(profile)

    def open_ai(self):
        app().open_ai_lab()

    def connect_google(self):
        a = app()
        if not a.drive.available:
            a.toast("La cuenta de Google se conecta en el teléfono (Android).")
            return
        a.toast("Elija la cuenta de Google y acepte el permiso")

        def done(ok, msg):
            Clock.schedule_once(lambda *_: (a.toast(msg), self.refresh()))

        a.drive.connect(done)

    def continue_local(self):
        app().enter_home()


class SectorRow(GlassButton):
    variety_id = NumericProperty(0)
    badge = StringProperty()
    title = StringProperty()
    subtitle = StringProperty()
    done = BooleanProperty(False)
    screen = ObjectProperty(None, allownone=True)


def unit_dialog(on_saved=None):
    """Nuevo sector de predio: se guarda como «Equipo de riego n, sector m» con su variedad."""
    a = app()
    form = VarietyForm()
    form.ids.name.hint_text = "Variedad"
    form.ids.code.hint_text = "Código de la variedad (opcional, va en el nombre de las fotos)"

    def ok(f):
        try:
            a.db.save_unit(f.ids.name.text, f.ids.code.text.strip(),
                           f.sector or None, f.irrigation or None)
        except ValueError as exc:
            a.toast(str(exc))
            return False
        remember_variety(f.ids.name.text, f.ids.code.text.strip(), "predio")
        a.toast(f"Agregado: {ph.unit_name(f.sector, f.irrigation)} · {f.ids.name.text.strip()}")
        if on_saved:
            on_saved()
        a.refresh_home()

    form_dialog("Nuevo sector", form, ok, "AGREGAR")


class SectorsScreen(MDScreen):
    """Predio: sectores (variedad + sector + equipo de riego) a muestrear esta semana.
    Se crean aquí mismo (también siguen en Ajustes › Registros)."""

    def on_pre_enter(self, *_):
        self.refresh()

    def refresh(self):
        a = app()
        ws = a.workspace or {}
        self.ids.bar.title = ws.get("name", "Sectores")
        units = sorted(a.db.list_varieties(),
                       key=lambda v: (v["irrigation"] or 99, v["sector"] or 99, v["name"].lower()))
        box = self.ids.rows
        fast_clear(box)
        done = 0
        for v in units:
            obs = a.db.get_observation(v["id"], a.week["id"])
            code = obs["bbch_code"] if obs else None
            done += code is not None
            row = SectorRow(variety_id=v["id"], screen=self, done=code is not None,
                            badge=ph.location_tag(v) or "—", title=v["name"],
                            subtitle=f"{v.get('cultivar') or 'Sin variedad'} · " + (
                                f"BBCH {ph.code_str(code)} registrado" if code is not None
                                else "sin registro esta semana"))
            row.bind(on_release=lambda w: a.open_observation(w.variety_id, a.week["id"]))
            box.add_widget(row)
        self.ids.hint.text = (f"{ph.week_title(a.week)} · {done} de {len(units)} sectores registrados. "
                              "Toque un sector para registrarlo." if units else
                              "Aún no hay sectores: cree el primero con «+ Nuevo sector» "
                              "(variedad, sector y equipo de riego).")

    def add_sector(self):
        unit_dialog(on_saved=self.refresh)

    def edit_sector(self, variety_id: int):
        app().open_variety(variety_id)


class WorkspaceBanner(GlassButton):
    text = StringProperty()
    icon = StringProperty("flask-outline")


class WorkspaceRow(GlassButton):
    title = StringProperty()
    subtitle = StringProperty()
    code = StringProperty()
    profile = StringProperty("id")
    current = BooleanProperty(False)
    ws = ObjectProperty(None, allownone=True)
    screen = ObjectProperty()


class WorkspacesScreen(MDScreen):
    """Menú inicial: perfil I+D (ensayos) o Predio, y la lista para elegir dónde trabajar."""
    profile = StringProperty("id")
    show_archived = BooleanProperty(False)

    menu_mode = BooleanProperty(False)   # abierto desde el inicio: solo I+D o solo Predio

    def on_pre_enter(self, *_):
        ws = getattr(app(), "workspace", None)
        if not self.menu_mode and ws:
            self.profile = ws["profile"]
        self.show_archived = False
        self.refresh()

    def set_profile(self, profile: str):
        self.profile = profile
        self.show_archived = False
        self.refresh()

    def toggle_archived(self):
        self.show_archived = not self.show_archived
        self.refresh()

    def _stats(self, ws) -> str:
        from workspaces import KINDS
        a = app()
        parts = [KINDS.get(ws["kind"], ws["kind"])]
        try:
            db = a.open_db(ws)
            n = db.query_one("SELECT COUNT(*) AS n FROM varieties WHERE active=1")["n"]
            one, many = {"tratamientos": ("parcela", "parcelas"), "predio": ("sector", "sectores")
                         }.get(ws["kind"], ("variedad", "variedades"))
            parts.append(f"{n} {one if n == 1 else many}")
            last = db.query_one("SELECT w.start_date FROM observations o JOIN sampling_weeks w "
                                "ON w.id = o.week_id WHERE o.bbch_code IS NOT NULL "
                                "ORDER BY w.start_date DESC LIMIT 1")
            if last:
                parts.append(f"último registro: {ph.week_title(last['start_date'])}")
        except Exception:  # noqa: BLE001 - nunca bloquear el menú por una base dañada
            pass
        return " · ".join(parts)

    def refresh(self):
        a = app()
        box = self.ids.ws_list
        fast_clear(box)
        items = a.workspaces.list(self.profile, archived=self.show_archived)
        cur = getattr(a, "workspace", None) or {}
        from workspaces import KINDS
        rows = []
        for ws in items:
            row = WorkspaceRow(title=ws["name"], code=ws["code"], profile=ws["profile"],
                               subtitle=KINDS.get(ws["kind"], ws["kind"]),
                               current=ws["id"] == cur.get("id"), ws=ws, screen=self)
            row.bind(on_release=lambda w: self.choose(w.ws))
            box.add_widget(row)
            rows.append(row)
        # Las estadísticas abren la base de cada ensayo: una por cuadro, ya con la lista a la vista.
        def fill(i=0):
            if i < len(rows) and rows[i].parent is box:
                rows[i].subtitle = self._stats(rows[i].ws)
                Clock.schedule_once(lambda *_: fill(i + 1), 0)
        Clock.schedule_once(lambda *_: fill(), 0.05)
        if not items:
            box.add_widget(MDLabel(text="No hay archivados." if self.show_archived else "Aún no hay nada aquí.",
                                   font_style="Caption", adaptive_height=True, halign="center",
                                   theme_text_color="Custom", text_color=c(theme.MUTED)))
        is_id = self.profile == "id"
        self.ids.hint.text = ("Ensayos de investigación y desarrollo: cada uno con sus variedades, "
                              "semanas, fotos, mediciones e informes por separado." if is_id else
                              "Predios comerciales: fenología por variedad, sector y equipo de riego.")
        if self.show_archived:
            self.ids.hint.text = "Archivados: no aparecen en la lista, pero sus datos se conservan."
        self.ids.add_btn.text = "+ Nuevo ensayo" if is_id else "+ Nuevo predio"
        self.ids.add_btn.opacity, self.ids.add_btn.disabled = (0, True) if self.show_archived else (1, False)
        self.ids.archived_btn.text = "Volver a la lista" if self.show_archived else "Ver archivados"

    def choose(self, ws):
        if ws["archived"]:
            app().toast("Está archivado: desarchívelo con ⋮ para trabajar en él.")
            return
        # Predio: primero la lista de sectores a muestrear; ensayo: el muestreo semanal.
        app().open_workspace(ws["id"], target="sectors" if ws["profile"] == "predio" else "home")

    def item_menu(self, ws):
        a = app()

        def rename():
            from kivy.factory import Factory
            form = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(0, dp(8), 0, 0))
            form.add_widget(Factory.Field(hint_text="Nombre", text=ws["name"]))

            def ok(f):
                try:
                    a.workspaces.rename(ws["id"], f.children[0].text)
                except ValueError as exc:
                    a.toast(str(exc))
                    return False
                a.workspace_renamed(ws["id"])
                self.refresh()

            form_dialog("Cambiar nombre", form, ok)

        def archive(flag):
            if flag and ws["id"] == (a.workspace or {}).get("id"):
                a.toast("Primero cambie a otro ensayo o predio.")
                return
            a.workspaces.set_archived(ws["id"], flag)
            self.refresh()

        items = [("Cambiar nombre", f"Código {ws['code']} (no cambia)", rename)]
        if ws["archived"]:
            items.append(("Desarchivar", "Vuelve a la lista", lambda: archive(False)))
        else:
            items.append(("Archivar", "Ocultar de la lista; los datos se conservan", lambda: archive(True)))
        pick_dialog(f"{ws['name']} · {ws['code']}", items)

    def new_item(self):
        a = app()
        from kivy.factory import Factory
        from workspaces import suggest_code
        is_id = self.profile == "id"
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                           padding=(0, dp(12), 0, 0))
        name = Factory.Field(hint_text="Nombre del ensayo" if is_id else "Nombre del predio")
        code = Factory.Field(hint_text="Código (2 a 4 letras, va en el nombre de las fotos)")
        state = {"auto": True}

        def on_name(_w, text):
            if state["auto"]:
                code.text = suggest_code(text, a.workspaces.codes())

        def on_code(_w, text):
            up = text.upper()[:4]
            if up != text:
                code.text = up
            if code.focus:
                state["auto"] = False

        name.bind(text=on_name)
        code.bind(text=on_code)
        form.add_widget(name)
        form.add_widget(code)
        state["kind"] = "variedades" if is_id else "predio"
        if is_id:
            seg = MDBoxLayout(adaptive_height=True, spacing=dp(8))
            b_var = SegButton(text="Variedades", selected=True)
            b_trt = SegButton(text="Tratamientos × rep.")
            seg.add_widget(b_var)
            seg.add_widget(b_trt)
            trt = MDBoxLayout(adaptive_height=True, spacing=dp(8))
            n_t = Factory.Field(hint_text="N.º tratamientos", input_filter="int", text="4")
            n_r = Factory.Field(hint_text="N.º repeticiones", input_filter="int", text="4")
            trt.add_widget(n_t)
            trt.add_widget(n_r)
            # Alto fijo: el diálogo no cambia de tamaño al elegir el tipo (KivyMD no lo reajusta).
            info = MDLabel(font_style="Caption", size_hint_y=None, height=dp(48), valign="top",
                           theme_text_color="Custom", text_color=c(theme.MUTED))
            info.bind(width=lambda w, v: setattr(w, "text_size", (v, dp(48))))
            form.add_widget(MDLabel(text="Tipo de ensayo", font_style="Caption", adaptive_height=True,
                                    theme_text_color="Custom", text_color=c(theme.MUTED)))
            form.add_widget(seg)
            form.add_widget(trt)
            form.add_widget(info)

            def set_kind(kind):
                state["kind"] = kind
                b_var.selected, b_trt.selected = kind == "variedades", kind == "tratamientos"
                trt.disabled = kind != "tratamientos"
                trt.opacity = 1 if kind == "tratamientos" else .35
                update_info()

            def update_info(*_):
                if state["kind"] == "variedades":
                    info.text = ("Ensayo de variedades: cada variedad se registra por separado y los "
                                 "informes comparan variedades.")
                    return
                try:
                    t, r = int(n_t.text or 0), int(n_r.text or 0)
                except ValueError:
                    t = r = 0
                info.text = (f"Se crearán {t * r} parcelas: T1R1 … T{t}R{r}. Los informes promedian las "
                             "repeticiones y comparan los tratamientos (ANOVA)." if t and r else
                             "Indique cuántos tratamientos y repeticiones tiene el ensayo.")

            b_var.bind(on_release=lambda *_: set_kind("variedades"))
            b_trt.bind(on_release=lambda *_: set_kind("tratamientos"))
            n_t.bind(text=update_info)
            n_r.bind(text=update_info)
            set_kind("variedades")

        def ok(_f):
            kind = state["kind"]
            size = None
            if kind == "tratamientos":
                try:
                    size = int(n_t.text or 0), int(n_r.text or 0)
                except ValueError:
                    size = (0, 0)
                from database import Database
                if not (1 <= size[0] <= Database.MAX_TREATMENTS and 1 <= size[1] <= Database.MAX_REPS):
                    a.toast(f"Tratamientos: 1 a {Database.MAX_TREATMENTS} · repeticiones: 1 a {Database.MAX_REPS}.")
                    return False
            try:
                ws = a.workspaces.create(self.profile, kind, name.text, code.text)
            except ValueError as exc:
                a.toast(str(exc))
                return False
            if size:
                db = a.open_db(ws)
                db.setup_trial(*size)
            a.open_workspace(ws["id"], target="sectors" if ws["profile"] == "predio" else "home")
            if size:
                a.toast(f"«{ws['name']}» creado con {size[0] * size[1]} parcelas. Ponga nombre a los "
                        "tratamientos en Ajustes › Parcelas")
            else:
                a.toast(f"«{ws['name']}» creado: agregue sus "
                        + ("variedades en Ajustes › Registros" if is_id
                           else "sectores con «+ Nuevo sector»"))

        form_dialog("Nuevo ensayo" if is_id else "Nuevo predio", form, ok, "CREAR")


class SettingsScreen(MDScreen):
    """Ajustes con dos pestañas: «General» y «Registros»."""

    def on_pre_enter(self, *_):
        self.ids.var_seg.text = "Registros"
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
        self.ids.week_kicker.text = ph.week_title(week).upper()
        self.ids.week_title.text = week["label"]
        self.ids.week_range.text = (f"{start.day} {ph.MESES[start.month - 1][:3]} – "
                                    f"{end.day} {ph.MESES[end.month - 1][:3]} {end.year}")
        rows = a.db.week_overview(week["id"])
        box = self.ids.rows
        # Las filas se reutilizan: crear/destruir widgets KivyMD es lo más costoso.
        cache = getattr(self, "_rows", {})
        if not rows:
            fast_clear(box)
            self._rows = {}
            is_predio = (getattr(a, "workspace", None) or {}).get("profile") == "predio"
            box.add_widget(MDLabel(
                text=("Aún no hay sectores en este predio. Agréguelos en Ajustes › Registros "
                      "(variedad, sector y equipo de riego)." if is_predio else
                      "Aún no hay variedades en este ensayo. Agréguelas en Ajustes › Registros."),
                font_style="Body2", adaptive_height=True, halign="center",
                theme_text_color="Custom", text_color=c(theme.MUTED)))
        elif list(cache) != [r["variety"]["id"] for r in rows]:
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
            complete += code is not None and n >= 1   # basta una foto (canopia o detalle)
            bg, fg = bbch_tag_colors(code)
            row = cache[v["id"]]
            row.title = v["name"]
            row.subtitle = (obs["bbch_label"] or ph.bbch_label(code, names)) if code is not None \
                else ("Foto sin estado asignado" if n else "Pendiente de registro")
            if v.get("cultivar"):   # predio: «Equipo de riego n, sector m» + variedad
                row.subtitle = f"{v['cultivar']} · {row.subtitle}"
            row.photos, row.photo_tag = n, ("SIN FOTOS" if not n else f"{n} FOTO" + ("S" if n > 1 else ""))
            row.code_tag = f"BBCH {ph.code_str(code)}" if code is not None else "BBCH —"
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
        # Semana no muestreada: casilla marcada, aviso y lista atenuada.
        skipped = bool(week.get("skipped"))
        self._loading_skip = True
        self.ids.skip_box.active = skipped
        self._loading_skip = False
        if skipped:
            self.ids.week_kicker.text += " · NO MUESTREADA"
            self.ids.progress_text.text = "omitida"
        box.opacity = .45 if skipped else 1
        # Semana recién completada (pasó de incompleta a completa mientras se registraba):
        # serpentinas y «SEMANA x · COMPLETADA». Abrir una semana ya completa no lo repite.
        done = bool(rows) and complete == len(rows) and not skipped
        state = self.__dict__.setdefault("_week_done", {})
        key = (getattr(a.db, "code", ""), week["id"])
        if done and state.get(key) is False:
            from ui import celebrate
            Clock.schedule_once(lambda *_: celebrate.week_completed(
                str(ph.iso_week(_dt.date.fromisoformat(week["start_date"]))[0])), .25)
        state[key] = done
        n_meas = sum(1 for m in a.db.list_measures(week["id"]) if m["n"])
        self.ids.measures_btn.text = ("Mediciones de la semana" if not n_meas
                                      else f"Mediciones de la semana · {n_meas} con datos")

    def set_skipped(self, active: bool):
        """Marca/desmarca la semana como «no muestreada»: se excluye de los informes de
        período, por variedad y matriz (los datos, si los hubiera, no se borran)."""
        if getattr(self, "_loading_skip", False):
            return
        a = app()
        if bool(a.week.get("skipped")) == bool(active):
            return
        a.db.set_week_skipped(a.week["id"], active)
        a.week = a.db.get_week(a.week["id"])
        a.toast(f"{ph.week_title(a.week)} marcada como no muestreada: no aparecerá en los informes"
                if active else f"{ph.week_title(a.week)} vuelve a incluirse en los informes")
        self.refresh()

    def open_measures(self):
        open_measures_menu()

    def shift_week(self, delta: int):
        a = app()
        n = a.week["week_number"] + delta
        if n < 1:
            a.toast("Es la primera semana de muestreo de la temporada.")
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

    def pick_catalog(self):
        def chosen(name, code):
            self.ids.name.text = name
            if code:
                self.ids.code.text = code
        pick_catalog(chosen)


class VarietiesTab(MDScreen):
    def refresh(self):
        a = app()
        season = a.season
        trial = a.db.is_trial
        self.ids.season_caption.text = (f"{ph.season_title(season)} · toque una "
                                        f"{'parcela' if trial else 'sector' if a.db.is_predio else 'variedad'} "
                                        f"para editar sus parámetros biométricos")
        self._trial_card(trial)
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
        trts = {t["num"]: t for t in a.db.list_treatments()} if trial else {}
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
            if v.get("cultivar"):
                parts.insert(0, v["cultivar"])
            if v.get("treatment"):
                t = trts.get(v["treatment"])
                parts.insert(0, f"{t['label'] if t else 'T' + str(v['treatment'])} · Repetición {v['rep']}")
            n_custom = len(a.db.list_custom_fields(v["id"], season))
            if n_custom:
                parts.append(f"{n_custom} campo(s) extra")
            row = cache[v["id"]]
            row.title, row.code = v["name"], v["code"] or ""
            row.summary = " · ".join(parts) or "Sin parámetros cargados"

    def open_variety(self, variety_id: int):
        app().open_variety(variety_id)

    # ------------------------------------------- tratamientos × repeticiones
    def _catalog_card(self, box):
        import catalog
        from kivy.factory import Factory
        a = app()
        profile = current_profile()
        names = [e["name"] for e in catalog.entries(a.workspaces.shared, profile)]
        card = Factory.PaperCard()
        card.add_widget(Factory.SectionLabel(
            text=f"CATÁLOGO DE VARIEDADES · {'PREDIO' if profile == 'predio' else 'I+D'}"))
        card.add_widget(Factory.Muted(
            text=(f"{len(names)} variedades: " + ", ".join(names[:8]) + ("…" if len(names) > 8 else ""))
            if names else "Vacío: agregue las variedades que usa para elegirlas de una lista al crear "
                          "registros, sectores o tratamientos."))
        b = Factory.GhostButton(icon="format-list-bulleted", text="Ver y agregar variedades")
        b.bind(on_release=lambda *_: manage_catalog(on_change=self.refresh))
        card.add_widget(b)
        box.add_widget(card)
        box.add_widget(Widget(size_hint_y=None, height=dp(8)))

    def _trial_card(self, trial: bool):
        from kivy.factory import Factory
        box = self.ids.trial_box
        box.clear_widgets()
        self._catalog_card(box)
        if not trial:
            return
        db = app().db
        nt, nr = db.trial_size()
        card = Factory.PaperCard()
        card.add_widget(Factory.SectionLabel(text="TRATAMIENTOS × REPETICIONES"))
        card.add_widget(Factory.Body(text=f"{nt} tratamientos × {nr} repeticiones = {nt * nr} parcelas"
                                     if nt else "Aún sin definir"))
        names = [t["label"] for t in db.list_treatments() if t["name"]]
        if names:
            card.add_widget(Factory.Muted(text=" · ".join(names)))
        row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
        b1 = Factory.GhostButton(icon="grid", text="Cambiar número")
        b1.bind(on_release=lambda *_: self.trial_size_dialog())
        b2 = Factory.GhostButton(icon="tag-text-outline", text="Nombres")
        b2.bind(on_release=lambda *_: self.treatments_dialog())
        row.add_widget(b1)
        row.add_widget(b2)
        card.add_widget(row)
        tv = db.trial_cultivar
        b3 = Factory.GhostButton(icon="sprout-outline",
                                 text=f"Variedad del ensayo: {tv}" if tv else "Variedad del ensayo: (ninguna)")
        b3.bind(on_release=lambda *_: pick_catalog(self._set_trial_variety, "id",
                                                   allow_none="— Sin variedad para todo el ensayo"))
        card.add_widget(b3)
        box.add_widget(card)
        box.add_widget(Widget(size_hint_y=None, height=dp(8)))

    def _set_trial_variety(self, name, _code):
        app().db.set_setting("trial_cultivar", name)
        app().toast(f"Variedad del ensayo: {name}" if name else "Ensayo sin variedad común")
        self.refresh()

    def trial_size_dialog(self):
        from kivy.factory import Factory
        a = app()
        nt, nr = a.db.trial_size()
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                           padding=(0, dp(12), 0, 0))
        row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
        f_t = Factory.Field(hint_text="N.º tratamientos", input_filter="int", text=str(nt or 4))
        f_r = Factory.Field(hint_text="N.º repeticiones", input_filter="int", text=str(nr or 4))
        row.add_widget(f_t)
        row.add_widget(f_r)
        form.add_widget(row)
        form.add_widget(MDLabel(
            text="Si reduce el número, las parcelas que sobran se archivan con sus registros "
                 "(no se borra nada); si lo vuelve a aumentar, reaparecen.",
            font_style="Caption", adaptive_height=True, theme_text_color="Custom", text_color=c(theme.MUTED)))

        def ok(_f):
            try:
                res = a.db.setup_trial(int(f_t.text or 0), int(f_r.text or 0))
            except ValueError as exc:
                a.toast(str(exc))
                return False
            msg = [f"{k} {v}" for k, v in (("nuevas", res["added"]), ("restauradas", res["restored"]),
                                           ("archivadas", res["archived"])) if v]
            a.toast("Parcelas: " + (", ".join(msg) if msg else "sin cambios"))
            self.refresh()
            a.refresh_home()

        form_dialog("Tratamientos × repeticiones", form, ok)

    def treatments_dialog(self):
        a = app()

        def edit(t):
            from kivy.factory import Factory
            form = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                               padding=(0, dp(12), 0, 0))
            f_n = Factory.Field(hint_text=f"Nombre de T{t['num']} (ej.: Testigo)", text=t["name"] or "")
            f_d = Factory.Field(hint_text="Descripción (dosis, producto, momento…)",
                                text=t["description"] or "", multiline=True)
            form.add_widget(f_n)
            form.add_widget(f_d)
            chosen = {"v": (t.get("cultivar") or "").strip()}
            default = a.db.trial_cultivar
            vb = Factory.GhostButton(icon="sprout-outline")

            def label():
                vb.text = (f"Variedad: {chosen['v']}" if chosen["v"] else
                           f"Variedad: la del ensayo ({default})" if default else "Variedad: (ninguna)")

            def pick(name, _code):
                chosen["v"] = name
                label()

            vb.bind(on_release=lambda *_: pick_catalog(pick, "id", allow_none="— La del ensayo / ninguna"))
            label()
            form.add_widget(vb)

            def ok(_f):
                a.db.update_treatment(t["num"], f_n.text, f_d.text, chosen["v"])
                self.refresh()

            form_dialog(f"Tratamiento T{t['num']}", form, ok)

        pick_dialog("Nombres de los tratamientos",
                    [(t["label"], " · ".join(x for x in (t["variety"], t["description"]) if x)
                      or f"Parcelas: {', '.join(p['name'] for p in t['parcels'])}",
                      lambda t=t: edit(t)) for t in a.db.list_treatments()])

    def add_dialog(self):
        if app().db.is_trial:
            self.trial_size_dialog()
            return
        if app().db.is_predio:
            unit_dialog(on_saved=self.refresh)
            return
        def ok(form):
            try:
                app().db.add_variety(form.ids.name.text, code=form.ids.code.text.strip(),
                                     sector=form.sector or None, irrigation=form.irrigation or None)
            except ValueError as exc:
                form.ids.name.error = True
                form.ids.name.helper_text = str(exc)
                form.ids.name.helper_text_mode = "on_error"
                return False
            remember_variety(form.ids.name.text, form.ids.code.text.strip())
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


class AttachmentRow(MDBoxLayout):
    caption = StringProperty()
    date = StringProperty()
    item = ObjectProperty(None, allownone=True)
    screen = ObjectProperty()


class CaptionForm(MDBoxLayout):
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
        self.predio = a.db.is_predio
        # Predio: se edita la variedad; el nombre («Equipo de riego n, sector m») sale solo.
        self.ids.name.hint_text = "Variedad" if self.predio else "Nombre de la variedad"
        self.ids.name.text = (v.get("cultivar") or "") if self.predio else v["name"]
        self.ids.code.text = v["code"] or ""
        self.ids.notes.text = v["notes"] or ""
        self.sector, self.irrigation = v["sector"] or 0, v["irrigation"] or 0
        self._sync_location_buttons()
        self.ids.metrics_title.text = f"PARÁMETROS BIOMÉTRICOS · {ph.season_title(a.season).upper()}"
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
        if getattr(self, "predio", False):
            v["cultivar"] = self.ids.name.text
        a = app()
        g = ph.photo_basename(v, a.week["start_date"], "canopy", trial=a.db.code)
        d = ph.photo_basename(v, a.week["start_date"], "detail", trial=a.db.code)
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
            if getattr(self, "predio", False):
                a.db.save_unit(name, self.ids.code.text.strip(), self.sector or None,
                               self.irrigation or None, self.variety_id)
                a.db.update_variety(self.variety_id, notes=self.ids.notes.text)
                name = a.db.get_variety(self.variety_id)["name"]
            else:
                a.db.update_variety(self.variety_id, name=name, code=self.ids.code.text.strip(),
                                    notes=self.ids.notes.text, sector=self.sector or None,
                                    irrigation=self.irrigation or None)
        except ValueError as exc:
            a.toast(str(exc))
            return
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
        r = a.configured_reports()
        a.run_report(lambda: r.variety(self.variety_id, a.season, "pdf"))


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
        self.ids.week_caption.text = f"{ph.week_title(self.week)} · {self.week['label']}"
        self._refresh_slot("canopy")
        self._refresh_slot("detail")
        self.load_attachments()
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
                text=f"BBCH {ph.code_str(code)} · {p:.0%}", theme_text_color="Custom",
                text_color=c(theme.LEAF_DARK), line_color=c(theme.LINE),
                on_release=lambda b, code=code: self._set_bbch(code)))

    def _set_bbch(self, code: int):
        self.ids.bbch.text = ph.bbch_label(code, app().db.bbch_names())

    # ------------------------------------------------------ fotos adjuntas
    def load_attachments(self):
        a = app()
        box = self.ids.attachments
        fast_clear(box)
        items = a.db.list_attachments(self.variety["id"], week_id=self.week["id"])
        for f in items:
            row = AttachmentRow(caption=f["caption"] or "", item=f, screen=self,
                                date=ph.format_date_es(_dt.date.fromisoformat(f["captured_at"][:10])))
            row.ids.thumb_box.add_widget(thumb_widget(f["path"], 160))
            box.add_widget(row)
        if not items:
            box.add_widget(MDLabel(text="Sin fotos adjuntas.", font_style="Caption",
                                   adaptive_height=True, theme_text_color="Custom",
                                   text_color=c(theme.MUTED)))

    def attach(self, source: str):
        """Fotos adjuntas a ESTE registro semanal (tomar o elegir de la galería) con descripción."""
        a = app()
        variety, week = self.variety, self.week
        base = ph.photo_basename(variety, week["start_date"], "attachment", trial=a.db.code)

        def done(result, origin):
            paths = [p for p in (result if isinstance(result, list) else [result]) if p]
            if not paths:
                if origin == "error":
                    a.toast("No se pudo obtener la foto.")
                return
            a.toast("Guardando foto…" if len(paths) == 1 else f"Guardando {len(paths)} fotos…")

            def work():
                ids, error = [], None
                try:
                    dest_dir = data_subdir("photos", f"T{week['season']}", f"S{week['week_number']:02d}")
                    for tmp in paths:
                        path = store_photo(tmp, dest_dir, base, exact=True)
                        ids.append(a.db.add_attachment(
                            variety["id"], week["season"], path, week_id=week["id"],
                            source="camera" if origin == "camera" else "gallery"))
                        a.thumb(path, 160)
                        a.backup_extra(path, week["start_date"], "Adjuntas")
                except Exception as exc:  # noqa: BLE001
                    error = exc
                Clock.schedule_once(lambda *_: stored(ids, error))

            def stored(ids, error):
                if self.week["id"] != week["id"] or self.variety["id"] != variety["id"]:
                    return
                self.load_attachments()
                if error:
                    a.toast(f"No se pudo guardar la foto: {error}")
                elif len(ids) == 1:
                    self.caption_dialog(a.db.query_one(
                        "SELECT * FROM variety_attachments WHERE id=?", (ids[0],)))

            a.workers.submit(work)

        if source == "camera":
            a.media.take_photo(done, base, exact=True)
        else:
            a.media.pick_images(done)

    def caption_dialog(self, item: dict | None):
        if not item:
            return
        a = app()
        form = CaptionForm()
        form.ids.caption.text = item["caption"] or ""

        def ok(f):
            a.db.set_attachment_caption(item["id"], f.ids.caption.text)
            self.load_attachments()

        form_dialog("Descripción de la foto", form, ok)

    def delete_attachment(self, item: dict):
        def go():
            app().db.delete_attachment(item["id"])
            self.load_attachments()

        confirm("Quitar foto adjunta", "La foto deja de aparecer en el registro y en los informes "
                "(el archivo se conserva en el teléfono).", [("Quitar", go)])

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
                    base = ph.photo_basename(self.variety, self.week["start_date"], kind, trial=a.db.code)
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
            # Mismo nombre que en la app y en Drive: 20260928-C11G, 20260928-C11G-2…
            seq = len(a.db.list_photos(self.obs["id"], kind)) + 1
            hint = ph.photo_basename(self.variety, self.week["start_date"], kind, seq, trial=a.db.code)
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
        bbch_dialog("Escala BBCH · frambueso", app().db, self._set_bbch,
                    current=ph.parse_bbch_code(self.ids.bbch.text or ""))

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
        if code is not None and a.db.get_setting("ai_auto_learn", True):
            # Aprendizaje continuo: cada foto de detalle del registro (con su estado
            # confirmado) se suma a la memoria de la IA, en segundo plano.
            photos = [p for p in a.db.list_photos(self.obs["id"], "detail") if os.path.exists(p["path"])]

            def learn():
                for p in photos:
                    a.classifier.add_reference(p["path"], code, photo_id=p["id"])

            if photos:
                a.workers.submit(learn)
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
                "calendar-week", f"{ph.week_title(self.sel_week)} · {self.sel_week['label']}",
                lambda b: self._pick_week(b, "week")))
        elif self.kind == "period":
            month = (f"{ph.MESES[self.sel_month[1] - 1].capitalize()} {self.sel_month[0]}"
                     if self.sel_month else "Elegir mes")
            box.add_widget(self._param_button("calendar-month", month, self._pick_month))
            row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
            row.add_widget(self._param_button("ray-start", f"Desde {ph.week_short(self.sel_from)}",
                                              lambda b: self._pick_week(b, "from")))
            row.add_widget(self._param_button("ray-end", f"Hasta {ph.week_short(self.sel_to)}",
                                              lambda b: self._pick_week(b, "to")))
            box.add_widget(row)
        elif self.kind == "variety":
            name = self.sel_variety["name"] if self.sel_variety else "Sin variedades"
            box.add_widget(self._param_button("fruit-cherries", name, self._pick_variety))
        else:
            box.add_widget(MDLabel(text=f"Toda la temporada · {ph.season_title(app().season)}",
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

        pick_dialog("Semana de muestreo", [(ph.week_title(w), w["label"], lambda w=w: choose(w))
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
            n = sm["missing_photos"]   # registros sin ninguna foto
            lack.append(f"{n} registro" + ("s" if n != 1 else "") + " sin fotos")
        if sm["missing_bbch"]:
            lack.append(f"{sm['missing_bbch']} estado" + ("s" if sm["missing_bbch"] != 1 else ""))
        ids.lack_text.text = ("Faltan " + " y ".join(lack)) if lack else "Completo"
        ids.lack_text.text_color = c(theme.BERRY) if lack else c(theme.LEAF)
        mb = sm["est_kb"] / 1024
        ids.size_text.text = f"≈ {mb:.1f} MB" if mb >= 1 else f"≈ {sm['est_kb']} KB"

    # ----------------------------------------------------------- vista previa
    def open_preview(self):
        a = app()
        r = a.configured_reports()
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
    tab = ObjectProperty(None, allownone=True)


class ReportGroup(GlassButton):
    """Encabezado desplegable de un grupo de informes (semanales, fichas…)."""
    title = StringProperty()
    count = StringProperty()
    key = StringProperty()
    expanded = BooleanProperty(False)
    paths = ListProperty()
    tab = ObjectProperty(None, allownone=True)


# Grupos de la lista de informes: (prefijo del archivo, título)
REPORT_GROUPS = [("semanal", "Reportes semanales"), ("periodo", "Evolución mensual / rango"),
                 ("variedad", "Fichas por variedad"), ("matriz", "Matriz comparativa"),
                 ("tratamientos", "Comparación de tratamientos"), ("", "Mediciones y otros")]


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
        self._photo_mode_label()
        weeks = a.db.list_weeks(a.season)
        if not weeks:
            weeks = [a.db.current_week()]
        if self.sel_week is None or self.sel_week["season"] != a.season:
            self.sel_week = a.week
            self.sel_from, self.sel_to = weeks[0], a.week
        if self.sel_variety is None or not a.db.get_variety(self.sel_variety["id"]):
            vs = a.db.list_varieties()
            self.sel_variety = vs[0] if vs else None
        trial = a.db.is_trial
        card = self.ids.trial_card
        if trial and card.parent is None:
            self._card_parent.add_widget(card, index=self._card_index)
        elif not trial and card.parent is not None:
            self._card_parent, self._card_index = card.parent, card.parent.children.index(card)
            card.parent.remove_widget(card)
        self.ids.variety_title.text = ("3 · FICHA COMPLETA POR PARCELA" if trial
                                       else "3 · FICHA COMPLETA POR VARIEDAD")
        self._labels()
        self.list_recent()

    def _labels(self):
        ids = self.ids
        ids.week_btn.text = f"{ph.week_title(self.sel_week)} · {self.sel_week['label']}"
        ids.from_btn.text = f"Desde {ph.week_short(self.sel_from)}"
        ids.to_btn.text = f"Hasta {ph.week_short(self.sel_to)}"
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

        pick_dialog("Semana de muestreo", [(ph.week_title(w), w["label"], lambda w=w: choose(w))
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

    def pick_photo_mode(self):
        a = app()

        def choose(mode):
            a.db.set_setting("report_photo_mode", mode)
            self._photo_mode_label()

        from reporter import ReportGenerator
        hints = {"both": "La foto general y la de detalle de cada variedad",
                 "detail": "Solo la foto de detalle representativa",
                 "canopy": "Solo la foto general (canopia) representativa"}
        pick_dialog("Fotos en los informes", [(label, hints[mode], lambda m=mode: choose(m))
                                              for mode, label in ReportGenerator.PHOTO_MODES.items()])

    def _photo_mode_label(self):
        from reporter import ReportGenerator
        mode = app().db.get_setting("report_photo_mode", "both")
        self.ids.photo_mode_btn.text = f"Fotos: {ReportGenerator.PHOTO_MODES.get(mode, mode)}"

    def generate(self, kind: str, package: str = "pdf"):
        a = app()
        r = a.configured_reports()
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
        elif kind == "treatments":
            job = lambda: r.treatments(a.season, package)  # noqa: E731
        else:
            job = lambda: r.matrix(a.season, package=package)  # noqa: E731
        a.run_report(job, on_done=lambda _res: self.list_recent())

    def list_recent(self):
        box = self.ids.recent
        fast_clear(box)
        a = app()
        folder = a.reports.out_dir
        code = a.db.code

        def mine(name: str) -> bool:   # solo los informes de este ensayo o predio
            if not code:
                return True
            if f"_{code}_" in name:
                return True
            # Informes de versiones anteriores (sin código): eran de «Nuevas variedades».
            return code == "NV" and not re.search(r"_[A-Z0-9]{2,4}_\d{4}", name)

        files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                        if f.endswith((".pdf", ".html", ".zip", ".xlsx")) and mine(f)),
                       key=os.path.getmtime, reverse=True)
        groups = {key: [] for key, _t in REPORT_GROUPS}
        for p in files:
            name = os.path.basename(p)
            key = next((k for k, _t in REPORT_GROUPS if k and name.startswith(k + "_")), "")
            groups[key].append(p)
        opened = getattr(self, "_opened", set())
        self._opened = opened
        trial = a.db.is_trial
        for key, title in REPORT_GROUPS:
            items = groups[key]
            if not items:
                continue
            if key == "variedad" and trial:
                title = "Fichas por parcela"
            head = ReportGroup(title=title, key=key, paths=items, tab=self, expanded=key in opened,
                               count=f"{len(items)} {'informe' if len(items) == 1 else 'informes'}")
            head.bind(on_release=self._toggle_group)
            box.add_widget(head)
            if key not in opened:
                continue
            for p in items:
                ts = _dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d-%m-%Y %H:%M")
                box.add_widget(ReportRow(title=os.path.basename(p), path=p, tab=self,
                                         meta=f"{ts} · {max(1, os.path.getsize(p) // 1024)} KB"))
        if not files:
            box.add_widget(MDLabel(text="Aún no se han generado informes.", font_style="Caption",
                                   adaptive_height=True, theme_text_color="Custom",
                                   text_color=c(theme.MUTED)))

    def _toggle_group(self, head):
        if head.key in self._opened:
            self._opened.discard(head.key)
        else:
            self._opened.add(head.key)
        self.list_recent()

    def delete_reports(self, paths: list, group: str | None = None):
        """Borra informes del teléfono (con confirmación). Las copias enviadas o subidas a
        Drive no se tocan."""
        a = app()
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        what = (f"«{os.path.basename(paths[0])}»" if len(paths) == 1 and not group
                else f"los {len(paths)} informes de «{group}»" if group else f"{len(paths)} informes")

        def go():
            n = 0
            for p in paths:
                try:
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
            a.db.log("delete", "report", None, ", ".join(os.path.basename(p) for p in paths)[:500])
            a.toast(f"{n} {'informe eliminado' if n == 1 else 'informes eliminados'}")
            self.list_recent()

        confirm("Eliminar informes",
                f"Se eliminará {what} de este teléfono. Las copias que ya envió o subió a "
                "Google Drive no se borran. Siempre puede volver a generarlos.",
                [("Eliminar", go)])


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
        self.ids.season_text.text = (f"{ph.season_title(a.season)} · semana en curso: "
                                     f"{ph.week_title(a.week)} ({a.week['label']})")
        self.ids.start_btn.text = f"Primera semana de muestreo: {ph.format_date_es(start)}"
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
        if on and st.get("account"):
            parts.insert(0, st["account"])
        self.ids.drive_text.text = " · ".join(parts)
        self.ids.drive_bar.value = 100 * st["done"] / total if on and total else 0
        self.ids.drive_bar_box.opacity = 1 if on and total else 0
        self.ids.drive_connect.text = ("Cambiar de cuenta" if on else "Conectar Google Drive")
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
        a.toast("Elija la cuenta de Google y acepte el permiso")

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
                "Las fotos dejarán de subirse y se quita el permiso de la app en esa cuenta. Lo que ya "
                "está en Drive y en el teléfono se conserva. Para usar otra cuenta, toque después "
                "«Conectar Google Drive» y elíjala de la lista.",
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
            a.toast(f"Primera semana de muestreo: {ph.format_date_es(value)}")

        if n:
            confirm("Cambiar la semana de inicio",
                    f"Hay {n} registro(s) en la temporada. Conservarán su orden (1.ª, 2.ª, 3.ª "
                    f"semana de muestreo…), pero sus fechas y semanas del año se recalcularán desde el "
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
                path = full_backup(a.db, workspaces=a.workspaces, open_db=a.open_db)
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
            from ai_share import is_knowledge_package
            if is_knowledge_package(path):
                a.toast("Ese es un paquete de la IA: impórtelo en el módulo de IA › Modelo")
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
                res = restore(a.db, path, progress=progress, workspaces=a.workspaces,
                              open_db=a.open_db)
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
            if not path.lower().endswith((".pdf", ".html", ".htm", ".zip")):
                a.toast("Elija un informe semanal (PDF, o .html/.zip de versiones anteriores)")
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

        a.media.pick_document(picked, exts=(".pdf", ".html", ".htm", ".zip"))

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
        card.add_widget(_mind("MindMuted", text=f"{p['variety']} · {p['week']}"
                                                + ("" if p.get("truth") is not None
                                                   else " · foto sin estado: tu respuesta la etiqueta")))
        img = MDBoxLayout(size_hint_y=None, height=dp(230))
        img.add_widget(thumb_widget(p["path"], 640))
        card.add_widget(img)
        grid = MDGridLayout(cols=2, spacing=dp(8), adaptive_height=True)
        opts = []
        for code in p["options"]:
            o = MindOption(title=f"BBCH {ph.code_str(code)}", subtitle=names.get(code, ""), code=code)
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
            msg = f"Etiquetada como BBCH {ph.code_str(option.code)}. +{res['xp']} XP"
        elif res["correct"]:
            msg = f"¡Correcto! +{res['xp']} XP"
        else:
            msg = f"Era BBCH {ph.code_str(truth)}. La IA lo aprende igual. +{res['xp']} XP"
        if res.get("ai") is not None:
            msg += f" · la IA había pensado BBCH {ph.code_str(res['ai'])}"
        self._feedback(msg, res)

    # ---- «Muéstrame este estado»
    def _capture_card(self, ch):
        a = app()
        code = ch["payload"]["target"]
        row = next((r for r in a.db.list_bbch() if r["code"] == code), None)
        card = _mind("MindCard")
        card.add_widget(_mind("MindSection", text="MUÉSTRAME ESTE ESTADO"))
        card.add_widget(MDLabel(text=f"BBCH {ph.code_str(code)}", font_style="H4", bold=True, adaptive_height=True,
                                theme_text_color="Custom", text_color=c(theme.MIND_ACCENT)))
        card.add_widget(MDLabel(text=row["label"] if row else "", font_style="H6", adaptive_height=True,
                                theme_text_color="Custom", text_color=c(theme.MIND_TEXT)))
        if row and row["description"]:
            card.add_widget(_mind("MindMuted", text=row["description"]))
        card.add_widget(_mind("MindMuted", text=ph.MACRO_STAGES.get(ph.macro_of(code), "")))
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
                    stored = store_photo(path, data_subdir("training"), f"desafio_bbch{ph.code_str(ch['payload']['target'])}")
                    res = self.game.complete_capture(ch["id"], stored)
                    res["path"] = stored
                    Clock.schedule_once(lambda *_: self._capture_done(res))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: (a.toast(f"No se pudo procesar: {error}"),
                                                    self._render_challenge()))

            a.workers.submit(work)

        if source == "camera":
            a.media.take_photo(done, f"entrenamiento_BBCH{ph.code_str(ch['payload']['target'])}")
        else:
            a.media.pick_image(done)

    def _capture_done(self, res):
        fast_clear(self.ids.task_box)
        card = _mind("MindCard")
        img = MDBoxLayout(size_hint_y=None, height=dp(200))
        img.add_widget(thumb_widget(res["path"], 640))
        card.add_widget(img)
        seen = f"La IA vio BBCH {ph.code_str(res['ai'])}"
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
        self.ids.auto_learn.active = bool(a.db.get_setting("ai_auto_learn", True))
        box = self.ids.photos
        fast_clear(box)
        photos = a.db.list_detail_photos(a.season)
        limit = getattr(self, "_label_limit", 12)
        for p in photos[:limit]:
            code = p["bbch_code"]
            row = LabelPhotoRow(
                title=f"{p['variety_name']} · {ph.week_short(p)}",
                subtitle=(f"BBCH {ph.code_str(code)}" if code is not None else "Sin estado")
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
            a.toast(f"Referencia agregada: BBCH {ph.code_str(code)}")
            self.refresh_label()

        extra = []
        if photo["bbch_code"] is not None:
            extra.append((f"✓ Usar el registro: BBCH {ph.code_str(photo['bbch_code'])}",
                          "Estado ya asignado en el muestreo", lambda: assign(photo["bbch_code"])))
        bbch_dialog("¿Qué estado muestra la foto?", a.db, assign, extra, current=photo["bbch_code"])

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
            box.add_widget(MindRow(title=f"BBCH {ph.code_str(code)}", subtitle=names.get(code, ""),
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
        alts = " · ".join(f"BBCH {ph.code_str(code)} {p:.0%}" for code, p in s.top[1:])
        self.ids.try_explain.text = f"Confianza {s.confidence:.0%} · alternativas: {alts}\n{s.explanation}"

    # ------------------------------------------- compartir entre teléfonos
    def export_knowledge(self):
        a = app()
        if not a.db.reference_counts():
            a.toast("Todavía no hay fotos etiquetadas en la memoria de la IA.")
            return
        a.toast("Preparando paquete…")

        def work():
            try:
                from ai_share import export_knowledge
                path, n = export_knowledge(a.db)
                Clock.schedule_once(lambda *_: a.report_actions(
                    path, "Conocimiento de la IA", f"{n} referencias · ábralo en el otro teléfono "
                    "con «Importar conocimiento»", viewable=False))
            except Exception as exc:  # noqa: BLE001
                error = exc
                Clock.schedule_once(lambda *_: a.toast(f"No se pudo exportar: {error}"))

        a.workers.submit(work)

    def import_knowledge(self):
        a = app()

        def picked(path, name):
            if not path:
                return
            a.toast("Sumando conocimiento…")

            def work():
                try:
                    from ai_share import import_knowledge
                    res = import_knowledge(a.db, path, a.classifier)
                    Clock.schedule_once(lambda *_: (self.refresh_model(),
                                                    confirm("Conocimiento importado", res.summary(), [])))
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    Clock.schedule_once(lambda *_: a.toast(f"No se pudo importar: {error}"))

            a.workers.submit(work)

        a.media.pick_document(picked, exts=(".zip",))

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
        rows = [(f"BBCH {ph.code_str(r['code'])}: {r['label']}", f"[{r['source']}] {r['description']}")
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


# ===========================================================================
# Mediciones personalizadas (registro de imágenes o planilla de datos)
# ===========================================================================
class EntryRow(MDBoxLayout):
    title = StringProperty()
    subtitle = StringProperty()
    has_thumb = BooleanProperty(False)
    item = ObjectProperty(None, allownone=True)
    screen = ObjectProperty()


def _variety_label(variety_id, varieties) -> str:
    v = next((v for v in varieties if v["id"] == variety_id), None)
    return v["name"] if v else "General"


def open_measures_menu():
    """Lista de mediciones de la semana actual + crear nueva + exportar todas a Excel."""
    a = app()
    week = a.week
    items = [("+ Nueva medición", "Registro de imágenes o planilla de datos", new_measure_dialog)]
    measures = a.db.list_measures(week["id"])
    for m in measures:
        unit = ("foto(s)" if m["kind"] == "images" else "fila(s)")
        kind = "Imágenes" if m["kind"] == "images" else "Planilla"
        items.append((m["name"], f"{kind} · {m['n']} {unit} esta semana",
                      lambda m=m: a.open_measure(m["id"], week["id"])))
    if measures:
        items.append(("Exportar todas a Excel", "Una hoja por medición · toda la temporada",
                      lambda: export_measures_xlsx([m["id"] for m in measures])))
    pick_dialog(f"Mediciones · {ph.week_title(week)}", items)


def export_measures_xlsx(measure_ids: list[int]):
    a = app()
    a.toast("Generando planilla Excel…")

    def work():
        try:
            from measures_export import export_measures
            path = export_measures(a.db, measure_ids, a.season)
            Clock.schedule_once(lambda *_: a.report_actions(
                path, "Planilla Excel (.xlsx)", "Mediciones de toda la temporada", viewable=False))
        except Exception as exc:  # noqa: BLE001
            error = exc
            Clock.schedule_once(lambda *_: a.toast(f"No se pudo exportar: {error}"))

    a.workers.submit(work)


def _measure_form(measure: dict | None = None):
    """Formulario: nombre y tipo (planilla / imágenes). Las columnas se crean en la grilla."""
    from kivy.factory import Factory
    box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8),
                      padding=(0, dp(8), 0, 0))
    box.name = Factory.Field(hint_text="Nombre de la medición")
    box.add_widget(box.name)
    box.kind = measure["kind"] if measure else "table"
    if measure:
        box.name.text = measure["name"]
        return box
    row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
    hint = MDLabel(font_style="Caption", adaptive_height=True, theme_text_color="Custom",
                   text_color=c(theme.MUTED))
    buttons = {}

    def choose(kind):
        box.kind = kind
        for k, b in buttons.items():
            on = k == kind
            b.md_bg_color = c(theme.LEAF_DARK) if on else c("#FFFFFF", 0)
            b.text_color = c("#FFFFFF") if on else c(theme.LEAF_DARK)
        hint.text = ("Una planilla como Excel: agregue filas y columnas y nombre cada columna."
                     if kind == "table" else "Fotos con descripción y, si quiere, la variedad.")

    for kind, label in (("table", "Planilla de datos"), ("images", "Imágenes")):
        b = MDRectangleFlatButton(text=label, theme_text_color="Custom", line_color=c(theme.LEAF_DARK),
                                  on_release=lambda *_, k=kind: choose(k))
        buttons[kind] = b
        row.add_widget(b)
    box.add_widget(row)
    box.add_widget(hint)
    Clock.schedule_once(lambda *_: choose("table"))
    return box


def new_measure_dialog():
    a = app()

    def ok(form):
        try:
            mid = a.db.add_measure(form.name.text, form.kind)
        except ValueError as exc:
            a.toast(str(exc))
            return False
        a.open_measure(mid, a.week["id"])

    form_dialog("Nueva medición", _measure_form(), ok, "CREAR")


def _cell_button(text, width, bold=False, bg=None, fg=None, on_release=None):
    """Celda-botón de la grilla (encabezados, n.º de fila, variedad)."""
    from kivy.uix.button import Button
    b = Button(text=text, size_hint=(None, None), size=(width, dp(42)), font_size="13sp",
               bold=bold, background_normal="", background_down="",
               background_color=bg or c(theme.LEAF_SOFT), color=fg or c(theme.INK),
               halign="center", valign="middle", shorten=True, shorten_from="right")
    b.bind(size=lambda w, sz: setattr(w, "text_size", (sz[0] - dp(8), sz[1])))
    if on_release:
        b.bind(on_release=on_release)
    return b


class MeasureScreen(MDScreen):
    """Registros de UNA medición en UNA semana: fotos con descripción o filas de planilla."""

    def load(self, measure_id: int, week_id: int):
        a = app()
        self.measure = a.db.get_measure(measure_id)
        self.week = a.db.get_week(week_id)
        self.ids.bar.title = self.measure["name"]
        kind = "PLANILLA DE DATOS" if self.measure["kind"] == "table" else "REGISTRO DE IMÁGENES"
        self.ids.kicker.text = f"{kind} · {ph.week_title(self.week).upper()}"
        self.ids.columns_text.text = (
            "Toque una celda para escribir · toque un encabezado para renombrar o eliminar la "
            "columna · toque el n.º de fila para eliminarla." if self.measure["kind"] == "table"
            else "Cada foto con su descripción y, si quiere, la variedad.")
        actions = self.ids.actions
        fast_clear(actions)
        from kivy.factory import Factory
        if self.measure["kind"] == "table":
            actions.add_widget(Factory.GhostButton(icon="table-row-plus-after", text="Agregar fila",
                                                   on_release=lambda *_: self.add_row()))
            actions.add_widget(Factory.GhostButton(icon="table-column-plus-after", text="Agregar columna",
                                                   on_release=lambda *_: self.add_column()))
        else:
            actions.add_widget(Factory.GhostButton(icon="camera-outline", text="Tomar foto",
                                                   on_release=lambda *_: self.add_images("camera")))
            actions.add_widget(Factory.GhostButton(icon="image-multiple-outline", text="Desde galería",
                                                   on_release=lambda *_: self.add_images("gallery")))
        self.refresh()

    def refresh(self):
        a = app()
        box = self.ids.entries
        fast_clear(box)
        entries = a.db.list_entries(self.measure["id"], week_id=self.week["id"])
        unit = "fila(s)" if self.measure["kind"] == "table" else "foto(s)"
        self.ids.count_text.text = f"{len(entries)} {unit} esta semana"
        if self.measure["kind"] == "table":
            self._build_grid(entries)
            return
        varieties = a.db.list_varieties(include_archived=True)
        for e in entries:
            row = EntryRow(title=e["caption"] or "Sin descripción",
                           subtitle=_variety_label(e["variety_id"], varieties), item=e,
                           screen=self, has_thumb=True)
            row.ids.thumb_box.add_widget(thumb_widget(e["path"], 160))
            box.add_widget(row)
        if not entries:
            box.add_widget(MDLabel(text="Sin registros esta semana.", font_style="Caption",
                                   adaptive_height=True, theme_text_color="Custom",
                                   text_color=c(theme.MUTED)))

    # ------------------------------------------------------ planilla (grilla)
    COL_W, NUM_W, VAR_W = dp(118), dp(40), dp(118)

    def _build_grid(self, entries):
        """Grilla editable: n.º | Variedad | columnas… · celdas = TextInput que se guardan
        al salir de ellas. Desplazamiento horizontal si hay muchas columnas."""
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.textinput import TextInput
        a = app()
        cols = list(self.measure["columns"])
        varieties = a.db.list_varieties(include_archived=True)
        self._rows = {e["id"]: dict(e["data"]) for e in entries}
        self._cells = []
        grid = GridLayout(cols=len(cols) + 2, size_hint=(None, None), spacing=dp(2), padding=dp(2))
        grid.bind(minimum_width=grid.setter("width"), minimum_height=grid.setter("height"))
        head_bg, head_fg = c(theme.LEAF_DARK), c("#FFFFFF")
        grid.add_widget(_cell_button("#", self.NUM_W, True, head_bg, head_fg))
        grid.add_widget(_cell_button("Variedad", self.VAR_W, True, head_bg, head_fg))
        for col in cols:
            grid.add_widget(_cell_button(col, self.COL_W, True, head_bg, head_fg,
                                         on_release=lambda *_, col=col: self.column_menu(col)))
        for i, e in enumerate(entries):
            grid.add_widget(_cell_button(str(i + 1), self.NUM_W, bg=c(theme.LEAF_SOFT),
                                         on_release=lambda *_, e=e, n=i + 1: self.row_menu(e, n)))
            grid.add_widget(_cell_button(_variety_label(e["variety_id"], varieties) if e["variety_id"]
                                         else "—", self.VAR_W, bg=c("#FFFFFF"), fg=c(theme.LEAF_DARK),
                                         on_release=lambda *_, e=e: self.pick_row_variety(e)))
            row_cells = []
            for col in cols:
                ti = TextInput(text=str(e["data"].get(col, "")), multiline=False, write_tab=False,
                               size_hint=(None, None), size=(self.COL_W, dp(42)), font_size="14sp",
                               padding=(dp(8), dp(11)), background_normal="", background_active="",
                               background_color=c("#FFFFFF"), foreground_color=c(theme.INK),
                               cursor_color=c(theme.LEAF_DARK))
                ti.entry_id, ti.col = e["id"], col
                ti.bind(focus=self._cell_focus)
                ti.bind(on_text_validate=self._cell_next)
                grid.add_widget(ti)
                row_cells.append(ti)
            self._cells.append(row_cells)
        sv = ScrollView(do_scroll_x=True, do_scroll_y=False, size_hint=(1, None),
                        bar_width=dp(4), scroll_type=["bars", "content"])
        grid.bind(height=lambda g, h: setattr(sv, "height", h + dp(8)))
        sv.add_widget(grid)
        self.ids.entries.add_widget(sv)
        if not entries:
            self.ids.entries.add_widget(MDLabel(
                text="Sin filas esta semana: toque «Agregar fila».", font_style="Caption",
                adaptive_height=True, theme_text_color="Custom", text_color=c(theme.MUTED)))

    def _cell_focus(self, ti, focused):
        if focused:
            return
        data = self._rows.get(ti.entry_id)
        if data is None:
            return
        value = ti.text.strip()
        if str(data.get(ti.col, "")) == value:
            return
        if value:
            data[ti.col] = value
        else:
            data.pop(ti.col, None)
        app().db.update_entry(ti.entry_id, data=data)

    def _cell_next(self, ti):
        """Enter: baja a la celda de la fila siguiente (misma columna)."""
        for r, row in enumerate(self._cells):
            if ti in row and r + 1 < len(self._cells):
                nxt = self._cells[r + 1][row.index(ti)]
                Clock.schedule_once(lambda *_: setattr(nxt, "focus", True), .05)
                return

    def commit_cells(self):
        """Guarda la celda en edición (al salir de la pantalla o antes de redibujar)."""
        for row in getattr(self, "_cells", []):
            for ti in row:
                if ti.focus:
                    ti.focus = False
                    self._cell_focus(ti, False)

    def on_pre_leave(self, *_):
        self.commit_cells()

    def save(self):
        """Disquete: guarda lo escrito (también se guarda solo al salir), avisa y vuelve."""
        self.commit_cells()
        from ui import celebrate
        app().back()
        celebrate.saved("Medición guardada")

    def add_row(self):
        self.commit_cells()
        a = app()
        a.db.add_entry(self.measure["id"], self.week["id"], None, data={})
        self.refresh()
        if self._cells:   # foco en la primera celda de la fila nueva
            Clock.schedule_once(lambda *_: setattr(self._cells[-1][0], "focus", True), .15)

    def add_column(self):
        self.commit_cells()
        a = app()
        from kivy.factory import Factory
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(0, dp(8), 0, 0))
        form.add_widget(Factory.Field(hint_text="Nombre de la columna (ej.: Largo (cm))"))

        def ok(f):
            try:
                a.db.add_measure_column(self.measure["id"], f.children[0].text)
            except ValueError as exc:
                a.toast(str(exc))
                return False
            self.measure = a.db.get_measure(self.measure["id"])
            self.refresh()

        form_dialog("Nueva columna", form, ok, "AGREGAR")

    def column_menu(self, col: str):
        self.commit_cells()
        a = app()
        mid = self.measure["id"]

        def rename():
            from kivy.factory import Factory
            form = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=(0, dp(8), 0, 0))
            form.add_widget(Factory.Field(hint_text="Nombre de la columna", text=col))

            def ok(f):
                try:
                    a.db.rename_measure_column(mid, col, f.children[0].text)
                except ValueError as exc:
                    a.toast(str(exc))
                    return False
                self.measure = a.db.get_measure(mid)
                self.refresh()

            form_dialog("Renombrar columna", form, ok)

        def remove():
            def go():
                try:
                    a.db.delete_measure_column(mid, col)
                except ValueError as exc:
                    a.toast(str(exc))
                    return
                self.measure = a.db.get_measure(mid)
                self.refresh()

            confirm("Eliminar columna", f"Se eliminará la columna «{col}» y sus datos en todas "
                    "las semanas.", [("Eliminar", go)])

        def move(step):
            cols = list(self.measure["columns"])
            i = cols.index(col)
            j = max(0, min(len(cols) - 1, i + step))
            cols[i], cols[j] = cols[j], cols[i]
            a.db.update_measure(mid, columns=cols)
            self.measure = a.db.get_measure(mid)
            self.refresh()

        pick_dialog(f"Columna «{col}»", [
            ("Renombrar", "Cambia el nombre (los datos se conservan)", rename),
            ("Mover a la izquierda", "Cambia el orden de las columnas", lambda: move(-1)),
            ("Mover a la derecha", "Cambia el orden de las columnas", lambda: move(1)),
            ("Eliminar columna", "Borra la columna y sus datos", remove),
        ])

    def row_menu(self, entry: dict, n: int):
        self.commit_cells()
        pick_dialog(f"Fila {n}", [
            ("Elegir variedad", "Asocia la fila a una variedad (sale en su informe)",
             lambda: self.pick_row_variety(entry)),
            ("Eliminar fila", "Borra los datos de esta fila", lambda: self.delete_entry(entry)),
        ])

    def pick_row_variety(self, entry: dict):
        self.commit_cells()
        a = app()

        def choose(vid):
            a.db.update_entry(entry["id"], variety_id=vid)
            self.refresh()

        pick_dialog("Variedad de la fila", [("— General", "Sin variedad específica", lambda: choose(None))]
                    + [(v["name"], v["code"] or "", lambda v=v: choose(v["id"]))
                       for v in a.db.list_varieties()])

    # ----------------------------------------------------------- registros
    def entry_dialog(self, entry: dict):
        """Descripción y variedad (opcional) de una foto de la medición."""
        a = app()
        from kivy.factory import Factory
        form = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6),
                           padding=(0, dp(22), 0, 0))
        state = {"variety_id": entry["variety_id"]}
        varieties = a.db.list_varieties()
        caption = Factory.Field(hint_text="Descripción", text=entry.get("caption") or "")
        vbtn = Factory.GhostButton(icon="fruit-cherries")

        def set_variety(vid):
            state["variety_id"] = vid
            vbtn.text = f"Variedad: {_variety_label(vid, varieties)}"

        vbtn.bind(on_release=lambda *_: pick_dialog(
            "Variedad", [("General", "Sin variedad específica", lambda: set_variety(None))]
            + [(v["name"], v["code"] or "", lambda v=v: set_variety(v["id"])) for v in varieties]))
        set_variety(state["variety_id"])
        form.add_widget(caption)
        form.add_widget(vbtn)

        def ok(_f):
            a.db.update_entry(entry["id"], caption=caption.text, variety_id=state["variety_id"])
            self.refresh()

        form_dialog("Foto", form, ok)

    def edit_entry(self, entry: dict):
        self.entry_dialog(entry)

    def delete_entry(self, entry: dict):
        def go():
            app().db.delete_entry(entry["id"])
            self.refresh()

        what = "esta fila" if self.measure["kind"] == "table" else "esta foto (el archivo queda en el teléfono)"
        confirm("Eliminar registro", f"¿Eliminar {what}?", [("Eliminar", go)])

    def add_images(self, source: str):
        a = app()
        measure, week = self.measure, self.week
        start = _dt.date.fromisoformat(week["start_date"][:10])
        from platform_utils import slugify
        code = f"{a.db.code}-" if a.db.code else ""
        base = f"{ph.photo_date(start)}-{code}{slugify(measure['name'])[:40] or 'medicion'}"

        def done(result, origin):
            paths = [p for p in (result if isinstance(result, list) else [result]) if p]
            if not paths:
                if origin == "error":
                    a.toast("No se pudo obtener la foto.")
                return
            a.toast("Guardando foto…" if len(paths) == 1 else f"Guardando {len(paths)} fotos…")

            def work():
                ids, error = [], None
                try:
                    dest_dir = data_subdir("photos", f"T{week['season']}", "mediciones")
                    for tmp in paths:
                        path = store_photo(tmp, dest_dir, base, exact=True)
                        ids.append(a.db.add_entry(measure["id"], week["id"], path=path))
                        a.thumb(path, 160)
                        a.backup_extra(path, week["start_date"], measure["name"])
                except Exception as exc:  # noqa: BLE001
                    error = exc
                Clock.schedule_once(lambda *_: stored(ids, error))

            def stored(ids, error):
                if self.measure["id"] != measure["id"] or self.week["id"] != week["id"]:
                    return
                self.refresh()
                if error:
                    a.toast(f"No se pudo guardar la foto: {error}")
                elif len(ids) == 1:
                    e = next((x for x in a.db.list_entries(measure["id"], week_id=week["id"])
                              if x["id"] == ids[0]), None)
                    if e:
                        self.entry_dialog(e)

            a.workers.submit(work)

        if source == "camera":
            a.media.take_photo(done, base, exact=True)
        else:
            a.media.pick_images(done)

    # --------------------------------------------------------- definición
    def edit(self):
        self.commit_cells()
        a = app()
        m = self.measure
        form = _measure_form(m)
        from kivy.factory import Factory
        form.add_widget(Factory.GhostButton(
            icon="delete-outline", text="Eliminar medición",
            on_release=lambda *_: (dialog.dismiss(), self.delete_measure())))

        def ok(f):
            try:
                a.db.update_measure(m["id"], name=f.name.text)
            except ValueError as exc:
                a.toast(str(exc))
                return False
            self.load(m["id"], self.week["id"])

        dialog = form_dialog("Editar medición", form, ok)

    def delete_measure(self):
        a = app()
        n = len(a.db.list_entries(self.measure["id"]))

        def go():
            a.db.delete_measure(self.measure["id"])
            a.toast("Medición eliminada")
            a.back()

        confirm("Eliminar medición",
                f"Se eliminará «{self.measure['name']}» y sus {n} registro(s) de todas las semanas. "
                "Las fotos quedan en el teléfono.", [("Eliminar", go)])

    def export(self):
        self.commit_cells()
        export_measures_xlsx([self.measure["id"]])
