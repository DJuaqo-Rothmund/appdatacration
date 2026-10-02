"""
PhenoRubus — Cuaderno de campo digital para el seguimiento fenológico y
biométrico de ensayos en frambueso (Rubus idaeus).

Punto de entrada y navegación KivyMD.

    python main.py                 # escritorio (PC)
    buildozer android debug        # APK (ver README.md)
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property

os.environ.setdefault("KIVY_LOG_MODE", "PYTHON")

from kivy.config import Config  # noqa: E402

# Rendimiento en gama media/baja: sin antialiasing multisample (MSAA duplica el
# costo de relleno en GPUs modestas; en pantallas de alta densidad no se nota).
Config.set("graphics", "multisamples", "0")
Config.set("kivy", "exit_on_escape", "0")

from kivy.clock import Clock, mainthread  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.lang import Builder  # noqa: E402
from kivy.metrics import dp  # noqa: E402
from kivy.utils import platform  # noqa: E402
from kivymd.app import MDApp  # noqa: E402
from kivymd.uix.label import MDLabel  # noqa: E402
from kivymd.uix.screenmanager import MDScreenManager  # noqa: E402
from kivymd.uix.snackbar import MDSnackbar  # noqa: E402
from kivy.uix.floatlayout import FloatLayout  # noqa: E402
from kivy.uix.image import Image  # noqa: E402
from kivy.uix.screenmanager import FadeTransition  # noqa: E402

from ui import theme  # noqa: E402

theme.install_palette()  # antes de cargar widgets KivyMD con color

from android_bridge import create_media, make_thumbnail, request_runtime_permissions  # noqa: E402
from database import Database, default_db_path  # noqa: E402
from notifications import ReminderManager  # noqa: E402
from platform_utils import data_subdir, resource_path  # noqa: E402
from ui.screens import (AILabScreen, HomeScreen, MeasureScreen, ObservationScreen,  # noqa: E402
                        PinForm, SettingsScreen, SplashScreen, VarietyScreen, form_dialog)

MIME = {".html": "text/html", ".zip": "application/zip", ".sqlite3": "application/x-sqlite3",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class FenoRubusApp(MDApp):
    title = "PhenoRubus"
    SPLASH_MIN_S = 1.6  # tiempo mínimo visible de la pantalla de inicio

    # ------------------------------------------------------------ build
    def build(self):
        import crashguard
        crashguard.install(notify=lambda text: self.toast(text))
        theme.apply(self.theme_cls)
        if platform not in ("android", "ios"):
            Window.size = (412, 860)
        self.db = Database(default_db_path())
        self.reminders = ReminderManager(self.db)
        self.media = create_media(data_subdir("tmp"), self._desktop_file_chooser)
        self.week = self.db.current_week()
        self.season = self.week["season"]
        self._history: list[str] = []
        self.workers = ThreadPoolExecutor(max_workers=2)  # miniaturas y fotos
        self._file_manager = None

        Builder.load_file(resource_path("ui", "layout.kv"))
        # Fundido corto: con pantallas translúcidas un deslizamiento superpondría contenidos.
        self.sm = MDScreenManager(transition=FadeTransition(duration=0.14))
        # Primero solo la pantalla de inicio (logo): el primer cuadro aparece de inmediato
        # y la pantalla principal se construye detrás, en on_start.
        self.sm.add_widget(SplashScreen(name="splash"))
        self.home = None
        Window.bind(on_keyboard=self._on_keyboard)
        Window.clearcolor = theme.c(theme.SPLASH_BG)  # sin destello entre presplash e inicio
        # Fondo difuminado verde/frambuesa detrás de todas las pantallas translúcidas.
        root = FloatLayout()
        root.add_widget(Image(source=theme.BACKGROUND, fit_mode="fill"))
        root.add_widget(self.sm)
        return root

    # --------------------------------------- carga diferida (arranque rápido)
    @cached_property
    def classifier(self):
        from ai_classifier import PhenologyClassifier  # numpy: solo al usar la IA
        return PhenologyClassifier(self.db)

    @cached_property
    def pin(self):
        from ai_classifier import PinGuard
        return PinGuard(self.db)

    @cached_property
    def reports(self):
        from reporter import ReportGenerator  # Jinja2: solo al generar informes
        return ReportGenerator(self.db, data_subdir("reports"))

    @cached_property
    def drive(self):
        from drive_backup import DriveBackup  # respaldo de fotos en Google Drive
        return DriveBackup(self.db)

    def _screen(self, cls, name):
        if not self.sm.has_screen(name):
            self.sm.add_widget(cls(name=name))
        return self.sm.get_screen(name)

    @property
    def observation(self):
        return self._screen(ObservationScreen, "observation")

    @property
    def variety(self):
        return self._screen(VarietyScreen, "variety")

    @property
    def measure(self):
        return self._screen(MeasureScreen, "measure")

    def open_measure(self, measure_id: int, week_id: int):
        self.measure.load(measure_id, week_id)
        self.go("measure")

    @property
    def ailab(self):
        return self._screen(AILabScreen, "ailab")

    @property
    def settings(self):
        return self._screen(SettingsScreen, "settings")

    def on_start(self):
        self._t_start = time.monotonic()
        self.sm.get_screen("splash").animate_in()
        Clock.schedule_once(self._boot, 0.15)  # deja pintar el logo antes de trabajar

    def _boot(self, *_):
        self.home = HomeScreen(name="home")  # el resto de pantallas se crea al primer uso
        self.sm.add_widget(self.home)
        self.home.refresh_current()
        wait = max(0.0, self.SPLASH_MIN_S - (time.monotonic() - self._t_start))
        Clock.schedule_once(self._leave_splash, wait)

    def _leave_splash(self, *_):
        self.sm.transition = FadeTransition(duration=0.35)
        self.sm.current = "home"
        self.sm.transition = FadeTransition(duration=0.14)
        # Libera la pantalla de inicio (y su textura) cuando termina el fundido.
        Clock.schedule_once(lambda *_: self.sm.remove_widget(self.sm.get_screen("splash")), 0.6)
        request_runtime_permissions()
        self.reminders.apply()
        Clock.schedule_interval(lambda *_: self._check_reminder(), 60)
        # Precarga, de a una y en reposo, las pantallas que más cuesta crear en teléfonos
        # básicos (p. ej. Samsung A06): así se abren al instante al tocarlas.
        self._prewarm = [lambda: self.observation,
                         lambda: self.settings,
                         lambda: self.settings.ids.varieties.refresh()]
        Clock.schedule_once(self._prewarm_next, 2.5)
        # Nombres de foto «ddmmaaaa-…» (hasta la 1.1.30) -> «aaaammdd-…»: archivos de la app,
        # galería del teléfono y (al respaldar) Google Drive.
        Clock.schedule_once(lambda *_: self.workers.submit(self._rename_photos), 4)
        # Sube lo que haya quedado en cola (sin red la última vez).
        # y respalda la base en Drive una vez al día (las fotos ya se suben solas).
        if self.db.get_setting("drive_enabled", False):
            Clock.schedule_once(lambda *_: self.workers.submit(self._drive_daily), 6)

    def _rename_photos(self):
        try:
            from photo_rename import migrate_local
            res = migrate_local(self.db)
        except Exception as exc:  # noqa: BLE001 (nunca debe impedir usar la app)
            print("rename photos:", exc)
            return
        Clock.schedule_once(lambda *_: self._rename_public(res["renamed"]), 0)

    def _rename_public(self, local: int):
        """Galería («Imágenes de Fenología»): en el hilo principal (Java), una sola vez."""
        public = 0
        if not self.db.get_setting("public_names_aaaammdd", False):
            try:
                public, _failed = self.media.rename_public_legacy()
                self.db.set_setting("public_names_aaaammdd", True)
            except Exception as exc:  # noqa: BLE001
                print("rename public:", exc)
        if local or public:
            self.toast(f"Fotos renombradas al formato año-mes-día: {max(local, public)}")
        if self.db.get_setting("drive_enabled", False):
            self.drive.flush_async()   # renombra también las ya subidas a Drive

    def _prewarm_next(self, *_):
        """Crea la siguiente pantalla pendiente solo si el usuario está en reposo en el
        inicio; si está usando la app, lo reintenta más tarde para no trabarla."""
        if not self._prewarm:
            return
        if self.sm.current != "home" or self.sm.transition.is_active:
            Clock.schedule_once(self._prewarm_next, 3)
            return
        step = self._prewarm.pop(0)
        try:
            step()
        except Exception as exc:  # noqa: BLE001 (la precarga nunca debe cerrar la app)
            print("prewarm:", exc)
        Clock.schedule_once(self._prewarm_next, 2)

    def on_resume(self):
        self.week = self.db.current_week() if self.week is None else self.week
        if "drive" in self.__dict__:
            self.drive.flush_async()
        if self.home is not None:
            self.refresh_home()

    def on_stop(self):
        self.workers.shutdown(wait=False)
        self.db.close()

    # ------------------------------------------------------- navegación
    def go(self, name: str, direction: str = "left"):
        if not self.sm.has_screen(name):
            getattr(self, name)  # carga diferida de la pantalla
        if self.sm.current != name:
            self._history.append(self.sm.current)
        self.sm.current = name

    def back(self):
        target = self._history.pop() if self._history else "home"
        self.sm.current = target
        if target == "home":
            self.refresh_home()

    def _on_keyboard(self, _window, key, *_args):
        if key == 27:  # botón «atrás» de Android / Esc
            if self.media.close_preview():  # cierra la vista previa del informe
                return True
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

    def open_settings(self):
        self.go("settings")

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
                   md_bg_color=theme.c(theme.LEAF_DARK, .94), radius=[dp(18)] * 4,
                   y=dp(108), pos_hint={"center_x": .5}, size_hint_x=.9, duration=2.5).open()

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
        if on_done:
            on_done(res)
        self.report_actions(res.path, title="Informe listo",
                            info=f"{os.path.basename(res.path)} · {res.size_kb} KB")

    # ------------------------------------------- enviar / guardar informes
    def report_actions(self, path: str, title: str = "Informe", info: str = "", viewable: bool = True):
        """Menú: WhatsApp, correo, guardar en el teléfono, ver u otras apps."""
        from ui.screens import pick_dialog
        size_mb = os.path.getsize(path) / 1e6
        heavy = size_mb > 20
        mail_note = (f"{size_mb:.0f} MB: puede superar el límite de adjuntos (use ZIP)" if heavy
                     else "Gmail, Outlook u otra app de correo")
        options = [
            ("WhatsApp", "Enviar como documento", lambda: self.share_file(path, target="whatsapp")),
            ("Correo electrónico", mail_note, lambda: self.send_email(path)),
            ("Guardar en el teléfono", "Descargas › PhenoRubus", lambda: self.save_file(path)),
            ("Guardar en Google Drive", "Carpeta de la semana en «PhenoRubus · Imágenes de Fenología»"
             if self.db.get_setting("drive_enabled", False) else "Primero conecte Drive en Ajustes",
             lambda: self.save_to_drive(path)),
            ("Ver informe", "Abrir en el navegador", lambda: self.open_file(path)),
            ("Otras apps…", "Drive, Telegram, Bluetooth…", lambda: self.share_file(path)),
        ]
        if not viewable:
            options = [o for o in options if o[0] != "Ver informe"]
        pick_dialog(f"{title}" + (f"\n{info}" if info else ""), options)

    def _mime(self, path: str) -> str:
        return MIME.get(os.path.splitext(path)[1], "*/*")

    def open_file(self, path: str):
        try:
            self.media.open(path, self._mime(path))
        except Exception as exc:  # noqa: BLE001
            self.toast(f"No se pudo abrir: {exc}")

    def save_file(self, path: str):
        try:
            where = self.media.save_public(path, self._mime(path))
            self.toast(f"Guardado en {where}")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"No se pudo guardar: {exc}")

    def send_email(self, path: str):
        """Una app de correo → directo; varias → el usuario elige; ninguna → menú general."""
        try:
            apps = self.media.email_apps()
        except Exception:  # noqa: BLE001
            apps = []
        if len(apps) == 1:
            self.share_file(path, target=f"email:{apps[0][1]}")
        elif apps:
            from ui.screens import pick_dialog
            pick_dialog("Enviar por correo con…", [
                (label, "", lambda pkg=pkg: self.share_file(path, target=f"email:{pkg}"))
                for label, pkg in apps])
        else:
            self.share_file(path, target="email")

    def save_to_drive(self, path: str):
        if not self.db.get_setting("drive_enabled", False):
            self.toast("Conecte Google Drive en Ajustes › Respaldo en Google Drive")
            return
        remote = self.drive.enqueue_report(path)
        self.toast(f"Se sube a Drive: {remote.rsplit('/', 1)[0]}")

    def share_file(self, path: str, mime: str | None = None, target: str | None = None):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            self.media.share(path, mime or self._mime(path), target=target,
                             subject=f"PhenoRubus · {name}",
                             text="Informe fenológico generado con PhenoRubus. Ábralo con el "
                                  "navegador del teléfono o del computador.")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"No se pudo compartir: {exc}")

    def _drive_daily(self):
        try:
            if self.drive.backup_database() is None:
                self.drive.flush_async()
        except Exception as exc:  # noqa: BLE001
            print("drive daily backup:", exc)

    def reload_data(self):
        """Tras restaurar un respaldo: descarta cachés y vuelve a leer todo."""
        for name in ("classifier", "reports"):
            self.__dict__.pop(name, None)
        self.week = self.db.current_week()
        self.season = self.week["season"]
        for name in ("observation", "variety", "ailab"):
            if self.sm.has_screen(name):
                self.sm.remove_widget(self.sm.get_screen(name))
        self.reminders.apply()
        self.refresh_home()

    def backup_photo(self, photo_id: int, path: str) -> None:
        """Encola la foto para respaldo en Google Drive (si está activado)."""
        if not self.db.get_setting("drive_enabled", False):
            return
        self.drive.enqueue(photo_id, path)

    def backup_extra(self, path: str, start_date: str, folder: str) -> None:
        """Fotos adjuntas o de mediciones → carpeta de la semana en Drive («…/Adjuntas», «…/<medición>»)."""
        if not self.db.get_setting("drive_enabled", False):
            return
        from drive_backup import DriveBackup
        safe = folder.replace("/", "-").strip() or "Otras"
        self.drive.enqueue(None, path, remote=f"{DriveBackup.week_folder(start_date)}/{safe}/"
                                              f"{os.path.basename(path)}")

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
