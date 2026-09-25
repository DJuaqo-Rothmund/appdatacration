"""
FenoRubus — Cuaderno de campo digital para el seguimiento fenológico y
biométrico de ensayos en frambueso (Rubus idaeus).

Punto de entrada y navegación KivyMD.

    python main.py                 # escritorio (PC)
    buildozer android debug        # APK (ver README.md)
"""
from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("KIVY_LOG_MODE", "PYTHON")

from kivy.clock import Clock, mainthread  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.lang import Builder  # noqa: E402
from kivy.metrics import dp  # noqa: E402
from kivy.utils import platform  # noqa: E402
from kivymd.app import MDApp  # noqa: E402
from kivymd.uix.label import MDLabel  # noqa: E402
from kivymd.uix.screenmanager import MDScreenManager  # noqa: E402
from kivymd.uix.snackbar import MDSnackbar  # noqa: E402
from kivy.uix.screenmanager import SlideTransition  # noqa: E402

from ui import theme  # noqa: E402

theme.install_palette()  # antes de cargar widgets KivyMD con color

from ai_classifier import PhenologyClassifier, PinGuard  # noqa: E402
from android_bridge import create_media, make_thumbnail, request_runtime_permissions  # noqa: E402
from database import Database, default_db_path  # noqa: E402
from notifications import ReminderManager  # noqa: E402
from platform_utils import data_subdir, resource_path  # noqa: E402
from reporter import ReportGenerator  # noqa: E402
from ui.screens import (AILabScreen, HomeScreen, ObservationScreen, PinForm,  # noqa: E402
                        VarietyScreen, form_dialog)

MIME = {".html": "text/html", ".zip": "application/zip", ".sqlite3": "application/x-sqlite3"}


