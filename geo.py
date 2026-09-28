"""
geo.py
======
Ubicación (opcional) de cada muestra.

* `exif_gps(path)`: coordenadas GPS guardadas por la cámara en la foto (EXIF).
* `LocationRequest`: posición actual del teléfono (LocationManager de Android,
  sin librerías extra). Se llama desde el hilo principal de Kivy.
* Utilidades de teselas OpenStreetMap para el mapa (`ui.mapview.MapPicker`),
  con caché en disco para reutilizarlas sin conexión.
"""
from __future__ import annotations

import math
import os
import threading
import urllib.request

from platform_utils import IS_ANDROID, data_subdir

TILE_SIZE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "PhenoRubus/1.1 (cuaderno fenologico de frambueso; Android)"
DEFAULT_CENTER = (-33.45, -70.66)   # Chile central (vista inicial sin otra referencia)


def fmt(lat: float, lon: float) -> str:
    return f"{lat:.6f}, {lon:.6f}"


def osm_link(lat: float, lon: float, zoom: int = 18) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"


# ------------------------------------------------------------------- EXIF
def _to_deg(value) -> float:
    d, m, s = (float(x) for x in value)
    return d + m / 60 + s / 3600


def exif_gps(path: str) -> tuple[float, float] | None:
    """(lat, lon) de la foto, o None si no tiene ubicación."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            gps = img.getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None
        lat, lon = _to_deg(gps[2]), _to_deg(gps[4])
        if str(gps.get(1, "N")).upper().startswith("S"):
            lat = -lat
        if str(gps.get(3, "E")).upper().startswith("W"):
            lon = -lon
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ teselas OSM
def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lr = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n
    return x, y


def tile_to_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def tile_path(z: int, x: int, y: int) -> str:
    return os.path.join(data_subdir("tiles", str(z), str(x)), f"{y}.png")


_fetching: set = set()
_lock = threading.Lock()


def fetch_tile(z: int, x: int, y: int) -> str | None:
    """Descarga (una vez) la tesela y devuelve su ruta en caché; None si falla."""
    n = 2 ** z
    if not (0 <= y < n):
        return None
    x %= n
    path = tile_path(z, x, y)
    if os.path.exists(path):
        return path
    key = (z, x, y)
    with _lock:
        if key in _fetching:
            return None
        _fetching.add(key)
    try:
        from drive_backup import _ssl_context
        req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y),
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            data = r.read()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
    except Exception:  # noqa: BLE001 (sin red: se muestra la cuadrícula vacía)
        return None
    finally:
        with _lock:
            _fetching.discard(key)


# ---------------------------------------------------- ubicación del teléfono
LOCATION_PERMISSIONS = ["android.permission.ACCESS_FINE_LOCATION",
                        "android.permission.ACCESS_COARSE_LOCATION"]


def request_location_permission(callback) -> None:
    """callback(bool) con el permiso de ubicación concedido o no."""
    if not IS_ANDROID:
        callback(True)
        return
    from android.permissions import check_permission, request_permissions  # type: ignore
    if any(check_permission(p) for p in LOCATION_PERMISSIONS):
        callback(True)
        return
    request_permissions(LOCATION_PERMISSIONS, lambda perms, grants: callback(any(grants)))


class LocationRequest:
    """Posición precisa del teléfono. callback(lat, lon, precisión_m) o error(msg).

    Escucha GPS (satélites) y red a la vez con LocationManager.requestLocationUpdates,
    guarda la mejor lectura y termina en cuanto la precisión llega a `target` metros
    (±10 m por defecto) o al vencer `timeout`, entregando la mejor obtenida.
    `progress(lat, lon, precisión)` informa cada mejora (para mostrarla en vivo).
    Debe iniciarse en el hilo principal de Kivy (clases resueltas por pyjnius).
    """

    TARGET_M = 10.0
    TIMEOUT_S = 45
    MAX_AGE_S = 20   # lecturas «últimas conocidas» más viejas no sirven como punto de partida

    def __init__(self, callback, error, progress=None, target: float | None = None):
        self.callback, self.error, self.progress = callback, error, progress
        self.target = target or self.TARGET_M
        self._refs = []
        self._best = None      # (precisión, lat, lon)
        self._done = False
        self._lm = None
        self._timer = None

    # ------------------------------------------------------------- lecturas
    def _offer(self, loc):
        """Llamado desde el hilo de Android con cada nueva posición."""
        if loc is None or self._done:
            return
        try:
            acc = float(loc.getAccuracy()) if loc.hasAccuracy() else 999.0
            lat, lon = float(loc.getLatitude()), float(loc.getLongitude())
        except Exception:  # noqa: BLE001
            return
        if self._best is None or acc < self._best[0]:
            self._best = (acc, lat, lon)
            from kivy.clock import Clock
            if self.progress:
                Clock.schedule_once(lambda *_: self.progress(lat, lon, acc) if not self._done else None)
            if acc <= self.target:
                Clock.schedule_once(lambda *_: self.finish())

    def finish(self, *_):
        """Termina y entrega la mejor lectura (puede llamarse para «usar ya»)."""
        if self._done:
            return
        self._done = True
        self._stop_updates()
        if self._timer is not None:
            self._timer.cancel()
        if self._best is None:
            self.error("No se obtuvo la ubicación. Active la ubicación del teléfono (modo alta "
                       "precisión) y reintente al aire libre.")
            return
        acc, lat, lon = self._best
        self.callback(lat, lon, acc)

    def cancel(self):
        self._done = True
        self._stop_updates()
        if self._timer is not None:
            self._timer.cancel()

    def _stop_updates(self):
        if self._lm is None:
            return
        for lis in self._refs:
            try:
                self._lm.removeUpdates(lis)
            except Exception:  # noqa: BLE001
                pass

    # --------------------------------------------------------------- inicio
    def start(self, timeout: float | None = None) -> None:
        if not IS_ANDROID:
            self.error("La ubicación GPS está disponible en el teléfono.")
            return
        import time
        from jnius import PythonJavaClass, autoclass, java_method  # type: ignore
        from kivy.clock import Clock
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        lm = act.getSystemService(Context.LOCATION_SERVICE)
        self._lm = lm
        LocationManager = autoclass("android.location.LocationManager")
        providers = [p for p in (LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                     if lm.getAllProviders().contains(p) and lm.isProviderEnabled(p)]
        if not providers:
            self.error("La ubicación del teléfono está desactivada. Actívela en Ajustes rápidos.")
            return
        if LocationManager.GPS_PROVIDER not in providers:
            self.error("El GPS del teléfono está desactivado: active «Ubicación» en modo de alta "
                       "precisión para lograr ±10 m.")
            return
        me = self

        class Listener(PythonJavaClass):
            """Implementa TODOS los métodos (también los «default» de Android 11+):
            un método que falte haría fallar la llamada desde Java."""
            __javainterfaces__ = ["android/location/LocationListener"]
            __javacontext__ = "app"

            @java_method("(Landroid/location/Location;)V", name="onLocationChanged")
            def on_location(self, loc):
                me._offer(loc)

            @java_method("(Ljava/util/List;)V", name="onLocationChanged")
            def on_locations(self, locs):
                try:
                    for i in range(locs.size()):
                        me._offer(locs.get(i))
                except Exception:  # noqa: BLE001
                    pass

            @java_method("(I)V")
            def onFlushComplete(self, code):  # noqa: N802
                pass

            @java_method("(Ljava/lang/String;ILandroid/os/Bundle;)V")
            def onStatusChanged(self, provider, status, extras):  # noqa: N802
                pass

            @java_method("(Ljava/lang/String;)V")
            def onProviderEnabled(self, provider):  # noqa: N802
                pass

            @java_method("(Ljava/lang/String;)V")
            def onProviderDisabled(self, provider):  # noqa: N802
                pass

        looper = autoclass("android.os.Looper").getMainLooper()
        for prov in providers:
            lis = Listener()
            self._refs.append(lis)
            lm.requestLocationUpdates(prov, 500, 0.0, lis, looper)
        # Punto de partida inmediato: última posición reciente del teléfono (si hay).
        now_ms = time.time() * 1000
        for prov in providers:
            try:
                last = lm.getLastKnownLocation(prov)
                if last is not None and now_ms - last.getTime() < self.MAX_AGE_S * 1000:
                    self._offer(last)
            except Exception:  # noqa: BLE001
                pass
        self._timer = Clock.schedule_once(self.finish, timeout or self.TIMEOUT_S)
