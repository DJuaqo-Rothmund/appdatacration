"""
drive_backup.py
===============
Respaldo de fotos en Google Drive (además de la copia en el teléfono).

* Cada foto nueva se encola en `drive_queue` (SQLite): el respaldo funciona sin
  conexión y se sube en cuanto hay red (reintentos con límite).
* Carpeta en «Mi unidad»:  PhenoRubus · Imágenes de Fenología / Temporada 2026-2027 / …
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

from platform_utils import IS_ANDROID

SCOPE = "https://www.googleapis.com/auth/drive.file"
ROOT_FOLDER = "PhenoRubus · Imágenes de Fenología"
FOLDER_MIME = "application/vnd.google-apps.folder"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
MAX_ATTEMPTS = 6
RC_DRIVE_AUTH = 7301


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
class AndroidAuthorizer:
    """Token OAuth de Drive vía `Identity.getAuthorizationClient(activity)`."""

    def __init__(self):
        from jnius import autoclass  # type: ignore
        from android import activity  # type: ignore
        self._autoclass = autoclass
        self.PythonActivity = autoclass("org.kivy.android.PythonActivity")
        self._pending = None          # (event, holder) de una autorización interactiva
        self._listeners = []          # referencias vivas para pyjnius
        activity.bind(on_activity_result=self._on_activity_result)

    def _request(self):
        ac = self._autoclass
        scopes = ac("java.util.ArrayList")()
        scopes.add(ac("com.google.android.gms.common.api.Scope")(SCOPE))
        req = ac("com.google.android.gms.auth.api.identity.AuthorizationRequest") \
            .builder().setRequestedScopes(scopes).build()
        client = ac("com.google.android.gms.auth.api.identity.Identity") \
            .getAuthorizationClient(self.PythonActivity.mActivity)
        return client, req

    def get_token(self, interactive: bool, timeout: float = 120) -> str:
        from jnius import PythonJavaClass, java_method  # type: ignore

        done = threading.Event()
        box: dict = {}

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
                box["error"] = exc.toString()
                done.set()

        ok, ko = Success(), Failure()
        self._listeners = [ok, ko]
        client, req = self._request()
        client.authorize(req).addOnSuccessListener(ok).addOnFailureListener(ko)
        if not done.wait(timeout):
            raise DriveError("Google no respondió a la autorización")
        if "error" in box:
            raise DriveError(f"Autorización rechazada: {box['error']}")
        from jnius import cast  # type: ignore
        result = cast("com.google.android.gms.auth.api.identity.AuthorizationResult", box["result"])
        if result.hasResolution():
            if not interactive:
                raise NeedsConsent("Conecte su cuenta de Google en Ajustes")
            return self._resolve(result.getPendingIntent(), timeout)
        token = result.getAccessToken()
        if not token:
            raise DriveError("Google no entregó un token de acceso")
        return token

    def _resolve(self, pending_intent, timeout: float) -> str:
        from android.runnable import run_on_ui_thread  # type: ignore

        ev, holder = threading.Event(), {}
        self._pending = (ev, holder)

        @run_on_ui_thread
        def launch():
            self.PythonActivity.mActivity.startIntentSenderForResult(
                pending_intent.getIntentSender(), RC_DRIVE_AUTH, None, 0, 0, 0, None)

        launch()
        if not ev.wait(max(timeout, 300)):  # el usuario elige cuenta y acepta
            raise DriveError("No se completó la conexión con Google")
        if "error" in holder:
            raise DriveError(holder["error"])
        return holder["token"]

    def _on_activity_result(self, request_code, result_code, data):
        if request_code != RC_DRIVE_AUTH or self._pending is None:
            return
        ev, holder = self._pending
        self._pending = None
        try:
            if data is None:
                raise DriveError("Conexión cancelada")
            client, _req = self._request()
            res = client.getAuthorizationResultFromIntent(data)
            token = res.getAccessToken()
            if not token:
                raise DriveError("Conexión cancelada")
            holder["token"] = token
        except Exception as exc:  # noqa: BLE001 (ApiException de Java incluida)
            holder["error"] = str(exc) or "Conexión cancelada"
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
            self._authorizer = AndroidAuthorizer()
        return self._authorizer

    @property
    def available(self) -> bool:
        return self.authorizer is not None

    def status(self) -> dict:
        counts = {r["status"]: r["n"] for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM drive_queue GROUP BY status")}
        st = self.db.get_setting("drive_status") or {}
        return {"enabled": self.enabled, "available": self.available, "wifi_only": self.wifi_only,
                "pending": counts.get("pending", 0), "done": counts.get("done", 0),
                "errors": counts.get("error", 0), "running": self.running,
                "last_sync": st.get("last_sync"), "message": st.get("message", "")}

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
    def _remote_name(self, photo_id: int | None, path: str) -> str:
        """«Temporada 2026-2027/<archivo>» según la observación de la foto."""
        row = None
        if photo_id is not None:
            row = self.db.query_one(
                "SELECT w.season FROM photos p JOIN observations o ON o.id = p.observation_id "
                "JOIN sampling_weeks w ON w.id = o.week_id WHERE p.id = ?", (photo_id,))
        base = os.path.basename(path)
        if row:
            return f"Temporada {row['season']}-{row['season'] + 1}/{base}"
        return base

    def enqueue(self, photo_id: int | None, path: str, flush: bool = True) -> int | None:
        if photo_id is not None and self.db.query_one(
                "SELECT id FROM drive_queue WHERE photo_id=? AND path=?", (photo_id, path)):
            return None
        cur = self.db.execute(
            "INSERT INTO drive_queue(photo_id, path, name, created_at) VALUES (?,?,?,?)",
            (photo_id, path, self._remote_name(photo_id, path), _now()))
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

    def retry_errors(self) -> None:
        self.db.execute("UPDATE drive_queue SET status='pending', attempts=0 WHERE status='error'")
        self.flush_async()

    # ------------------------------------------------------------ conexión
    def connect(self, callback=None) -> None:
        """Autorización interactiva (desde Ajustes). callback(ok, mensaje)."""
        def work():
            try:
                if not self.available:
                    raise DriveError("El respaldo en Drive está disponible en el teléfono (Android)")
                token = self.authorizer.get_token(interactive=True)
                self._token = (token, time.monotonic() + self.TOKEN_TTL)
                self.db.set_setting("drive_enabled", True)
                self._set_message("Cuenta conectada")
                ok, msg = True, "Google Drive conectado"
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, str(exc)
            if callback:
                callback(ok, msg)
            if ok:
                self.flush()

        threading.Thread(target=work, daemon=True).start()

    def disconnect(self) -> None:
        self.db.set_setting("drive_enabled", False)
        self._token = None
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
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
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

    # --------------------------------------------------------------- envío
    def flush_async(self) -> None:
        if self.enabled:
            threading.Thread(target=self.flush, daemon=True).start()

    def flush(self) -> int:
        """Sube lo pendiente. Devuelve cuántas fotos se subieron."""
        if not self.enabled:
            return 0
        if not self._flush_lock.acquire(blocking=False):
            self._again = True  # hay otro envío en curso: que repase la cola al terminar
            return 0
        uploaded = 0
        self.running = True
        try:
            while True:
                self._again = False
                if self.wifi_only and self.metered():
                    raise Offline("Esperando Wi-Fi (datos móviles desactivados para el respaldo)")
                rows = self.db.query("SELECT * FROM drive_queue WHERE status='pending' ORDER BY id")
                if not rows:
                    break
                for r in rows:
                    if not os.path.exists(r["path"]):
                        self.db.execute("UPDATE drive_queue SET status='error', error=? WHERE id=?",
                                        ("El archivo ya no existe en el teléfono", r["id"]))
                        continue
                    try:
                        fid = self._upload(r["path"], r["name"])
                    except (Offline, NeedsConsent):
                        raise
                    except DriveError as exc:
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
            pending = self.status()["pending"]
            message, synced = ("Todo respaldado" if not pending else f"{pending} en espera"), True
        except Offline as exc:
            message, synced = ("Esperando Wi-Fi" if "Wi-Fi" in str(exc)
                               else "Sin conexión: se subirá al volver la red"), False
        except NeedsConsent as exc:
            message, synced = str(exc), False
        except Exception as exc:  # noqa: BLE001
            message, synced = f"Error: {str(exc)[:120]}", False
        finally:
            self.running = False
            self._flush_lock.release()
        self._set_message(message, synced=synced)
        return uploaded