class FenoRubusApp(MDApp):
    title = "FenoRubus"

    # ------------------------------------------------------------ build
    def build(self):
        theme.apply(self.theme_cls)
        if platform not in ("android", "ios"):
            Window.size = (412, 860)
        self.db = Database(default_db_path())
        self.classifier = PhenologyClassifier(self.db)
        self.pin = PinGuard(self.db)
        self.reports = ReportGenerator(self.db, data_subdir("reports"))
        self.reminders = ReminderManager(self.db)
        self.media = create_media(data_subdir("tmp"), self._desktop_file_chooser)
        self.week = self.db.current_week()
        self.season = self.week["season"]
        self._history: list[str] = []
        self.workers = ThreadPoolExecutor(max_workers=2)  # miniaturas y fotos
        self._file_manager = None

        Builder.load_file(resource_path("ui", "layout.kv"))
        self.sm = MDScreenManager(transition=SlideTransition(duration=0.18))
        self.home = HomeScreen(name="home")
        self.observation = ObservationScreen(name="observation")
        self.variety = VarietyScreen(name="variety")
        self.ailab = AILabScreen(name="ailab")
        for s in (self.home, self.observation, self.variety, self.ailab):
            self.sm.add_widget(s)
        Window.bind(on_keyboard=self._on_keyboard)
        return self.sm

    def on_start(self):
        self.home.refresh_current()
        request_runtime_permissions()
        self.reminders.apply()
        Clock.schedule_interval(lambda *_: self._check_reminder(), 60)

    def on_resume(self):
        self.week = self.db.current_week() if self.week is None else self.week
        self.refresh_home()

    def on_stop(self):
        self.workers.shutdown(wait=False)
        self.db.close()

    # ------------------------------------------------------- navegación
    def go(self, name: str, direction: str = "left"):
        if self.sm.current != name:
            self._history.append(self.sm.current)
        self.sm.transition.direction = direction
        self.sm.current = name

    def back(self):
        target = self._history.pop() if self._history else "home"
        self.sm.transition.direction = "right"
        self.sm.current = target
        if target == "home":
            self.refresh_home()

    def _on_keyboard(self, _window, key, *_args):
        if key == 27:  # botón «atrás» de Android / Esc
            if self._file_manager and self._file_manager._window_manager_open:
                self._file_manager.close()
                return True
            if self.sm.current != "home":
                self.back()
                return True
        return False

    def refresh_home(self):
        # Solo la pestaña visible; las demás se refrescan al seleccionarlas.
        self.home.refresh_current()

    def open_observation(self, variety_id: int, week_id: int):
        self.observation.load(variety_id, week_id)
        self.go("observation")

    def open_variety(self, variety_id: int):
        self.variety.load(variety_id)
        self.go("variety")

    def open_ai_lab(self):
        """Módulo de calibración: requiere PIN (por defecto 1234)."""
        form = PinForm()

        def ok(f):
            if self.pin.verify(f.ids.pin.text):
                self.go("ailab")
                return True
            wait = self.pin.locked_seconds
            f.ids.msg.text = (f"Demasiados intentos. Espere {wait} s." if wait
                              else "PIN incorrecto.")
            f.ids.pin.text = ""
            return False

        form_dialog("Acceso restringido · Calibración IA", form, ok, "INGRESAR")

    # ---------------------------------------------------------- utilidades
    def toast(self, text: str):
        MDSnackbar(MDLabel(text=text, theme_text_color="Custom", text_color=(1, 1, 1, 1)),
                   md_bg_color=theme.c(theme.SLATE), y=dp(24), pos_hint={"center_x": .5},
                   size_hint_x=.92, duration=2.5).open()

    def _thumb_dest(self, path: str, size: int) -> str | None:
        try:
            key = hashlib.md5(f"{path}|{os.path.getmtime(path)}|{size}".encode()).hexdigest()
        except OSError:
            return None
        return os.path.join(data_subdir("thumbs"), key + ".jpg")

    def thumb(self, path: str, size: int = 320) -> str:
        """Miniatura cacheada (síncrona; usar thumb_async desde la interfaz)."""
        dest = self._thumb_dest(path, size)
        if dest is None:
            return path
        if not os.path.exists(dest):
            try:
                make_thumbnail(path, dest, size)
            except Exception:
                return path
        return dest

    def thumb_async(self, path: str, size: int, callback) -> None:
        """Entrega la miniatura a `callback` sin bloquear la interfaz."""
        dest = self._thumb_dest(path, size)
        if dest and os.path.exists(dest):
            callback(dest)
            return

        def work():
            result = self.thumb(path, size)
            Clock.schedule_once(lambda *_: callback(result))

        self.workers.submit(work)

    def run_report(self, job, on_done=None):
        self.toast("Generando informe…")

        def work():
            try:
                res = job()
                self._report_done(res, None, on_done)
            except Exception as exc:  # noqa: BLE001
                self._report_done(None, exc, on_done)

        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _report_done(self, res, error, on_done):
        if error:
            self.toast(f"Error al generar: {error}")
            return
        self.toast(f"Listo: {os.path.basename(res.path)} ({res.size_kb} KB)")
        if on_done:
            on_done(res)
        self.open_file(res.path)

    def open_file(self, path: str):
        try:
            self.media.open(path, MIME.get(os.path.splitext(path)[1], "*/*"))
        except Exception as exc:  # noqa: BLE001
            self.toast(f"No se pudo abrir: {exc}")

    def share_file(self, path: str, mime: str | None = None):
        try:
            self.media.share(path, mime or MIME.get(os.path.splitext(path)[1], "*/*"))
        except Exception as exc:  # noqa: BLE001
            self.toast(f"No se pudo compartir: {exc}")

    def _check_reminder(self):
        if self.reminders.check_due():
            self.toast("Recordatorio: hoy corresponde el muestreo semanal.")

    def _desktop_file_chooser(self, callback, exts):
        from kivymd.uix.filemanager import MDFileManager

        def select(path):
            self._file_manager.close()
            callback(path if os.path.isfile(path) else None)

        def exit_manager(*_):
            self._file_manager.close()
            callback(None)

        self._file_manager = MDFileManager(select_path=select, exit_manager=exit_manager,
                                           ext=list(exts), preview=False)
        self._file_manager.show(os.path.expanduser("~"))


if __name__ == "__main__":
    FenoRubusApp().run()
