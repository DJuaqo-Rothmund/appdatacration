"""
drive_backup.py
===============
Respaldo de fotos en Google Drive (además de la copia en el teléfono).

* Cada foto nueva se encola en `drive_queue` (SQLite): el respaldo funciona sin
  conexión y se sube en cuanto hay red (reintentos con límite).
* Carpeta en «Mi unidad»:  PhenoRubus · Imágenes de Fenología / Año 2026 / Semana 37 · 07-09-2026 / …
* Permiso mínimo `drive.file`: la app solo ve los archivos que ella misma creó.
* Android: autorización con Google Identity Services (AuthorizationClient de
  play-services-auth); la subida usa la API REST v3 con urllib (sin librerías extra).
* El transporte HTTP y el proveedor de token son inyectables (pruebas / escritorio).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import phenology as ph
from platform_utils import IS_ANDROID

SCOPE = "https://www.googleapis.com/auth/drive.file"
ROOT_FOLDER = "PhenoRubus · Imágenes de Fenología"
FOLDER_MIME = "application/vnd.google-apps.folder"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
MAX_ATTEMPTS = 6
RC_DRIVE_AUTH = 7301
RC_PICK_ACCOUNT = 7302


class DriveError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class NeedsConsent(DriveError):
    """El usuario debe (re)conectar su cuenta desde Ajustes."""


class Offline(DriveError):
    """Sin red: se reintenta más tarde sin gastar intentos."""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _ssl_context():
    import ssl
    try:
        import certifi  # en Android no hay almacén de CA accesible para OpenSSL
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise Offline(f"Sin conexión: {getattr(e, 'reason', e)}") from e


# --------------------------------------------------------------- autorización
# Códigos de ApiException (CommonStatusCodes) más frecuentes al conectar.
API_HINTS = {
    "10": "DEVELOPER_ERROR: Google no reconoce la app. Revise en Google Cloud el ID de cliente "
          "Android: paquete org.rubus.fenorubus y huella SHA-1 exacta.",
    "12500": "Error al iniciar sesión: actualice Servicios de Google Play y revise la pantalla "
             "de consentimiento (usuario de prueba agregado).",
    "12501": "Conexión cancelada.",
    "16": "Conexión cancelada.",
    "7": "Sin conexión a internet.",
    "8": "Error interno de Google: intente de nuevo.",
    "17": "Servicios de Google Play no disponibles en este teléfono.",
}


def explain(error: str) -> str:
    """Convierte «ApiException: 10: …» en una explicación útil."""
    import re as _re
    m = _re.search(r"ApiException:\s*(\d+)", error or "")
    if m and m[1] in API_HINTS:
        return f"{API_HINTS[m[1]]} (código {m[1]})"
    if "ClassNotFound" in (error or "") or "NoClassDefFound" in (error or "") \
            or "Class not found" in (error or ""):
        # El comienzo dice QUÉ clase faltó (el final es solo la pila de llamadas).
        head = " ".join((error or "").split())[:220]
        return f"La app no pudo cargar una clase de Android/Google. Detalle: {head}"
    return error


def _detach():
    try:
        from jnius import detach  # type: ignore
        detach()  # obligatorio al terminar un hilo de Python que usó Java
    except Exception:  # noqa: BLE001
        pass


class AndroidAuthorizer:
    """Token OAuth de Drive vía `Identity.getAuthorizationClient(activity)`.

    Importante: en Android, pyjnius busca clases con JNI FindClass, que desde un hilo
    creado por Python solo ve las clases del sistema (no las de la app, como Google
    Play Services). Por eso toda llamada a clases de Google se hace en el hilo
    principal de Kivy (`_on_main`); el hilo de fondo solo espera.
    """

    AUTHORIZE_TIMEOUT = 45
    CONSENT_TIMEOUT = 300
    GOOGLE_CLASSES = ("com.google.android.gms.auth.api.identity.Identity",
                      "com.google.android.gms.auth.api.identity.AuthorizationRequest",
                      "com.google.android.gms.auth.api.identity.AuthorizationResult",
                      "com.google.android.gms.common.api.Scope")

    def __init__(self, log=None):
        from android import activity  # type: ignore
        self.log = log or (lambda msg: None)
        self._pending = {}            # código -> (event, holder) de una pantalla de Google
        self.account = None           # correo de la cuenta elegida (None: la que recuerde Google)
        self._listeners = []          # referencias vivas para pyjnius
        activity.bind(on_activity_result=self._on_activity_result)

    # ---------------------------------------------------------- hilo principal
    @staticmethod
    def _on_main(fn, timeout: float = 20):
        """Ejecuta fn en el hilo principal de Kivy y devuelve su resultado."""
        if threading.current_thread() is threading.main_thread():
            return fn()
        from kivy.clock import Clock
        ev, box = threading.Event(), {}

        def run(*_):
            try:
                box["value"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            ev.set()

        Clock.schedule_once(run, 0)
        if not ev.wait(timeout):
            raise DriveError("La app no respondió (hilo principal ocupado)")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def preload(self) -> None:
        """Carga las clases de Google (llamar en el hilo principal)."""
        from jnius import autoclass  # type: ignore
        for name in self.GOOGLE_CLASSES:
            autoclass(name)

    def _request(self):
        from jnius import autoclass  # type: ignore
        scopes = autoclass("java.util.ArrayList")()
        scopes.add(autoclass("com.google.android.gms.common.api.Scope")(SCOPE))
        builder = autoclass("com.google.android.gms.auth.api.identity.AuthorizationRequest") \
            .builder().setRequestedScopes(scopes)
        if self.account:   # cuenta elegida por el usuario (si no, Google usa la última autorizada)
            builder = builder.setAccount(autoclass("android.accounts.Account")(self.account, "com.google"))
        req = builder.build()
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        client = autoclass("com.google.android.gms.auth.api.identity.Identity").getAuthorizationClient(act)
        return client, req

    # ----------------------------------------------------------- autorización
    def get_token(self, interactive: bool, timeout: float | None = None) -> str:
        done = threading.Event()
        box: dict = {}

        def start():
            from jnius import PythonJavaClass, java_method  # type: ignore

            class Success(PythonJavaClass):
                __javainterfaces__ = ["com/google/android/gms/tasks/OnSuccessListener"]
                __javacontext__ = "app"

                @java_method("(Ljava/lang/Object;)V")
                def onSuccess(self, result):  # noqa: N802
                    box["result"] = result
                    done.set()

            class Failure(PythonJavaClass):
                __javainterfaces__ = ["com/google/android/gms/tasks/OnFailureListener"]
                __javacontext__ = "app"

                @java_method("(Ljava/lang/Exception;)V")
                def onFailure(self, exc):  # noqa: N802
                    try:
                        box["error"] = exc.toString()
                    except Exception as e:  # noqa: BLE001
                        box["error"] = repr(e)
                    done.set()

            self.preload()
            client, req = self._request()
            ok, ko = Success(), Failure()
            self._listeners = [ok, ko]
            task = client.authorize(req)
            task.addOnSuccessListener(ok)
            task.addOnFailureListener(ko)

        self.log("Pidiendo autorización a Google…")
        try:
            self._on_main(start)
        except DriveError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DriveError(explain(f"No se pudo preparar la autorización: {exc}")) from exc
        if not done.wait(timeout or self.AUTHORIZE_TIMEOUT):
            raise DriveError("Google no respondió (¿Servicios de Google Play desactualizados o sin red?)")
        if "error" in box:
            raise DriveError(explain(box["error"]))

        def read_result():
            from jnius import cast  # type: ignore
            res = cast("com.google.android.gms.auth.api.identity.AuthorizationResult", box["result"])
            if res.hasResolution():
                return None, res.getPendingIntent()
            return res.getAccessToken(), None

        token, pending_intent = self._on_main(read_result)
        if pending_intent is not None:
            if not interactive:
                raise NeedsConsent("Conecte su cuenta de Google en Ajustes")
            self.log("Abriendo el selector de cuenta de Google…")
            return self._resolve(pending_intent)
        if not token:
            raise DriveError("Google no entregó un token de acceso")
        return token

    def choose_account(self) -> str:
        """Selector de cuentas de Google del teléfono. Devuelve el correo elegido."""
        ev, holder = threading.Event(), {}
        self._pending[RC_PICK_ACCOUNT] = (ev, holder)

        def launch():
            from jnius import autoclass  # type: ignore
            AccountManager = autoclass("android.accounts.AccountManager")
            current = autoclass("android.accounts.Account")(self.account, "com.google") \
                if self.account else None
            intent = AccountManager.newChooseAccountIntent(
                current, None, ["com.google"], "Cuenta de Google para el respaldo en Drive",
                None, None, None)
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.startActivityForResult(intent, RC_PICK_ACCOUNT)

        self.log("Abriendo la lista de cuentas de Google…")
        try:
            self._on_main(launch)
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(RC_PICK_ACCOUNT, None)
            raise DriveError(explain(f"No se pudo abrir la lista de cuentas: {exc}")) from exc
        if not ev.wait(self.CONSENT_TIMEOUT):
            self._pending.pop(RC_PICK_ACCOUNT, None)
            raise DriveError("No se eligió ninguna cuenta (tiempo agotado)")
        if "error" in holder:
            raise DriveError("No se eligió ninguna cuenta")
        name = self._on_main(lambda data=holder["data"]: data.getStringExtra("authAccount"))
        if not name:
            raise DriveError("No se eligió ninguna cuenta")
        return str(name)

    def _resolve(self, pending_intent) -> str:
        ev, holder = threading.Event(), {}
        self._pending[RC_DRIVE_AUTH] = (ev, holder)

        def launch():
            from jnius import autoclass  # type: ignore
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            # Versión de 6 argumentos (sin Bundle): evita ambigüedad de sobrecargas.
            act.startIntentSenderForResult(pending_intent.getIntentSender(), RC_DRIVE_AUTH,
                                           None, 0, 0, 0)

        try:
            self._on_main(launch)
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(RC_DRIVE_AUTH, None)
            raise DriveError(f"No se pudo abrir el selector de cuenta: {exc}") from exc
        if not ev.wait(self.CONSENT_TIMEOUT):  # el usuario elige cuenta y acepta
            self._pending.pop(RC_DRIVE_AUTH, None)
            raise DriveError("No se completó la conexión con Google (tiempo agotado)")
        if "error" in holder:
            raise DriveError(explain(holder["error"]))

        def read(data=holder.get("data")):
            client, _req = self._request()
            return client.getAuthorizationResultFromIntent(data).getAccessToken()

        try:
            token = self._on_main(read)
        except Exception as exc:  # noqa: BLE001 (ApiException de Java incluida)
            raise DriveError(explain(str(exc)) or "Conexión cancelada") from exc
        if not token:
            raise DriveError("Google no entregó un token de acceso")
        return token

    def _on_activity_result(self, request_code, result_code, data):
        if request_code not in self._pending:
            return
        ev, holder = self._pending.pop(request_code)
        if data is None or (request_code == RC_PICK_ACCOUNT and result_code != -1):   # -1 = RESULT_OK
            holder["error"] = f"Conexión cancelada (resultado {result_code})"
        else:
            holder["data"] = data   # se interpreta en el hilo principal de Kivy
        ev.set()


def is_metered() -> bool:
    """True si la red activa es de datos móviles (o medida)."""
    if not IS_ANDROID:
        return False
    try:
        from jnius import autoclass  # type: ignore
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        cm = act.getSystemService(Context.CONNECTIVITY_SERVICE)
        # «Solo con Wi-Fi» = conectado por Wi-Fi o cable (aunque Android marque esa red Wi-Fi
        # como «de uso medido», p. ej. un punto de acceso).
        caps = cm.getNetworkCapabilities(cm.getActiveNetwork())
        if caps is not None:
            NC = autoclass("android.net.NetworkCapabilities")
            if caps.hasTransport(NC.TRANSPORT_WIFI) or caps.hasTransport(NC.TRANSPORT_ETHERNET):
                return False
            return bool(caps.hasTransport(NC.TRANSPORT_CELLULAR)) or bool(cm.isActiveNetworkMetered())
        return bool(cm.isActiveNetworkMetered())
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------- respaldo
class DriveBackup:
    TOKEN_TTL = 45 * 60

    def __init__(self, db, authorizer=None, transport=None, metered=is_metered):
        self.db = db
        self._authorizer = authorizer
        self.transport = transport or urllib_transport
        self.metered = metered
        self._token: tuple[str, float] | None = None
        self._flush_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._again = False
        self.running = False
        self.listeners: list = []   # callbacks(status) al cambiar el estado

    # ----------------------------------------------------------- ajustes
    @property
    def enabled(self) -> bool:
        return bool(self.db.get_setting("drive_enabled", False))

    @property
    def wifi_only(self) -> bool:
        return bool(self.db.get_setting("drive_wifi_only", True))

    @property
    def authorizer(self):
        if self._authorizer is None and IS_ANDROID:
            # Se crea en el hilo principal: al registrarse para recibir el resultado de la
            # cuenta de Google, pyjnius crea un «listener» Java que solo encuentra las
            # clases de la app desde ese hilo (desde un hilo de fondo fallaba con
            # ClassNotFoundException · «No se encontró un componente…»).
            self._authorizer = AndroidAuthorizer._on_main(lambda: AndroidAuthorizer(log=self.log))
            self._authorizer.account = self.account
        return self._authorizer

    @property
    def account(self) -> str | None:
        """Correo de la cuenta de Google elegida (None en conexiones de versiones anteriores)."""
        return self.db.get_setting("drive_account") or None

    def log(self, message: str, error: bool = False) -> None:
        """Paso visible en la tarjeta de Ajustes + historial para «Ver diagnóstico»."""
        hist = self.db.get_setting("drive_log") or []
        hist.append(f"{_dt.datetime.now():%d/%m %H:%M:%S} {'✗ ' if error else ''}{message}")
        self.db.set_setting("drive_log", hist[-40:])
        self._set_message(message)

    def diagnostics(self) -> list[str]:
        """Archivos que no se pudieron subir (con su motivo) + historial reciente."""
        return self.failed_items() + list(reversed(self.db.get_setting("drive_log") or []))

    @property
    def available(self) -> bool:
        return self.authorizer is not None

    def status(self) -> dict:
        counts = {r["status"]: r["n"] for r in self.db.query(   # «skipped» no cuenta
            "SELECT status, COUNT(*) AS n FROM drive_queue GROUP BY status")}
        st = self.db.get_setting("drive_status") or {}
        return {"enabled": self.enabled, "available": self.available, "wifi_only": self.wifi_only,
                "pending": counts.get("pending", 0), "done": counts.get("done", 0),
                "errors": counts.get("error", 0), "running": self.running,
                "last_sync": st.get("last_sync"), "message": st.get("message", ""),
                "account": self.account}

    def _set_message(self, message: str, synced: bool = False) -> None:
        st = self.db.get_setting("drive_status") or {}
        st["message"] = message
        if synced:
            st["last_sync"] = _now()
        self.db.set_setting("drive_status", st)
        for cb in list(self.listeners):
            try:
                cb(self.status())
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- cola
    @staticmethod
    def week_folder(start_date: str) -> str:
        """«Año 2026/Semana 40 · 28-09-2026»: semana del año ISO (el número ordena las carpetas)."""
        d = _dt.date.fromisoformat(str(start_date)[:10])
        week, year = ph.iso_week(d)
        return f"Año {year}/Semana {week:02d} · {d:%d-%m-%Y}"

    @property
    def prefix(self) -> str:
        """Carpeta del ensayo o predio: «I+D/Nuevas variedades/», «Predio/El Amanecer/»."""
        ws = getattr(self.db, "workspace", None) or {}
        if not ws:
            return ""
        from workspaces import Workspaces
        return Workspaces.drive_prefix(ws) + "/"

    def week_path(self, start_date: str) -> str:
        return f"{self.prefix}{self.week_folder(start_date)}"

    def _remote_name(self, photo_id: int | None, path: str) -> str:
        """Carpeta de la SEMANA de muestreo de la foto + nombre del archivo."""
        row = None
        if photo_id is not None:
            row = self.db.query_one(
                "SELECT w.start_date FROM photos p "
                "JOIN observations o ON o.id = p.observation_id "
                "JOIN sampling_weeks w ON w.id = o.week_id WHERE p.id = ?", (photo_id,))
        base = os.path.basename(path)
        if row:
            return f"{self.week_path(row['start_date'])}/{base}"
        return f"{self.prefix}{base}"

    def enqueue_report(self, path: str, flush: bool = True) -> str:
        """Sube un informe: los semanales van a la carpeta de su semana; el resto a
        «Año …/Informes». Devuelve la ruta en Drive."""
        import re
        base = os.path.basename(path)
        remote = None
        m = re.match(r"semanal_(?:[A-Z0-9]{2,4}_)?(\d{4})_S(\d+)", base)   # semanal_NV_2026_S37
        old = re.match(r"semanal_T(\d{4})_S(\d+)", base)       # formato anterior (n.º de muestreo)
        if m:
            try:
                start = _dt.date.fromisocalendar(int(m[1]), int(m[2]), 1)
            except ValueError:
                start = None
            if start:
                w = self.db.query_one(
                    "SELECT start_date FROM sampling_weeks WHERE start_date BETWEEN ? AND ? "
                    "ORDER BY start_date LIMIT 1",
                    (start.isoformat(), (start + _dt.timedelta(days=6)).isoformat()))
                remote = f"{self.week_path(w['start_date'] if w else start.isoformat())}/{base}"
        elif old:
            w = self.db.query_one("SELECT start_date FROM sampling_weeks WHERE season=? AND week_number=?",
                                  (int(old[1]), int(old[2])))
            if w:
                remote = f"{self.week_path(w['start_date'])}/{base}"
        if remote is None:
            t = re.search(r"_T?(\d{4})(?=[_.-]|$)", base)
            year = int(t[1]) if t else _dt.date.today().year
            remote = f"{self.prefix}Año {year}/Informes/{base}"
        self.enqueue(None, path, remote=remote, flush=flush)
        return remote

    def enqueue(self, photo_id: int | None, path: str, flush: bool = True,
                remote: str | None = None) -> int | None:
        if photo_id is not None and self.db.query_one(
                "SELECT id FROM drive_queue WHERE photo_id=? AND path=?", (photo_id, path)):
            return None
        cur = self.db.execute(
            "INSERT INTO drive_queue(photo_id, path, name, created_at) VALUES (?,?,?,?)",
            (photo_id, path, remote or self._remote_name(photo_id, path), _now()))
        if flush:
            self.flush_async()
        return cur.lastrowid

    def enqueue_existing(self) -> int:
        """Encola todas las fotos guardadas que aún no están en la cola."""
        rows = self.db.query(
            "SELECT p.id, p.path FROM photos p WHERE NOT EXISTS "
            "(SELECT 1 FROM drive_queue q WHERE q.photo_id = p.id AND q.path = p.path)")
        n = 0
        for r in rows:
            if os.path.exists(r["path"]):
                self.enqueue(r["id"], r["path"], flush=False)
                n += 1
        if n:
            self.flush_async()
        return n

    DB_BACKUP_EVERY_H = 24
    DB_BACKUPS_KEPT = 5

    def backup_database(self, force: bool = False) -> str | None:
        """Copia diaria de la base (sin fotos, que ya están en Drive) a «Respaldos»."""
        last = self.db.get_setting("db_backup_last")      # por ensayo (no es ajuste global)
        if not force and last and (_dt.datetime.now() - _dt.datetime.fromisoformat(last)
                                   ).total_seconds() < self.DB_BACKUP_EVERY_H * 3600:
            return None
        from data_transfer import _checkpoint_copy
        from platform_utils import data_subdir
        folder = data_subdir("backups", "diarios")
        dest = _checkpoint_copy(self.db, os.path.join(
            folder, f"PhenoRubus_base_{self.db.code + '_' if self.db.code else ''}"
                    f"{_dt.datetime.now():%Y-%m-%d_%H%M}.sqlite3"))
        old = sorted(f for f in os.listdir(folder) if f.endswith(".sqlite3"))
        for f in old[:-self.DB_BACKUPS_KEPT]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
        self.db.set_setting("db_backup_last", _now())
        self.enqueue(None, dest, remote=f"{self.prefix}Respaldos/{os.path.basename(dest)}")
        return dest

    def retry_errors(self) -> None:
        self._skip_missing()
        self.db.execute("UPDATE drive_queue SET status='pending', attempts=0 WHERE status='error'")
        self.flush_async()

    def _skip_missing(self) -> int:
        """Entradas con error cuyo archivo ya no existe (foto borrada o reemplazada): se
        omiten para que no queden «pegadas» en 181 de 182."""
        n = 0
        for r in self.db.query("SELECT id, path FROM drive_queue WHERE status='error'"):
            if not os.path.exists(r["path"] or ""):
                self.db.execute("UPDATE drive_queue SET status='skipped', error=? WHERE id=?",
                                ("El archivo ya no existe en el teléfono", r["id"]))
                n += 1
        return n

    def failed_items(self) -> list[str]:
        return [f"✗ {os.path.basename(r['path'] or r['name'])}: {r['error'] or 'sin detalle'}"
                for r in self.db.query("SELECT path, name, error FROM drive_queue "
                                       "WHERE status='error' ORDER BY id LIMIT 20")]

    # ------------------------------------------------------------ conexión
    def connect(self, callback=None, choose: bool = True) -> None:
        """Autorización interactiva (desde Ajustes). callback(ok, mensaje).
        choose=True: primero la lista de cuentas de Google del teléfono (cambiar de cuenta)."""
        def work():
            try:
                self.log("Conectando con Google…")
                if not self.available:
                    raise DriveError("El respaldo en Drive está disponible en el teléfono (Android)")
                auth = self.authorizer
                if choose and hasattr(auth, "choose_account"):
                    name = auth.choose_account()
                    self.log(f"Cuenta elegida: {name}")
                    self.use_account(name)
                token = auth.get_token(interactive=True)
                self._token = (token, time.monotonic() + self.TOKEN_TTL)
                self.db.set_setting("drive_enabled", True)
                who = self.account
                self.log(f"Cuenta conectada{': ' + who if who else ''}")
                ok, msg = True, f"Google Drive conectado{' (' + who + ')' if who else ''}"
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, explain(str(exc)) or exc.__class__.__name__
                self.log(msg, error=True)
            if callback:
                callback(ok, msg)
            try:
                if ok:
                    self.flush()
            finally:
                _detach()

        threading.Thread(target=work, daemon=True).start()

    def use_account(self, name: str | None) -> None:
        """Cambia la cuenta de Google. Las carpetas en caché son de la cuenta anterior."""
        if (name or None) != self.account:
            self.db.set_setting("drive_account", name or None)
            self.db.set_setting("drive_folders", {})
            self._token = None
        if self._authorizer is not None:
            self._authorizer.account = name or None

    def _sync_account_queue(self) -> None:
        """Si se cambió de cuenta, lo ya subido a la cuenta anterior se vuelve a subir a la
        nueva (cada ensayo o predio lo hace al sincronizar)."""
        acc = self.account
        if not acc:
            return
        mine = self.db.get_setting("queue_account")    # ajuste de ESTE ensayo (no global)
        if mine and mine != acc:
            n = self.db.execute("UPDATE drive_queue SET status='pending', attempts=0, drive_id=NULL, "
                                "error='' WHERE status != 'pending'").rowcount
            self.log(f"Nueva cuenta {acc}: {n} archivos se suben de nuevo")
        if mine != acc:
            self.db.set_setting("queue_account", acc)

    def disconnect(self) -> None:
        """Deja de subir y retira el permiso de la app en la cuenta (para poder elegir otra)."""
        token = self._token[0] if self._token else None
        self.db.set_setting("drive_enabled", False)
        self._token = None
        if token:   # revocar en Google: la próxima conexión vuelve a pedir cuenta y permiso
            def revoke():
                try:
                    self.transport("POST", "https://oauth2.googleapis.com/revoke?" +
                                   urllib.parse.urlencode({"token": token}),
                                   {"Content-Type": "application/x-www-form-urlencoded"}, b"")
                except Exception:  # noqa: BLE001 - sin red: igual queda desconectada en la app
                    pass
            threading.Thread(target=revoke, daemon=True).start()
        self._set_message("Respaldo desactivado")

    def _get_token(self) -> str:
        if self._token and self._token[1] > time.monotonic():
            return self._token[0]
        if not self.available:
            raise NeedsConsent("Sin cuenta de Google conectada")
        token = self.authorizer.get_token(interactive=False)
        self._token = (token, time.monotonic() + self.TOKEN_TTL)
        return token

    # ------------------------------------------------------------ API REST
    def _call(self, method: str, url: str, body: bytes | None = None,
              content_type: str | None = None) -> dict:
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._get_token()}"}
            if content_type:
                headers["Content-Type"] = content_type
            status, data = self.transport(method, url, headers, body)
            if status == 401 and attempt == 0:
                self._token = None  # token vencido: pedir otro una vez
                continue
            if status >= 400:
                try:
                    msg = json.loads(data)["error"]["message"]
                except Exception:  # noqa: BLE001
                    msg = data[:200].decode("utf-8", "replace")
                if status == 401:
                    raise NeedsConsent("Google rechazó el acceso: reconecte su cuenta", status)
                raise DriveError(f"Drive {status}: {msg}", status)
            return json.loads(data or b"{}")
        raise NeedsConsent("Google rechazó el acceso")

    def _folder(self, name: str, parent: str | None) -> str:
        cache = self.db.get_setting("drive_folders") or {}
        key = f"{parent or 'root'}/{name}"
        if key in cache:
            return cache[key]
        esc = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name='{esc}' and mimeType='{FOLDER_MIME}' and trashed=false"
        if parent:
            q += f" and '{parent}' in parents"
        found = self._call("GET", f"{API}?" + urllib.parse.urlencode(
            {"q": q, "fields": "files(id)", "spaces": "drive"})).get("files", [])
        if found:
            fid = found[0]["id"]
        else:
            meta = {"name": name, "mimeType": FOLDER_MIME}
            if parent:
                meta["parents"] = [parent]
            fid = self._call("POST", f"{API}?fields=id", json.dumps(meta).encode(),
                             "application/json; charset=UTF-8")["id"]
        cache[key] = fid
        self.db.set_setting("drive_folders", cache)
        return fid

    def _upload(self, path: str, remote: str) -> str:
        *dirs, fname = remote.split("/")
        parent = self._folder(ROOT_FOLDER, None)
        for d in dirs:
            parent = self._folder(d, parent)
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime = {".png": "image/png", ".zip": "application/zip", ".sqlite3": "application/x-sqlite3",
                ".html": "text/html", ".pdf": "application/pdf",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(ext, "image/jpeg")
        boundary = uuid.uuid4().hex
        meta = json.dumps({"name": fname, "parents": [parent]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + meta + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
                + data + f"\r\n--{boundary}--\r\n".encode())
        try:
            return self._call("POST", UPLOAD, body, f"multipart/related; boundary={boundary}")["id"]
        except DriveError as exc:
            if exc.status == 404:  # la carpeta se borró en Drive: recrearla la próxima vez
                self.db.set_setting("drive_folders", {})
            raise

    LEGACY_TOP = ("Año ", "Temporada ", "Respaldos")

    def relocate_legacy(self) -> int:
        """Hasta la 1.1.33 todo se subía directo a la carpeta raíz. Una sola vez, lo
        existente (que es del ensayo «Nuevas variedades») pasa a su carpeta de ensayo."""
        if self.db.code != "NV" or self.db.get_setting("drive_layout_v2", False):
            return 0
        root = self._folder(ROOT_FOLDER, None)
        q = f"'{root}' in parents and mimeType='{FOLDER_MIME}' and trashed=false"
        found = self._call("GET", f"{API}?" + urllib.parse.urlencode(
            {"q": q, "fields": "files(id,name)", "spaces": "drive", "pageSize": "200"})).get("files", [])
        moving = [f for f in found if f["name"].startswith(self.LEGACY_TOP)]
        moved = 0
        if moving:
            dest = root
            for part in self.prefix.strip("/").split("/"):
                dest = self._folder(part, dest)
            for f in moving:
                self._call("PATCH", f"{API}/{f['id']}?" + urllib.parse.urlencode(
                    {"addParents": dest, "removeParents": root, "fields": "id"}),
                    b"{}", "application/json; charset=UTF-8")
                moved += 1
            self.db.set_setting("drive_folders", {})   # las rutas en caché cambiaron
            self.db.log("move", "drive", None, f"{moved} carpetas a {self.prefix.strip('/')}")
        self.db.set_setting("drive_layout_v2", True)
        return moved

    def rename_legacy(self) -> int:
        """Fotos ya subidas con el nombre antiguo «ddmmaaaa-…»: se renombran en Drive a
        «aaaammdd-…» (la app solo puede tocar los archivos que ella misma subió)."""
        from photo_rename import known_codes, new_name
        known = known_codes(self.db)
        done = 0
        todo = []
        for r in self.db.query("SELECT id, name, drive_id FROM drive_queue "
                               "WHERE status='done' AND drive_id IS NOT NULL"):
            head, _, base = (r["name"] or "").rpartition("/")
            nn = new_name(base, self.db.code, known)
            if nn:
                todo.append((r, head, nn))
        if todo:
            self.log(f"Renombrando {len(todo)} fotos ya subidas (aaaammdd-{self.db.code or ''}…)")
        for i, (r, head, nn) in enumerate(todo, 1):
            if i % 10 == 0:
                self._set_message(f"Renombrando en Drive {i} de {len(todo)}…")
            try:
                self._call("PATCH", f"{API}/{r['drive_id']}?fields=id",
                           json.dumps({"name": nn}).encode(), "application/json; charset=UTF-8")
                done += 1
            except DriveError as exc:
                if exc.status not in (403, 404):   # 404: ya no está en Drive; 403: no es de la app
                    raise
            self.db.execute("UPDATE drive_queue SET name=? WHERE id=?",
                            (f"{head}/{nn}" if head else nn, r["id"]))
        if done:
            self.db.log("rename", "drive", None, f"{done} fotos renombradas en Drive (aaaammdd)")
        return done

    # --------------------------------------------------------------- envío
    def flush_async(self) -> None:
        if self.enabled:
            threading.Thread(target=self._flush_thread, daemon=True).start()

    def _flush_thread(self) -> None:
        try:
            self.flush()
        finally:
            _detach()

    def flush(self) -> int:
        """Sube lo pendiente. Devuelve cuántas fotos se subieron."""
        if not self.enabled:
            return 0
        if not self._flush_lock.acquire(blocking=False):
            self._again = True  # hay otro envío en curso: que repase la cola al terminar
            return 0
        uploaded = failed = 0
        self.running = True
        try:
            if self.wifi_only and self.metered():
                raise Offline("Esperando Wi-Fi (datos móviles desactivados para el respaldo)")
            self._sync_account_queue()
            self._skip_missing()
            # Primero mover lo de versiones anteriores (rápido: unas pocas carpetas)…
            self.relocate_legacy()  # lo subido antes de los ensayos -> «I+D/Nuevas variedades»
            while True:
                self._again = False
                if self.wifi_only and self.metered():
                    raise Offline("Esperando Wi-Fi (datos móviles desactivados para el respaldo)")
                rows = self.db.query("SELECT * FROM drive_queue WHERE status='pending' ORDER BY id")
                if not rows:
                    break
                for i, r in enumerate(rows, 1):
                    if i == 1 or i % 5 == 0:
                        self._set_message(f"Subiendo {i} de {len(rows)}…")
                    if not os.path.exists(r["path"]):
                        # Foto borrada o reemplazada en la app: no hay nada que subir.
                        self.db.execute("UPDATE drive_queue SET status='skipped', error=? WHERE id=?",
                                        ("El archivo ya no existe en el teléfono", r["id"]))
                        self.log(f"Omitida (ya no está en el teléfono): {os.path.basename(r['path'])}")
                        continue
                    try:
                        # Fotos: la carpeta se calcula al subir (semana actual del registro,
                        # también para lo que quedó en cola con el esquema anterior).
                        name = (self._remote_name(r["photo_id"], r["path"]) if r["photo_id"]
                                else r["name"])
                        fid = self._upload(r["path"], name)
                    except (Offline, NeedsConsent):
                        raise
                    except DriveError as exc:
                        failed += 1
                        if failed <= 3:   # en el diagnóstico, sin llenarlo
                            self.log(f"{os.path.basename(r['path'])}: {explain(str(exc))[:140]}", error=True)
                        attempts = r["attempts"] + 1
                        self.db.execute(
                            "UPDATE drive_queue SET attempts=?, error=?, status=? WHERE id=?",
                            (attempts, str(exc), "error" if attempts >= MAX_ATTEMPTS else "pending",
                             r["id"]))
                        continue
                    self.db.execute("UPDATE drive_queue SET status='done', drive_id=?, error='', "
                                    "uploaded_at=? WHERE id=?", (fid, _now(), r["id"]))
                    uploaded += 1
                if not self._again:
                    left = self.db.query_one(
                        "SELECT COUNT(*) AS n FROM drive_queue WHERE status='pending' AND attempts=0")
                    if not left["n"]:
                        break
            if uploaded:
                self.log(f"{uploaded} archivo(s) subidos")
            # …y al final renombrar lo ya subido con el nombre antiguo (puede ser lento: no debe
            # retrasar las fotos nuevas).
            self.rename_legacy()   # nombres antiguos ya subidos -> aaaammdd-NV-… (una sola vez)
            pending = self.status()["pending"]
            message, synced = ("Todo respaldado" if not pending else f"{pending} en espera"), True
        except Offline as exc:
            message, synced = ("Esperando Wi-Fi" if "Wi-Fi" in str(exc)
                               else "Sin conexión: se subirá al volver la red"), False
        except NeedsConsent as exc:
            message, synced = explain(str(exc)), False
        except Exception as exc:  # noqa: BLE001
            message, synced = f"Error: {explain(str(exc))[:160]}", False
        finally:
            self.running = False
            self._flush_lock.release()
        if synced:
            self._set_message(message, synced=True)
        else:
            self.log(message, error=not message.startswith(("Esperando", "Sin conexión")))
        return uploaded
